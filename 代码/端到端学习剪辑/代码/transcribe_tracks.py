#!/usr/bin/env python3
"""Transcribe aligned tracks locally with stable word IDs and sample timestamps."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / ".tools" / "python"))


def parse_track(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("track must use LABEL=/absolute/path")
    label, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser()
    if not label or not path.is_absolute():
        raise argparse.ArgumentTypeError("track must use LABEL=/absolute/path")
    return label, path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def wav_info(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE" or audio.getnchannels() != 1:
            raise ValueError(f"expected mono PCM WAV: {path}")
        return audio.getframerate(), audio.getnframes()


def clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=parse_track)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tracks = [(label, path.expanduser()) for label, path in args.track]
    model_path = args.model.expanduser()
    output_dir = args.output_dir.expanduser()
    paths = [path for _, path in tracks]
    if not output_dir.is_absolute() or not model_path.is_absolute() or any(not p.is_absolute() for p in paths):
        parser.error("all paths must be absolute")
    if len(tracks) < 2 or any(not path.is_file() for path in paths):
        parser.error("at least two existing tracks are required")
    if not model_path.is_dir() or not (model_path / "model.bin").is_file():
        parser.error("--model must point to a complete local faster-whisper model")

    infos = [wav_info(path) for path in paths]
    if len(set(infos)) != 1:
        raise ValueError("aligned tracks must have equal sample rate and frame count")
    sample_rate, frame_count = infos[0]
    targets = [output_dir / f"{label}.transcript.json" for label, _ in tracks]
    targets.extend(output_dir / f"{label}.transcript.txt" for label, _ in tracks)
    manifest_path = output_dir / "transcript_manifest.json"
    targets.append(manifest_path)
    if any(path.exists() for path in targets):
        raise FileExistsError("refusing to overwrite an existing transcript output")

    plan = {
        "tracks": [str(path) for path in paths],
        "model": str(model_path),
        "language": args.language,
        "word_timestamps": True,
        "outputs": [str(path) for path in targets],
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    from faster_whisper import WhisperModel, __version__ as faster_whisper_version

    output_dir.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type=args.compute_type,
        cpu_threads=args.cpu_threads,
        local_files_only=True,
    )
    output_records = []
    for label, path in tracks:
        segments_iter, info = model.transcribe(
            str(path),
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=True,
        )
        segments = []
        words = []
        for segment_index, segment in enumerate(segments_iter, start=1):
            segment_words = []
            for raw_word in segment.words or []:
                text = clean_text(raw_word.word)
                if not text or raw_word.start is None or raw_word.end is None:
                    continue
                start_sample = max(0, min(frame_count, round(raw_word.start * sample_rate)))
                end_sample = max(start_sample + 1, min(frame_count, round(raw_word.end * sample_rate)))
                record = {
                    "word_id": f"{label}:w{len(words) + 1:06d}",
                    "segment_id": f"{label}:s{segment_index:05d}",
                    "track": label,
                    "text": text,
                    "start_seconds": round(start_sample / sample_rate, 6),
                    "end_seconds": round(end_sample / sample_rate, 6),
                    "start_sample": start_sample,
                    "end_sample": end_sample,
                    "probability": round(float(raw_word.probability), 6),
                }
                words.append(record)
                segment_words.append(record["word_id"])
            start_sample = max(0, min(frame_count, round(segment.start * sample_rate)))
            end_sample = max(start_sample + 1, min(frame_count, round(segment.end * sample_rate)))
            segments.append(
                {
                    "segment_id": f"{label}:s{segment_index:05d}",
                    "track": label,
                    "text": clean_text(segment.text),
                    "start_seconds": round(start_sample / sample_rate, 6),
                    "end_seconds": round(end_sample / sample_rate, 6),
                    "start_sample": start_sample,
                    "end_sample": end_sample,
                    "word_ids": segment_words,
                }
            )

        transcript = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "timeline": "aligned_source_samples",
            "track": label,
            "source_path": str(path),
            "source_sha256": sha256_file(path),
            "sample_rate_hz": sample_rate,
            "frame_count": frame_count,
            "language": info.language,
            "language_probability": round(float(info.language_probability), 6),
            "segments": segments,
            "words": words,
        }
        json_path = output_dir / f"{label}.transcript.json"
        text_path = output_dir / f"{label}.transcript.txt"
        json_path.write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        text_path.write_text(
            "\n".join(
                f"[{item['start_seconds']:09.3f} --> {item['end_seconds']:09.3f}] {item['text']}"
                for item in segments
            )
            + "\n",
            encoding="utf-8",
        )
        output_records.append(
            {
                "track": label,
                "source_path": str(path),
                "source_sha256": transcript["source_sha256"],
                "transcript_json": str(json_path),
                "transcript_sha256": sha256_file(json_path),
                "segment_count": len(segments),
                "word_count": len(words),
            }
        )
        print(f"{label}: {len(segments)} segments, {len(words)} words", flush=True)

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "machine_transcript_requires_human_review",
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "model": {
            "path": str(model_path),
            "model_bin_sha256": sha256_file(model_path / "model.bin"),
            "faster_whisper_version": faster_whisper_version,
            "compute_type": args.compute_type,
            "beam_size": args.beam_size,
            "vad_filter": True,
            "word_timestamps": True,
        },
        "tracks": output_records,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
