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

import refresh_label_learning_snapshot as refresh  # noqa: E402


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class RefreshLabelLearningSnapshotTests(unittest.TestCase):
    def make_review_run(
        self,
        root: Path,
        *,
        episode_id: str,
        run_id: str,
        decision_filename: str = "human_decisions.json",
    ) -> Path:
        run = root / "main" / "runs" / episode_id / run_id
        candidate = {
            "candidate_id": "C001",
            "semantic_sha256": "candidate-sha",
            "reason_key": "filler_hesitation",
            "source_track_id": "track_01",
            "start_sample": 4800,
            "end_sample": 9600,
            "start_seconds": 0.1,
            "end_seconds": 0.2,
            "sample_rate_hz": 48000,
            "proposed_delete_text": "额",
        }
        package = {
            "package_id": f"pkg-{episode_id}",
            "review_manifest_sha256": f"manifest-{episode_id}",
            "episode_id": episode_id,
            "sample_rate_hz": 48000,
            "tracks": [{"track_id": "track_01", "audio_sha256": f"audio-{episode_id}"}],
            "candidates": [candidate],
        }
        decisions = {
            "schema_version": "human-decisions-mvp-v1",
            "package_id": package["package_id"],
            "review_manifest_sha256": package["review_manifest_sha256"],
            "reviewer": "熊镇正",
            "decisions": [{
                "candidate_id": "C001",
                "candidate_semantic_sha256": "candidate-sha",
                "decision": "accept",
                "reviewer": "熊镇正",
                "decided_at": "2026-08-17T00:00:00Z",
                "feedback": "可剪。",
            }],
        }
        write(run / "run_identity.json", {"run_id": run_id, "episode_id": episode_id})
        write(
            run / "input_manifest.json",
            {
                "episode_id": episode_id,
                "sample_rate_hz": 48000,
                "tracks": [{"track_id": "track_01", "audio_sha256": f"audio-{episode_id}"}],
            },
        )
        write(run / "review_bundle" / "review_package.json", package)
        write(run / decision_filename, decisions)
        return run

    def test_refresh_writes_immutable_run_and_atomically_activates_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_one = self.make_review_run(root, episode_id="EP01", run_id="EP01-review")
            self.make_review_run(root, episode_id="EP02", run_id="EP02-review")
            package_before = (run_one / "review_bundle/review_package.json").read_bytes()
            result = refresh.refresh_after_human_submit(
                project_root=root,
                review_run=run_one,
                run_id="LABEL-LEARNING-AUTO-TEST-0001",
            )
            self.assertEqual(result["status"], "ACTIVE")
            pointer = root / refresh.ACTIVE_POINTER_RELPATH
            self.assertTrue(pointer.is_file())
            active = refresh.resolve_active_snapshot(root)
            self.assertEqual(active, Path(result["snapshot_manifest"]).resolve())
            self.assertEqual((run_one / "review_bundle/review_package.json").read_bytes(), package_before)
            evidence = Path(result["refresh_run"])
            self.assertTrue((evidence / "backtest_report.json").is_file())
            self.assertTrue((evidence / "refresh_manifest.json").is_file())
            pointer_doc = json.loads(pointer.read_text(encoding="utf-8"))
            self.assertEqual(
                pointer_doc["active_snapshot_manifest_sha256"],
                hashlib.sha256(active.read_bytes()).hexdigest(),
            )

    def test_broken_active_pointer_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pointer = root / refresh.ACTIVE_POINTER_RELPATH
            write(
                pointer,
                {
                    "schema_version": "active-label-learning-snapshot-pointer-v1",
                    "active_snapshot_manifest_relpath": "main/runs/missing/snapshot_manifest.json",
                    "active_snapshot_manifest_sha256": "0" * 64,
                },
            )
            with self.assertRaisesRegex(refresh.SnapshotRefreshError, "missing"):
                refresh.resolve_active_snapshot(root)

    def test_live_label_save_is_idempotent_but_feedback_change_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self.make_review_run(
                root,
                episode_id="EP01",
                run_id="EP01-review",
                decision_filename=refresh.LIVE_HUMAN_DECISIONS_FILENAME,
            )
            # A second episode gives the backtest enough shape to run without
            # weakening the test to a no-op single-episode snapshot.
            self.make_review_run(root, episode_id="EP02", run_id="EP02-review")
            source = run / refresh.LIVE_HUMAN_DECISIONS_FILENAME

            first = refresh.refresh_after_human_label_save(
                project_root=root,
                review_run=run,
                run_id="LABEL-LEARNING-AUTO-LIVE-0001",
            )
            self.assertEqual(first["status"], "ACTIVE")
            source_before = source.read_bytes()
            pointer_before = (root / refresh.ACTIVE_POINTER_RELPATH).read_bytes()

            unchanged = refresh.refresh_after_human_label_save(
                project_root=root,
                review_run=run,
                run_id="LABEL-LEARNING-AUTO-LIVE-0002",
            )
            self.assertEqual(unchanged["status"], "UNCHANGED")
            self.assertFalse((root / "main/runs/LABEL-LEARNING-AUTO-LIVE-0002").exists())
            self.assertEqual(source.read_bytes(), source_before)
            self.assertEqual((root / refresh.ACTIVE_POINTER_RELPATH).read_bytes(), pointer_before)

            changed = json.loads(source.read_text(encoding="utf-8"))
            changed["decisions"][0]["feedback"] = "完整句末的犹豫音，可剪。"
            write(source, changed)
            refreshed = refresh.refresh_after_human_label_save(
                project_root=root,
                review_run=run,
                run_id="LABEL-LEARNING-AUTO-LIVE-0003",
            )
            self.assertEqual(refreshed["status"], "ACTIVE")
            self.assertTrue((root / "main/runs/LABEL-LEARNING-AUTO-LIVE-0001").is_dir())
            self.assertTrue((root / "main/runs/LABEL-LEARNING-AUTO-LIVE-0003").is_dir())
            pointer = json.loads((root / refresh.ACTIVE_POINTER_RELPATH).read_text(encoding="utf-8"))
            self.assertEqual(pointer["source_review_run_relpath"], "main/runs/EP01/EP01-review")
            self.assertEqual(
                pointer["source_decision_content_sha256"],
                refresh.decision_content_sha256(changed),
            )

    def test_withdrawing_the_last_live_label_removes_it_from_next_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = self.make_review_run(
                root,
                episode_id="EP01",
                run_id="EP01-review",
                decision_filename=refresh.LIVE_HUMAN_DECISIONS_FILENAME,
            )
            self.make_review_run(root, episode_id="EP02", run_id="EP02-review")
            first = refresh.refresh_after_human_label_save(
                project_root=root,
                review_run=run,
                run_id="LABEL-LEARNING-AUTO-WITHDRAW-0001",
            )
            pointer_before = (root / refresh.ACTIVE_POINTER_RELPATH).read_bytes()
            draft = {
                "schema_version": "human-decisions-mvp-v1",
                "package_id": "pkg-EP01",
                "review_manifest_sha256": "manifest-EP01",
                "reviewer": "熊镇正",
                "decisions": [{
                    "candidate_id": "C001",
                    "candidate_semantic_sha256": "candidate-sha",
                    "decision": "pending",
                    "feedback": "改回待定，暂不进入经验。",
                }],
            }
            write(run / "review_draft.json", draft)

            withdrawn = refresh.refresh_after_human_label_withdrawal(
                project_root=root,
                review_run=run,
                run_id="LABEL-LEARNING-AUTO-WITHDRAW-0002",
            )
            self.assertEqual(withdrawn["status"], "WITHDRAWN_AND_ACTIVE")
            self.assertFalse((run / refresh.LIVE_HUMAN_DECISIONS_FILENAME).exists())
            self.assertNotEqual(
                (root / refresh.ACTIVE_POINTER_RELPATH).read_bytes(), pointer_before
            )
            evidence = Path(withdrawn["refresh_run"])
            trigger = json.loads((evidence / "run_identity.json").read_text(encoding="utf-8"))["trigger"]
            self.assertEqual(trigger["kind"], "human_label_withdrawal")
            self.assertTrue((evidence / "source_review_draft.json").is_file())
            self.assertTrue((evidence / "withdrawn_human_labels.before.json").is_file())
            _, _, records = refresh.learning_driver.load_snapshot(
                Path(withdrawn["snapshot_manifest"]).parent
            )
            self.assertFalse(any(record.get("run_id") == "EP01-review" for record in records))

            unchanged = refresh.refresh_after_human_label_withdrawal(
                project_root=root,
                review_run=run,
                run_id="LABEL-LEARNING-AUTO-WITHDRAW-0003",
            )
            self.assertEqual(unchanged["status"], "SKIPPED_NO_EFFECTIVE_HUMAN_LABEL")
            self.assertFalse((root / "main/runs/LABEL-LEARNING-AUTO-WITHDRAW-0003").exists())


if __name__ == "__main__":
    unittest.main()
