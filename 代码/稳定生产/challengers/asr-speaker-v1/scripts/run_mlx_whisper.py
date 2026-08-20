"""Runner: MLX Whisper on the 12 benchmark segments (Apple M3 only).

Requires:
    pip install mlx mlx-whisper (Apple Silicon arm64 only)

Downloads model once into --model-dir; subsequent runs use HF_HUB_OFFLINE=1.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def _prepare_offline() -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-json", type=Path, required=True)
    ap.add_argument("--segments-dir", type=Path, required=True)
    ap.add_argument("--raw-out", type=Path, required=True)
    ap.add_argument("--model-repo", default="mlx-community/whisper-large-v3-turbo",
                    help="hf repo id or a local dir (must exist).")
    ap.add_argument("--condition-on-previous-text", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    _prepare_offline()

    gold = json.loads(args.gold_json.read_text(encoding="utf-8"))
    if args.dry_run:
        for seg in gold["segments"]:
            print(seg["id"])
        return 0

    import mlx_whisper  # type: ignore

    engine_v = _capture_versions()
    runtime = []
    is_first_run = True
    for seg in gold["segments"]:
        seg_id = seg["id"]
        offset = float(seg["start_seconds_in_ep03"])
        for track in ("female", "male", "speech_mix"):
            wav = args.segments_dir / seg_id / f"{track}.wav"
            if not wav.is_file():
                continue
            t0 = time.perf_counter()
            result = mlx_whisper.transcribe(
                str(wav),
                path_or_hf_repo=args.model_repo,
                language="zh",
                word_timestamps=True,
                condition_on_previous_text=bool(args.condition_on_previous_text),
            )
            t_wall = time.perf_counter() - t0
            _write(args.raw_out / "mlx_whisper_turbo" / seg_id / f"{track}.raw.json", result)
            runtime.append({
                "segment_id": seg_id, "track": track,
                "wall_s": t_wall,
                "first_run": is_first_run,
                "offset_seconds_in_ep03": offset,
            })
            is_first_run = False

    _write(args.raw_out / "mlx_run_meta.json", {
        "engine_versions": engine_v,
        "model_repo": args.model_repo,
        "condition_on_previous_text": bool(args.condition_on_previous_text),
        "runtime": runtime,
    })
    print("done")
    return 0


def _capture_versions() -> dict:
    try:
        import mlx  # type: ignore
        mlx_v = getattr(mlx, "__version__", "unknown")
    except Exception as e:
        mlx_v = f"import-failed: {e}"
    try:
        import mlx_whisper  # type: ignore
        mw_v = getattr(mlx_whisper, "__version__", "unknown")
    except Exception as e:
        mw_v = f"import-failed: {e}"
    return {"mlx": mlx_v, "mlx_whisper": mw_v}


if __name__ == "__main__":
    raise SystemExit(main())
