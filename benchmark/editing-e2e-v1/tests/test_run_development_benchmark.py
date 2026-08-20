#!/usr/bin/env python3
"""Focused tests for the non-blocking development-benchmark lifecycle wrapper."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "run_development_benchmark.py"
SPEC = importlib.util.spec_from_file_location("run_development_benchmark", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class DevelopmentBenchmarkWrapperTests(unittest.TestCase):
    def test_existing_audit_reuses_the_frozen_sampling_parameters(self) -> None:
        """A lifecycle refresh must not replace a historical audit's seed."""

        with tempfile.TemporaryDirectory() as temporary:
            audit_path = Path(temporary) / "no_candidate_windows.json"
            write_json(
                audit_path,
                {
                    "schema_version": "no-candidate-window-audit-v1",
                    "provenance": {
                        "run_id": "EPTEST-v20",
                        "run_identity_sha256": "a" * 64,
                    },
                    "parameters": {
                        "seed": "EPTEST-legacy-stable-seed",
                        "count": 11,
                        "window_seconds": "27.5",
                        "candidate_handle_seconds": "6",
                    },
                },
            )
            self.assertEqual(
                MODULE.existing_audit_parameters(
                    audit_path,
                    run_id="EPTEST-v20",
                    run_identity_sha256="a" * 64,
                ),
                ("EPTEST-legacy-stable-seed", 11, "27.5", "6"),
            )

    def test_existing_audit_with_a_different_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            audit_path = Path(temporary) / "no_candidate_windows.json"
            write_json(
                audit_path,
                {
                    "schema_version": "no-candidate-window-audit-v1",
                    "provenance": {
                        "run_id": "EPTEST-v20",
                        "run_identity_sha256": "a" * 64,
                    },
                    "parameters": {
                        "seed": "stable",
                        "count": 8,
                        "window_seconds": "25",
                        "candidate_handle_seconds": "5",
                    },
                },
            )
            with self.assertRaisesRegex(MODULE.BenchmarkLifecycleError, "identity SHA differs"):
                MODULE.existing_audit_parameters(
                    audit_path,
                    run_id="EPTEST-v20",
                    run_identity_sha256="b" * 64,
                )


if __name__ == "__main__":
    unittest.main()
