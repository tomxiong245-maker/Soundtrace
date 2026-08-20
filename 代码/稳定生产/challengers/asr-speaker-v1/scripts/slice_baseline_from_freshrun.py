"""Slice baseline faster-whisper transcript into 12 benchmark segments.

Runs on M3 or any Python 3.10+. Reads:
    main/runs/EP03-freshrun-20260810-1730/05_asr/female.transcript.json
    main/runs/EP03-freshrun-20260810-1730/05_asr/male.transcript.json
and writes per-segment word lists to:
    main/runs/EP03-asr-speaker-v1/raw/faster_whisper_small_vad_on/S*/{female,male}.raw.json

Also writes normalized outputs via faster_whisper_adapter.

The tool never modifies the input files; it only reads them.

We treat the transcript's word timestamps as being on the aligned EP03 timeline
at 48 kHz (that's what transcribe_tracks.py wrote), so slicing here means:

    for a benchmark segment starting at S.start_seconds_in_ep03 and lasting
    S.duration_seconds, keep all words w with:
        w.end_seconds >= S.start_seconds_in_ep03
        AND w.start_seconds <  S.start_seconds_in_ep03 + S.duration_seconds
    and shift timestamps: w.start_seconds -= S.start_seconds_in_ep03
                          w.end_seconds   -= S.start_seconds_in_ep03
                          w.start_sample  -= S.start_seconds_in_ep03 * 48000
                          w.end_sample    -= S.start_seconds_in_ep03 * 48000

Words that cross a boundary are kept (with unclipped shifted timestamps)
because we want to preserve the ASR's original spans; scorer & hypotheses
layer both know how to handle boundary words.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parent / "adapters"))
from faster_whisper_adapter import normalize  # noqa: E402
from _common import sha256_file, write_json  # noqa: E402


def slice_words(all_words: list[dict], start: float, end: float, sr_hz: int) -> list[dict]:
    kept = []
    for w in all_words:
        if w["end_seconds"] < start or w["start_seconds"] >= end:
            continue
        shifted = dict(w)
        shifted["start_seconds"] = round(w["start_seconds"] - start, 6)
        shifted["end_seconds"] = round(w["end_seconds"] - start, 6)
        shifted["start_sample"] = int(round(shifted["start_seconds"] * sr_hz))
        shifted["end_sample"] = int(round(shifted["end_seconds"] * sr_hz))
        if shifted["end_sample"] <= shifted["start_sample"]:
            shifted["end_sample"] = shifted["start_sample"] + 1
        kept.append(shifted)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freshrun-asr-dir", type=Path, required=True,
                    help="e.g. main/runs/EP03-freshrun-20260810-1730/05_asr")
    ap.add_argument("--gold-json", type=Path, required=True,
                    help="benchmark/EP03-ASR-mini-gold-v1/gold.json")
    ap.add_argument("--segments-dir", type=Path, required=True,
                    help="benchmark/EP03-ASR-mini-gold-v1/segments")
    ap.add_argument("--raw-out", type=Path, required=True)
    ap.add_argument("--normalized-out", type=Path, required=True)
    ap.add_argument("--engine-version", default="faster-whisper-unknown; from freshrun")
    ap.add_argument("--model-id", default="openai/whisper-small (int8 via CTranslate2)")
    ap.add_argument("--model-revision", default=None)
    args = ap.parse_args()

    female_t = json.loads((args.freshrun_asr_dir / "female.transcript.json").read_text(encoding="utf-8"))
    male_t = json.loads((args.freshrun_asr_dir / "male.transcript.json").read_text(encoding="utf-8"))
    if female_t["sample_rate_hz"] != male_t["sample_rate_hz"]:
        raise SystemExit("female/male transcripts have different sample rates")
    sr = int(female_t["sample_rate_hz"])
    gold = json.loads(args.gold_json.read_text(encoding="utf-8"))
    for seg in gold["segments"]:
        seg_id = seg["id"]
        start = float(seg["start_seconds_in_ep03"])
        end = start + float(seg["duration_seconds"])
        for track_name, transcript in (("female", female_t), ("male", male_t)):
            words = slice_words(transcript["words"], start, end, sr)
            raw = {"schema": "sliced_faster_whisper_words_v1",
                   "words": words,
                   "source_transcript_sha256": None,
                   "note": "Sliced from Champion transcript; timestamps are relative to segment start."}
            raw_path = args.raw_out / seg_id / f"{track_name}.raw.json"
            write_json(raw_path, raw)
            # audit-track original transcript sha
            raw["source_transcript_sha256"] = sha256_file(args.freshrun_asr_dir / f"{track_name}.transcript.json")
            write_json(raw_path, raw)

            audio_path = args.segments_dir / seg_id / f"{track_name}.wav"
            normalized = normalize(
                raw,
                segment_id=seg_id,
                source_track=track_name,
                source_audio_path=audio_path,
                segment_start_offset_seconds_in_ep03=start,
                engine_version=args.engine_version,
                model_id=args.model_id,
                model_revision=args.model_revision,
                sample_rate_hz=sr,
            )
            normalized["raw_ref"] = {
                "raw_path": str(raw_path.relative_to(args.raw_out.parents[2])),
                "raw_sha256": sha256_file(raw_path),
            }
            out_path = args.normalized_out / seg_id / f"{track_name}.words.json"
            write_json(out_path, normalized)
    print("wrote", args.raw_out, "and", args.normalized_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
