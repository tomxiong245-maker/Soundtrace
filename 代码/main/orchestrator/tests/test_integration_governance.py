from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys


ORCHESTRATOR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import integration_governance as governance  # noqa: E402


REGISTRY = Path("<PROJECT_ROOT>/main/knowledge/integration_governance/owner_attested_mainline.v1.json")


class IntegrationGovernanceTests(unittest.TestCase):
    def test_registry_is_valid_and_keeps_semantic_gate_separate(self) -> None:
        path, registry = governance.load_registry(REGISTRY)
        self.assertEqual(path, REGISTRY.resolve())
        self.assertIn("self_correction_wordlevel", governance.mainline_capabilities(registry))
        self.assertIn("transient_cough_detection", governance.mainline_capabilities(registry))
        self.assertIn("supervised_learning_model", {item["capability_id"] for item in registry["mainline_exclusions"]})
        self.assertTrue(registry["policy"]["semantic_edit_gate"].startswith("human_accept"))

    def test_owner_attested_status_cannot_claim_independent_pass(self) -> None:
        _, registry = governance.load_registry(REGISTRY)
        registry["mainline"][0]["independent_verification"] = "PASS"
        errors = governance.validate_registry(registry)
        self.assertTrue(any("independent PASS" in error for error in errors))

    def test_freeze_is_hash_bound_and_non_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "frozen" / "integration_registry.json"
            result = governance.freeze_registry(REGISTRY, output)
            self.assertEqual(result["source_sha256"], result["frozen_sha256"])
            self.assertIn("mainline_automix" if False else "main_mic_automix", result["mainline_capabilities"])
            with self.assertRaises(FileExistsError):
                governance.freeze_registry(REGISTRY, output)


if __name__ == "__main__":
    unittest.main()
