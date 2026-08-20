#!/usr/bin/env python3
"""Apply mild, deterministic, duration-preserving denoise to aligned WAV tracks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path


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


def wav_info(path: Path) -> dict[str, int]:
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError(f"compressed WAV is unsupported: {path}")
        return {
            "sample_rate_hz": audio.getframerate(),
            "frame_count": audio.getnframes(),
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
        }


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


def run(command: list[str]) -> None:
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=parse_track)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--noise-reduction-db", type=float, default=8.0)
    parser.add_argument("--noise-floor-db", type=float, default=-55.0)
    parser.add_argument("--gain-smooth", type=int, default=5)
    parser.add_argument("--latency-compensation-ms", type=float, default=25.0)
    parser.add_argument("--preview-start", action="append", type=float)
    parser.add_argument("--preview-duration", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tracks = [(label, path.expanduser()) for label, path in args.track]
    output_dir = args.output_dir.expanduser()
    if not output_dir.is_absolute() or any(not path.is_absolute() for _, path in tracks):
        parser.error("all paths must be absolute")
    if len(tracks) < 2 or any(not path.is_file() for _, path in tracks):
        parser.error("at least two existing tracks are required")
    if not 0.01 <= args.noise_reduction_db <= 30:
        parser.error("--noise-reduction-db must be within 0.01-30 for this MVP")
    if not -80 <= args.noise_floor_db <= -20:
        parser.error("--noise-floor-db must be within -80 to -20")
    if not 0 <= args.gain_smooth <= 50:
        parser.error("--gain-smooth must be within 0-50")
    if not 0 <= args.latency_compensation_ms <= 100:
        parser.error("--latency-compensation-ms must be within 0-100")

    infos = [wav_info(path) for _, path in tracks]
    shared = {(item["sample_rate_hz"], item["frame_count"]) for item in infos}
    if len(shared) != 1:
        raise ValueError("aligned tracks must have equal sample rate and frame count")
    if any(item["channels"] != 1 for item in infos):
        raise ValueError("MVP currently expects mono source tracks")
    sample_rate, frame_count = shared.pop()

    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    preview_starts = args.preview_start or [60.0, 900.0, 1500.0]
    denoise_filter = (
        f"afftdn=nr={args.noise_reduction_db}:nf={args.noise_floor_db}:"
        f"tn=1:gs={args.gain_smooth}"
    )
    compensation_samples = round(sample_rate * args.latency_compensation_ms / 1000.0)
    filter_spec = denoise_filter
    if compensation_samples:
        filter_spec = (
            f"{denoise_filter},"
            f"atrim=start_sample={compensation_samples},"
            f"apad=pad_len={compensation_samples},"
            f"atrim=end_sample={frame_count}"
        )
    denoised = [output_dir / f"{label}.denoised.wav" for label, _ in tracks]
    mix_path = output_dir / "speech_mix.denoised.wav"
    manifest_path = output_dir / "denoise_manifest.json"
    targets = [*denoised, mix_path, manifest_path]
    if any(path.exists() for path in targets):
        raise FileExistsError("refusing to overwrite an existing denoise output")

    plan = {
        "filter": filter_spec,
        "denoise_filter": denoise_filter,
        "latency_compensation_samples": compensation_samples,
        "tracks": [str(path) for path in denoised],
        "mix": str(mix_path),
        "preview_starts_seconds": preview_starts,
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(exist_ok=True)
    for (_, source), destination in zip(tracks, denoised):
        run(
            [
                ffmpeg,
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
                str(destination),
            ]
        )
        output_info = wav_info(destination)
        if output_info["sample_rate_hz"] != sample_rate or output_info["frame_count"] != frame_count:
            raise RuntimeError(f"denoise changed the timeline for {destination.name}")

    input_args: list[str] = []
    for path in denoised:
        input_args.extend(["-i", str(path)])
    weight = round(1 / len(denoised), 9)
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            *input_args,
            "-filter_complex",
            f"amix=inputs={len(denoised)}:normalize=0:weights='{' '.join([str(weight)] * len(denoised))}'[mix]",
            "-map",
            "[mix]",
            "-c:a",
            "pcm_s24le",
            str(mix_path),
        ]
    )
    if wav_info(mix_path)["frame_count"] != frame_count:
        raise RuntimeError("denoised mix does not match the source timeline")

    preview_records = []
    for label, source in tracks:
        clean = output_dir / f"{label}.denoised.wav"
        for index, start in enumerate(preview_starts, start=1):
            for variant, path in (("raw", source), ("denoised", clean)):
                target = preview_dir / f"{index:02d}.{label}.{variant}.mp3"
                run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-ss",
                        str(start),
                        "-t",
                        str(args.preview_duration),
                        "-i",
                        str(path),
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        str(target),
                    ]
                )
                preview_records.append(
                    {"label": label, "variant": variant, "start_seconds": start, "path": str(target)}
                )

    version = subprocess.run(
        [ffmpeg, "-version"], check=False, capture_output=True, text=True
    ).stdout.splitlines()[0]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "duration_preserving_denoise_ab_not_subjectively_approved",
        "filter": filter_spec,
        "denoise_filter": denoise_filter,
        "latency_compensation": {
            "milliseconds": args.latency_compensation_ms,
            "samples": compensation_samples,
            "method": "trim delayed leading samples and append equal tail padding",
        },
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "tools": {"ffmpeg": version},
        "tracks": [
            {
                "label": label,
                "source_path": str(source),
                "source_sha256": sha256_file(source),
                "output_path": str(destination),
                "output_sha256": sha256_file(destination),
            }
            for (label, source), destination in zip(tracks, denoised)
        ],
        "mix": {"path": str(mix_path), "sha256": sha256_file(mix_path)},
        "previews": preview_records,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
