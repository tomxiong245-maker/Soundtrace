"""Contract tests for explainable, read-only similar-case memory."""

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

import case_memory as memory  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record(
    *,
    case_id: str,
    episode_id: str,
    decision: str,
    text: str = "呃",
    feedback: str = "",
    complete_identity: bool = True,
    run_id: str | None = None,
) -> dict:
    provenance = {}
    if complete_identity:
        provenance = {
            "source_bundle_sha256": f"bundle-{episode_id}",
            "source_audio_sha256": f"audio-{episode_id}",
            "run_identity_sha256": f"identity-{episode_id}",
            "decision_file_relpath": f"main/runs/{episode_id}/human_decisions.json",
            "decision_file_sha256": f"decision-{episode_id}",
            "package_file_relpath": f"main/runs/{episode_id}/review_bundle/review_package.json",
            "package_file_sha256": f"package-{episode_id}",
        }
    return {
        "case_id": case_id,
        "episode_id": episode_id,
        "run_id": run_id or f"{episode_id}-review",
        "candidate_id": case_id.rsplit("::", 1)[-1],
        "candidate": {
            "reason_key": "filler_hesitation",
            "proposed_text": text,
            "filler_subtype": "strong_hesitation_sound",
            "clause_position": "clause-tail",
            "duration_seconds": 0.42,
            "source_track_id": "track_01",
        },
        "label": {
            "decision": decision,
            "reviewer": "Fixture Reviewer",
            "decided_at": "2026-08-17T00:00:00Z",
            "feedback": feedback,
        },
        "feedback_classification": {
            "primary_class": "semantic_cut" if decision == "accept" else "semantic_keep",
        },
        "provenance": provenance,
    }


class CaseMemoryTests(unittest.TestCase):
    def snapshot(self, root: Path, records: list[dict]) -> Path:
        root.mkdir(parents=True)
        aggregate = root / "aggregated.json"
        write_json(aggregate, {"records": records})
        write_json(root / "snapshot_manifest.json", {
            "schema_version": "preference-snapshot-manifest-v1",
            "snapshot_id": "fixture-memory-snapshot",
            "artifacts": {"aggregated.json": hashlib.sha256(aggregate.read_bytes()).hexdigest()},
        })
        return root

    def target(self, root: Path, *, add_draft: bool = False) -> tuple[Path, Path, Path]:
        run = root / "EP04-target"
        source = {
            "episode_id": "EP04",
            "run_id": "EP04-target",
            "candidates": [{
                "candidate_id": "C001",
                "reason_key": "filler_hesitation",
                "source_track_id": "track_01",
                "proposed_delete_text": "呃",
                "filler_subtype": "strong_hesitation_sound",
                "clause_position": "clause-tail",
                "duration_seconds": 0.40,
            }],
        }
        package = {
            "episode_id": "EP04",
            "run_id": "EP04-target",
            "review_manifest_sha256": "fixture-review-manifest",
            "candidates": [{
                **source["candidates"][0],
                "semantic_sha256": "semantic-C001",
                "start_sample": 48000,
                "end_sample": 67200,
            }],
        }
        write_json(run / "run_identity.json", {"episode_id": "EP04", "run_id": "EP04-target"})
        write_json(run / "input_manifest.json", {
            "episode_id": "EP04",
            "run_id": "EP04-target",
            "tracks": [{"track_id": "track_01", "audio_sha256": "target-audio"}],
        })
        source_path = run / "candidates/candidate_source.json"
        write_json(source_path, source)
        package_path = run / "review_bundle/review_package.json"
        write_json(package_path, package)
        if add_draft:
            write_json(run / "review_draft.json", {"decisions": []})
        return run, source_path, package_path

    def test_retrieves_explainable_cases_without_creating_current_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = self.snapshot(root / "snapshot", [
                record(case_id="EP03::C005", episode_id="EP03", decision="accept", feedback="删除后很自然。"),
                record(case_id="EP02::C003", episode_id="EP02", decision="reject", feedback="这次是完整句的一部分。", complete_identity=False),
                # Same-episode evidence must be handled only by explicit event routes,
                # never by generic cross-episode case memory.
                record(case_id="EP04::C099", episode_id="EP04", decision="accept", run_id="EP04-old"),
            ])
            run, source, package = self.target(root)
            source_before = source.read_bytes()
            package_before = package.read_bytes()

            document = memory.build_case_memory(
                snapshot_dir=snapshot,
                candidate_source=source,
                target_run_dir=run,
                review_package=package,
            )

            row = document["candidate_memory"]["C001"]
            self.assertEqual(row["similar_case_count"], 2)
            self.assertEqual(row["historical_decision_counts"], {"accept": 1, "reject": 1})
            self.assertEqual(row["signal"], "mixed_historical_memory")
            self.assertIsNone(row["current_decision"])
            self.assertFalse(row["creates_edl_action"])
            self.assertFalse(row["creates_autocut_permission"])
            self.assertEqual(document["snapshot"]["total_snapshot_records"], 3)
            self.assertEqual(document["snapshot"]["exclusions"]["same_episode"], 1)
            matches = row["matches"]
            self.assertEqual({match["case_id"] for match in matches}, {"EP03::C005", "EP02::C003"})
            self.assertTrue(any("拟删文本均为" in reason for reason in matches[0]["matching_reasons"]))
            self.assertIn("LEGACY_IDENTITY_INCOMPLETE", {match["identity_status"] for match in matches})
            self.assertEqual(memory.validate_case_memory(document, json.loads(package.read_text(encoding="utf-8"))), [])
            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual(package.read_bytes(), package_before)

    def test_reason_family_alone_never_counts_as_a_similar_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unrelated = record(
                case_id="EP03::C005",
                episode_id="EP03",
                decision="accept",
                text="这个很长的不同内容",
            )
            unrelated["candidate"].update({
                "filler_subtype": "repeated_weak_filler",
                "clause_position": "clause-mid",
                "duration_seconds": 2.4,
            })
            snapshot = self.snapshot(root / "snapshot", [
                unrelated,
            ])
            run, source, _package = self.target(root)
            document = memory.build_case_memory(
                snapshot_dir=snapshot,
                candidate_source=source,
                target_run_dir=run,
            )
            row = document["candidate_memory"]["C001"]
            self.assertEqual(row["signal"], "no_similar_case")
            self.assertEqual(row["similar_case_count"], 0)

    def test_review_bundle_write_refuses_active_draft_and_manifest_mismatch_is_detectable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = self.snapshot(root / "snapshot", [
                record(case_id="EP03::C005", episode_id="EP03", decision="accept"),
            ])
            run, source, package_path = self.target(root, add_draft=True)
            with self.assertRaisesRegex(ValueError, "active reviewer draft"):
                memory.write_case_memory(
                    snapshot_dir=snapshot,
                    candidate_source=source,
                    target_run_dir=run,
                    review_package=package_path,
                    out_path=run / "review_bundle/case_memory.json",
                )
            (run / "review_draft.json").unlink()
            output = memory.write_case_memory(
                snapshot_dir=snapshot,
                candidate_source=source,
                target_run_dir=run,
                review_package=package_path,
                out_path=run / "review_bundle/case_memory.json",
            )
            document = json.loads(output.read_text(encoding="utf-8"))
            package = json.loads(package_path.read_text(encoding="utf-8"))
            package["review_manifest_sha256"] = "different-manifest"
            self.assertIn("case memory is bound to a different review manifest", memory.validate_case_memory(document, package))


if __name__ == "__main__":
    unittest.main()
