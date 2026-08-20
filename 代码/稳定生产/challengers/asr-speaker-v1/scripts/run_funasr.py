"""Runner: FunASR Paraformer + FSMN-VAD + CAM++ on the 12 benchmark segments.

MUST be executed on the Apple M3 host. Reads only from
    benchmark/EP03-ASR-mini-gold-v1/segments/S*/{female,male,speech_mix}.wav
Writes only to
    main/runs/EP03-asr-speaker-v1/raw/funasr_*/

Enforces offline inference by setting HF_HUB_OFFLINE=1 & MODELSCOPE_OFFLINE=1
BEFORE importing funasr; you must have downloaded models once (see
exact_commands.sh) or the run will fail loudly, not silently phone home.

Refuses hot-words by default (only accept them via --hotwords-file with an
explicit non-empty path AND --hotwords-source string that names the origin).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def _prepare_offline() -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
    os.environ.setdefault("MODELSCOPE_TELEMETRY_ENABLED", "0")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _load_funasr_models(paraformer_dir: str, vad_dir: str, campp_dir: str):
    from funasr import AutoModel  # type: ignore

    asr = AutoModel(model=paraformer_dir, disable_update=True)
    vad = AutoModel(model=vad_dir, disable_update=True)
    dia = AutoModel(model=campp_dir, disable_update=True)
    return asr, vad, dia


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-json", type=Path, required=True)
    ap.add_argument("--segments-dir", type=Path, required=True)
    ap.add_argument("--raw-out", type=Path, required=True)
    ap.add_argument("--paraformer-dir", required=True,
                    help="Local dir of the Paraformer-large-zh-timestamp model.")
    ap.add_argument("--vad-dir", required=True)
    ap.add_argument("--campp-dir", required=True)
    ap.add_argument("--hotwords-file", type=Path, default=None)
    ap.add_argument("--hotwords-source", default="",
                    help="Human-readable provenance; required if --hotwords-file is set.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.hotwords_file and not args.hotwords_source:
        raise SystemExit("--hotwords-source is required when --hotwords-file is set")

    _prepare_offline()

    gold = json.loads(args.gold_json.read_text(encoding="utf-8"))
    if args.dry_run:
        print("planned runs:")
        for seg in gold["segments"]:
            print(f"  {seg['id']}: {seg['duration_seconds']}s")
        return 0

    hotwords = None
    hotwords_meta = None
    if args.hotwords_file:
        hotwords = args.hotwords_file.read_text(encoding="utf-8").splitlines()
        hotwords_meta = {"count": len(hotwords), "source": args.hotwords_source,
                         "file": str(args.hotwords_file)}

    asr, vad, dia = _load_funasr_models(args.paraformer_dir, args.vad_dir, args.campp_dir)

    runtime = []
    for seg in gold["segments"]:
        seg_id = seg["id"]
        offset = float(seg["start_seconds_in_ep03"])
        for track in ("female", "male", "speech_mix"):
            wav = args.segments_dir / seg_id / f"{track}.wav"
            if not wav.is_file():
                continue
            # ---- ASR ----
            t0 = time.perf_counter()
            asr_kwargs = {}
            if hotwords is not None:
                asr_kwargs["hotword"] = " ".join(hotwords)
            asr_raw = asr.generate(input=str(wav), **asr_kwargs)
            t_asr = time.perf_counter() - t0
            _write(args.raw_out / "funasr_paraformer" / seg_id / f"{track}.raw.json", asr_raw)
            # ---- VAD ----
            t0 = time.perf_counter()
            vad_raw = vad.generate(input=str(wav))
            t_vad = time.perf_counter() - t0
            _write(args.raw_out / "funasr_fsmn_vad" / seg_id / f"{track}.raw.json", vad_raw)
            # ---- speaker diarization only on speech_mix ----
            t_dia = None
            if track == "speech_mix":
                t0 = time.perf_counter()
                dia_raw = dia.generate(input=str(wav))
                t_dia = time.perf_counter() - t0
                _write(args.raw_out / "funasr_campp" / seg_id / f"{track}.raw.json", dia_raw)
            runtime.append({
                "segment_id": seg_id, "track": track,
                "asr_wall_s": t_asr, "vad_wall_s": t_vad,
                "dia_wall_s": t_dia,
                "offset_seconds_in_ep03": offset,
                "hotwords": hotwords_meta,
            })

    _write(args.raw_out / "run_meta.json", {
        "engine_versions": _capture_versions(),
        "hotwords": hotwords_meta,
        "runtime": runtime,
    })
    print("done")
    return 0


def _capture_versions() -> dict:
    try:
        import funasr  # type: ignore
        funasr_v = getattr(funasr, "__version__", "unknown")
    except Exception as e:
        funasr_v = f"import-failed: {e}"
    try:
        import torch  # type: ignore
        torch_v = torch.__version__
    except Exception as e:
        torch_v = f"import-failed: {e}"
    return {"funasr": funasr_v, "torch": torch_v}


if __name__ == "__main__":
    raise SystemExit(main())
