"""Verify that Champion & other protected files are byte-identical to their
baseline SHA-256 as recorded in before_metrics.json of cross-track-safety-v1
(the last known-good frozen SHAs)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as h:
        while chunk := h.read(1024 * 1024):
            d.update(chunk)
    return d.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True,
                    help="project root, e.g. /path/to/audio-clips")
    ap.add_argument("--baseline-sha-file", type=Path, required=True,
                    help="cross-track-safety-v1/before_metrics.json")
    args = ap.parse_args()

    doc = json.loads(args.baseline_sha_file.read_text(encoding="utf-8"))
    expected = doc.get("baseline_sha256")
    if not expected:
        print("missing baseline_sha256 in doc", file=sys.stderr)
        return 2
    failures = []
    for rel, exp_sha in expected.items():
        full = args.repo / rel
        if not full.is_file():
            failures.append((rel, "MISSING"))
            continue
        act = sha256_file(full)
        if act != exp_sha:
            failures.append((rel, f"MISMATCH expected={exp_sha} actual={act}"))
    result = {
        "checked_at": None,
        "n_files": len(expected),
        "n_failures": len(failures),
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
