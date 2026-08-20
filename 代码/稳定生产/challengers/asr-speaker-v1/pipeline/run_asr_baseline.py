"""run_asr_baseline.py — reuse Champion faster-whisper transcript sliced to 12 segs.

No model call, no venv beyond stdlib+ - runs anywhere with python3.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slice_words(all_words, start, end):
    out = []
    for w in all_words:
        if w["end_seconds"] < start or w["start_seconds"] >= end:
            continue
        out.append({
            "text": w["text"],
            "raw_text": w["text"],
            "start_seconds": round(w["start_seconds"] - start, 6),
            "end_seconds": round(w["end_seconds"] - start, 6),
            "confidence": w.get("probability"),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freshrun-asr-dir", type=Path, required=True)
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--norm-out", type=Path, required=True)
    args = ap.parse_args()

    female = json.loads((args.freshrun_asr_dir / "female.transcript.json").read_text(encoding="utf-8"))
    male = json.loads((args.freshrun_asr_dir / "male.transcript.json").read_text(encoding="utf-8"))
    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    for seg in gold["segments"]:
        seg_id = seg["id"]
        start = float(seg["start_seconds_in_ep03"])
        end = start + float(seg["duration_seconds"])
        for name, tr in (("female", female), ("male", male)):
            words = slice_words(tr["words"], start, end)
            text = "".join(w["text"] for w in words)
            _write(args.norm_out / seg_id / f"{name}.words.json", {
                "engine": "faster_whisper_small",
                "model_id": "openai/whisper-small (int8 via CTranslate2)",
                "segment_id": seg_id,
                "source_track": name,
                "segment_start_offset_seconds_in_ep03": start,
                "text": text,
                "words": words,
                "timestamp_reliable": True,
                "clipping_eligible": True,
                "not_eligible_reason": None,
            })
    print("baseline slice ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
