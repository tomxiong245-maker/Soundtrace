#!/usr/bin/env python3
"""Tests for cross-episode isolation in the generic P1 entrypoint."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from episode_config import load_episode_config, package_identity_errors  # noqa: E402
from build_mvp_package import review_scope_from_source  # noqa: E402
import server_episode  # noqa: E402
from server_episode import build_or_reuse, validate_draft  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EpisodeConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "main/runs").mkdir(parents=True)
        self.source = self.root / "source.json"
        self.source.write_text(
            json.dumps({"episode_id": "EP04", "candidates": []}), encoding="utf-8"
        )
        self.manifest = self.root / "tracks.json"
        self.audio = self.root / "track_01.wav"
        with wave.open(str(self.audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(b"\0\0" * 4800)
        self.manifest.write_text(
            json.dumps(
                {
                    "episode_id": "EP04",
                    "tracks": [
                        {
                            "track_id": "track_01",
                            "source_key": "track_01",
                            "audio_path": str(self.audio),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.frontend = self.root / "mvp.html"
        self.frontend.write_text("<!doctype html>", encoding="utf-8")
        self.ffmpeg = self.root / "ffmpeg"
        self.ffmpeg.write_text("binary placeholder", encoding="utf-8")
        self.config_path = self.root / "ep04.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_config(self, **overrides: object) -> None:
        value = {
            "schema_version": "review-episode-config-v1",
            "episode_id": "EP04",
            "source_package": str(self.source),
            "tracks_manifest": str(self.manifest),
            "frontend": str(self.frontend),
            "ffmpeg": str(self.ffmpeg),
            "run_dir": str(self.root / "main/runs/EP04-review-product-v1"),
            "port": 8768,
        }
        value.update(overrides)
        self.config_path.write_text(json.dumps(value), encoding="utf-8")

    def test_valid_ep04_config(self) -> None:
        self.write_config()
        config = load_episode_config(self.config_path, project_root=self.root)
        self.assertEqual(config.episode_id, "EP04")
        self.assertEqual(config.run_dir.name, "EP04-review-product-v1")

    def test_rejects_manifest_from_another_episode(self) -> None:
        self.manifest.write_text(
            json.dumps({"episode_id": "EP03", "tracks": [{"track_id": "track_01"}]}),
            encoding="utf-8",
        )
        self.write_config()
        with self.assertRaisesRegex(ValueError, "tracks_manifest episode_id"):
            load_episode_config(self.config_path, project_root=self.root)

    def test_rejects_output_outside_main_runs(self) -> None:
        self.write_config(run_dir=str(self.root / "elsewhere/EP04-review-product-v1"))
        with self.assertRaisesRegex(ValueError, "run_dir must be inside"):
            load_episode_config(self.config_path, project_root=self.root)

    def test_rejects_run_name_for_another_episode(self) -> None:
        self.write_config(run_dir=str(self.root / "main/runs/EP03-review-product-v1"))
        with self.assertRaisesRegex(ValueError, "basename"):
            load_episode_config(self.config_path, project_root=self.root)

    def test_existing_package_identity_is_exact(self) -> None:
        self.write_config()
        config = load_episode_config(self.config_path, project_root=self.root)
        package = {
            "episode_id": "EP04",
            "source_package_path": str(config.source_package),
            "source_package_sha256": sha(config.source_package),
            "tracks_manifest_path": str(config.tracks_manifest),
            "tracks_manifest_sha256": sha(config.tracks_manifest),
            "ui_sha256": sha(config.frontend),
        }
        self.assertEqual(package_identity_errors(config, package), [])
        package["episode_id"] = "EP03"
        self.assertIn(
            "existing package episode_id does not match config",
            package_identity_errors(config, package),
        )

    def test_build_once_then_reuse_without_overwrite(self) -> None:
        self.write_config()
        config = load_episode_config(self.config_path, project_root=self.root)
        self.assertEqual(build_or_reuse(config), "BUILT")
        first_bytes = config.review_package.read_bytes()
        self.assertEqual(build_or_reuse(config), "REUSED")
        self.assertEqual(config.review_package.read_bytes(), first_bytes)

    def test_draft_allows_pending_rows_without_creating_human_decision(self) -> None:
        package = {
            "package_id": "pkg-1",
            "review_manifest_sha256": "manifest-1",
            "candidates": [{"candidate_id": "C001", "semantic_sha256": "candidate-1"}],
        }
        draft = {
            "schema_version": "human-decisions-mvp-v1",
            "package_id": "pkg-1",
            "review_manifest_sha256": "manifest-1",
            "reviewer": "",
            "decisions": [{
                "candidate_id": "C001",
                "candidate_semantic_sha256": "candidate-1",
                "decision": "pending",
                "feedback": "先记下：需要回听。",
            }],
        }
        self.assertEqual(validate_draft(package, draft), [])

    def test_draft_rejects_oversized_feedback(self) -> None:
        package = {
            "package_id": "pkg-1",
            "review_manifest_sha256": "manifest-1",
            "candidates": [{"candidate_id": "C001", "semantic_sha256": "candidate-1"}],
        }
        draft = {
            "schema_version": "human-decisions-mvp-v1",
            "package_id": "pkg-1",
            "review_manifest_sha256": "manifest-1",
            "decisions": [{
                "candidate_id": "C001",
                "candidate_semantic_sha256": "candidate-1",
                "decision": "pending",
                "feedback": "x" * 501,
            }],
        }
        self.assertTrue(validate_draft(package, draft))

    def test_save_of_new_human_label_refreshes_snapshot_without_waiting_for_submit(self) -> None:
        """The browser's auto-save is the learning trigger, not final submit."""

        self.write_config()
        config = load_episode_config(self.config_path, project_root=self.root)
        config.bundle_dir.mkdir(parents=True)
        candidate = {
            "candidate_id": "C001",
            "semantic_sha256": "candidate-1",
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
            "package_id": "pkg-1",
            "review_manifest_sha256": "manifest-1",
            "episode_id": "EP04",
            "source_package_path": str(config.source_package),
            "source_package_sha256": sha(config.source_package),
            "tracks_manifest_path": str(config.tracks_manifest),
            "tracks_manifest_sha256": sha(config.tracks_manifest),
            "ui_sha256": sha(config.frontend),
            "sample_rate_hz": 48000,
            "tracks": [{"track_id": "track_01", "audio_sha256": "audio-1"}],
            "candidates": [candidate],
        }
        config.review_package.write_text(json.dumps(package), encoding="utf-8")

        def draft(decision: str, feedback: str = "") -> dict:
            return {
                "schema_version": "human-decisions-mvp-v1",
                "package_id": "pkg-1",
                "review_manifest_sha256": "manifest-1",
                "reviewer": "熊镇正",
                "session_started_at": "2026-08-17T00:00:00Z",
                "session_ended_at": "2026-08-17T00:01:00Z",
                "decisions": [{
                    "candidate_id": "C001",
                    "candidate_semantic_sha256": "candidate-1",
                    "decision": decision,
                    "reviewer": "熊镇正",
                    "decided_at": "2026-08-17T00:00:30Z" if decision != "pending" else None,
                    "review_basis": "text_only",
                    "listened_previews": {},
                    "feedback": feedback,
                }],
            }

        previous_root = server_episode.PROJECT_ROOT
        server_episode.PROJECT_ROOT = self.root
        try:
            pending = server_episode.persist_draft_and_refresh_learning(
                config.run_dir, draft("pending"), {}
            )
            self.assertEqual(pending["status"], "SKIPPED_NO_EFFECTIVE_HUMAN_LABEL")
            self.assertFalse((self.root / "main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json").exists())

            active = server_episode.persist_draft_and_refresh_learning(
                config.run_dir, draft("accept", "完整句末的犹豫音，可剪。"), {}
            )
            self.assertEqual(active["status"], "ACTIVE")
            pointer = self.root / "main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json"
            self.assertTrue(pointer.is_file())
            live_path = config.run_dir / "human_decisions_and_feedback.live.json"
            self.assertTrue(live_path.is_file())
            self.assertFalse((config.run_dir / "human_decisions.json").exists())
            auto_runs = sorted((self.root / "main/runs").glob("LABEL-LEARNING-AUTO-*"))
            self.assertEqual(len(auto_runs), 1)

            unchanged_doc = draft("accept", "完整句末的犹豫音，可剪。")
            unchanged_doc["session_ended_at"] = "2026-08-17T00:02:00Z"
            unchanged = server_episode.persist_draft_and_refresh_learning(
                config.run_dir, unchanged_doc, {}
            )
            self.assertEqual(unchanged["status"], "UNCHANGED")
            self.assertEqual(len(list((self.root / "main/runs").glob("LABEL-LEARNING-AUTO-*"))), 1)

            changed = server_episode.persist_draft_and_refresh_learning(
                config.run_dir, draft("accept", "这个口语词在完整句末，剪掉自然。"), {}
            )
            self.assertEqual(changed["status"], "ACTIVE")
            self.assertEqual(len(list((self.root / "main/runs").glob("LABEL-LEARNING-AUTO-*"))), 2)

            withdrawn = server_episode.persist_draft_and_refresh_learning(
                config.run_dir, draft("pending", "改回待定，暂不进入经验。"), {}
            )
            self.assertEqual(withdrawn["status"], "WITHDRAWN_AND_ACTIVE")
            self.assertFalse(live_path.exists())
            self.assertEqual(len(list((self.root / "main/runs").glob("LABEL-LEARNING-AUTO-*"))), 3)

            no_live_label = server_episode.persist_draft_and_refresh_learning(
                config.run_dir, draft("pending", "改回待定，暂不进入经验。"), {}
            )
            self.assertEqual(no_live_label["status"], "SKIPPED_NO_EFFECTIVE_HUMAN_LABEL")
            self.assertEqual(len(list((self.root / "main/runs").glob("LABEL-LEARNING-AUTO-*"))), 3)
        finally:
            server_episode.PROJECT_ROOT = previous_root

    def test_review_scope_uses_frozen_selection_not_confidence_tier(self) -> None:
        source = {
            "delivery_calibration_selection": {
                "high_risk_policy": "all high-risk candidates are selected",
                "selection_report": {"high_risk": ["C002"]},
            }
        }
        scope = review_scope_from_source(source, ["C001", "C002", "C003"])
        self.assertTrue(scope["available"])
        self.assertEqual(scope["high_risk_candidate_ids"], ["C002"])
        self.assertEqual(scope["low_risk_candidate_ids"], ["C001", "C003"])
        self.assertEqual(scope["high_risk_count"], 1)
        self.assertEqual(scope["low_risk_count"], 2)

    def test_review_scope_rejects_unknown_frozen_high_risk_candidate(self) -> None:
        source = {
            "delivery_calibration_selection": {
                "selection_report": {"high_risk": ["C404"]},
            }
        }
        with self.assertRaisesRegex(SystemExit, "outside this review package"):
            review_scope_from_source(source, ["C001"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
