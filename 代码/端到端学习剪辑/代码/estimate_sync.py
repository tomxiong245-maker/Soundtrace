#!/usr/bin/env python3
"""Estimate offset and drift between two mono PCM WAV files."""

from __future__ import annotations

import argparse
import json
import math
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal

from inspect_audio import decode_pcm


def rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def read_window(path: Path, start_frame: int, frame_count: int) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getcomptype() != "NONE":
            raise ValueError(f"expected mono PCM WAV: {path}")
        sample_rate = audio.getframerate()
        audio.setpos(start_frame)
        raw = audio.readframes(frame_count)
        samples, _ = decode_pcm(raw, audio.getsampwidth(), 1)
    return sample_rate, samples[:, 0]


def downsample_feature(samples: np.ndarray, factor: int, feature: str) -> np.ndarray:
    usable = samples.size - samples.size % factor
    if usable <= 0:
        raise ValueError("window is too short for requested analysis rate")
    blocks = samples[:usable].reshape(-1, factor)
    if feature == "mean_waveform":
        return blocks.mean(axis=1)
    if feature == "rms_envelope":
        return np.sqrt(np.mean(np.square(blocks, dtype=np.float64), axis=1))
    raise ValueError(f"unknown sync feature: {feature}")


def estimate_window(
    reference: Path,
    candidate: Path,
    start_frame: int,
    frame_count: int,
    analysis_rate: int,
    max_lag_ms: float,
    feature: str,
) -> dict[str, object]:
    sample_rate, reference_samples = read_window(reference, start_frame, frame_count)
    candidate_rate, candidate_samples = read_window(candidate, start_frame, frame_count)
    if candidate_rate != sample_rate:
        raise ValueError("sample rates differ; resample before estimating sync")
    if sample_rate % analysis_rate:
        raise ValueError("sample rate must be an integer multiple of analysis rate")

    factor = sample_rate // analysis_rate
    reference_low = downsample_feature(reference_samples, factor, feature)
    candidate_low = downsample_feature(candidate_samples, factor, feature)
    reference_low -= reference_low.mean()
    candidate_low -= candidate_low.mean()

    correlation = signal.correlate(candidate_low, reference_low, mode="full", method="fft")
    lags = signal.correlation_lags(candidate_low.size, reference_low.size, mode="full")
    max_lag = max(1, round(max_lag_ms * analysis_rate / 1000))
    allowed = np.abs(lags) <= max_lag
    allowed_corr = correlation[allowed]
    allowed_lags = lags[allowed]
    peak_index = int(
        np.argmax(np.abs(allowed_corr)) if feature == "mean_waveform" else np.argmax(allowed_corr)
    )
    lag_low = int(allowed_lags[peak_index])
    peak = float(allowed_corr[peak_index])
    denominator = math.sqrt(
        float(np.square(reference_low).sum()) * float(np.square(candidate_low).sum())
    )
    normalized = peak / denominator if denominator else 0.0

    lag_samples = lag_low * factor
    return {
        "window_start_seconds": round(start_frame / sample_rate, 6),
        "window_center_seconds": round((start_frame + frame_count / 2) / sample_rate, 6),
        "window_duration_seconds": round(frame_count / sample_rate, 6),
        "candidate_vs_reference_lag_samples": lag_samples,
        "candidate_vs_reference_lag_ms": round(lag_samples * 1000 / sample_rate, 6),
        "correlation_polarity": 1 if normalized >= 0 else -1,
        "absolute_normalized_correlation": round(abs(normalized), 6),
    }


