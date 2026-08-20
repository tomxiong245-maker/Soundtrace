#!/usr/bin/env python3
"""Classify transcript segments as primary mic, bleed, or ambiguous by track energy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def parse_label_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("value must use LABEL=/absolute/path")
    label, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser()
    if not label or not path.is_absolute():
        raise argparse.ArgumentTypeError("value must use LABEL=/absolute/path")
    return label, path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def decode_pcm(data: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 2:
        return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
    if sample_width == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        values = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        values = np.where(values & 0x800000, values - 0x1000000, values)
        return values.astype(np.float32) / 8388608
    if sample_width == 4:
        return np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648
    raise ValueError(f"unsupported PCM width: {sample_width}")


def energy_envelope(path: Path, window_samples: int) -> tuple[int, int, np.ndarray]:
    values = []
    pending = np.empty(0, dtype=np.float32)
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE" or audio.getnchannels() != 1:
            raise ValueError(f"expected mono PCM WAV: {path}")
        sample_rate = audio.getframerate()
        frame_count = audio.getnframes()
        sample_width = audio.getsampwidth()
        while data := audio.readframes(window_samples * 2048):
            samples = decode_pcm(data, sample_width)
            if pending.size:
                samples = np.concatenate((pending, samples))
            whole = samples.size // window_samples
            if whole:
                blocks = samples[: whole * window_samples].reshape(whole, window_samples)
                values.extend(np.mean(np.square(blocks, dtype=np.float64), axis=1))
            pending = samples[whole * window_samples :]
        if pending.size:
            values.append(float(np.mean(np.square(pending, dtype=np.float64))))
    return sample_rate, frame_count, np.asarray(values, dtype=np.float64)


def interval_db(envelope: np.ndarray, start: int, end: int, window_samples: int) -> float:
    first = max(0, start // window_samples)
    last = min(envelope.size, max(first + 1, math.ceil(end / window_samples)))
    power = float(np.median(envelope[first:last])) if last > first else 0.0
    return 10 * math.log10(max(power, 1e-12))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=parse_label_path)
    parser.add_argument("--transcript", action="append", required=True, type=parse_label_path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--window-ms", type=int, default=20)
    parser.add_argument("--dominance-db", type=float, default=3.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tracks = dict(args.track)
    transcripts = dict(args.transcript)
    output_dir = args.output_dir.expanduser()
    inputs = [*tracks.values(), *transcripts.values()]
    if not output_dir.is_absolute() or any(not path.is_absolute() for path in inputs):
        parser.error("all paths must be absolute")
    if len(tracks) < 2 or tracks.keys() != transcripts.keys():
        parser.error("at least two matching track and transcript labels are required")
    if any(not path.is_file() for path in inputs):
        parser.error("an input file does not exist")
    if not 1 <= args.window_ms <= 100 or not 0 <= args.dominance_db <= 20:
        parser.error("invalid window or dominance threshold")
    targets = [output_dir / f"{label}.classified.json" for label in tracks]
    manifest_path = output_dir / "activity_manifest.json"
    if any(path.exists() for path in [*targets, manifest_path]):
        raise FileExistsError("refusing to overwrite activity classification outputs")
    if args.dry_run:
        print(json.dumps({"labels": list(tracks), "outputs": [str(path) for path in targets]}, indent=2))
        return 0

    first_track = next(iter(tracks.values()))
    with wave.open(str(first_track), "rb") as audio:
        window_samples = round(audio.getframerate() * args.window_ms / 1000)
    envelopes = {}
    shared_info = set()
    for label, path in tracks.items():
        sample_rate, frame_count, envelope = energy_envelope(path, window_samples)
        envelopes[label] = envelope
        shared_info.add((sample_rate, frame_count, envelope.size))
    if len(shared_info) != 1:
        raise ValueError("aligned tracks produced different energy timelines")
    sample_rate, frame_count, _ = shared_info.pop()

    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for label, transcript_path in transcripts.items():
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        counts = {"primary": 0, "bleed": 0, "ambiguous": 0}
        segment_classes = {}
        other_labels = [item for item in tracks if item != label]
        for segment in transcript["segments"]:
            own_db = interval_db(
                envelopes[label], segment["start_sample"], segment["end_sample"], window_samples
            )
            other_db = max(
                interval_db(
                    envelopes[other], segment["start_sample"], segment["end_sample"], window_samples
                )
                for other in other_labels
            )
            dominance = own_db - other_db
            if dominance >= args.dominance_db:
                classification = "primary"
            elif dominance <= -args.dominance_db:
                classification = "bleed"
            else:
                classification = "ambiguous"
            segment["activity"] = {
                "classification": classification,
                "own_rms_dbfs": round(own_db, 3),
                "strongest_other_rms_dbfs": round(other_db, 3),
                "dominance_db": round(dominance, 3),
            }
            segment_classes[segment["segment_id"]] = segment["activity"]
            counts[classification] += 1
        for word in transcript["words"]:
            word["activity"] = segment_classes.get(word["segment_id"])
        transcript["activity_classification"] = {
            "method": "median_20ms_window_energy_comparison",
            "window_ms": args.window_ms,
            "dominance_threshold_db": args.dominance_db,
            "warning": "heuristic crosstalk label; ambiguous and semantic speaker identity require review",
        }
        output_path = output_dir / f"{label}.classified.json"
        output_path.write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        summaries.append(
            {
                "track": label,
                "source_transcript": str(transcript_path),
                "output": str(output_path),
                "output_sha256": sha256_file(output_path),
                "segment_counts": counts,
            }
        )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "heuristic_activity_labels_require_review",
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "window_ms": args.window_ms,
        "dominance_threshold_db": args.dominance_db,
        "tracks": summaries,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
