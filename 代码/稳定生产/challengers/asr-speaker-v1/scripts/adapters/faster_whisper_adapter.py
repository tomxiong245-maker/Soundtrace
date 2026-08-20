"""Adapter: faster-whisper JSON → normalized_transcript.

Input: object as written by 端到端学习剪辑/代码/transcribe_tracks.py's transcript.json,
       optionally sliced by scripts/slice_baseline_from_freshrun.py.

We treat only 'words' array; segments are ignored for normalization since we
work per benchmark segment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import (
    AdapterError,
    WordRecord,
    _clean_text,
    make_normalized_transcript,
    sha256_file,
    validate_and_sort_words,
    write_json,
)


ENGINE_ID = "faster_whisper_small_vad_on"


def normalize(
    raw: dict,
    *,
    segment_id: str,
    source_track: str,
    source_audio_path: Path,
    segment_start_offset_seconds_in_ep03: float,
    engine_version: str,
    model_id: str,
    model_revision: str | None,
    sample_rate_hz: int,
) -> dict:
    if "words" not in raw:
        raise AdapterError("faster_whisper raw missing 'words'")

    audio_sha = sha256_file(source_audio_path)
    words: list[WordRecord] = []
    for idx, w in enumerate(raw["words"]):
        for key in ("text", "start_seconds", "end_seconds", "start_sample", "end_sample"):
            if key not in w:
                raise AdapterError(f"faster_whisper word missing '{key}': {w!r}")
        text_raw = w["text"]
        text = _clean_text(text_raw)
        if not text:
            continue
        # Enforce sample_rate mapping: if the source (ep03 full) was 48k but our benchmark seg is 16k, we
        # need to know both. We store the timeline in the tool's native rate; timestamp_mapping records both.
        words.append(
            WordRecord(
                word_id=f"{ENGINE_ID}:{segment_id}:{source_track}:w{idx + 1:06d}",
                text=text,
                raw_text=text_raw,
                start_seconds=float(w["start_seconds"]),
                end_seconds=float(w["end_seconds"]),
                start_sample=int(w["start_sample"]),
                end_sample=int(w["end_sample"]),
                sample_rate_hz=sample_rate_hz,
                source_track=source_track,
                source_audio_sha256=audio_sha,
                model_id=model_id,
                model_revision=model_revision,
                confidence=float(w["probability"]) if w.get("probability") is not None else None,
                confidence_semantics="word_probability_softmax"
                if w.get("probability") is not None
                else "not_provided",
                timestamp_origin="faster_whisper_word_ts",
                timestamp_mapping={
                    "from_sample_rate_hz": sample_rate_hz,
                    "to_sample_rate_hz": 48000,
                    "segment_start_offset_seconds_in_ep03": segment_start_offset_seconds_in_ep03,
                    "note": "start_sample/end_sample are on the tool's native sample rate for this segment; ep03 mapping = sample/native + offset*48000.",
                },
                speaker_id_hint=None,
            )
        )
    words = validate_and_sort_words(words)
    return make_normalized_transcript(
        engine=ENGINE_ID,
        engine_version=engine_version,
        model_id=model_id,
        model_revision=model_revision,
        segment_id=segment_id,
        source_track=source_track,
        source_audio_sha256=audio_sha,
        segment_start_offset_seconds_in_ep03=segment_start_offset_seconds_in_ep03,
        sample_rate_hz=sample_rate_hz,
        words=words,
    )


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--segment-id", required=True)
    ap.add_argument("--source-track", required=True)
    ap.add_argument("--source-audio", type=Path, required=True)
    ap.add_argument("--offset-seconds", type=float, required=True)
    ap.add_argument("--engine-version", default="unknown")
    ap.add_argument("--model-id", default="openai/whisper-small (faster-whisper int8)")
    ap.add_argument("--model-revision", default=None)
    ap.add_argument("--sample-rate-hz", type=int, default=48000)
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
        model_id=args.model_id,
        model_revision=args.model_revision,
        sample_rate_hz=args.sample_rate_hz,
    )
    write_json(args.output, normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
