"""The artifact predictor may flag risk, never remove review coverage."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "predict_cut_artifact.py"
SPEC = importlib.util.spec_from_file_location("predict_cut_artifact", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ArtifactRiskContractTests(unittest.TestCase):
    def test_block_is_a_risk_verdict(self) -> None:
        result = MODULE.predict_one(
            {
                "candidate_id": "C-risk",
                "candidate_kind": "immediate_repetition",
                "reason_key": "immediate_repetition",
                "start_seconds": 1.0,
                "end_seconds": 1.45,
                "clause_position": "clause-mid",
                "boundary_snap": {
                    "start_rms_at_snap": 500000,
                    "end_rms_at_snap": 500000,
                },
            }
        )
        self.assertEqual(result["verdict"], "BLOCK")
        self.assertIn("score", result)

    def test_artifact_manifest_uses_flagged_not_hidden(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"flagged_by_artifact_risk"', source)
        self.assertIn("不得自动隐藏", source)


if __name__ == "__main__":
    unittest.main()
