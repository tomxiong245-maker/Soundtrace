"""run_asr_mlx.py — MLX Whisper Turbo on M3 Metal."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _normalize(raw: dict, seg_id: str, track: str, offset: float, model_id: str) -> dict:
    words = []
    for seg in raw.get("segments", []):
        for w in seg.get("words", []) or []:
            words.append({
                "text": (w.get("word") or "").strip(),
                "raw_text": w.get("word", ""),
                "start_seconds": float(w["start"]),
                "end_seconds": float(w["end"]),
                "confidence": float(w["probability"]) if w.get("probability") is not None else None,
            })
    text = "".join(w["text"] for w in words)
    reliable = bool(words)
    return {
        "engine": "mlx_whisper_turbo",
        "model_id": model_id,
        "segment_id": seg_id,
        "source_track": track,
        "segment_start_offset_seconds_in_ep03": offset,
        "text": text,
        "words": words,
        "timestamp_reliable": reliable,
        "clipping_eligible": reliable,
        "not_eligible_reason": None if reliable else "no_word_level_timestamps",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--segments-dir", type=Path, required=True)
    ap.add_argument("--raw-out", type=Path, required=True)
    ap.add_argument("--norm-out", type=Path, required=True)
    ap.add_argument("--model-repo", default="mlx-community/whisper-large-v3-turbo")
    args = ap.parse_args()

    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    import mlx_whisper  # type: ignore

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    runtime = []
    first = True
    for seg in gold["segments"]:
        seg_id = seg["id"]
        offset = float(seg["start_seconds_in_ep03"])
        for track in ("female", "male", "speech_mix"):
            wav = args.segments_dir / seg_id / f"{track}.wav"
            if not wav.is_file():
                continue
            t0 = time.perf_counter()
            r = mlx_whisper.transcribe(
                str(wav), path_or_hf_repo=args.model_repo,
                language="zh", word_timestamps=True,
                condition_on_previous_text=False,
            )
            wall = time.perf_counter() - t0
            _write(args.raw_out / seg_id / f"{track}.raw.json", r)
            _write(args.norm_out / seg_id / f"{track}.words.json",
                   _normalize(r, seg_id, track, offset, args.model_repo))
            runtime.append({"seg": seg_id, "track": track, "wall_s": wall, "first_run": first})
            first = False
    _write(args.raw_out / "run_meta.json",
           {"engine": "mlx_whisper_turbo", "model_repo": args.model_repo, "runtime": runtime})
    print("mlx ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
