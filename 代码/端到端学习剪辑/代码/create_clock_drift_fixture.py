#!/usr/bin/env python3
"""Create a same-length WAV with known start offset and clock drift."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def wav_info(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE" or audio.getnchannels() != 1:
            raise ValueError("source must be a mono PCM WAV")
        return audio.getframerate(), audio.getnframes(), audio.getsampwidth()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--clock-ppm", type=float, default=100.0)
    parser.add_argument("--start-offset-ms", type=float, default=75.0)
    args = parser.parse_args()

    source = args.source.expanduser()
    output_dir = args.output_dir.expanduser()
    ffmpeg = args.ffmpeg.expanduser()
    if any(not path.is_absolute() for path in (source, output_dir, ffmpeg)):
        parser.error("all paths must be absolute")
    if not source.is_file() or not ffmpeg.is_file():
        parser.error("source and ffmpeg must exist")
    if output_dir.exists():
        parser.error("refusing to overwrite an existing fixture directory")
    if not -1000.0 <= args.clock_ppm <= 1000.0:
        parser.error("--clock-ppm must be within -1000 to 1000")
    if not 0.0 <= args.start_offset_ms <= 5000.0:
        parser.error("--start-offset-ms must be within 0-5000")

    sample_rate, frame_count, sample_width = wav_info(source)
    actual_rate = sample_rate * (1.0 + args.clock_ppm / 1_000_000.0)
    offset_samples = round(sample_rate * args.start_offset_ms / 1000.0)
    output_dir.mkdir(parents=True)
    candidate = output_dir / "candidate.clock-drift.wav"
    manifest_path = output_dir / "fixture_manifest.json"
    filter_spec = (
        f"asetrate={actual_rate:.9f},aresample={sample_rate},"
        f"adelay={offset_samples}S:all=1,apad,atrim=end_sample={frame_count}"
    )
    process = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(source),
            "-af",
            filter_spec,
            "-c:a",
            "pcm_s24le",
            str(candidate),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip())
    candidate_rate, candidate_frames, candidate_width = wav_info(candidate)
    if (candidate_rate, candidate_frames) != (sample_rate, frame_count):
        raise RuntimeError("fixture did not preserve declared sample rate and frame count")

    duration_seconds = frame_count / sample_rate
    expected_end_lag_ms = args.start_offset_ms - args.clock_ppm * duration_seconds / 1000.0
    version = subprocess.run(
        [str(ffmpeg), "-version"], check=False, capture_output=True, text=True
    ).stdout.splitlines()[0]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "known-offset known-clock-drift sync estimator fixture",
        "source": {
            "path": str(source),
            "sha256": sha256_file(source),
            "sample_rate_hz": sample_rate,
            "frame_count": frame_count,
            "sample_width_bytes": sample_width,
        },
        "candidate": {
            "path": str(candidate),
            "sha256": sha256_file(candidate),
            "sample_rate_hz": candidate_rate,
            "frame_count": candidate_frames,
            "sample_width_bytes": candidate_width,
        },
        "parameters": {
            "clock_ppm": args.clock_ppm,
            "start_offset_ms": args.start_offset_ms,
            "start_offset_samples": offset_samples,
            "simulated_actual_clock_rate_hz": actual_rate,
            "filter": filter_spec,
        },
        "expected_model": {
            "candidate_lag_ms_at_reference_time_t": (
                "start_offset_ms - clock_ppm * reference_seconds / 1000"
            ),
            "expected_lag_ms_at_start": args.start_offset_ms,
            "expected_lag_ms_at_end": round(expected_end_lag_ms, 6),
            "expected_estimated_drift_ppm": -args.clock_ppm,
        },
        "tool": version,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
