from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ORCHESTRATOR = Path(__file__).resolve().parents[1]
if str(ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR))

import label_learning_driver as driver  # noqa: E402


def _record(
    *,
    case_id: str,
    episode_id: str,
    decision: str,
    text: str = "呃",
    feedback_class: str = "unknown",
    reviewer: str = "审核人A",
    source_bundle_sha256: str | None = None,
    source_audio_sha256: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Build one independently identifiable human-review record."""

    classes = [] if feedback_class == "unknown" else [feedback_class]
    bundle = source_bundle_sha256 or f"bundle-{episode_id}"
    audio = source_audio_sha256 or f"audio-{episode_id}"
    return {
        "case_id": case_id,
        "episode_id": episode_id,
        "run_id": run_id or f"{episode_id}-run",
        "quality": {"rule_analysis_eligible": True, "generalization_eligible": True},
        "candidate": {
            "reason_key": "filler_hesitation",
            "proposed_text": text,
            "filler_subtype": "strong_hesitation_sound",
            "clause_position": "clause-tail",
            "duration_seconds": 0.4,
            "source_track_id": "track_01",
        },
        "provenance": {
            "source_bundle_sha256": bundle,
            "source_audio_sha256": audio,
            "run_identity_sha256": f"identity-{episode_id}",
        },
        "label": {"decision": decision, "reviewer": reviewer},
        "feedback_classification": {
            "primary_class": feedback_class,
            "classes": classes,
        },
    }


def _legacy_record(**kwargs: object) -> dict:
    record = _record(**kwargs)  # type: ignore[arg-type]
    record["provenance"] = {}
    return record


class LabelLearningDriverTests(unittest.TestCase):
    def _snapshot(self, root: Path, records: list[dict]) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        aggregate = root / "aggregated.json"
        aggregate.write_text(json.dumps({"records": records}, ensure_ascii=False), encoding="utf-8")
        manifest = root / "snapshot_manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": "preference-snapshot-manifest-v1",
            "snapshot_id": "fixture-snapshot",
            "artifacts": {"aggregated.json": hashlib.sha256(aggregate.read_bytes()).hexdigest()},
        }), encoding="utf-8")
        return root

    def _target(
        self,
        root: Path,
        *,
        episode_id: str = "EP04",
        run_id: str = "EP04-target",
        candidates: list[dict] | None = None,
    ) -> tuple[Path, Path, Path]:
        target = root / run_id
        (target / "candidates").mkdir(parents=True)
        (target / "review_bundle").mkdir()
        identity = {
            "schema_version": "run-identity-v1",
            "episode_id": episode_id,
            "run_id": run_id,
        }
        manifest = {
            "tracks": [{"track_id": "track_01", "audio_sha256": f"target-audio-{episode_id}"}],
        }
        source = {
            "episode_id": episode_id,
            "run_id": run_id,
            "candidates": candidates if candidates is not None else [{
                "candidate_id": "C1",
                "reason_key": "filler_hesitation",
                "source_track": "track_01",
                "proposed_delete_text": "呃",
                "filler_subtype": "strong_hesitation_sound",
                "clause_position": "clause-tail",
                "duration_seconds": 0.4,
            }],
        }
        (target / "run_identity.json").write_text(json.dumps(identity), encoding="utf-8")
        (target / "input_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        source_path = target / "candidates/candidate_source.json"
        source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        (target / "all_candidates.json").write_text(json.dumps({"candidates": source["candidates"]}), encoding="utf-8")
        (target / "state.json").write_text(json.dumps({"state": "CALIBRATION_REVIEW_REQUIRED"}), encoding="utf-8")
        (target / "review_bundle/review_package.json").write_text(json.dumps({"package_id": "fixture"}), encoding="utf-8")
        (target / "review_bundle/index.html").write_text("<!doctype html>", encoding="utf-8")
        (target / "review_draft.json").write_text(json.dumps({"decisions": []}), encoding="utf-8")
        return target, source_path, target / "input_manifest.json"

    def _predict(self, snapshot: Path, target: Path, source: Path, manifest: Path) -> dict:
        return driver.predict_document(
            snapshot_dir=snapshot,
            candidate_source=source,
            input_manifest=manifest,
            target_run_dir=target,
        )

    def test_single_false_positive_requires_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP01::C1", episode_id="EP01", decision="reject", text="额", feedback_class="false_positive"),
            ])
            target, source, manifest = self._target(root, candidates=[{
                "candidate_id": "C1", "reason_key": "filler_hesitation", "source_track": "track_01",
                "proposed_delete_text": "额", "filler_subtype": "strong_hesitation_sound",
                "clause_position": "clause-tail", "duration_seconds": 0.4,
            }])
            prediction = self._predict(snapshot, target, source, manifest)["predictions"][0]
            self.assertEqual(prediction["machine_label"], driver.HUMAN_REVIEW)
            self.assertIn("event_groups=1", prediction["reason"])
            self.assertFalse(prediction["creates_human_decision"])
            self.assertFalse(prediction["creates_edl_action"])

    def test_three_independent_false_positives_can_suggest_preserve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP01::C1", episode_id="EP01", decision="reject", text="额", feedback_class="false_positive", reviewer="审核人A"),
                _record(case_id="EP02::C1", episode_id="EP02", decision="reject", text="额", feedback_class="false_positive", reviewer="审核人B"),
                _record(case_id="EP03::C1", episode_id="EP03", decision="reject", text="额", feedback_class="false_positive", reviewer="审核人A"),
            ])
            target, source, manifest = self._target(root, candidates=[{
                "candidate_id": "C1", "reason_key": "filler_hesitation", "source_track": "track_01",
                "proposed_delete_text": "额", "filler_subtype": "strong_hesitation_sound",
                "clause_position": "clause-tail", "duration_seconds": 0.4,
            }])
            prediction = self._predict(snapshot, target, source, manifest)["predictions"][0]
            self.assertEqual(prediction["machine_label"], driver.MACHINE_PRESERVE)
            self.assertTrue(prediction["requires_human_review"])
            self.assertEqual(prediction["independent_source_bundle_count"], 3)
            self.assertEqual(prediction["independent_reviewer_count"], 2)

    def test_execution_only_reject_never_becomes_semantic_preserve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP01::C1", episode_id="EP01", decision="reject", feedback_class="execution_issue"),
            ])
            target, source, manifest = self._target(root)
            prediction = self._predict(snapshot, target, source, manifest)["predictions"][0]
            self.assertEqual(prediction["machine_label"], driver.HUMAN_REVIEW)
            self.assertTrue(prediction["execution_warning"])

    def test_episode_backtest_excludes_same_audio_bundle_and_incomplete_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP01::C1", episode_id="EP01", decision="accept"),
                _record(case_id="EP02::C1", episode_id="EP02", decision="accept"),
                _legacy_record(case_id="EP03::C1", episode_id="EP03", decision="accept"),
            ])
            report = driver.backtest_document(snapshot_dir=snapshot)
            self.assertEqual(report["method"]["split"], "leave_one_episode_out")
            self.assertEqual(report["summary"]["harmful_suggestion_count"], 0)
            self.assertEqual(report["data_quality"]["legacy_identity_incomplete_record_count"], 1)
            for fold in report["folds"]:
                self.assertEqual(fold["case_id_overlap"], [])
                self.assertNotIn(fold["held_out_episode_id"], fold["training_episode_ids"])

    def test_backtest_never_calls_two_episodes_one_reviewer_generalization(self) -> None:
        """After 2026-08-17 the 3-episode / 2-reviewer generalization gate is removed
        by explicit user directive. This test now verifies the removal: with 2 episodes
        and 1 reviewer, backtest no longer emits `episodes=... < 3` or
        `independent_reviewers=... < 2` blockers. Status may still be
        INSUFFICIENT_DATA_FOR_CROSS_EPISODE_GENERALIZATION when source_audio /
        source_bundle identity coverage is incomplete (a separate anti-leakage gate that
        was NOT removed). raw_precision must still be None because reporting numeric
        precision without cross-episode data would be a false quality claim."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP01::C1", episode_id="EP01", decision="accept", reviewer="审核人A"),
                _record(case_id="EP02::C1", episode_id="EP02", decision="accept", reviewer="审核人A"),
            ])
            report = driver.backtest_document(snapshot_dir=snapshot)
            blockers = report["data_quality"]["blockers"]
            self.assertFalse(
                any("episodes=" in b and "< 3" in b for b in blockers),
                f"episode gate must be removed; got blockers={blockers!r}",
            )
            self.assertFalse(
                any("independent_reviewers=" in b for b in blockers),
                f"reviewer gate must be removed; got blockers={blockers!r}",
            )
            # raw_precision stays None when the machine did not emit any suggestion.
            self.assertIsNone(report["summary"]["suggestion_precision"])

    def test_legacy_runs_episode_id_is_grouped_by_run_prefix(self) -> None:
        self.assertEqual(driver._logical_episode_id({"episode_id": "runs", "run_id": "EP03-review-product-v1"}), "EP03")

    def test_target_identity_is_bound_to_shadow_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [])
            target, source, manifest = self._target(root, episode_id="EP02", run_id="EP02-shadow-target", candidates=[])
            document = self._predict(snapshot, target, source, manifest)
            expected_identity_sha = hashlib.sha256((target / "run_identity.json").read_bytes()).hexdigest()
            self.assertEqual(document["candidate_input"]["run_id"], "EP02-shadow-target")
            self.assertEqual(document["target_identity"]["run_identity_sha256"], expected_identity_sha)
            self.assertIn("EP02", document["snapshot"]["excluded_episode_ids"])

    def test_input_manifest_enriches_candidate_without_rewriting_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [])
            target, source, manifest = self._target(root)
            before = source.read_bytes()
            prediction = self._predict(snapshot, target, source, manifest)["predictions"][0]
            self.assertEqual(prediction["feature_view"]["source_audio_sha256"], "target-audio-EP04")
            self.assertNotIn("source_audio_sha256", prediction["missing_features"])
            self.assertEqual(source.read_bytes(), before)

    def test_target_run_present_in_snapshot_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, source, manifest = self._target(root)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP04-target::C1", episode_id="EP04", decision="accept", run_id="EP04-target"),
            ])
            with self.assertRaisesRegex(ValueError, "target run labels are present"):
                self._predict(snapshot, target, source, manifest)

    def test_same_episode_record_is_excluded_from_generic_pattern_votes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP04-old::C1", episode_id="EP04", decision="reject", text="额", feedback_class="false_positive", run_id="EP04-old"),
                _record(case_id="EP01::C1", episode_id="EP01", decision="reject", text="额", feedback_class="false_positive", reviewer="审核人A"),
                _record(case_id="EP02::C1", episode_id="EP02", decision="reject", text="额", feedback_class="false_positive", reviewer="审核人B"),
                _record(case_id="EP03::C1", episode_id="EP03", decision="reject", text="额", feedback_class="false_positive", reviewer="审核人A"),
            ])
            target, source, manifest = self._target(root, candidates=[{
                "candidate_id": "C1", "reason_key": "filler_hesitation", "source_track": "track_01",
                "proposed_delete_text": "额", "filler_subtype": "strong_hesitation_sound",
                "clause_position": "clause-tail", "duration_seconds": 0.4,
            }])
            document = self._predict(snapshot, target, source, manifest)
            prediction = document["predictions"][0]
            self.assertEqual(document["leakage_audit"]["same_episode_records_excluded"], 1)
            self.assertNotIn("EP04-old::C1", {row["case_id"] for row in prediction["matched_cases"]})
            self.assertEqual(prediction["machine_label"], driver.MACHINE_PRESERVE)

    def test_evidence_package_hashes_frozen_target_before_and_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [
                _record(case_id="EP01::C1", episode_id="EP01", decision="accept"),
                _record(case_id="EP02::C1", episode_id="EP02", decision="accept"),
            ])
            target, source, _ = self._target(root)
            source_before = source.read_bytes()
            evidence = driver.evidence_document(
                snapshot_dir=snapshot,
                target_run_dir=target,
                out_dir=root / "evidence",
            )
            self.assertEqual(evidence["target_integrity"]["comparison"], "PASS")
            self.assertEqual(evidence["driver"]["source_sha256"], driver.driver_source_sha256())
            self.assertEqual(source.read_bytes(), source_before)
            self.assertTrue((root / "evidence/evidence_manifest.json").is_file())
            shadow = json.loads((root / "evidence/shadow_prediction_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(shadow["target_identity"]["run_id"], "EP04-target")

    def test_snapshot_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = self._snapshot(root / "snapshot", [])
            (snapshot / "aggregated.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                driver.load_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
