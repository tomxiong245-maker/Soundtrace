#!/usr/bin/env python3
"""Adapter: summarize_inspection_adapter.

Reads-only. Takes an existing inspection.json (produced by inspect_audio) and
writes a small summary.json used to prove multi-step orchestration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input-json", required=True, type=Path)
    p.add_argument("--output-json", required=True, type=Path)
    args = p.parse_args()

    data = json.loads(args.input_json.read_text(encoding="utf-8"))
    inputs = data.get("inputs", [])
    summary = {
        "tool": "summarize_inspection_adapter",
        "input_json_sha256": hashlib.sha256(args.input_json.read_bytes()).hexdigest(),
        "track_count": len(inputs),
        "sample_rates": sorted({i["audio"]["sample_rate_hz"] for i in inputs if "audio" in i}),
        "channel_counts": sorted({i["audio"]["channels"] for i in inputs if "audio" in i}),
        "durations_seconds": [round(i["audio"]["duration_seconds"], 3) for i in inputs if "audio" in i],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
