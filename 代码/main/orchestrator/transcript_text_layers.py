#!/usr/bin/env python3
"""Build immutable raw/match/display text layers for a word transcript.

This is the single canonical implementation used by production adapters and
the isolated multilingual ASR Challenger.  The three layers deliberately
have different jobs:

``raw_text``
    Verbatim upstream ASR text and word-level evidence.  It is never edited.
``match_text``
    Deterministic retrieval/event-identity text (NFKC, bounded traditional to
    simplified mapping, punctuation/space removal and case-folding).
``display_text``
    A reviewer-facing rendering.  It may normalize traditional characters and
    conservatively join adjacent ASCII subword fragments, but it never creates
    a new word ID, timestamp, candidate or edit decision.

The output retains both the canonical ``document``/``words`` interface and
the historical Challenger ``word_layers``/``display_spans`` interface.  This
keeps existing callers working while ensuring there is only one normalization
implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

from event_identity import TRADITIONAL_TO_SIMPLIFIED, normalize_event_text


SCHEMA_VERSION = "transcript-text-layers-v1"
LEGACY_CHALLENGER_SCHEMA_VERSION = "asr-text-layers-v1"
NORMALIZATION_VERSION = "transcript-text-layers-v1.deterministic-t2s-nfkc"

_WHITESPACE = re.compile(r"\s+", flags=re.UNICODE)
_ASCII_FRAGMENT = re.compile(r"^[A-Za-z0-9]+$")
_ASCII_STANDALONE = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "he", "i",
    "in", "is", "it", "of", "on", "or", "the", "to", "we", "you", "ai",
}
_TIMING_KEYS = (
    "start_seconds", "end_seconds", "start_sample", "end_sample",
    "raw_start_seconds", "raw_end_seconds",
)


class ContractError(ValueError):
    """Raised when a transcript cannot safely be represented as a sidecar."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a string")
    return value


def normalize_for_match(value: str) -> str:
    """Return comparison-only text; never use it to rewrite raw ASR."""

    return normalize_event_text(value)


def normalize_for_display(value: str) -> str:
    """Return a readable token without inventing punctuation or timestamps."""

    # NFC keeps Chinese punctuation visually stable.  Width equivalence belongs
    # to match_text, not the raw evidence or the reviewer display.
    text = unicodedata.normalize("NFC", value).translate(TRADITIONAL_TO_SIMPLIFIED)
    return _WHITESPACE.sub(" ", text).strip()


def display_text(value: Any) -> str:
    """Backward-compatible public display helper for one token/value."""

    return normalize_for_display(str(value or ""))


def _word_timing_evidence(word: Mapping[str, Any]) -> dict[str, Any]:
    return {key: word[key] for key in _TIMING_KEYS if key in word}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ascii_fragment(value: str) -> bool:
    return bool(_ASCII_FRAGMENT.fullmatch(value))


