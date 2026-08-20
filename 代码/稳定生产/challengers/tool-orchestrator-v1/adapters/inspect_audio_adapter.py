#!/usr/bin/env python3
"""Adapter: inspect_audio_adapter.

Bridges the runner's uniform --input-wav / --output-json contract to the
Champion inspect_audio.py CLI (--input LABEL=/abs/path [--input ...] --output).

Never modifies Champion. Only calls it as a subprocess in reads_only mode.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CHAMPION = PROJECT_ROOT / "端到端学习剪辑" / "代码" / "inspect_audio.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-wav", required=True, action="append",
                        help="repeatable: LABEL=/abs/path or /abs/path (auto label)")
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()

    if not CHAMPION.is_file():
        print(f"ERROR: Champion inspect_audio not found: {CHAMPION}", file=sys.stderr)
        return 2

    cmd = [sys.executable, str(CHAMPION), "--output", str(args.output_json.resolve())]
    for i, entry in enumerate(args.input_wav):
        if "=" in entry:
            label, path = entry.split("=", 1)
        else:
            label = f"track_{i+1:02d}"
            path = entry
        p = Path(path).expanduser().resolve()
        cmd += ["--input", f"{label}={p}"]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sys.stdout.buffer.write(proc.stdout)
    sys.stderr.buffer.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
