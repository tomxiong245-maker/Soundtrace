#!/usr/bin/env python3
"""Focused safety and contract tests for filler-global-pause-v1."""

from __future__ import annotations

import array
import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_filler_global_pause_review_source import build  # noqa: E402
from build_review_package import enrich  # noqa: E402
from validate_review_package import validate_decisions, validate_package  # noqa: E402


RULES = (
    Path(__file__).resolve().parents[1]
    / "rules/candidate-generation.filler-global-pause-v1.json"
)
V13_RULES = (
    Path(__file__).resolve().parents[1]
    / "rules/candidate-generation.filler-global-pause-v13.json"
)
V16_RULES = (
    Path(__file__).resolve().parents[2]
    / "filler-global-pause-v14/rules/candidate_rules.v16.json"
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


class FillerGlobalPauseTests(unittest.TestCase):
    sample_rate = 8000
    duration = 30.0

    def write_wav(
        self,
        path: Path,
        transients: list[tuple[float, float, int]] | None = None,
    ) -> None:
        frame_count = round(self.duration * self.sample_rate)
        samples = array.array("h", [0]) * frame_count
        for start, duration, amplitude in transients or []:
            first = round(start * self.sample_rate)
            last = min(frame_count, round((start + duration) * self.sample_rate))
            for index in range(first, last):
                samples[index] = amplitude if index % 2 == 0 else -amplitude
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(samples.tobytes())

    def make_p0_run(
        self,
        root: Path,
        tracks: dict[str, list[dict]],
        transients_by_track: dict[str, list[tuple[float, float, int]]] | None = None,
    ) -> Path:
        report_tracks = []
        frame_count = round(self.duration * self.sample_rate)
        for track_id, words in tracks.items():
            audio = root / f"{track_id}.wav"
            self.write_wav(audio, (transients_by_track or {}).get(track_id))
            transcript = root / f"{track_id}.transcript.json"
            transcript.write_text(
                json.dumps(
                    {
                        "schema_version": "ntrack-transcript-v1",
                        "track_id": track_id,
                        "label": track_id,
                        "source_audio_path": str(audio),
                        "source_audio_sha256": "fixture-recomputed-by-builder",
                        "sample_rate_hz": self.sample_rate,
                        "frame_count": frame_count,
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
                    "sample_rate_hz": self.sample_rate,
                    "frame_count": frame_count,
                    "engineering_gate": "PASS",
                    "tracks": report_tracks,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return report

    def run_build(
        self,
        root: Path,
        tracks: dict[str, list[dict]],
        transients_by_track: dict[str, list[tuple[float, float, int]]] | None = None,
        rules: Path = RULES,
    ) -> tuple[dict, dict, Path]:
        report = self.make_p0_run(root, tracks, transients_by_track)
        out = root / "out"
        build(
            report,
            out,
            rules,
            "FIXTURE",
            "2026-08-12T00:00:00+00:00",
        )
        source = json.loads((out / "candidate_source.json").read_text(encoding="utf-8"))
        blocked = json.loads((out / "blocked_candidates.json").read_text(encoding="utf-8"))
        return source, blocked, out

    def regular_context(self, track_id: str) -> list[dict]:
        return [
            word(track_id, 1, "前", 9.0, 9.2),
            word(track_id, 2, "后", 14.0, 14.2),
        ]

    def test_strong_hesitation_is_nominated_for_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "呃", 10.0, 10.3),
                        word("track_01", 3, "后", 14.0, 14.2),
                    ],
                    "track_02": self.regular_context("track_02"),
                    "track_03": self.regular_context("track_03"),
                },
            )
            fillers = [
                item for item in source["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            ]
            self.assertEqual(len(fillers), 1)
            self.assertEqual(fillers[0]["evidence_text"], "呃")
            self.assertEqual(fillers[0]["safety_status"], "NEEDS_HUMAN_REVIEW")

    def test_single_weak_fillers_are_not_nominated(self) -> None:
        for token in ("对", "然后", "就是", "这个", "那个", "啊"):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, blocked, _ = self.run_build(
                    root,
                    {
                        "track_01": [
                            word("track_01", 1, "前", 9.0, 9.2),
                            word("track_01", 2, token, 10.0, 10.3),
                            word("track_01", 3, "后", 14.0, 14.2),
                        ],
                        "track_02": self.regular_context("track_02"),
                        "track_03": self.regular_context("track_03"),
                    },
                )
                all_items = source["candidates"] + blocked["candidates"]
                self.assertFalse(
                    any(item["candidate_kind"] == "filler_hesitation" for item in all_items)
                )

    def test_v13_preserves_normal_um_and_only_nominates_long_um(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "嗯", 10.0, 10.3),
                        word("track_01", 3, "嗯", 11.0, 11.9),
                        word("track_01", 4, "后", 14.0, 14.2),
                    ],
                    "track_02": self.regular_context("track_02"),
                    "track_03": self.regular_context("track_03"),
                },
                rules=V13_RULES,
            )
            fillers = [
                item
                for item in source["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            ]
            self.assertEqual(len(fillers), 1)
            self.assertEqual(fillers[0]["evidence_text"], "嗯")
            self.assertEqual(fillers[0]["filler_subtype"], "long_acknowledgement")
            self.assertAlmostEqual(fillers[0]["start_seconds"], 11.0)

    def test_v13_keeps_long_repeated_weak_words_and_uses_banded_pause_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tracks = {
                "track_01": [
                    word("track_01", 1, "前", 9.0, 9.2),
                    word("track_01", 2, "然后", 10.0, 10.45),
                    word("track_01", 3, "然后", 10.5, 10.95),
                    word("track_01", 4, "后", 13.2, 13.4),
                ],
                "track_02": [
                    word("track_02", 1, "前", 9.0, 9.2),
                    word("track_02", 2, "后", 13.2, 13.4),
                ],
                "track_03": [
                    word("track_03", 1, "前", 9.0, 9.2),
                    word("track_03", 2, "后", 13.2, 13.4),
                ],
            }
            source, blocked, _ = self.run_build(root, tracks, rules=V13_RULES)
            all_fillers = [
                item
                for item in source["candidates"] + blocked["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            ]
            self.assertEqual(all_fillers, [])
            pause = next(
                item
                for item in source["candidates"]
                if item["candidate_kind"] == "global_long_pause"
            )
            self.assertAlmostEqual(pause["global_silence"]["original_silence_seconds"], 2.25)
            self.assertAlmostEqual(pause["global_silence"]["retained_silence_seconds"], 0.6)
            self.assertEqual(
                pause["global_silence"]["retention_rule"]["mode"],
                "duration_banded_head_tail_retention",
            )

    def test_v16_preserves_acknowledgement_um_even_when_long_or_dense(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, blocked, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "嗯", 10.0, 10.9),
                        word("track_01", 3, "嗯", 11.0, 11.9),
                        word("track_01", 4, "后", 14.0, 14.2),
                    ],
                    "track_02": self.regular_context("track_02"),
                    "track_03": self.regular_context("track_03"),
                },
                rules=V16_RULES,
            )
            all_items = source["candidates"] + blocked["candidates"]
            self.assertFalse(any(item.get("evidence_text") == "嗯" for item in all_items))

    def test_v16_immediate_repetition_is_separate_and_not_blocked_by_clause_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, _ = self.run_build(
                root,
                {
                    # v20.6: 换用非代词 token · "这个" 已加入 Q5 代词豁免白名单
                    # 本测试测 clause_gate 不挡 immediate_repetition, token 只是载体.
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "然后", 10.0, 10.12),
                        word("track_01", 3, "然后", 10.14, 10.26),
                        word("track_01", 4, "后", 14.0, 14.2),
                    ],
                    "track_02": self.regular_context("track_02"),
                    "track_03": self.regular_context("track_03"),
                },
                rules=V16_RULES,
            )
            repeats = [item for item in source["candidates"] if item["candidate_kind"] == "immediate_repetition"]
            self.assertEqual(len(repeats), 1)
            self.assertEqual(repeats[0]["proposed_delete_text"], "然后")
            self.assertEqual(repeats[0]["clause_position"], "unknown")
            self.assertEqual(repeats[0]["default_action"], "human_review_required")

    def test_repeated_weak_filler_is_nominated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "然后", 10.0, 10.2),
                        word("track_01", 3, "然后", 10.3, 10.5),
                        word("track_01", 4, "后", 14.0, 14.2),
                    ],
                    "track_02": self.regular_context("track_02"),
                    "track_03": self.regular_context("track_03"),
                },
            )
            filler = next(
                item for item in source["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            )
            self.assertEqual(filler["evidence_text"], "然后然后")
            self.assertEqual(filler["proposed_delete_text"], "然后")
            self.assertEqual(filler["filler_subtype"], "repeated_weak_filler")
            self.assertEqual(
                [item["text"] for item in filler["retained_evidence_words"]],
                ["然后"],
            )
            self.assertEqual(
                (filler["start_seconds"], filler["end_seconds"]),
                (10.0, 10.2),
            )

    def test_same_filler_on_several_mics_is_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "呃", 10.0, 10.3, 0.8),
                        word("track_01", 3, "后", 14.0, 14.2),
                    ],
                    "track_02": [
                        word("track_02", 1, "前", 9.0, 9.2),
                        word("track_02", 2, "呃", 10.01, 10.29, 0.9),
                        word("track_02", 3, "后", 14.0, 14.2),
                    ],
                    "track_03": self.regular_context("track_03"),
                },
            )
            fillers = [
                item for item in source["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            ]
            self.assertEqual(len(fillers), 1)
            self.assertEqual(
                fillers[0]["corroborated_track_ids"], ["track_01", "track_02"]
            )

    def test_different_weak_run_lengths_on_mics_become_one_conservative_cut(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "对", 10.00, 10.25),
                        word("track_01", 3, "对", 10.25, 10.50),
                        word("track_01", 4, "对", 10.50, 10.75),
                        word("track_01", 5, "后", 14.0, 14.2),
                    ],
                    "track_02": [
                        word("track_02", 1, "前", 9.0, 9.2),
                        word("track_02", 2, "对", 10.55, 10.78),
                        word("track_02", 3, "对", 10.78, 11.02),
                        word("track_02", 4, "后", 14.0, 14.2),
                    ],
                    "track_03": self.regular_context("track_03"),
                },
            )
            fillers = [
                item for item in source["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            ]
            self.assertEqual(len(fillers), 1)
            self.assertEqual(
                fillers[0]["corroborated_track_ids"], ["track_01", "track_02"]
            )
            self.assertEqual(fillers[0]["source_track"], "track_01")
            self.assertEqual(
                (fillers[0]["start_seconds"], fillers[0]["end_seconds"]),
                (10.0, 10.5),
            )
            self.assertEqual(len(fillers[0]["cross_mic_variants"]), 2)

    def test_other_track_speech_blocks_filler(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, blocked, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "呃", 10.0, 10.3),
                        word("track_01", 3, "后", 14.0, 14.2),
                    ],
                    "track_02": [
                        word("track_02", 1, "前", 9.0, 9.2),
                        word("track_02", 2, "重要", 10.02, 10.28),
                        word("track_02", 3, "后", 14.0, 14.2),
                    ],
                    "track_03": self.regular_context("track_03"),
                },
            )
            self.assertFalse(
                any(item["candidate_kind"] == "filler_hesitation" for item in source["candidates"])
            )
            filler = next(
                item for item in blocked["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            )
            self.assertIn("OTHER_TRACK_CONFLICTING_TRANSCRIPT", filler["reason_codes"])

    def test_same_token_spanning_cut_boundary_on_other_track_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, blocked, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 9.0, 9.2),
                        word("track_01", 2, "对", 10.00, 10.30),
                        word("track_01", 3, "对", 10.30, 10.60),
                        word("track_01", 4, "后", 14.0, 14.2),
                    ],
                    "track_02": [
                        word("track_02", 1, "前", 9.0, 9.2),
                        word("track_02", 2, "对", 9.90, 10.45),
                        word("track_02", 3, "后", 14.0, 14.2),
                    ],
                    "track_03": self.regular_context("track_03"),
                },
            )
            self.assertFalse(
                any(item["candidate_kind"] == "filler_hesitation" for item in source["candidates"])
            )
            filler = next(
                item for item in blocked["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
            )
            self.assertIn(
                "OTHER_TRACK_SAME_TOKEN_SPANS_CUT_BOUNDARY",
                filler["reason_codes"],
            )

    def test_one_track_speaking_breaks_apparent_global_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, blocked, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "前", 10.0, 10.2),
                        word("track_01", 2, "后", 14.0, 14.2),
                    ],
                    "track_02": [
                        word("track_02", 1, "前", 10.0, 10.2),
                        word("track_02", 2, "还在说", 11.4, 12.8),
                        word("track_02", 3, "后", 14.0, 14.2),
                    ],
                    "track_03": [
                        word("track_03", 1, "前", 10.0, 10.2),
                        word("track_03", 2, "后", 14.0, 14.2),
                    ],
                },
            )
            all_items = source["candidates"] + blocked["candidates"]
            self.assertFalse(
                any(item["candidate_kind"] == "global_long_pause" for item in all_items)
            )

    def test_all_tracks_quiet_creates_compression_not_full_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tracks = {
                track_id: [
                    word(track_id, 1, "前", 10.0, 10.2),
                    word(track_id, 2, "后", 13.2, 13.4),
                ]
                for track_id in ("track_01", "track_02", "track_03")
            }
            source, _, _ = self.run_build(root, tracks)
            pause = next(
                item for item in source["candidates"]
                if item["candidate_kind"] == "global_long_pause"
            )
            evidence = pause["global_silence"]
            self.assertAlmostEqual(evidence["original_silence_seconds"], 3.0)
            self.assertAlmostEqual(evidence["retained_silence_seconds"], 0.75)
            self.assertAlmostEqual(pause["duration_seconds"], 2.25)
            self.assertAlmostEqual(
                evidence["original_silence_seconds"] - pause["duration_seconds"],
                0.75,
            )
            self.assertEqual(
                pause["boundary_policy"],
                "centered_global_pause_compression_preserve_natural_silence",
            )
            self.assertTrue(pause["review_display"]["requires_audio_review"])

    def test_transient_on_one_track_blocks_wordless_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tracks = {
                track_id: [
                    word(track_id, 1, "前", 10.0, 10.2),
                    word(track_id, 2, "后", 13.2, 13.4),
                ]
                for track_id in ("track_01", "track_02", "track_03")
            }
            source, blocked, _ = self.run_build(
                root,
                tracks,
                {"track_03": [(11.5, 0.03, 26000)]},
            )
            self.assertFalse(
                any(item["candidate_kind"] == "global_long_pause" for item in source["candidates"])
            )
            pause = next(
                item for item in blocked["candidates"]
                if item["candidate_kind"] == "global_long_pause"
            )
            self.assertIn("ACOUSTIC_ACTIVITY_track_03", pause["reason_codes"])
            self.assertGreater(pause["acoustic_guard"]["track_03"]["loud_window_count"], 0)

    def test_opening_and_closing_fillers_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, blocked, _ = self.run_build(
                root,
                {
                    "track_01": [
                        word("track_01", 1, "呃", 2.0, 2.3),
                        word("track_01", 2, "中", 15.0, 15.2),
                        word("track_01", 3, "嗯", 27.0, 27.3),
                    ],
                    "track_02": [word("track_02", 1, "中", 15.0, 15.2)],
                    "track_03": [word("track_03", 1, "中", 15.0, 15.2)],
                },
            )
            self.assertFalse(
                any(item["candidate_kind"] == "filler_hesitation" for item in source["candidates"])
            )
            reasons = {
                reason
                for item in blocked["candidates"]
                if item["candidate_kind"] == "filler_hesitation"
                for reason in item["reason_codes"]
            }
            self.assertIn("OPENING_PROTECTION", reasons)
            self.assertIn("CLOSING_PROTECTION", reasons)

    def test_source_is_consumed_by_existing_ntrack_review_builder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tracks = {
                track_id: [
                    word(track_id, 1, "前", 10.0, 10.2),
                    word(track_id, 2, "后", 13.2, 13.4),
                ]
                for track_id in ("track_01", "track_02", "track_03")
            }
            source, _, out = self.run_build(root, tracks)
            self.assertEqual(len(source["candidates"]), 1)
            candidate_id = source["candidates"][0]["candidate_id"]
            previews = root / "previews"
            previews.mkdir()
            (previews / f"{candidate_id}.original.mp3").write_bytes(b"original")
            (previews / f"{candidate_id}.proposed-cut.mp3").write_bytes(b"proposed")
            frontend = root / "mvp.html"
            frontend.write_text("<!doctype html><title>fixture</title>", encoding="utf-8")
            review_out = root / "review"
            subprocess.run(
                [
                    sys.executable,
                    str(REVIEW_BUILDER),
                    "--source-package",
                    str(out / "candidate_source.json"),
                    "--previews-dir",
                    str(previews),
                    "--tracks-manifest",
                    str(out / "tracks.manifest.json"),
                    "--frontend",
                    str(frontend),
                    "--out",
                    str(review_out),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            enrich(review_out / "review_package.json", out / "candidate_source.json")
            self.assertEqual(
                validate_package(review_out / "review_package.json"),
                [],
            )
            package = json.loads(
                (review_out / "review_package.json").read_text(encoding="utf-8")
            )
            self.assertEqual(package["track_count"], 3)
            self.assertEqual(
                package["candidates"][0]["global_cut"]["applies_to_tracks"],
                ["track_01", "track_02", "track_03"],
            )
            candidate = package["candidates"][0]
            self.assertEqual(candidate["evidence_text"], "global_shared_silence")
            self.assertEqual(
                candidate["review_requirements"]["must_listen_to"],
                ["original", "proposed_cut"],
            )
            decision = {
                "schema_version": "human-decisions-mvp-v1",
                "package_id": package["package_id"],
                "review_manifest_sha256": package["review_manifest_sha256"],
                "reviewer": "测试审核人",
                "decisions": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "candidate_semantic_sha256": candidate["semantic_sha256"],
                        "decision": "reject",
                        "reviewer": "测试审核人",
                        "decided_at": "2026-08-12T00:00:00Z",
                        "review_basis": "text_only",
                        "listened_previews": {},
                    }
                ],
            }
            self.assertIn(
                f"{candidate['candidate_id']}: original preview is required for this candidate",
                validate_decisions(package, decision),
            )
            decision["decisions"][0]["listened_previews"] = {
                "original_sha256": candidate["previews"]["original_sha256"],
                "original_listened_at": "2026-08-12T00:00:01Z",
                "proposed_cut_sha256": candidate["previews"]["proposed_cut_sha256"],
                "proposed_cut_listened_at": "2026-08-12T00:00:02Z",
            }
            decision["decisions"][0]["review_basis"] = "text_and_audio"
            self.assertEqual(validate_decisions(package, decision), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
