#!/usr/bin/env python3
"""Focused tests for the P0 to N-track review-source bridge."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_ntrack_review_source import build, canonicalize_words  # noqa: E402


RULES = (
    Path(__file__).resolve().parents[1]
    / "rules/candidate-generation.ntrack-safe-v1.json"
)
PROJECT_ROOT = Path(__file__).resolve().parents[4]
REVIEW_BUILDER = (
    PROJECT_ROOT
    / "稳定生产/challengers/review-product-v1/scripts/build_mvp_package.py"
)


def word(
    track: str,
    index: int,
    text: str,
    start: float,
    end: float,
    probability: float = 0.9,
) -> dict:
    return {
        "word_id": f"{track}:w{index:06d}",
        "text": text,
        "start_seconds": start,
        "end_seconds": end,
        "probability": probability,
    }


class BridgeTests(unittest.TestCase):
    def make_p0_run(
        self, root: Path, tracks: dict[str, list[dict]]
    ) -> Path:
        report_tracks = []
        for track_id, words in tracks.items():
            audio = root / f"{track_id}.wav"
            audio.write_bytes(b"fixture-audio")
            transcript = root / f"{track_id}.transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "schema_version": "ntrack-transcript-v1",
                        "track_id": track_id,
                        "label": track_id,
                        "source_audio_path": str(audio),
                        "source_audio_sha256": "fixture-only",
                        "sample_rate_hz": 48000,
                        "frame_count": 480000,
                        "engine": "fixture",
                        "model_ref": "fixture",
                        "words": words,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_tracks.append(
                {
                    "track_id": track_id,
                    "label": track_id,
                    "transcript_path": str(transcript),
                    "status": "PASS",
                }
            )
        report = root / "p0_mvp_report.json"
        report.write_text(
            json.dumps(
                {
                    "schema_version": "p0-mvp-report-v1",
                    "track_count": len(report_tracks),
                    "sample_rate_hz": 48000,
                    "frame_count": 480000,
                    "engineering_gate": "PASS",
                    "tracks": report_tracks,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return report

    def test_glossary_merge_keeps_raw_back_reference(self) -> None:
        words = [
            word("track_01", 1, "fe", 1.00, 1.10),
            word("track_01", 2, "ature", 1.10, 1.22),
            word("track_01", 3, "AI", 1.35, 1.45),
            word("track_01", 4, "Agent", 1.45, 1.60),
        ]
        canonical = canonicalize_words(
            words, "track_01", {"feature"}, 0.08
        )
        self.assertEqual(
            [item["text"] for item in canonical],
            ["feature", "AI", "Agent"],
        )
        self.assertEqual(
            canonical[0]["raw_word_ids"],
            ["track_01:w000001", "track_01:w000002"],
        )
        self.assertEqual(
            (
                canonical[0]["start_seconds"],
                canonical[0]["end_seconds"],
            ),
            (1.0, 1.22),
        )

    def test_three_track_source_is_generic_and_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p0_report = self.make_p0_run(
                root,
                {
                    "track_01": [word("track_01", 1, "呃", 2.0, 2.22)],
                    "track_02": [],
                    "track_03": [],
                },
            )
            out = root / "bridge"
            result = build(
                p0_report,
                out,
                RULES,
                "EP04",
                "2026-08-11T12:00:00+00:00",
            )
            self.assertEqual(result["status"], "READY_FOR_REVIEW_PACKAGE")
            manifest = json.loads(
                (out / "tracks.manifest.json").read_text(encoding="utf-8")
            )
            source = json.loads(
                (out / "candidate_source.json").read_text(encoding="utf-8")
            )
            candidate = source["candidates"][0]
            self.assertEqual(source["episode_id"], "EP04")
            self.assertEqual(
                [track["track_id"] for track in manifest["tracks"]],
                ["track_01", "track_02", "track_03"],
            )
            self.assertEqual(candidate["source_track"], "track_01")
            self.assertEqual(
                candidate["safety_status"], "NEEDS_HUMAN_REVIEW"
            )
            self.assertEqual(
                candidate["boundary_policy"],
                "whole_canonical_token_no_padding",
            )
            self.assertEqual(
                (candidate["start_sample"], candidate["end_sample"]),
                (96000, 106560),
            )

    def test_other_track_conflicting_text_blocks_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p0_report = self.make_p0_run(
                root,
                {
                    "track_01": [word("track_01", 1, "呃", 2.0, 2.22)],
                    "track_02": [
                        word("track_02", 1, "重要", 2.02, 2.20)
                    ],
                    "track_03": [],
                },
            )
            out = root / "bridge"
            build(
                p0_report,
                out,
                RULES,
                "EP04",
                "2026-08-11T12:00:00+00:00",
            )
            source = json.loads(
                (out / "candidate_source.json").read_text(encoding="utf-8")
            )
            blocked = json.loads(
                (out / "blocked_candidates.json").read_text(encoding="utf-8")
            )
            self.assertEqual(source["candidates"], [])
            self.assertEqual(
                blocked["candidates"][0]["safety_status"], "BLOCKED"
            )
            self.assertIn(
                "OTHER_TRACK_CONFLICTING_TRANSCRIPT",
                blocked["candidates"][0]["reason_codes"],
            )

    def test_same_filler_on_multiple_tracks_collapses_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p0_report = self.make_p0_run(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "呃", 2.0, 2.22, 0.8)
                    ],
                    "track_02": [
                        word("track_02", 1, "呃", 2.01, 2.21, 0.9)
                    ],
                    "track_03": [],
                },
            )
            out = root / "bridge"
            build(
                p0_report,
                out,
                RULES,
                "EP04",
                "2026-08-11T12:00:00+00:00",
            )
            source = json.loads(
                (out / "candidate_source.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(source["candidates"]), 1)
            self.assertEqual(
                source["candidates"][0]["corroborated_track_ids"],
                ["track_01", "track_02"],
            )

    def test_adjacent_repetition_run_becomes_one_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p0_report = self.make_p0_run(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "一些", 1.0, 1.2),
                        word("track_01", 2, "一些", 1.2, 1.4),
                        word("track_01", 3, "一些", 1.4, 1.6),
                        word("track_01", 4, "一些", 1.6, 1.8),
                    ],
                    "track_02": [],
                    "track_03": [],
                },
            )
            out = root / "bridge"
            build(
                p0_report,
                out,
                RULES,
                "EP04",
                "2026-08-11T12:00:00+00:00",
            )
            source = json.loads(
                (out / "candidate_source.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(source["candidates"]), 1)
            candidate = source["candidates"][0]
            self.assertEqual(candidate["merged_repetition_proposals"], 3)
            self.assertEqual(
                (candidate["start_seconds"], candidate["end_seconds"]),
                (1.0, 1.6),
            )

    def test_too_short_candidate_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p0_report = self.make_p0_run(
                root,
                {
                    "track_01": [word("track_01", 1, "呃", 2.0, 2.02)],
                    "track_02": [],
                    "track_03": [],
                },
            )
            out = root / "bridge"
            build(
                p0_report,
                out,
                RULES,
                "EP04",
                "2026-08-11T12:00:00+00:00",
            )
            source = json.loads(
                (out / "candidate_source.json").read_text(encoding="utf-8")
            )
            blocked = json.loads(
                (out / "blocked_candidates.json").read_text(encoding="utf-8")
            )
            self.assertEqual(source["candidates"], [])
            self.assertIn(
                "CANDIDATE_TOO_SHORT",
                blocked["candidates"][0]["reason_codes"],
            )

    def test_output_is_consumed_by_existing_review_builder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p0_report = self.make_p0_run(
                root,
                {
                    "track_01": [word("track_01", 1, "呃", 2.0, 2.22)],
                    "track_02": [],
                    "track_03": [],
                },
            )
            for track_id in ("track_01", "track_02", "track_03"):
                with wave.open(str(root / f"{track_id}.wav"), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(48000)
                    wav.writeframes(b"\0\0" * 480000)

            bridge_out = root / "bridge"
            build(
                p0_report,
                bridge_out,
                RULES,
                "EP04",
                "2026-08-11T12:00:00+00:00",
            )
            previews = root / "previews"
            previews.mkdir()
            (previews / "C001.original.mp3").write_bytes(b"original-fixture")
            (previews / "C001.proposed-cut.mp3").write_bytes(
                b"proposed-fixture"
            )
            frontend = root / "mvp.html"
            frontend.write_text("<!doctype html><title>fixture</title>")
            review_out = root / "review"
            subprocess.run(
                [
                    sys.executable,
                    str(REVIEW_BUILDER),
                    "--source-package",
                    str(bridge_out / "candidate_source.json"),
                    "--previews-dir",
                    str(previews),
                    "--tracks-manifest",
                    str(bridge_out / "tracks.manifest.json"),
                    "--frontend",
                    str(frontend),
                    "--out",
                    str(review_out),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            package = json.loads(
                (review_out / "review_package.json").read_text(encoding="utf-8")
            )
            self.assertEqual(package["episode_id"], "EP04")
            self.assertEqual(package["track_count"], 3)
            self.assertEqual(
                package["candidates"][0]["source_track_id"], "track_01"
            )
            self.assertEqual(
                package["candidates"][0]["global_cut"]["applies_to_tracks"],
                ["track_01", "track_02", "track_03"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
