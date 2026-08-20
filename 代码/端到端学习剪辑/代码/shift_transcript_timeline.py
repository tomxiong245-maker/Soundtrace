#!/usr/bin/env python3
"""Shift an existing word-level transcript onto a latency-corrected WAV timeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import wave
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--new-source", required=True, type=Path)
    parser.add_argument("--shift-samples", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    input_path = args.input.expanduser()
    source_path = args.new_source.expanduser()
    output_path = args.output.expanduser()
    if any(not path.is_absolute() for path in (input_path, source_path, output_path)):
        parser.error("all paths must be absolute")
    if not input_path.is_file() or not source_path.is_file():
        parser.error("input transcript and new WAV source must exist")
    if output_path.exists():
        parser.error("refusing to overwrite an existing shifted transcript")

    transcript = json.loads(input_path.read_text(encoding="utf-8"))
    with wave.open(str(source_path), "rb") as audio:
        if audio.getcomptype() != "NONE" or audio.getnchannels() != 1:
            raise ValueError("new source must be a mono PCM WAV")
        sample_rate = audio.getframerate()
        frame_count = audio.getnframes()
    if transcript.get("sample_rate_hz") != sample_rate:
        raise ValueError("transcript and new source sample rates differ")
    if transcript.get("frame_count") != frame_count:
        raise ValueError("transcript and new source frame counts differ")

    for collection in ("segments", "words"):
        for item in transcript.get(collection, []):
            start = max(0, min(frame_count, item["start_sample"] + args.shift_samples))
            end = max(0, min(frame_count, item["end_sample"] + args.shift_samples))
            if end <= start:
                raise ValueError(f"shift collapsed {collection} item: {item}")
            item["start_sample"] = start
            item["end_sample"] = end
            item["start_seconds"] = round(start / sample_rate, 6)
            item["end_seconds"] = round(end / sample_rate, 6)

    prior_source = {
        "path": transcript.get("source_path"),
        "sha256": transcript.get("source_sha256"),
        "transcript_path": str(input_path),
        "transcript_sha256": sha256_file(input_path),
    }
    transcript["created_at"] = datetime.now(timezone.utc).isoformat()
    transcript["timeline"] = "latency_compensated_aligned_source_samples"
    transcript["source_path"] = str(source_path)
    transcript["source_sha256"] = sha256_file(source_path)
    transcript["timeline_adjustment"] = {
        "method": "constant_sample_shift_without_asr_rerun",
        "shift_samples": args.shift_samples,
        "shift_seconds": round(args.shift_samples / sample_rate, 6),
        "reason": "map existing word IDs onto the afftdn latency-compensated source",
        "prior_source": prior_source,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
