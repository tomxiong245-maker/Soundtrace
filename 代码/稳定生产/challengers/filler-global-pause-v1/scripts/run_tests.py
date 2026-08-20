#!/usr/bin/env python3
"""Run the focused Challenger test suite without writing bytecode caches."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
tests = Path(__file__).resolve().parents[1] / "tests"
suite = unittest.defaultTestLoader.discover(str(tests), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
