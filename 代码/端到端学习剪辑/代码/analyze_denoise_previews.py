#!/usr/bin/env python3
"""Measure aligned waveform and spectral differences in raw/denoised audio."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal


BANDS = (
    ("0_200_hz", 0.0, 200.0),
    ("200_1000_hz", 200.0, 1000.0),
    ("1000_4000_hz", 1000.0, 4000.0),
    ("4000_12000_hz", 4000.0, 12000.0),
    ("12000_24000_hz", 12000.0, 24000.0),
)


def parse_pair(value: str) -> tuple[str, Path, Path, float, float]:
    if "=" not in value or "," not in value:
        raise argparse.ArgumentTypeError(
            "pair must use LABEL=/raw/path,/denoised/path[,start_seconds,duration_seconds]"
        )
    label, paths = value.split("=", 1)
    parts = paths.split(",")
    if len(parts) not in (2, 4):
        raise argparse.ArgumentTypeError(
            "pair must contain two paths and optional start/duration values"
        )
    raw, denoised = (Path(part).expanduser() for part in parts[:2])
    start = float(parts[2]) if len(parts) == 4 else 0.0
    duration = float(parts[3]) if len(parts) == 4 else 0.0
    if not label or not raw.is_absolute() or not denoised.is_absolute():
        raise argparse.ArgumentTypeError("pair paths must be absolute")
    if start < 0 or duration < 0:
        raise argparse.ArgumentTypeError("pair start and duration must not be negative")
    return label, raw, denoised, start, duration


def resolve_ffmpeg(explicit: Path | None) -> str:
    if explicit and explicit.expanduser().is_file():
        return str(explicit.expanduser())
    override = os.environ.get("FFMPEG_BIN")
    if override and Path(override).is_file():
        return override
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    raise FileNotFoundError("ffmpeg was not found; pass --ffmpeg or set FFMPEG_BIN")


def decode_mono(
    ffmpeg: str,
    path: Path,
    sample_rate: int,
    start_seconds: float,
    duration_seconds: float,
) -> np.ndarray:
    trim = ["-ss", str(start_seconds)] if start_seconds else []
    limit = ["-t", str(duration_seconds)] if duration_seconds else []
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            *trim,
            "-i",
            str(path),
            *limit,
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "-",
        ],
        check=False,
        capture_output=True,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace").strip())
    return np.frombuffer(process.stdout, dtype="<f4").astype(np.float64)


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))


def to_db(value: float) -> float:
    return 20.0 * math.log10(max(abs(value), 1e-12))


def correlation(left: np.ndarray, right: np.ndarray) -> float:
    left_centered = left - left.mean()
    right_centered = right - right.mean()
    denominator = np.linalg.norm(left_centered) * np.linalg.norm(right_centered)
    return float(np.dot(left_centered, right_centered) / max(denominator, 1e-12))


def align_pair(
    raw: np.ndarray,
    denoised: np.ndarray,
    sample_rate: int,
    max_alignment_ms: float,
) -> tuple[np.ndarray, np.ndarray, int, float]:
    frame_count = min(raw.size, denoised.size)
    raw = raw[:frame_count]
    denoised = denoised[:frame_count]
    max_lag = min(round(max_alignment_ms * sample_rate / 1000.0), frame_count - 1)
    if max_lag <= 0:
        return raw, denoised, 0, correlation(raw, denoised)

    # FFT correlation finds deterministic filter or codec latency before null testing.
    raw_centered = raw - raw.mean()
    denoised_centered = denoised - denoised.mean()
    full = signal.correlate(denoised_centered, raw_centered, mode="full", method="fft")
    lags = signal.correlation_lags(denoised.size, raw.size, mode="full")
    selected = (lags >= -max_lag) & (lags <= max_lag)
    lag = int(lags[selected][np.argmax(full[selected])])

    if lag > 0:
        raw_aligned = raw[: frame_count - lag]
        denoised_aligned = denoised[lag:frame_count]
    elif lag < 0:
        raw_aligned = raw[-lag:frame_count]
        denoised_aligned = denoised[: frame_count + lag]
    else:
        raw_aligned = raw
        denoised_aligned = denoised
    return raw_aligned, denoised_aligned, lag, correlation(raw_aligned, denoised_aligned)


def band_power(frequencies: np.ndarray, density: np.ndarray, low: float, high: float) -> float:
    selected = (frequencies >= low) & (frequencies < high)
    if not np.any(selected):
        return 0.0
    return float(np.trapezoid(density[selected], frequencies[selected]))


def analyze_pair(
    ffmpeg: str,
    label: str,
    raw_path: Path,
    denoised_path: Path,
    start_seconds: float,
    duration_seconds: float,
    sample_rate: int,
    max_alignment_ms: float,
) -> dict[str, object]:
    raw = decode_mono(ffmpeg, raw_path, sample_rate, start_seconds, duration_seconds)
    denoised = decode_mono(
        ffmpeg, denoised_path, sample_rate, start_seconds, duration_seconds
    )
    raw, denoised, alignment_samples, aligned_correlation = align_pair(
        raw, denoised, sample_rate, max_alignment_ms
    )
    frame_count = raw.size
    residual = raw - denoised
    frequencies, raw_psd = signal.welch(raw, sample_rate, nperseg=4096)
    _, residual_psd = signal.welch(residual, sample_rate, nperseg=4096)
    residual_total = band_power(frequencies, residual_psd, 0.0, sample_rate / 2)
    bands = {}
    for name, low, high in BANDS:
        high = min(high, sample_rate / 2)
        raw_power = band_power(frequencies, raw_psd, low, high)
        residual_power = band_power(frequencies, residual_psd, low, high)
        bands[name] = {
            "residual_share_percent": round(
                100.0 * residual_power / max(residual_total, 1e-18), 3
            ),
            "residual_to_raw_db": round(
                10.0 * math.log10(max(residual_power, 1e-18) / max(raw_power, 1e-18)),
                3,
            ),
        }
    raw_rms = rms(raw)
    denoised_rms = rms(denoised)
    residual_rms = rms(residual)
    return {
        "label": label,
        "raw_path": str(raw_path),
        "denoised_path": str(denoised_path),
        "source_start_seconds": start_seconds,
        "duration_seconds": round(frame_count / sample_rate, 6),
        "alignment_samples": alignment_samples,
        "alignment_ms": round(1000.0 * alignment_samples / sample_rate, 6),
        "raw_rms_dbfs": round(to_db(raw_rms), 3),
        "denoised_rms_dbfs": round(to_db(denoised_rms), 3),
        "denoised_minus_raw_rms_db": round(to_db(denoised_rms) - to_db(raw_rms), 3),
        "waveform_correlation": round(aligned_correlation, 6),
        "residual_to_raw_rms_db": round(to_db(residual_rms) - to_db(raw_rms), 3),
        "spectral_residual": bands,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", action="append", required=True, type=parse_pair)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--max-alignment-ms", type=float, default=100.0)
    parser.add_argument("--ffmpeg", type=Path)
    args = parser.parse_args()

    output = args.output.expanduser()
    if not output.is_absolute():
        parser.error("--output must be absolute")
    if output.exists():
        parser.error(f"refusing to overwrite existing report: {output}")
    if not 0 <= args.max_alignment_ms <= 1000:
        parser.error("--max-alignment-ms must be within 0-1000")
    if any(
        not path.is_file()
        for _, raw, denoised, _, _ in args.pair
        for path in (raw, denoised)
    ):
        parser.error("a preview input does not exist")

    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    report = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "objective_denoise_difference_subjective_review_pending",
        "sample_rate_hz": args.sample_rate,
        "pairs": [
            analyze_pair(
                ffmpeg,
                label,
                raw,
                denoised,
                start,
                duration,
                args.sample_rate,
                args.max_alignment_ms,
            )
            for label, raw, denoised, start, duration in args.pair
        ],
        "limitations": [
            "Waveform metrics are calculated from aligned WAV windows, avoiding MP3 preview delay.",
            "Alignment corrects one constant lag but cannot undo frequency-dependent phase change.",
            "Small waveform change does not prove absence of musical noise, pumping, or speech damage.",
            "A listener must still approve intelligibility, naturalness, and fatigue.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