def _can_merge_ascii_fragments(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Merge only obvious adjacent ASCII subword fragments for display."""

    left_text = str(left["display_text"])
    right_text = str(right["display_text"])
    if not left_text or not right_text:
        return False
    if not (_ascii_fragment(left_text) and _ascii_fragment(right_text)):
        return False
    if len(left_text) > 24 or len(right_text) > 24:
        return False
    if len(left_text) > 2 and len(right_text) > 2:
        return False
    if left_text.casefold() in _ASCII_STANDALONE:
        return False
    # A source space is an evidence boundary, not an ASR subword split.
    if right.get("source_leading_space"):
        return False
    previous_end = _number(left.get("last_end_seconds"))
    next_start = _number(right.get("first_start_seconds"))
    if previous_end is None or next_start is None:
        return False
    return previous_end - 0.020 <= next_start <= previous_end + 0.030


def _make_display_spans(word_layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for layer in word_layers:
        next_span = {
            "span_id": f"display_span_{len(spans) + 1:06d}",
            "source_word_ids": list(layer["source_word_ids"]),
            "raw_text": layer["raw_text"],
            "match_text": layer["match_text"],
            "display_text": layer["display_text"],
            "source_leading_space": layer["source_leading_space"],
            # Timing fields exist only during conservative merge inspection and
            # are removed before the sidecar is written.
            "first_start_seconds": layer.get("source_start_seconds"),
            "last_end_seconds": layer.get("source_end_seconds"),
            "kind": "one_to_one",
        }
        if spans and _can_merge_ascii_fragments(spans[-1], next_span):
            current = spans[-1]
            current["source_word_ids"].extend(next_span["source_word_ids"])
            current["raw_text"] += next_span["raw_text"]
            current["match_text"] += next_span["match_text"]
            current["display_text"] += next_span["display_text"]
            current["last_end_seconds"] = next_span["last_end_seconds"]
            current["kind"] = "ascii_subword_display_merge"
        else:
            spans.append(next_span)
    for span in spans:
        span.pop("first_start_seconds", None)
        span.pop("last_end_seconds", None)
    return spans


def _join_display_spans(spans: Iterable[Mapping[str, Any]]) -> str:
    output = ""
    for span in spans:
        text = str(span.get("display_text", ""))
        if not text:
            continue
        if output and span.get("source_leading_space"):
            output += " "
        output += text
    return output


def _source_summary(doc: Mapping[str, Any], source_path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": doc.get("schema_version"),
        "track_id": doc.get("track_id"),
        "engine": doc.get("engine"),
        "model_ref": doc.get("model_ref"),
        "source_audio_sha256": doc.get("source_audio_sha256"),
    }
    if source_path is not None:
        result["path"] = str(source_path.resolve())
        result["sha256"] = sha256_file(source_path)
    return result


def build_text_layers(
    transcript: Mapping[str, Any],
    source_path: Path | None = None,
) -> dict[str, Any]:
    """Build a sidecar while proving source text/IDs/timing stayed intact."""

    if not isinstance(transcript, Mapping):
        raise ContractError("source transcript must be a JSON object")
    before = _canonical_json(transcript)
    words = transcript.get("words")
    if not isinstance(words, list) or not words:
        raise ContractError("source transcript needs a non-empty words array")

    word_layers: list[dict[str, Any]] = []
    canonical_words: list[dict[str, Any]] = []
    raw_evidence: list[dict[str, Any]] = []
    timing_evidence: list[dict[str, Any]] = []
    word_ids: list[str] = []
    for index, word in enumerate(words):
        if not isinstance(word, Mapping):
            raise ContractError(f"words[{index}] must be an object")
        raw_word_id = word.get("word_id", word.get("source_word_id"))
        word_id = _as_text(raw_word_id, field=f"words[{index}].word_id")
        raw_text = _as_text(word.get("text", word.get("word")), field=f"words[{index}].text")
        if not word_id:
            raise ContractError(f"words[{index}].word_id is empty")
        if word_id in word_ids:
            raise ContractError(f"duplicate word_id: {word_id}")
        word_ids.append(word_id)
        timing = _word_timing_evidence(word)
        raw_evidence.append({"word_id": word_id, "text": raw_text, "timing": timing})
        timing_evidence.append({"word_id": word_id, "timing": timing})
        match = normalize_for_match(raw_text)
        displayed = normalize_for_display(raw_text)
        leading_space = bool(raw_text and raw_text[0].isspace())
        layer = {
            "source_word_ids": [word_id],
            "source_word_index": index,
            "raw_text": raw_text,
            "match_text": match,
            "display_text": displayed,
            "source_leading_space": leading_space,
            # Used only while deciding a display merge; removed before write.
            "source_start_seconds": word.get("start_seconds", word.get("start")),
            "source_end_seconds": word.get("end_seconds", word.get("end")),
        }
        word_layers.append(layer)
        canonical_words.append(
            {
                "source_word_id": word_id,
                "raw_text": raw_text,
                "match_text": match,
                "display_text": displayed,
                "start_seconds": word.get("start_seconds", word.get("start")),
                "end_seconds": word.get("end_seconds", word.get("end")),
            }
        )

    display_spans = _make_display_spans(word_layers)
    for layer in word_layers:
        layer.pop("source_start_seconds", None)
        layer.pop("source_end_seconds", None)
    mapped_ids = [word_id for span in display_spans for word_id in span["source_word_ids"]]
    if mapped_ids != word_ids:
        raise ContractError("display span mapping lost or reordered a source word")
    if _canonical_json(transcript) != before:
        raise ContractError("sidecar builder attempted to mutate source transcript")

    raw_joined = "".join(layer["raw_text"] for layer in word_layers)
    match_joined = "".join(layer["match_text"] for layer in word_layers)
    display_joined = _join_display_spans(display_spans)
    integrity = {
        "raw_word_count": len(word_ids),
        "raw_word_ids_sha256": _sha256_value(word_ids),
        "raw_word_timing_sha256": _sha256_value(timing_evidence),
        "raw_word_evidence_sha256": _sha256_value(raw_evidence),
        "source_word_id_order_preserved": True,
        "every_source_word_mapped_exactly_once": True,
        "raw_transcript_mutated": False,
        "timestamps_rewritten": False,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "legacy_schema_version": LEGACY_CHALLENGER_SCHEMA_VERSION,
        "layer_kind": "derived_text_layers",
        "normalization_version": NORMALIZATION_VERSION,
        "source_transcript": _source_summary(transcript, source_path),
        # Kept for callers that used the original canonical shape.
        "source_transcript_sha256": (
            sha256_file(source_path) if source_path is not None else None
        ),
        "track_id": transcript.get("track_id"),
        "raw_text": raw_joined,
        "match_text": match_joined,
        "display_text": display_joined,
        "document": {
            "raw_text": raw_joined,
            "match_text": match_joined,
            "display_text": display_joined,
        },
        "words": canonical_words,
        "word_layers": word_layers,
        "display_spans": display_spans,
        "policy": {
            "raw_is_immutable": True,
            "match_is_for_retrieval_only": True,
            "display_is_for_reading_only": True,
            "forbidden": [
                "rewrite_source_transcript",
                "alter_word_timestamps",
                "decide_cut",
            ],
        },
        "integrity": integrity,
        "out_of_scope": {
            "asr_correction": "NOT_INCLUDED",
            "timestamp_repair": "NOT_INCLUDED",
            "punctuation_invention": "NOT_INCLUDED",
            "candidate_generation": "NOT_INCLUDED",
            "deletion_decision": "NOT_INCLUDED",
        },
    }


def write_json(path: Path, value: Mapping[str, Any], *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise ContractError(f"refusing to overwrite existing sidecar: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_layers(
    transcript_path: Path,
    out_path: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    result = build_text_layers(transcript, transcript_path)
    write_json(out_path, result, overwrite=overwrite)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transcript", "--input", dest="transcript", type=Path, required=True,
        help="read-only word-level transcript JSON",
    )
    parser.add_argument("--out", type=Path, required=True, help="new sidecar JSON path")
    parser.add_argument("--force", action="store_true", help="explicitly replace an existing sidecar")
    args = parser.parse_args(argv)
    transcript_path = args.transcript.resolve()
    output_path = args.out.resolve()
    if transcript_path == output_path:
        raise SystemExit("--out must be a separate sidecar; raw transcript is read-only")
    try:
        result = write_layers(transcript_path, output_path, overwrite=args.force)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise SystemExit(f"text layer build failed: {exc}") from exc
    print(json.dumps({
        "status": "PASS",
        "schema_version": SCHEMA_VERSION,
        "track_id": result.get("track_id"),
        "word_count": len(result.get("word_layers") or []),
        "out": str(output_path),
        "source_sha256": result.get("source_transcript_sha256"),
        "raw_word_evidence_sha256": result["integrity"]["raw_word_evidence_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
