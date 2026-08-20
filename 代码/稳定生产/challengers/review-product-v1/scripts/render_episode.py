#!/usr/bin/env python3
"""Render the human-approved EDL belonging to one episode config."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from episode_config import load_episode_config


RENDERER = Path(__file__).with_name("render_ntrack_edl.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_episode_config(args.config)
    if not config.edl_path.is_file():
        raise SystemExit(
            f"no human-approved EDL for {config.episode_id}; finish review first: {config.edl_path}"
        )
    if args.output_dir and not args.output_dir.expanduser().is_absolute():
        parser.error("output-dir must be absolute")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else config.run_dir / f"final-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )

    command = [
        sys.executable,
        str(RENDERER),
        "--edl",
        str(config.edl_path),
        "--output-dir",
        str(output_dir),
        "--ffmpeg",
        str(config.ffmpeg),
    ]
    if args.dry_run:
        command.append("--dry-run")
    subprocess.run(command, check=True)
    print(
        json.dumps(
            {
                "status": "DRY_RUN" if args.dry_run else "RENDERED",
                "episode_id": config.episode_id,
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
