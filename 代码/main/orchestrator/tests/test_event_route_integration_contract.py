"""Small source-level contracts for future-run event-route integration."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path("<HOME>/Desktop/minglue/剪辑项目")
ORCHESTRATOR = PROJECT / "main/orchestrator"
DELIVERY = ORCHESTRATOR / "delivery_orchestrator.py"
FRONTEND = PROJECT / "审核前端/challenger-review-product-v1/mvp.html"
SYNC_CHECK = ORCHESTRATOR / "check_current_delivery_sync.py"


def load_delivery():
    spec = importlib.util.spec_from_file_location("delivery_orchestrator_event_route_contract", DELIVERY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_sync_check():
    spec = importlib.util.spec_from_file_location("sync_check_event_route_contract", SYNC_CHECK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EventRouteIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.delivery = load_delivery()
        cls.sync_check = load_sync_check()
        cls.frontend_text = FRONTEND.read_text(encoding="utf-8")

    def test_new_run_cli_accepts_explicit_human_history_sources(self) -> None:
        args = self.delivery.parser().parse_args([
            "start",
            "--input-dir",
            "/tmp/future-episode",
            "--event-history-run",
            "main/runs/EP04/EP04-human-approved-v12-20260813-152847",
        ])
        self.assertEqual(len(args.event_history_run), 1)
        self.assertEqual(args.event_history_run[0].name, "EP04-human-approved-v12-20260813-152847")

    def test_delivery_code_builds_sidecar_without_rewriting_package(self) -> None:
        source = DELIVERY.read_text(encoding="utf-8")
        # After the 2026-08-17 tools.json refactor, EVENT_ROUTE_SCRIPT is resolved
        # via tool_lookup instead of hardcoded PROJECT_ROOT / "..." — check that
        # the tool_lookup binding is in place and points at the right tool_name.
        self.assertIn('EVENT_ROUTE_SCRIPT = _script_for("review_event_routes")', source)
        self.assertIn('"event_route_enrichment"', source)
        self.assertIn('"event_routes": {', source)
        self.assertIn('"metadata_relpath": "review_bundle/event_routes.json"', source)
        self.assertIn('"policy": "metadata_only; historical decisions never become current human decisions"', source)

    def test_frontend_consumes_sidecar_and_does_not_set_a_decision(self) -> None:
        text = self.frontend_text
        self.assertIn('fetch("event_routes.json")', text)
        self.assertIn("eventRoutePanel", text)
        self.assertIn("state.eventRoutes=null", text)
        self.assertIn('decision:d.decision||"pending"', text)

    def test_sync_gate_keeps_in_progress_package_bound_to_its_frozen_ui(self) -> None:
        source = SYNC_CHECK.read_text(encoding="utf-8")
        self.assertIn('frozen_frontend = run_dir / "review_bundle" / "index.html"', source)
        self.assertIn("review package UI SHA does not match its frozen frontend copy", source)
        self.assertIn("review_ui_capability_errors(frozen_frontend_text, label=\"frozen review frontend\")", source)

    def test_sync_helpers_protect_the_frozen_ui_and_empty_reviewer_draft(self) -> None:
        self.assertEqual(
            self.sync_check.review_ui_capability_errors(self.frontend_text, label="frozen review frontend"),
            [],
        )
        self.assertIn(
            "frozen review frontend is missing required review capability: data-feedback",
            self.sync_check.review_ui_capability_errors("", label="frozen review frontend"),
        )
        package = {
            "package_id": "package-1",
            "review_manifest_sha256": "manifest-1",
            "candidates": [{"candidate_id": "C1", "semantic_sha256": "semantic-1"}],
        }
        pending_draft = {
            "package_id": "package-1",
            "review_manifest_sha256": "manifest-1",
            "reviewer": "",
            "decisions": [{
                "candidate_id": "C1",
                "candidate_semantic_sha256": "semantic-1",
                "decision": "pending",
                "feedback": "",
            }],
        }
        self.assertEqual(self.sync_check.review_draft_errors(pending_draft, package), [])
        stale_draft = dict(pending_draft, review_manifest_sha256="old-manifest")
        self.assertIn(
            "review draft review_manifest_sha256 disagrees with frozen review package",
            self.sync_check.review_draft_errors(stale_draft, package),
        )

    def test_delivery_build_path_writes_only_route_sidecar_for_a_future_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history = root / "main/runs/EPTEST/EPTEST-history"
            current = root / "main/runs/EPTEST/EPTEST-future"

            def write(path: Path, value: object) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

            def package(run: Path, run_id: str, candidate_id: str) -> None:
                write(run / "run_identity.json", {"episode_id": "EPTEST", "run_id": run_id})
                write(run / "input_manifest.json", {
                    "episode_id": "EPTEST", "run_id": run_id, "sample_rate_hz": 48000,
                    "tracks": [{"track_id": "track_01", "audio_sha256": "audio-sha"}],
                })
                write(run / "review_bundle/review_package.json", {
                    "episode_id": "EPTEST", "run_id": run_id, "review_manifest_sha256": f"manifest-{run_id}",
                    "candidates": [{"candidate_id": candidate_id, "source_track_id": "track_01", "proposed_delete_text": "呃", "start_seconds": 1.0, "end_seconds": 1.3}],
                })

            package(history, "EPTEST-history", "OLD")
            write(history / "human_decisions.json", {"decisions": [{"candidate_id": "OLD", "decision": "human_accept", "feedback": "很好"}]})
            package(current, "EPTEST-future", "NEW")

            delivery = load_delivery()
            original_root = delivery.PROJECT_ROOT
            try:
                delivery.PROJECT_ROOT = root
                history_relpath = "main/runs/EPTEST/EPTEST-history"
                write(current / "plan.json", {
                    "event_routing": {
                        "schema_version": "review-event-routes-v1",
                        "history_runs": [{
                            "run_relpath": history_relpath,
                            "input_manifest_sha256": delivery.sha256_file(history / "input_manifest.json"),
                            "review_package_sha256": delivery.sha256_file(history / "review_bundle/review_package.json"),
                            "human_decisions_sha256": delivery.sha256_file(history / "human_decisions.json"),
                        }],
                    },
                })
                document = delivery.build_event_route_metadata(current, python=sys.executable)
            finally:
                delivery.PROJECT_ROOT = original_root

            self.assertEqual(document["route_summary"], {"already_reviewed_exact": 1})
            sidecar = current / "review_bundle/event_routes.json"
            self.assertTrue(sidecar.is_file())
            self.assertEqual(document["routes"]["NEW"]["current_decision"], None)
            self.assertFalse((current / "human_decisions.json").exists())
            self.assertFalse((current / "human_approved.edl.json").exists())


if __name__ == "__main__":
    unittest.main()
