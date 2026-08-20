#!/usr/bin/env python3
"""Measure integrated loudness, loudness range, and true peak with FFmpeg."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_input(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("input must use LABEL=/absolute/path")
    label, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser()
    if not label or not path.is_absolute():
        raise argparse.ArgumentTypeError("input must use LABEL=/absolute/path")
    return label, path


def resolve_ffmpeg(explicit: Path | None) -> str:
    if explicit:
        path = explicit.expanduser()
        if path.is_file():
            return str(path)
        raise FileNotFoundError(path)
    override = os.environ.get("FFMPEG_BIN")
    if override and Path(override).is_file():
        return override
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    raise FileNotFoundError("ffmpeg was not found; pass --ffmpeg or set FFMPEG_BIN")


def last_float(pattern: str, text: str) -> float:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if not matches:
        raise ValueError(f"FFmpeg output did not match: {pattern}")
    return float(matches[-1])


def measure(ffmpeg: str, label: str, path: Path) -> dict[str, object]:
    process = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-nostdin",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true:framelog=verbose",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise RuntimeError(f"FFmpeg failed for {label}: {process.stderr.strip()}")
    stderr = process.stderr
    return {
        "label": label,
        "path": str(path),
        "sha256": sha256_file(path),
        "integrated_lufs": last_float(r"^\s*I:\s+(-?[\d.]+) LUFS$", stderr),
        "loudness_range_lu": last_float(r"^\s*LRA:\s+([\d.]+) LU$", stderr),
        "true_peak_dbtp": last_float(r"^\s*Peak:\s+(-?[\d.]+) dBFS$", stderr),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True, type=parse_input)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    args = parser.parse_args()

    output = args.output.expanduser()
    if not output.is_absolute():
        parser.error("--output must be an absolute path")
    if output.exists():
        parser.error(f"refusing to overwrite existing report: {output}")
    for _, path in args.input:
        if not path.is_file():
            parser.error(f"input does not exist: {path}")

    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    version = subprocess.run(
        [ffmpeg, "-version"], check=False, capture_output=True, text=True
    ).stdout.splitlines()[0]
    report = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "standard": "ITU-R BS.1770 via FFmpeg ebur128",
        "tool": version,
        "measurements": [measure(ffmpeg, label, path) for label, path in args.input],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
