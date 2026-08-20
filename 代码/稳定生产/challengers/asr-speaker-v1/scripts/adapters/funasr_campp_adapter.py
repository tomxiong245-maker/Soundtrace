"""Adapter: FunASR CAM++ speaker diarization raw → normalized speaker intervals.

Expected raw shape (from AutoModel speaker-diarization pipeline):
    [{"key": "seg", "value": [{"start": ms|s, "end": ms|s, "spk": "0", "score": ...}, ...]}]
or a flat list of dicts. Time unit is auto-detected: if any 'end' > 3600 treat as ms;
otherwise seconds. The audit md tells the operator which model was used and its unit.
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


def _extract_entries(raw: object) -> list[dict]:
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "value" in raw[0]:
        return raw[0]["value"]
    if isinstance(raw, list):
        return raw
    raise AdapterError(f"unrecognized campp raw shape: {type(raw)}")


def normalize(
    raw: object,
    *,
    segment_id: str,
    source_track: str,
    source_audio_path: Path,
    segment_start_offset_seconds_in_ep03: float,
    engine_version: str,
    unit: str = "auto",
    tool_sample_rate_hz: int = 16000,
) -> dict:
    entries = _extract_entries(raw)
    if unit == "auto":
        maxv = max((float(e.get("end", 0)) for e in entries), default=0)
        unit = "ms" if maxv > 3600 else "s"
    audio_sha = sha256_file(source_audio_path)
    intervals: list[dict] = []
    for idx, entry in enumerate(entries):
        if "start" not in entry or "end" not in entry:
            raise AdapterError(f"campp entry {idx} missing start/end: {entry!r}")
        start = float(entry["start"])
        end = float(entry["end"])
        if unit == "ms":
            start_s, end_s = start / 1000, end / 1000
        else:
            start_s, end_s = start, end
        if end_s <= start_s:
            raise AdapterError(f"campp entry {idx} zero/negative length")
        start_sample = int(round(start_s * tool_sample_rate_hz))
        end_sample = int(round(end_s * tool_sample_rate_hz))
        if end_sample <= start_sample:
            end_sample = start_sample + 1
        raw_spk = str(entry.get("spk", entry.get("speaker", "unknown")))
        intervals.append(
            {
                "interval_id": f"campp:{segment_id}:{source_track}:i{idx + 1:04d}",
                "start_seconds": start_s,
                "end_seconds": end_s,
                "start_sample": start_sample,
                "end_sample": end_sample,
                "sample_rate_hz": tool_sample_rate_hz,
                "speaker_id": f"campp:c{raw_spk}",
                "profile_match": None,
                "overlap": "unknown",
                "decision_source": "campp_cluster",
                "confidence": float(entry["score"]) if entry.get("score") is not None else None,
                "confidence_semantics": "cluster_membership_score"
                if entry.get("score") is not None
                else "not_provided",
            }
        )
    intervals.sort(key=lambda x: x["start_sample"])
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": "funasr_campp",
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
    ap.add_argument("--unit", choices=["auto", "s", "ms"], default="auto")
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
        unit=args.unit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
