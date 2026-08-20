#!/usr/bin/env python3
"""Run the pinned local DeepFilterNet CLI on aligned mono WAV tracks.

The upstream v0.5.6 ``--compensate-delay`` mode removes 1,440 samples at
48 kHz.  This adapter restores the original sample count by appending the
unaltered final 30 ms from the source track.  It never overwrites a source
or an existing output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEEP_FILTER = PROJECT_ROOT / ".tools/deepfilternet-v0.5.6/deep-filter"
PINNED_BINARY_SHA256 = "4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081"
MODEL_SAMPLE_RATE_HZ = 48_000
COMPENSATED_DELAY_SAMPLES = 1_440


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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
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
        return str(explicit.expanduser().resolve())
    override = os.environ.get("FFMPEG_BIN")
    if override and Path(override).is_file():
        return str(Path(override).resolve())
    discovered = shutil.which("ffmpeg")
    if discovered:
        return str(Path(discovered).resolve())
    raise FileNotFoundError("ffmpeg was not found; pass --ffmpeg or set FFMPEG_BIN")


def resolve_deep_filter(explicit: Path | None) -> Path:
    candidate = (explicit or DEFAULT_DEEP_FILTER).expanduser()
    if not candidate.is_file():
        raise FileNotFoundError(
            f"DeepFilterNet CLI is missing: {candidate}; run the local tool installer first"
        )
    actual = sha256_file(candidate)
    if actual != PINNED_BINARY_SHA256:
        raise RuntimeError(
            "DeepFilterNet CLI SHA-256 mismatch; refusing an unpinned binary "
            f"(expected {PINNED_BINARY_SHA256}, got {actual})"
        )
    return candidate.resolve()


def run(command: list[str], label: str) -> str:
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode:
        tail = (process.stderr or process.stdout)[-1_200:].strip()
        raise RuntimeError(f"{label} failed: {tail}")
    return process.stdout.strip()


def make_pcm16(source: Path, destination: Path, ffmpeg: str, sample_rate: int) -> None:
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        f"PCM16 preparation for {source.name}",
    )


def restore_tail(
    *,
    enhanced_short: Path,
    source: Path,
    destination: Path,
    short_frames: int,
    source_frames: int,
    sample_rate: int,
    ffmpeg: str,
) -> None:
    # The final 30 ms is deliberately copied from the original, not padded with
    # silence: this preserves an ending consonant or room decay while keeping the
    # rest of the stream aligned with the original sample timeline.
    filter_graph = (
        f"[0:a]atrim=end_sample={short_frames},asetpts=PTS-STARTPTS[enhanced];"
        f"[1:a]atrim=start_sample={short_frames}:end_sample={source_frames},"
        "asetpts=PTS-STARTPTS[raw_tail];"
        "[enhanced][raw_tail]concat=n=2:v=0:a=1,"
        f"atrim=end_sample={source_frames}[out]"
    )
    partial = destination.with_name(f".{destination.stem}.partial.wav")
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(enhanced_short),
            "-i",
            str(source),
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(partial),
        ],
        f"timeline restoration for {source.name}",
    )
    os.replace(partial, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", action="append", required=True, type=parse_track)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--deep-filter", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument(
        "--atten-lim-db",
        type=float,
        default=12.0,
        help="maximum attenuation; 12 dB is a conservative production starting point",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tracks = [(label, path.expanduser()) for label, path in args.track]
    output_dir = args.output_dir.expanduser()
    if not output_dir.is_absolute() or any(not path.is_absolute() for _, path in tracks):
        parser.error("all paths must be absolute")
    if not tracks or any(not path.is_file() for _, path in tracks):
        parser.error("at least one existing track is required")
    if output_dir.exists():
        raise FileExistsError(f"refusing to reuse DeepFilterNet output directory: {output_dir}")
    if not 0 <= args.atten_lim_db <= 100:
        parser.error("--atten-lim-db must be within 0-100")

    infos = [wav_info(path) for _, path in tracks]
    shared_timelines = {(item["sample_rate_hz"], item["frame_count"]) for item in infos}
    if len(shared_timelines) != 1:
        raise ValueError("aligned tracks must have equal sample rate and frame count")
    if any(item["channels"] != 1 for item in infos):
        raise ValueError("DeepFilterNet adapter expects mono input tracks")
    sample_rate, frame_count = shared_timelines.pop()
    if sample_rate != MODEL_SAMPLE_RATE_HZ:
        raise ValueError(
            f"DeepFilterNet v0.5.6 adapter is pinned to {MODEL_SAMPLE_RATE_HZ} Hz, got {sample_rate}"
        )

    deep_filter = resolve_deep_filter(args.deep_filter)
    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    version = run([str(deep_filter), "--version"], "DeepFilterNet version check")
    plan = {
        "backend": "DeepFilterNet",
        "backend_version": version,
        "binary": str(deep_filter),
        "binary_sha256": PINNED_BINARY_SHA256,
        "atten_lim_db": args.atten_lim_db,
        "sample_rate_hz": sample_rate,
        "frame_count": frame_count,
        "delay_compensation": {
            "upstream_flag": "--compensate-delay",
            "expected_shortfall_samples": COMPENSATED_DELAY_SAMPLES,
            "tail_policy": "append_unprocessed_source_tail",
        },
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    output_dir.mkdir(parents=True, exist_ok=False)
    outputs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix=".deepfilter-stage-", dir=output_dir) as raw_stage:
        stage_root = Path(raw_stage)
        for (label, source), source_info in zip(tracks, infos):
            pcm16 = stage_root / f"{label}.input.pcm16.wav"
            make_pcm16(source, pcm16, ffmpeg, sample_rate)
            prepared_info = wav_info(pcm16)
            if prepared_info["frame_count"] != frame_count:
                raise RuntimeError(f"PCM16 preparation changed timeline for {source.name}")
            deep_out = stage_root / f"{label}.deepfilter-out"
            deep_out.mkdir()
            run(
                [
                    str(deep_filter),
                    "--compensate-delay",
                    "--atten-lim-db",
                    str(args.atten_lim_db),
                    "--output-dir",
                    str(deep_out),
                    str(pcm16),
                ],
                f"DeepFilterNet for {source.name}",
            )
            enhanced_short = deep_out / pcm16.name
            if not enhanced_short.is_file():
                raise RuntimeError(f"DeepFilterNet did not produce {enhanced_short.name}")
            enhanced_info = wav_info(enhanced_short)
            shortfall = frame_count - enhanced_info["frame_count"]
            if enhanced_info["sample_rate_hz"] != sample_rate or shortfall != COMPENSATED_DELAY_SAMPLES:
                raise RuntimeError(
                    f"DeepFilterNet timeline contract changed for {source.name}: "
                    f"rate={enhanced_info['sample_rate_hz']}, shortfall={shortfall}"
                )
            destination = output_dir / f"{label}.deepfiltered.wav"
            restore_tail(
                enhanced_short=enhanced_short,
                source=source,
                destination=destination,
                short_frames=enhanced_info["frame_count"],
                source_frames=frame_count,
                sample_rate=sample_rate,
                ffmpeg=ffmpeg,
            )
            output_info = wav_info(destination)
            if output_info["sample_rate_hz"] != sample_rate or output_info["frame_count"] != frame_count:
                raise RuntimeError(f"restored DeepFilterNet output changed timeline for {source.name}")
            outputs.append(
                {
                    "track_id": label,
                    "source_path": str(source),
                    "source_sha256": sha256_file(source),
                    "source_sample_width_bytes": source_info["sample_width_bytes"],
                    "prepared_pcm16_path": "temporary_only",
                    "deepfilter_short_frames": enhanced_info["frame_count"],
                    "restored_tail_samples": shortfall,
                    "output_path": str(destination),
                    "output_sha256": sha256_file(destination),
                    "output_sample_width_bytes": output_info["sample_width_bytes"],
                }
            )

    manifest = {
        "schema_version": "deepfilternet-denoise-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "USER_AUTHORIZED_DIRECT_INTEGRATION__SUBJECTIVE_REVIEW_PENDING",
        **plan,
        "tracks": outputs,
    }
    (output_dir / "denoise_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output_dir / "denoise_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
