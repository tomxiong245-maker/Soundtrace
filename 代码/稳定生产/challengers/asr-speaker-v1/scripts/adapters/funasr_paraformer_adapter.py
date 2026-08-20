"""Adapter: FunASR Paraformer raw dict → normalized_transcript.

Expected raw format (as returned by AutoModel(model=paraformer).generate(...)):

    [
      {
        "key": "S01_female",
        "text": "遇到 的 各种 问题 ...",
        "timestamp": [[0, 300], [300, 420], ...],   # ms pairs, per token
        # optional fields we ignore: 'sentence_info', 'raw_text'
      }
    ]

If the chosen Paraformer model returns only sentence-level 'sentence_info' with
[start_ms, end_ms] for the *whole sentence*, this adapter refuses (AdapterError):
we require token/word timing. See audits/funasr-paraformer.md.
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

ENGINE_ID = "funasr_paraformer"


def _timestamped_tokens(text: str, timestamps: list) -> list[str]:
    """Return the surface units that correspond to Paraformer's timestamps.

    Paraformer has two real output shapes in the wild:

    * whitespace-delimited tokens (often used by older demos), and
    * character-level timestamps for Chinese text (the current model contract).

    The old adapter only implemented the first shape and therefore rejected a
    valid Paraformer-large result whenever ``text`` had no spaces.  We keep the
    raw surface units and never perform linguistic cleanup.  If neither shape
    matches exactly, we fail closed instead of inventing timings.
    """
    spaced = text.split()
    if len(spaced) == len(timestamps):
        return spaced

    chars = [ch for ch in text if not ch.isspace()]
    if len(chars) == len(timestamps):
        return chars

    raise AdapterError(
        "funasr timestamp/token mismatch: "
        f"whitespace_tokens={len(spaced)} chars={len(chars)} timestamps={len(timestamps)}; "
        "adapter refuses sentence-level or ambiguous timing."
    )


def normalize(
    raw_entry: dict,
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
    if "text" not in raw_entry:
        raise AdapterError("funasr paraformer raw missing 'text'")
    if "timestamp" not in raw_entry or not isinstance(raw_entry["timestamp"], list):
        raise AdapterError("funasr paraformer raw missing token-level 'timestamp'")

    ts = raw_entry["timestamp"]
    tokens = _timestamped_tokens(str(raw_entry["text"]), ts)

    audio_sha = sha256_file(source_audio_path)
    words: list[WordRecord] = []
    for idx, (tok, ts_pair) in enumerate(zip(tokens, ts)):
        if not isinstance(ts_pair, (list, tuple)) or len(ts_pair) != 2:
            raise AdapterError(f"funasr timestamp[{idx}] not a [start_ms,end_ms] pair")
        start_ms, end_ms = float(ts_pair[0]), float(ts_pair[1])
        start_seconds = start_ms / 1000.0
        end_seconds = end_ms / 1000.0
        # if the tool returns end == start (rare), stretch by one sample
        start_sample = int(round(start_seconds * tool_sample_rate_hz))
        end_sample = int(round(end_seconds * tool_sample_rate_hz))
        if end_sample <= start_sample:
            end_sample = start_sample + 1
        text_raw = tok
        text = _clean_text(text_raw)
        if not text:
            continue
        words.append(
            WordRecord(
                word_id=f"{ENGINE_ID}:{segment_id}:{source_track}:w{idx + 1:06d}",
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
                confidence=None,
                confidence_semantics="not_provided",
                timestamp_origin="funasr_paraformer_timestamp",
                timestamp_mapping={
                    "from_sample_rate_hz": tool_sample_rate_hz,
                    "to_sample_rate_hz": 48000,
                    "segment_start_offset_seconds_in_ep03": segment_start_offset_seconds_in_ep03,
                    "note": "Paraformer tokens are in the model's 16 kHz native rate; ep03 mapping = start_seconds + offset then *48000.",
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
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--model-revision", default=None)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    raw_wrapper = json.loads(args.raw.read_text(encoding="utf-8"))
    entry = raw_wrapper[0] if isinstance(raw_wrapper, list) else raw_wrapper
    normalized = normalize(
        entry,
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
