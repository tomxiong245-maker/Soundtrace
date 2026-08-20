"""Publish normalized transcript / speaker files as the hypotheses layer under
benchmark/EP03-ASR-mini-gold-v1/hypotheses/<engine>/S*/.

Currently copies files by SHA-256; caller must guarantee source integrity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as h:
        while chunk := h.read(1024 * 1024):
            d.update(chunk)
    return d.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True,
                    help="e.g. main/runs/EP03-asr-speaker-v1/normalized")
    ap.add_argument("--dest", type=Path, required=True,
                    help="e.g. benchmark/EP03-ASR-mini-gold-v1/hypotheses")
    ap.add_argument("--engine", required=True)
    args = ap.parse_args()

    src_root = args.source / args.engine
    if not src_root.is_dir():
        # Write a STATUS.json instead
        stub = args.dest / args.engine / "STATUS.json"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(json.dumps({
            "engine": args.engine, "status": "WAITING_FOR_M3_RUN",
            "note": "Source dir absent — engine has not been run on M3 yet."
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote stub", stub)
        return 0
    n = 0
    for seg_dir in sorted(src_root.iterdir()):
        if not seg_dir.is_dir():
            continue
        for f in sorted(seg_dir.iterdir()):
            if f.suffix != ".json":
                continue
            rel = f.relative_to(src_root)
            dest = args.dest / args.engine / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            n += 1
    print(f"copied {n} files to {args.dest / args.engine}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
