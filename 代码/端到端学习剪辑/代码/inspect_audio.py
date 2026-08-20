#!/usr/bin/env python3
"""Inspect local audio inputs without modifying them."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


CHUNK_FRAMES = 1_048_576
SILENCE_THRESHOLD = 10 ** (-60 / 20)


def parse_input(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("input must use LABEL=/absolute/path")
    label, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser()
    if not label or not path.is_absolute():
        raise argparse.ArgumentTypeError("input must use LABEL=/absolute/path")
    return label, path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def dbfs(value: float) -> float | None:
    if value <= 0:
        return None
    return round(20 * math.log10(value), 3)


def decode_pcm(data: bytes, sample_width: int, channels: int) -> np.ndarray:
    if sample_width == 1:
        values = (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128) / 128
        full_scale = 128
    elif sample_width == 2:
        values = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768
        full_scale = 32768
    elif sample_width == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        values_i32 = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        values_i32 = np.where(values_i32 & 0x800000, values_i32 - 0x1000000, values_i32)
        values = values_i32.astype(np.float64) / 8388608
        full_scale = 8388608
    elif sample_width == 4:
        values = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648
        full_scale = 2147483648
    else:
        raise ValueError(f"unsupported PCM sample width: {sample_width} bytes")
    if values.size % channels:
        raise ValueError("PCM data does not contain complete frames")
    return values.reshape(-1, channels), full_scale


def inspect_pcm_wav(path: Path) -> dict[str, object]:
    with wave.open(str(path), "rb") as audio:
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        sample_rate = audio.getframerate()
        frame_count = audio.getnframes()
        compression = audio.getcomptype()
        if compression != "NONE":
            raise ValueError(f"compressed WAV is unsupported: {compression}")

        sums = np.zeros(channels, dtype=np.float64)
        sums_sq = np.zeros(channels, dtype=np.float64)
        peaks = np.zeros(channels, dtype=np.float64)
        clipped = np.zeros(channels, dtype=np.int64)
        silent = np.zeros(channels, dtype=np.int64)
        observed_frames = 0

        while data := audio.readframes(CHUNK_FRAMES):
            samples, full_scale = decode_pcm(data, sample_width, channels)
            observed_frames += samples.shape[0]
            sums += samples.sum(axis=0)
            sums_sq += np.square(samples).sum(axis=0)
            peaks = np.maximum(peaks, np.abs(samples).max(axis=0))
            clipped += (np.abs(samples) >= (1 - 1 / full_scale)).sum(axis=0)
            silent += (np.abs(samples) < SILENCE_THRESHOLD).sum(axis=0)

    channel_stats = []
    for index in range(channels):
        rms = math.sqrt(sums_sq[index] / observed_frames) if observed_frames else 0
        channel_stats.append(
            {
                "channel": index + 1,
                "peak_dbfs": dbfs(float(peaks[index])),
                "rms_dbfs": dbfs(rms),
                "dc_offset": round(float(sums[index] / observed_frames), 9),
                "possible_clipped_samples": int(clipped[index]),
                "possible_clipped_ratio": round(float(clipped[index] / observed_frames), 9),
                "sample_silence_ratio_below_minus_60_dbfs": round(
                    float(silent[index] / observed_frames), 6
                ),
            }
        )

    return {
        "container": "WAVE",
        "codec": "pcm_sint",
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "bits_per_sample": sample_width * 8,
        "frame_count": frame_count,
        "duration_seconds": round(frame_count / sample_rate, 6),
        "observed_frames": observed_frames,
        "channel_stats": channel_stats,
    }


def resolve_tool(name: str) -> str | None:
    override = os.environ.get(f"{name.upper()}_BIN")
    if override and Path(override).is_file():
        return override
    return shutil.which(name)


def inspect_with_ffmpeg(path: Path) -> dict[str, object]:
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        return {"status": "unavailable", "reason": "ffmpeg is not installed"}
    process = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-t", "0", "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    probe_text = process.stderr.strip()
    duration_match = re.search(
        r"Duration: (\d+):(\d+):([\d.]+).*?bitrate: (\d+) kb/s", probe_text
    )
    audio_match = re.search(
        r"Audio: ([^,]+), (\d+) Hz, ([^,]+), ([^,\n]+)", probe_text
    )
    data: dict[str, object] = {"probe_text": probe_text}
    if duration_match:
        hours, minutes, seconds, bitrate = duration_match.groups()
        data.update(
            {
                "duration_seconds": round(
                    int(hours) * 3600 + int(minutes) * 60 + float(seconds), 6
                ),
                "container_bitrate_kbps": int(bitrate),
            }
        )
    if audio_match:
        codec, sample_rate, channel_layout, sample_format = audio_match.groups()
        data.update(
            {
                "codec": codec.strip(),
                "sample_rate_hz": int(sample_rate),
                "channel_layout": channel_layout.strip(),
                "sample_format": sample_format.strip(),
            }
        )
    return {"status": "ok", "data": data}


def inspect_with_ffprobe(path: Path) -> dict[str, object]:
    ffprobe = resolve_tool("ffprobe")
    if not ffprobe:
        return inspect_with_ffmpeg(path)
    process = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode:
        return {
            "status": "error",
            "exit_code": process.returncode,
            "stderr": process.stderr.strip(),
        }
    return {"status": "ok", "data": json.loads(process.stdout)}


def tool_version(name: str) -> str | None:
    executable = resolve_tool(name)
    if not executable:
        return None
    process = subprocess.run(
        [executable, "-version"], check=False, capture_output=True, text=True
    )
    first_line = (process.stdout or process.stderr).splitlines()
    return first_line[0] if first_line else executable


def inspect(label: str, path: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "label": label,
        "path": str(path),
        "exists": path.is_file(),
    }
    if not path.is_file():
        return result
    result.update(
        {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    )
    if path.suffix.lower() in {".wav", ".wave"}:
        try:
            result["audio"] = inspect_pcm_wav(path)
        except (wave.Error, ValueError) as error:
            result["wav_inspection_error"] = str(error)
            result["ffprobe"] = inspect_with_ffprobe(path)
    else:
        result["ffprobe"] = inspect_with_ffprobe(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        type=parse_input,
        help="repeatable LABEL=/absolute/path input",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    output = args.output.expanduser()
    if not output.is_absolute():
        parser.error("--output must be an absolute path")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        parser.error(f"refusing to overwrite existing report: {output}")

    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "processing_policy": "local_read_only_inputs",
        "tools": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "ffmpeg": tool_version("ffmpeg"),
            "ffprobe": tool_version("ffprobe"),
        },
        "inputs": [inspect(label, path) for label, path in args.input],
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
