#!/usr/bin/env python3
"""Create aligned intro, middle, and outro previews for MVP/reference comparison."""

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


def duration(ffmpeg: str, path: Path) -> float:
    process = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", process.stderr)
    if not match:
        raise ValueError(f"could not determine duration: {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def run(command: list[str]) -> None:
    process = subprocess.run(command, check=False, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mvp", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--timeline-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--speech-crossfade-ms", type=int, default=50)
    args = parser.parse_args()

    mvp = args.mvp.expanduser()
    reference = args.reference.expanduser()
    report_path = args.timeline_report.expanduser()
    output_dir = args.output_dir.expanduser()
    inputs = (mvp, reference, report_path)
    if not output_dir.is_absolute() or any(not path.is_absolute() for path in inputs):
        parser.error("all paths must be absolute")
    if any(not path.is_file() for path in inputs):
        parser.error("an input file does not exist")
    if output_dir.exists():
        raise FileExistsError("refusing to overwrite an existing A/B directory")

    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    mvp_duration = duration(ffmpeg, mvp)
    reference_duration = duration(ffmpeg, reference)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    candidates = report.get("inferred_cut_candidates", [])
    segments = [
        ("intro", 0.0, 30.0),
        ("middle", reference_duration / 2 - 15.0, 30.0),
        ("outro", reference_duration - 60.0, 60.0),
    ]

    output_dir.mkdir(parents=True)
    records = []
    for name, reference_start, preview_duration in segments:
        preceding_cuts = sum(
            1
            for candidate in candidates
            if candidate["reference_boundary_range_seconds"][1] <= reference_start
        )
        mvp_start = max(
            0.0, reference_start - preceding_cuts * args.speech_crossfade_ms / 1000
        )
        for label, path, start in (
            ("mvp", mvp, mvp_start),
            ("mentor", reference, reference_start),
        ):
            target = output_dir / f"{name}.{label}.mp3"
            run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-ss",
                    f"{start:.6f}",
                    "-t",
                    str(preview_duration),
                    "-i",
                    str(path),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "160k",
                    str(target),
                ]
            )
            records.append(
                {
                    "segment": name,
                    "version": label,
                    "source_start_seconds": round(start, 6),
                    "duration_seconds": preview_duration,
                    "path": str(target),
                    "sha256": sha256_file(target),
                }
            )

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "aligned_ab_for_subjective_review",
        "mvp": {"path": str(mvp), "duration_seconds": mvp_duration},
        "mentor": {"path": str(reference), "duration_seconds": reference_duration},
        "alignment": {
            "method": "reference_time_minus_accumulated_speech_crossfade",
            "speech_crossfade_ms": args.speech_crossfade_ms,
            "warning": "approximate listening alignment, not a sample-accurate null test",
        },
        "previews": records,
    }
    manifest_path = output_dir / "ab_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
