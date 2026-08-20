#!/usr/bin/env python3
"""Synthetic tests for the no-candidate audit sampler.

The fixtures deliberately contain only JSON metadata and fake audio path
strings.  A successful test therefore also demonstrates that the sampler does
not need to open or decode any media file.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from sample_no_candidate_windows import (  # noqa: E402
    AuditError,
    build_audit_document,
    check_audit,
    main,
    run_audit,
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_synthetic_run(root: Path, *, frame_count: int = 100_000) -> Path:
    run_dir = root / "SYNTHETIC-v1"
    run_dir.mkdir()
    identity = {
        "schema_version": "run-identity-v1",
        "episode_id": "SYNTHETIC",
        "run_id": "SYNTHETIC-v1",
    }
    identity_path = run_dir / "run_identity.json"
    write_json(identity_path, identity)
    identity_sha = sha256_file(identity_path)
    input_manifest = {
        "schema_version": "delivery-input-manifest-v1",
        "episode_id": "SYNTHETIC",
        "run_id": "SYNTHETIC-v1",
        "run_identity_sha256": identity_sha,
        "sample_rate_hz": 100,
        "frame_count": frame_count,
        "tracks": [
            {
                "track_id": "track_01",
                "input_relpath": "inputs/not-opened-01.wav",
                "sample_rate_hz": 100,
                "frame_count": frame_count,
            },
            {
                "track_id": "track_02",
                "input_relpath": "inputs/not-opened-02.wav",
                "sample_rate_hz": 100,
                "frame_count": frame_count,
            },
        ],
    }
    write_json(run_dir / "input_manifest.json", input_manifest)
    all_candidates = {
        "schema_version": "delivery-all-candidates-v1",
        "episode_id": "SYNTHETIC",
        "run_id": "SYNTHETIC-v1",
        "run_identity_sha256": identity_sha,
        "candidates": [
            {"candidate_id": "C001", "start_sample": 10_000, "end_sample": 10_200},
            {"candidate_id": "C002", "start_sample": 42_000, "end_sample": 42_400},
            {"candidate_id": "C003", "start_sample": 76_000, "end_sample": 76_100},
        ],
    }
    write_json(run_dir / "all_candidates.json", all_candidates)
    return run_dir


class NoCandidateWindowTests(unittest.TestCase):
    def test_fixed_seed_is_reproducible_and_excludes_candidate_handles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = make_synthetic_run(Path(temp))
            first = build_audit_document(
                run_dir=run_dir,
                seed="synthetic-fixed-seed",
                count=8,
                window_seconds_raw="20",
                handle_seconds_raw="2",
            )
            second = build_audit_document(
                run_dir=run_dir,
                seed="synthetic-fixed-seed",
                count=8,
                window_seconds_raw="20",
                handle_seconds_raw="2",
            )
            self.assertEqual(first, second)
            windows = first["windows"]
            self.assertEqual(len(windows), 8)
            self.assertEqual([window["window_id"] for window in windows], [f"NC{index:03d}" for index in range(1, 9)])
            for before, after in zip(windows, windows[1:]):
                self.assertLessEqual(before["end_sample"], after["start_sample"])
            for window in windows:
                for interval in first["sampling"]["protected_intervals"]:
                    self.assertTrue(
                        window["end_sample"] <= interval["start_sample"]
                        or window["start_sample"] >= interval["end_sample"],
                        msg=f"window {window['window_id']} overlaps {interval}",
                    )
            self.assertEqual(
                first["provenance"]["media_access"],
                "none; no WAV/MP3/media path was opened, decoded, copied, or hashed",
            )

    def test_insufficient_capacity_fails_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = make_synthetic_run(root, frame_count=15_000)
            output_dir = root / "should-not-exist"
            with self.assertRaises(AuditError):
                run_audit(
                    run_dir=run_dir,
                    output_dir=output_dir,
                    seed="too-small",
                    count=8,
                    window_seconds_raw="20",
                    handle_seconds_raw="2",
                )
            self.assertFalse(output_dir.exists())

    def test_written_bundle_contains_json_and_markdown_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = make_synthetic_run(root)
            output_dir = root / "audit"
            document = run_audit(
                run_dir=run_dir,
                output_dir=output_dir,
                seed="output-check",
                count=8,
                window_seconds_raw="20",
                handle_seconds_raw="2",
            )
            self.assertTrue((output_dir / "no_candidate_windows.json").is_file())
            self.assertTrue((output_dir / "no_candidate_windows.md").is_file())
            self.assertEqual(sorted(path.name for path in output_dir.iterdir()), ["no_candidate_windows.json", "no_candidate_windows.md"])
            written = json.loads((output_dir / "no_candidate_windows.json").read_text(encoding="utf-8"))
            self.assertEqual(written, document)
            markdown = (output_dir / "no_candidate_windows.md").read_text(encoding="utf-8")
            self.assertIn("不证明", markdown)
            self.assertIn("人工试听", markdown)

    def test_check_ignores_human_listening_results_and_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = make_synthetic_run(root)
            output_dir = root / "audit"
            run_audit(
                run_dir=run_dir,
                output_dir=output_dir,
                seed="check-human-results",
                count=8,
                window_seconds_raw="20",
                handle_seconds_raw="2",
            )
            json_path = output_dir / "no_candidate_windows.json"
            markdown_path = output_dir / "no_candidate_windows.md"
            existing = json.loads(json_path.read_text(encoding="utf-8"))
            existing["windows"][0].update(
                {
                    "human_review_status": "NO_CLEAR_ISSUE",
                    "human_finding": "NO_CLEAR_ISSUE",
                    "human_notes": "试听后确认此处不需要新增候选。",
                }
            )
            write_json(json_path, existing)
            before = {path: path.read_bytes() for path in (json_path, markdown_path)}

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = main(
                    [
                        "--check",
                        "--run-dir",
                        str(run_dir),
                        "--output-dir",
                        str(output_dir),
                        "--seed",
                        "check-human-results",
                        "--count",
                        "8",
                        "--window-seconds",
                        "20",
                        "--handle-seconds",
                        "2",
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(before, {path: path.read_bytes() for path in (json_path, markdown_path)})

    def test_check_rejects_every_nonhuman_difference_with_a_precise_path(self) -> None:
        mutations = [
            (
                "provenance",
                lambda audit: audit["provenance"].__setitem__("run_id", "different-run"),
                "$['provenance']['run_id']",
            ),
            (
                "parameters",
                lambda audit: audit["parameters"].__setitem__("seed", "different-seed"),
                "$['parameters']['seed']",
            ),
            (
                "sampling",
                lambda audit: audit["sampling"].__setitem__(
                    "free_samples", audit["sampling"]["free_samples"] + 1
                ),
                "$['sampling']['free_samples']",
            ),
            (
                "window boundary",
                lambda audit: audit["windows"][0].__setitem__(
                    "start_sample", audit["windows"][0]["start_sample"] + 1
                ),
                "$['windows'][0]['start_sample']",
            ),
            (
                "unexpected nonhuman field",
                lambda audit: audit["windows"][0].__setitem__("reviewer", "not-an-ignored-field"),
                "$['windows'][0]['reviewer']",
            ),
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = make_synthetic_run(root)
            for index, (label, mutate, expected_path) in enumerate(mutations, start=1):
                with self.subTest(label=label):
                    output_dir = root / f"audit-{index}"
                    run_audit(
                        run_dir=run_dir,
                        output_dir=output_dir,
                        seed="strict-check",
                        count=8,
                        window_seconds_raw="20",
                        handle_seconds_raw="2",
                    )
                    json_path = output_dir / "no_candidate_windows.json"
                    altered = json.loads(json_path.read_text(encoding="utf-8"))
                    mutate(altered)
                    write_json(json_path, altered)
                    before = json_path.read_bytes()

                    with self.assertRaisesRegex(AuditError, re.escape(expected_path)):
                        check_audit(
                            run_dir=run_dir,
                            output_dir=output_dir,
                            seed="strict-check",
                            count=8,
                            window_seconds_raw="20",
                            handle_seconds_raw="2",
                        )
                    self.assertEqual(json_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
