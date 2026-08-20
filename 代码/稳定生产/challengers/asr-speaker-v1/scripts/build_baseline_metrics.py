"""Write main/runs/EP03-asr-speaker-v1/before_metrics.json.

Records:
 - the 12 benchmark segments (id, start, duration, note, files.*.sha256 from gold.json)
 - the frozen baseline SHA-256 (copied from cross-track-safety-v1/before_metrics.json)
 - status per engine (BASELINE_FROM_CHAMPION_SLICE / WAITING_FOR_M3_RUN)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as h:
        while chunk := h.read(1024 * 1024):
            d.update(chunk)
    return d.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--baseline-sha-file", type=Path, required=True,
                    help="cross-track-safety-v1/before_metrics.json (source of frozen SHAs)")
    ap.add_argument("--segments-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    frozen = json.loads(args.baseline_sha_file.read_text(encoding="utf-8"))["baseline_sha256"]

    # Verify audio SHA-256 against gold.json (self-consistency; SHAs are stored inside gold.json).
    seg_reports = []
    for seg in gold["segments"]:
        entry = {"id": seg["id"], "start_seconds_in_ep03": seg["start_seconds_in_ep03"],
                 "duration_seconds": seg["duration_seconds"], "note": seg.get("note", ""),
                 "linked_candidate": seg.get("linked_candidate"), "tracks": {}}
        for track in ("female", "male", "speech_mix"):
            wav_key = f"{track}_wav"
            sha_key = f"{track}_wav_sha256"
            rel = seg["files"].get(wav_key)
            expected_sha = seg["files"].get(sha_key)
            if not rel:
                entry["tracks"][track] = {"status": "MISSING_IN_GOLD_JSON"}
                continue
            actual_path = args.segments_dir / seg["id"] / f"{track}.wav"
            if not actual_path.is_file():
                entry["tracks"][track] = {"status": "FILE_NOT_FOUND", "path": str(actual_path)}
                continue
            actual_sha = sha256_file(actual_path)
            entry["tracks"][track] = {
                "status": "OK" if actual_sha == expected_sha else "SHA_MISMATCH",
                "path": str(actual_path.relative_to(args.repo)),
                "sha256": actual_sha,
                "expected_sha256": expected_sha,
            }
        seg_reports.append(entry)

    doc = {
        "task": "EP03 ASR / VAD / 说话人 Challenger v1 · Before Metrics",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_sha_source": str(args.baseline_sha_file.relative_to(args.repo)),
        "baseline_sha256_frozen": frozen,
        "benchmark_segments": seg_reports,
        "engine_status": {
            "faster_whisper_small_vad_on": "BASELINE_FROM_CHAMPION_SLICE",
            "funasr_paraformer": "WAITING_FOR_M3_RUN",
            "funasr_fsmn_vad": "WAITING_FOR_M3_RUN",
            "funasr_campp": "WAITING_FOR_M3_RUN",
            "mlx_whisper_turbo": "WAITING_FOR_M3_RUN",
        },
        "note": ("faster-whisper 基线 SHA 直接冻结自 cross-track-safety-v1/before_metrics.json；"
                 " 本 Challenger 不重跑，改由 slice_baseline_from_freshrun.py 从冻结产物切段。"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
