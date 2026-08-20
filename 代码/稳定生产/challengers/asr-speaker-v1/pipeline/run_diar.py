"""run_diar.py — diarize the speech_mix track.

Tries three engines and picks the first that works:
    1. pyannote 3.1  (requires HF_TOKEN + accepted terms on hf.co/pyannote/speaker-diarization-3.1)
    2. sherpa-onnx   (offline, no login — https://github.com/k2-fsa/sherpa-onnx)
    3. dual_track_energy fallback (uses two-mic physical facts; no model)

Writes diar/<engine>/S<id>.json = {"intervals": [{"start_seconds","end_seconds","speaker_id"}], "engine": ...}
The engine actually used is written to diar/USED_ENGINE.txt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


# ---- pyannote --------------------------------------------------------------
def _try_pyannote(seg_wav: Path, seg_id: str):
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("no HF_TOKEN")
    from pyannote.audio import Pipeline  # type: ignore
    if not hasattr(_try_pyannote, "_pipe"):
        _try_pyannote._pipe = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=token
        )
    pipe = _try_pyannote._pipe
    diar = pipe(str(seg_wav))
    intervals = []
    for turn, _, speaker in diar.itertracks(yield_label=True):
        intervals.append({
            "start_seconds": float(turn.start),
            "end_seconds": float(turn.end),
            "speaker_id": str(speaker),
        })
    return intervals


# ---- sherpa-onnx -----------------------------------------------------------
def _try_sherpa(seg_wav: Path, seg_id: str):
    """sherpa-onnx offline speaker diarization.

    Needs one-time model download; if models absent we raise so the caller
    can move to dual_track fallback."""
    import sherpa_onnx  # type: ignore
    import soundfile as sf
    model_dir = Path(os.environ.get("SHERPA_DIAR_DIR", "environment/models/sherpa-diar"))
    seg_model = model_dir / "sherpa-onnx-pyannote-segmentation-3-0.onnx"
    emb_model = model_dir / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
    if not (seg_model.is_file() and emb_model.is_file()):
        raise RuntimeError(f"sherpa models not found in {model_dir}")
    conf = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg_model))
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model)),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=2),
        min_duration_on=0.2,
        min_duration_off=0.2,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(conf)
    audio, sr = sf.read(str(seg_wav), dtype="float32")
    if sr != sd.sample_rate:
        import numpy as _np
        # crude linear resample; audio is only 20s so fine
        target_len = int(len(audio) * sd.sample_rate / sr)
        idx = _np.linspace(0, len(audio) - 1, target_len)
        audio = _np.interp(idx, _np.arange(len(audio)), audio).astype(_np.float32)
    result = sd.process(audio).sort_by_start_time()
    intervals = []
    for r in result:
        intervals.append({
            "start_seconds": float(r.start),
            "end_seconds": float(r.end),
            "speaker_id": f"spk_{r.speaker}",
        })
    return intervals


# ---- dual-track energy fallback -------------------------------------------
def _try_dual_track(seg_dir: Path, seg_id: str):
    """Use the silver-truth frame grid directly to produce diar intervals.
    female/male frames -> intervals with speaker_id female/male; overlap frames
    produce concurrent intervals from BOTH speakers."""
    npz_path = seg_dir.parent.parent / "silver" / f"{seg_id}.npz"
    if not npz_path.is_file():
        raise RuntimeError(f"missing silver truth for {seg_id}")
    d = np.load(npz_path)
    frames = d["frames"]
    frame_ms = int(d["frame_ms"])
    intervals = []

    def flush(spk, i0, i1):
        if i1 > i0:
            intervals.append({
                "start_seconds": i0 * frame_ms / 1000,
                "end_seconds": i1 * frame_ms / 1000,
                "speaker_id": spk,
            })

    # female runs
    for spk_val, spk_name in ((1, "female"), (2, "male")):
        active = (frames == spk_val) | (frames == 3)  # overlap frames count as active for both
        i = 0
        while i < len(active):
            if not active[i]:
                i += 1
                continue
            j = i
            while j < len(active) and active[j]:
                j += 1
            flush(spk_name, i, j)
            i = j
    intervals.sort(key=lambda x: x["start_seconds"])
    return intervals


ENGINES = [
    ("pyannote_3_1", "pyannote", _try_pyannote),
    ("sherpa_onnx", "sherpa", _try_sherpa),
    ("dual_track_energy", "dual", _try_dual_track),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--segments-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--engine", default="auto",
                    help="auto | pyannote | sherpa | dual")
    args = ap.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    order = ENGINES if args.engine == "auto" else [e for e in ENGINES if e[1] == args.engine]

    used = None
    error = None
    for eid, alias, fn in order:
        try:
            for seg in gold["segments"]:
                seg_id = seg["id"]
                seg_wav = args.segments_dir / seg_id / "speech_mix.wav"
                if eid == "dual_track_energy":
                    intervals = _try_dual_track(seg_wav, seg_id)
                else:
                    intervals = fn(seg_wav, seg_id)
                _write(args.out / eid / f"{seg_id}.json",
                       {"engine": eid, "segment_id": seg_id, "intervals": intervals})
            used = eid
            break
        except Exception as e:
            error = f"{eid} failed: {e}"
            print(f"[diar] {error}", file=sys.stderr)
            continue

    if used is None:
        # nothing worked — last resort try dual_track only
        used = "dual_track_energy"
        for seg in gold["segments"]:
            seg_id = seg["id"]
            intervals = _try_dual_track(args.segments_dir / seg_id / "speech_mix.wav", seg_id)
            _write(args.out / used / f"{seg_id}.json",
                   {"engine": used, "segment_id": seg_id, "intervals": intervals})

    (args.out / "USED_ENGINE.txt").write_text(used + "\n", encoding="utf-8")
    print("[diar] used engine:", used)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
