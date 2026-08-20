"""run_asr_sensevoice.py — SenseVoice-Small via FunASR AutoModel.

Runs on the M3, needs venv-sensevoice. Reads segments/S*/{female,male,speech_mix}.wav,
writes raw + normalized to raw/sensevoice_small/ and normalized/sensevoice_small/.

SenseVoice-Small is https://github.com/FunAudioLLM/SenseVoice — 2024 model, better
zh-CN CER than paraformer, tokens carry per-char timestamps via 'timestamp' field
when model exposes it; if not, we fall back to CTC alignment via
`generate(..., use_itn=True, output_timestamp=True)`.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def _prepare_offline() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("MODELSCOPE_TELEMETRY_ENABLED", "0")


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _normalize(raw_entry: dict, seg_id: str, track: str, offset: float, model_id: str) -> dict:
    """Convert SenseVoice output to normalized words list.

    SenseVoice returns text with special tokens like <|zh|><|NEUTRAL|><|Speech|><|withitn|>
    followed by transcription. We strip these tokens. If token-level timestamps exist
    they're in `timestamp` (list of [start_ms, end_ms]); otherwise we distribute uniformly.
    """
    import re
    text = raw_entry.get("text", "")
    # strip funasr special tokens
    cleaned = re.sub(r"<\|[^|]+\|>", "", text).strip()
    ts = raw_entry.get("timestamp") or []
    # SenseVoice sometimes emits chars glued; split unicode chars
    chars = list(cleaned.replace(" ", ""))
    words = []
    if ts and len(ts) == len(chars):
        for i, (c, tp) in enumerate(zip(chars, ts)):
            s_ms, e_ms = float(tp[0]), float(tp[1])
            words.append({
                "text": c, "raw_text": c,
                "start_seconds": s_ms / 1000, "end_seconds": e_ms / 1000,
                "confidence": None,
            })
    else:
        # A uniform split is not a timestamp. It may be useful for a text-only
        # CER experiment, but it is unsafe for filler/pause candidates and must
        # never enter the clipping layer.
        words = []
    return {
        "engine": "sensevoice_small",
        "model_id": model_id,
        "segment_id": seg_id,
        "source_track": track,
        "segment_start_offset_seconds_in_ep03": offset,
        "text": cleaned,
        "words": words,
        "timestamp_reliable": bool(ts) and len(ts) == len(chars),
        "clipping_eligible": bool(ts) and len(ts) == len(chars),
        "not_eligible_reason": None if (ts and len(ts) == len(chars)) else "no_character_level_timestamps",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--segments-dir", type=Path, required=True)
    ap.add_argument("--raw-out", type=Path, required=True)
    ap.add_argument("--norm-out", type=Path, required=True)
    ap.add_argument("--model", default="iic/SenseVoiceSmall")
    args = ap.parse_args()

    _prepare_offline()
    from funasr import AutoModel  # type: ignore
    model = AutoModel(model=args.model, disable_update=True, device="cpu")

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    runtime = []
    for seg in gold["segments"]:
        seg_id = seg["id"]
        offset = float(seg["start_seconds_in_ep03"])
        for track in ("female", "male", "speech_mix"):
            wav = args.segments_dir / seg_id / f"{track}.wav"
            if not wav.is_file():
                continue
            t0 = time.perf_counter()
            raw = model.generate(input=str(wav), cache={}, language="zn",
                                 use_itn=True, batch_size_s=60)
            wall = time.perf_counter() - t0
            entry = raw[0] if isinstance(raw, list) else raw
            _write(args.raw_out / seg_id / f"{track}.raw.json", entry)
            norm = _normalize(entry, seg_id, track, offset, args.model)
            _write(args.norm_out / seg_id / f"{track}.words.json", norm)
            runtime.append({"seg": seg_id, "track": track, "wall_s": wall,
                            "chars": len(norm["words"])})
    _write(args.raw_out / "run_meta.json", {
        "engine": "sensevoice_small",
        "model_id": args.model,
        "runtime": runtime,
    })
    print("sensevoice ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
