"""Common helpers for asr-speaker-v1 adapters.

Adapters convert raw tool output into the normalized_transcript schema. They
MUST:
  * preserve word-level timing (raise if the tool only gives sentence-level).
  * keep raw text (fillers, negations, numbers, English names, punctuation).
  * refuse timestamps that are non-monotonic, out-of-range, or zero-length.
  * emit sample indices on the tool's native sample rate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class AdapterError(ValueError):
    """Raised when raw output cannot be safely normalized."""


@dataclass
class WordRecord:
    word_id: str
    text: str
    raw_text: str
    start_seconds: float
    end_seconds: float
    start_sample: int
    end_sample: int
    sample_rate_hz: int
    source_track: str
    source_audio_sha256: str
    model_id: str
    model_revision: str | None
    confidence: float | None
    confidence_semantics: str
    timestamp_origin: str
    timestamp_mapping: dict
    speaker_id_hint: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_text(value: str) -> str:
    """Only strip leading/trailing whitespace; keep punctuation and case."""
    return value.strip()


def validate_and_sort_words(words: list[WordRecord], *, allow_zero_conf: bool = True) -> list[WordRecord]:
    if not words:
        return words
    sorted_words = sorted(words, key=lambda w: (w.start_sample, w.end_sample))
    prev_end_sample = -1
    max_sample = None
    for w in sorted_words:
        if w.end_sample <= w.start_sample:
            raise AdapterError(f"non-positive duration: {w.word_id} [{w.start_sample},{w.end_sample}]")
        if w.start_sample < 0:
            raise AdapterError(f"negative start sample: {w.word_id}")
        if not allow_zero_conf and w.confidence is None:
            raise AdapterError(f"missing confidence but disallowed: {w.word_id}")
        if max_sample is not None and w.end_sample > max_sample:
            raise AdapterError(f"end_sample out of range for {w.word_id}: {w.end_sample} > {max_sample}")
        if w.start_sample < prev_end_sample:
            # allow strict monotonic starts; overlapping words are a tool bug
            raise AdapterError(
                f"non-monotonic word timing: {w.word_id} start {w.start_sample} < prev end {prev_end_sample}"
            )
        prev_end_sample = w.end_sample
    return sorted_words


def make_normalized_transcript(
    *,
    engine: str,
    engine_version: str,
    model_id: str,
    model_revision: str | None,
    segment_id: str,
    source_track: str,
    source_audio_sha256: str,
    segment_start_offset_seconds_in_ep03: float,
    sample_rate_hz: int,
    words: Iterable[WordRecord],
    raw_ref: dict | None = None,
    normalization_rules_version: str = "asr-speaker-v1.normalization.v1",
) -> dict:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
        "engine_version": engine_version,
        "model_id": model_id,
        "model_revision": model_revision,
        "segment_id": segment_id,
        "source_track": source_track,
        "source_audio_sha256": source_audio_sha256,
        "segment_start_offset_seconds_in_ep03": segment_start_offset_seconds_in_ep03,
        "sample_rate_hz": sample_rate_hz,
        "words": [w.to_json() for w in words],
        "raw_ref": raw_ref or {},
        "normalization": {
            "rules_version": normalization_rules_version,
            "removed_llm_cleanup": True,
            "kept_fillers": True,
            "kept_negations": True,
            "kept_numbers": True,
            "kept_english_names": True,
        },
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
