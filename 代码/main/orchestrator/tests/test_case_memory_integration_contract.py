"""Source-level contracts for the read-only similar-case memory path."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


PROJECT = Path("/Users/renting/Desktop/minglue/剪辑项目")
DELIVERY = PROJECT / "main/orchestrator/delivery_orchestrator.py"
FRONTEND = PROJECT / "审核前端/challenger-review-product-v1/mvp.html"
TOOLS = PROJECT / "main/tools/tools.json"


class CaseMemoryIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.delivery = DELIVERY.read_text(encoding="utf-8")
        cls.frontend = FRONTEND.read_text(encoding="utf-8")
        cls.tools = json.loads(TOOLS.read_text(encoding="utf-8"))

    def test_registered_tool_is_wired_into_new_run_and_refresh_paths(self) -> None:
        tools = {item["name"]: item for item in self.tools["tools"]}
        self.assertEqual(tools["build_case_memory"]["full_path"], "main/orchestrator/case_memory.py")
        self.assertIn('CASE_MEMORY_SCRIPT = _script_for("build_case_memory")', self.delivery)
        self.assertIn('"case_memory_pre_review"', self.delivery)
        self.assertIn('"case_memory_review_bundle"', self.delivery)
        self.assertIn('build_case_memory_metadata(run_dir, python=python)', self.delivery)
        self.assertIn('"current_case_memory_sha256"', self.delivery)

    def test_memory_only_influences_review_priority_and_never_creates_a_decision(self) -> None:
        self.assertIn('case_memory = candidate.get("case_memory_signal") or {}', self.delivery)
        self.assertIn('int(case_memory.get("review_priority", 0))', self.delivery)
        self.assertIn('never creates a current decision, EDL action or autocut permission', self.delivery)

    def test_frontend_loads_manifest_bound_sidecar_without_setting_a_decision(self) -> None:
        self.assertIn('fetch("case_memory.json")', self.frontend)
        self.assertIn('function caseMemoryPanel(c)', self.frontend)
        self.assertIn('state.caseMemory=null', self.frontend)
        self.assertIn('case_memory_rejected', self.frontend)
        self.assertIn('decision:d.decision||"pending"', self.frontend)


if __name__ == "__main__":
    unittest.main()
