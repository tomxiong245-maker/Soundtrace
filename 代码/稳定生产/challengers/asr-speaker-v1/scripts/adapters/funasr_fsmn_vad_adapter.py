"""Adapter: FunASR FSMN-VAD raw → normalized VAD intervals.

FunASR VAD raw output looks like:
    [{"key": "seg", "value": [[start_ms, end_ms], ...]}]
or
    [[start_ms, end_ms], ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


class AdapterError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as h:
        while chunk := h.read(1024 * 1024):
            d.update(chunk)
    return d.hexdigest()


def normalize(
    raw: object,
    *,
    segment_id: str,
    source_track: str,
    source_audio_path: Path,
    segment_start_offset_seconds_in_ep03: float,
    engine_version: str,
    tool_sample_rate_hz: int = 16000,
) -> dict:
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "value" in raw[0]:
        raw_pairs = raw[0]["value"]
    elif isinstance(raw, list):
        raw_pairs = raw
    else:
        raise AdapterError(f"unrecognized funasr fsmn-vad raw shape: {type(raw)}")

    audio_sha = sha256_file(source_audio_path)
    intervals = []
    for idx, pair in enumerate(raw_pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise AdapterError(f"vad pair {idx} malformed: {pair!r}")
        start_ms, end_ms = float(pair[0]), float(pair[1])
        if end_ms <= start_ms:
            raise AdapterError(f"vad pair {idx} zero/negative length")
        start_sample = int(round(start_ms / 1000 * tool_sample_rate_hz))
        end_sample = int(round(end_ms / 1000 * tool_sample_rate_hz))
        if end_sample <= start_sample:
            end_sample = start_sample + 1
        intervals.append(
            {
                "interval_id": f"fsmn_vad:{segment_id}:{source_track}:i{idx + 1:04d}",
                "start_seconds": start_ms / 1000,
                "end_seconds": end_ms / 1000,
                "start_sample": start_sample,
                "end_sample": end_sample,
                "sample_rate_hz": tool_sample_rate_hz,
                "speaker_id": "unknown",
                "profile_match": None,
                "overlap": "unknown",
                "decision_source": "fsmn_vad",
                "confidence": None,
                "confidence_semantics": "not_provided",
            }
        )
    # sort + monotonic check
    intervals.sort(key=lambda x: x["start_sample"])
    prev = -1
    for iv in intervals:
        if iv["start_sample"] < prev:
            raise AdapterError("fsmn-vad intervals overlap")
        prev = iv["end_sample"]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "funasr_fsmn_vad",
        "engine_version": engine_version,
        "segment_id": segment_id,
        "source_track": source_track,
        "source_audio_sha256": audio_sha,
        "sample_rate_hz": tool_sample_rate_hz,
        "segment_start_offset_seconds_in_ep03": segment_start_offset_seconds_in_ep03,
        "intervals": intervals,
    }


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--segment-id", required=True)
    ap.add_argument("--source-track", required=True)
    ap.add_argument("--source-audio", type=Path, required=True)
    ap.add_argument("--offset-seconds", type=float, required=True)
    ap.add_argument("--engine-version", default="unknown")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    normalized = normalize(
        raw,
        segment_id=args.segment_id,
        source_track=args.source_track,
        source_audio_path=args.source_audio,
        segment_start_offset_seconds_in_ep03=args.offset_seconds,
        engine_version=args.engine_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
