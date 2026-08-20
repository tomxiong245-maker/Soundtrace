#!/usr/bin/env python3
"""Compatibility entry point for the canonical transcript text-layer builder.

The multilingual Challenger intentionally does not keep a second copy of
normalization, Traditional→Simplified mapping, display-span logic, or
integrity checks.  It imports the production canonical module and preserves
the historical ``--input``/``--out`` command-line entry point.
"""

from __future__ import annotations

import sys
from pathlib import Path


ORCHESTRATOR = Path(__file__).resolve().parents[4] / "main" / "orchestrator"
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

from transcript_text_layers import *  # noqa: F401,F403,E402
from transcript_text_layers import main as _canonical_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_canonical_main())
