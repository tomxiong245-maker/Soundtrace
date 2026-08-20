#!/usr/bin/env python3
"""Apply a trusted offset/drift fit to place a candidate WAV on reference time."""

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
            raise ValueError("WAV inputs must be mono PCM")
        return audio.getframerate(), audio.getnframes(), audio.getsampwidth()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--sync-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--allow-manual-fit", action="store_true")
    args = parser.parse_args()

    reference = args.reference.expanduser()
    candidate = args.candidate.expanduser()
    report_path = args.sync_report.expanduser()
    output_dir = args.output_dir.expanduser()
    ffmpeg = args.ffmpeg.expanduser()
    paths = (reference, candidate, report_path, output_dir, ffmpeg)
    if any(not path.is_absolute() for path in paths):
        parser.error("all paths must be absolute")
    if any(not path.is_file() for path in (reference, candidate, report_path, ffmpeg)):
        parser.error("reference, candidate, sync report, and ffmpeg must exist")
    if output_dir.exists():
        parser.error("refusing to overwrite an existing correction directory")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("automatic_correction_allowed") and not args.allow_manual_fit:
        raise ValueError("sync report is not trusted enough for automatic correction")
    offset_samples = report.get("estimated_initial_offset_samples")
    drift_ppm = report.get("estimated_drift_ppm")
    if not isinstance(offset_samples, (int, float)) or not isinstance(drift_ppm, (int, float)):
        raise ValueError("sync report has no usable offset/drift estimate")

    reference_rate, reference_frames, _ = wav_info(reference)
    candidate_rate, _, _ = wav_info(candidate)
    if candidate_rate != reference_rate:
        raise ValueError("candidate and reference sample rates must match before correction")
    offset = round(offset_samples)
    if offset < 0:
        raise ValueError("negative initial offsets are not supported by this MVP corrector")
    lag_slope = drift_ppm / 1_000_000.0
    rate_multiplier = 1.0 + lag_slope
    if not 0.99 <= rate_multiplier <= 1.01:
        raise ValueError("estimated rate multiplier is outside the MVP safety range")

    output_dir.mkdir(parents=True)
    corrected = output_dir / "candidate.corrected.wav"
    manifest_path = output_dir / "correction_manifest.json"
    filter_spec = (
        f"atrim=start_sample={offset},asetpts=PTS-STARTPTS,"
        f"asetrate={reference_rate * rate_multiplier:.9f},aresample={reference_rate},"
        f"apad,atrim=end_sample={reference_frames}"
    )
    process = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(candidate),
            "-af",
            filter_spec,
            "-c:a",
            "pcm_s24le",
            str(corrected),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip())
    corrected_rate, corrected_frames, corrected_width = wav_info(corrected)
    if (corrected_rate, corrected_frames) != (reference_rate, reference_frames):
        raise RuntimeError("corrected WAV does not match reference timeline")

    version = subprocess.run(
        [str(ffmpeg), "-version"], check=False, capture_output=True, text=True
    ).stdout.splitlines()[0]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "trusted_sync_fit_corrected_not_subjectively_approved",
        "reference": {
            "path": str(reference),
            "sha256": sha256_file(reference),
            "sample_rate_hz": reference_rate,
            "frame_count": reference_frames,
        },
        "candidate": {"path": str(candidate), "sha256": sha256_file(candidate)},
        "sync_report": {"path": str(report_path), "sha256": sha256_file(report_path)},
        "correction": {
            "initial_offset_samples_trimmed": offset,
            "initial_offset_ms_trimmed": round(offset * 1000 / reference_rate, 6),
            "candidate_lag_slope_ppm": drift_ppm,
            "asetrate_multiplier": rate_multiplier,
            "filter": filter_spec,
        },
        "output": {
            "path": str(corrected),
            "sha256": sha256_file(corrected),
            "sample_rate_hz": corrected_rate,
            "frame_count": corrected_frames,
            "sample_width_bytes": corrected_width,
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
