"""Regression tests for the safe human-label preference loop."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import apply_preference_snapshot as apply  # noqa: E402
import build_preference_snapshot as snapshot  # noqa: E402


class PreferenceSnapshotLoopTests(unittest.TestCase):
    def test_canonical_case_paths_dedupe_against_discovered_run_names(self) -> None:
        self.assertEqual(
            snapshot.record_dedupe_key("EP04-review-product-v2", "C047"),
            snapshot.record_dedupe_key("main/runs/EP04-review-product-v2", "C047"),
        )
        self.assertNotEqual(
            snapshot.record_dedupe_key("EP04-review-product-v2", "C047"),
            snapshot.record_dedupe_key("EP04-review-product-v2", "C048"),
        )

    def test_history_only_adds_review_signal_and_keeps_no_decision_policy(self) -> None:
        records = [
            {
                "case_id": "case-reject",
                "quality": {"rule_analysis_eligible": True},
                "candidate": {"reason_key": "filler_hesitation", "proposed_text": "对", "clause_position": "clause-mid"},
                "label": {"decision": "reject"},
            },
            {
                "case_id": "case-accept",
                "quality": {"rule_analysis_eligible": True},
                "candidate": {"reason_key": "filler_hesitation", "proposed_text": "呃", "clause_position": "clause-tail"},
                "label": {"decision": "accept"},
            },
        ]
        candidates, report = apply.annotate_candidates(
            [
                {"candidate_id": "C1", "reason_key": "filler_hesitation", "proposed_text": "对", "clause_position": "clause-mid"},
                {"candidate_id": "C2", "reason_key": "filler_hesitation", "proposed_text": "呃", "clause_position": "clause-tail"},
            ], {}, records, "snapshot-sha"
        )
        self.assertEqual(candidates[0]["experience_signal"]["signal"], "historical_reject")
        self.assertEqual(candidates[1]["experience_signal"]["signal"], "historical_accept")
        self.assertEqual(report["policy"], "review_priority_only; no decision, no auto-cut, no filtering")
        self.assertNotIn("decision", candidates[0]["experience_signal"])

    def test_snapshot_manifest_hash_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aggregate = root / "aggregated.json"
            aggregate.write_text(json.dumps({"records": []}), encoding="utf-8")
            digest = hashlib.sha256(aggregate.read_bytes()).hexdigest()
            manifest = root / "snapshot_manifest.json"
            manifest.write_text(json.dumps({"schema_version": "preference-snapshot-manifest-v1", "artifacts": {"aggregated.json": digest}}), encoding="utf-8")
            loaded, records = apply.load_records(root)
            self.assertEqual(loaded["schema_version"], "preference-snapshot-manifest-v1")
            self.assertEqual(records, [])
            aggregate.write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                apply.load_records(root)


if __name__ == "__main__":
    unittest.main()