def wav_info(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getcomptype() != "NONE":
            raise ValueError(f"expected mono PCM WAV: {path}")
        return audio.getframerate(), audio.getnframes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--window-seconds", type=float, default=60.0)
    parser.add_argument("--window-count", type=int, default=5)
    parser.add_argument("--analysis-rate", type=int, default=1000)
    parser.add_argument("--max-lag-ms", type=float, default=200.0)
    parser.add_argument(
        "--feature", choices=("rms_envelope", "mean_waveform"), default="rms_envelope"
    )
    parser.add_argument("--minimum-correlation", type=float, default=0.6)
    args = parser.parse_args()

    for path in (args.reference, args.candidate, args.output):
        if not path.expanduser().is_absolute():
            parser.error("all paths must be absolute")
    reference = args.reference.expanduser()
    candidate = args.candidate.expanduser()
    output = args.output.expanduser()
    if output.exists():
        parser.error(f"refusing to overwrite existing report: {output}")
    if not 3 <= args.window_count <= 25:
        parser.error("--window-count must be within 3-25")
    if not 0.0 <= args.minimum_correlation <= 1.0:
        parser.error("--minimum-correlation must be within 0-1")

    reference_rate, reference_frames = wav_info(reference)
    candidate_rate, candidate_frames = wav_info(candidate)
    if (reference_rate, reference_frames) != (candidate_rate, candidate_frames):
        raise ValueError("inputs must currently have equal sample rate and frame count")

    window_frames = min(round(args.window_seconds * reference_rate), reference_frames)
    last_start = max(0, reference_frames - window_frames)
    starts = sorted(
        {
            round(value)
            for value in np.linspace(0, last_start, args.window_count, dtype=np.float64)
        }
    )
    windows = [
        estimate_window(
            reference,
            candidate,
            start,
            window_frames,
            args.analysis_rate,
            args.max_lag_ms,
            args.feature,
        )
        for start in starts
    ]
    trusted = [
        window
        for window in windows
        if window["absolute_normalized_correlation"] >= args.minimum_correlation
    ]
    factor = reference_rate // args.analysis_rate
    minimum_trusted = max(3, math.ceil(len(windows) * 0.6))
    if len(trusted) >= 2:
        centers = np.asarray(
            [window["window_center_seconds"] * reference_rate for window in trusted],
            dtype=np.float64,
        )
        lags = np.asarray(
            [window["candidate_vs_reference_lag_samples"] for window in trusted],
            dtype=np.float64,
        )
        slope, intercept = np.polyfit(centers, lags, 1)
        predicted = slope * centers + intercept
        residuals = lags - predicted
        residual_rms = float(np.sqrt(np.mean(np.square(residuals))))
        drift_ppm = float(slope * 1_000_000)
        initial_offset_samples = float(intercept)
        end_lag_samples = float(slope * reference_frames + intercept)
    else:
        drift_ppm = None
        initial_offset_samples = None
        end_lag_samples = None
        residual_rms = None
    fit_is_tight = residual_rms is not None and residual_rms <= factor * 2
    automatic_correction_allowed = len(trusted) >= minimum_trusted and fit_is_tight
    status = (
        "trusted_linear_fit"
        if automatic_correction_allowed
        else "candidate_fit_manual_confirmation_required"
        if len(trusted) >= 2
        else "insufficient_confident_windows"
    )

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reference": str(reference),
        "candidate": str(candidate),
        "sample_rate_hz": reference_rate,
        "frame_count": reference_frames,
        "analysis_rate_hz": args.analysis_rate,
        "analysis_feature": args.feature,
        "max_lag_ms": args.max_lag_ms,
        "minimum_correlation": args.minimum_correlation,
        "windows": windows,
        "trusted_window_count": len(trusted),
        "minimum_trusted_window_count": minimum_trusted,
        "estimation_status": status,
        "automatic_correction_allowed": automatic_correction_allowed,
        "estimated_initial_offset_samples": rounded(initial_offset_samples),
        "estimated_initial_offset_ms": rounded(
            initial_offset_samples * 1000 / reference_rate
            if initial_offset_samples is not None
            else None
        ),
        "estimated_drift_ppm": rounded(drift_ppm),
        "estimated_lag_at_end_samples": rounded(end_lag_samples),
        "estimated_lag_at_end_ms": rounded(
            end_lag_samples * 1000 / reference_rate
            if end_lag_samples is not None
            else None
        ),
        "fit_residual_rms_samples": rounded(residual_rms),
        "fit_residual_rms_ms": rounded(
            residual_rms * 1000 / reference_rate if residual_rms is not None else None
        ),
        "interpretation": (
            "candidate_vs_reference lag is fit against window-center time. Automatic correction "
            "requires enough high-correlation windows and a tight linear fit; otherwise inspect "
            "metadata or confirm alignment manually."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
