"""Adapter: mlx-whisper raw dict → normalized_transcript.

mlx-whisper.transcribe(..., word_timestamps=True) returns:
    {
      "text": "...",
      "segments": [
        {"start": s, "end": s, "text": "...",
         "words": [{"word": "...", "start": s, "end": s, "probability": p}, ...]},
        ...
      ],
      "language": "zh"
    }
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

ENGINE_ID = "mlx_whisper_turbo"


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
    tool_sample_rate_hz: int = 16000,
) -> dict:
    if "segments" not in raw or not isinstance(raw["segments"], list):
        raise AdapterError("mlx-whisper raw missing 'segments'")

    words: list[WordRecord] = []
    audio_sha = sha256_file(source_audio_path)
    idx = 0
    for seg in raw["segments"]:
        seg_words = seg.get("words")
        if not seg_words:
            raise AdapterError(
                "mlx-whisper segment lacks 'words'; adapter refuses to downgrade "
                "to segment-level timestamps. See audits/mlx-whisper.md."
            )
        for w in seg_words:
            idx += 1
            for k in ("word", "start", "end"):
                if k not in w:
                    raise AdapterError(f"mlx-whisper word missing {k}: {w!r}")
            start_seconds = float(w["start"])
            end_seconds = float(w["end"])
            if end_seconds <= start_seconds:
                # occasionally 0-length; expand by 1 sample not more
                end_seconds = start_seconds + 1 / tool_sample_rate_hz
            start_sample = int(round(start_seconds * tool_sample_rate_hz))
            end_sample = int(round(end_seconds * tool_sample_rate_hz))
            if end_sample <= start_sample:
                end_sample = start_sample + 1
            text_raw = w["word"]
            text = _clean_text(text_raw)
            if not text:
                continue
            words.append(
                WordRecord(
                    word_id=f"{ENGINE_ID}:{segment_id}:{source_track}:w{idx:06d}",
                    text=text,
                    raw_text=text_raw,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    start_sample=start_sample,
                    end_sample=end_sample,
                    sample_rate_hz=tool_sample_rate_hz,
                    source_track=source_track,
                    source_audio_sha256=audio_sha,
                    model_id=model_id,
                    model_revision=model_revision,
                    confidence=float(w["probability"]) if w.get("probability") is not None else None,
                    confidence_semantics="word_probability_softmax"
                    if w.get("probability") is not None
                    else "not_provided",
                    timestamp_origin="mlx_whisper_word_ts",
                    timestamp_mapping={
                        "from_sample_rate_hz": tool_sample_rate_hz,
                        "to_sample_rate_hz": 48000,
                        "segment_start_offset_seconds_in_ep03": segment_start_offset_seconds_in_ep03,
                        "note": "mlx-whisper decodes at 16 kHz; ep03 mapping = start_seconds + offset then *48000.",
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
        sample_rate_hz=tool_sample_rate_hz,
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
    ap.add_argument("--model-id", default="mlx-community/whisper-large-v3-turbo")
    ap.add_argument("--model-revision", default=None)
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
    )
    write_json(args.output, normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
