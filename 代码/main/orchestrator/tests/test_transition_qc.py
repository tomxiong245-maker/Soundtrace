#!/usr/bin/env python3
"""Contract tests for rendered transition QC.

All audio here is disposable synthetic PCM.  These tests prove schema mapping,
identity validation, ranking and the no-overwrite boundary; they do not make a
claim about real-program listening quality.
"""

from __future__ import annotations

import array
import hashlib
import importlib.util
import json
import math
import tempfile
import unittest
import wave
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "transition_qc.py"
SPEC = importlib.util.spec_from_file_location("transition_qc", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def write_pcm(path: Path, samples: list[float], sample_rate: int = 1000) -> None:
    frames = array.array("h")
    for sample in samples:
        frames.append(max(-32768, min(32767, round(sample * 32767))))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames.tobytes())


def fixture_speech_mix(frame_count: int = 760) -> list[float]:
    """Two mapped cuts: first ordinary, second deliberately acoustically odd."""

    samples = [0.08 * math.sin(2 * math.pi * 18 * index / 1000) for index in range(frame_count)]
    # The second rendered transition is at samples 460--480.  Surround it with
    # a large level/frequency change so the ranking assertion is meaningful.
    for index in range(420, 460):
        samples[index] = 0.02 * math.sin(2 * math.pi * 10 * index / 1000)
    for index in range(460, 480):
        samples[index] = 0.55 * math.sin(2 * math.pi * 190 * index / 1000)
    for index in range(480, 530):
        samples[index] = 0.75 * math.sin(2 * math.pi * 210 * index / 1000)
    return samples


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_variant(run_dir: Path, variant: str, identity_sha: str) -> None:
    render_dir = run_dir / f"render_{variant}"
    render_dir.mkdir()
    speech_mix = render_dir / "speech_mix.wav"
    master = render_dir / f"EPTEST-run.{variant}.master.wav"
    samples = fixture_speech_mix()
    write_pcm(speech_mix, samples)
    write_pcm(master, samples)
    edl = {
        "schema_version": "delivery-edl-v1",
        "episode_id": "EPTEST",
        "run_id": "EPTEST-run",
        "run_identity_sha256": identity_sha,
        "variant": variant,
        "sample_rate_hz": 1000,
        "frame_count": 1000,
        "global_sync_actions": [
            {
                "action_id": "cut-a",
                "action_type": "global_sync_cut",
                "candidate_id": "C001",
                "start_sample": 200,
                "end_sample": 300,
                "decision_provenance": "human_individual_review",
                "risk_level": "low",
            },
            {
                "action_id": "cut-b",
                "action_type": "global_sync_cut",
                "candidate_id": "C002",
                "start_sample": 600,
                "end_sample": 700,
                "decision_provenance": "human_individual_review",
                "risk_level": "low",
            },
        ],
        "render_sync_cuts": [
            {
                "start_sample": 200,
                "end_sample": 300,
                "crossfade_samples": 20,
                "crossfade_ms": 20.0,
                "source_action_ids": ["cut-a"],
            },
            {
                "start_sample": 600,
                "end_sample": 700,
                "crossfade_samples": 20,
                "crossfade_ms": 20.0,
                "source_action_ids": ["cut-b"],
            },
        ],
        "source_track_gates": [{"action_id": "gate-1", "action_type": "source_track_gate"}],
    }
    edl_path = run_dir / f"{variant}.edl.json"
    write_json(edl_path, edl)
    write_json(
        render_dir / "render_manifest.json",
        {
            "schema_version": "delivery-render-manifest-v1",
            "episode_id": "EPTEST",
            "run_id": "EPTEST-run",
            "run_identity_sha256": identity_sha,
            "variant": variant,
            "source_edl_relpath": f"{variant}.edl.json",
            "source_edl_sha256": sha256(edl_path),
            "outputs": {
                "speech_mix": f"render_{variant}/speech_mix.wav",
                "master_wav": f"render_{variant}/{master.name}",
                "master_wav_sha256": sha256(master),
            },
        },
    )


def make_run(root: Path) -> Path:
    run_dir = root / "EPTEST-run"
    run_dir.mkdir()
    identity = {
        "schema_version": "run-identity-v1",
        "episode_id": "EPTEST",
        "run_id": "EPTEST-run",
        "run_dir_rel": "main/runs/EPTEST/EPTEST-run",
    }
    identity_path = run_dir / "run_identity.json"
    write_json(identity_path, identity)
    identity_sha = sha256(identity_path)
    write_json(
        run_dir / "state.json",
        {
            "schema_version": "delivery-state-v1",
            "episode_id": "EPTEST",
            "run_id": "EPTEST-run",
            "state": "FINAL_QC_REQUIRED",
        },
    )
    write_json(
        run_dir / "input_manifest.json",
        {
            "schema_version": "delivery-input-manifest-v1",
            "episode_id": "EPTEST",
            "run_id": "EPTEST-run",
            "run_identity_sha256": identity_sha,
            "sample_rate_hz": 1000,
            "frame_count": 1000,
        },
    )
    for variant in MODULE.VARIANTS:
        make_variant(run_dir, variant, identity_sha)
    return run_dir


class RenderedTransitionQCTests(unittest.TestCase):
    def test_dual_edl_render_schema_generates_ranked_listening_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = make_run(Path(temp))
            reports = [
                MODULE.generate_transition_qc(run_dir, variant, context_ms=30, priority_count=1)
                for variant in MODULE.VARIANTS
            ]
            for report in reports:
                self.assertEqual(report["status"], MODULE.STATUS)
                self.assertEqual(report["transition_count"], 2)
                self.assertEqual(report["priority_relisten_count"], 1)
                self.assertEqual(report["scope"]["source_track_gates_excluded_count"], 1)
                self.assertEqual(report["ranked_transition_ids"][0], "render-cut-0002")
                self.assertEqual(
                    report["transitions"][0]["recommended_human_action"], "priority_relisten"
                )
                self.assertEqual(
                    report["transitions"][0]["decision_authority"],
                    "none_objective_metrics_do_not_approve_or_reject_edits",
                )
                self.assertIn("authorizes automatic editing", report["human_review_rule"])
                output = run_dir / f"render_{report['variant']}/transition_qc.json"
                self.assertTrue(output.is_file())
                self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["variant"], report["variant"])

    def test_refuses_overwrite_or_mismatched_render_edl_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = make_run(Path(temp))
            MODULE.generate_transition_qc(run_dir, "human_approved", context_ms=30)
            with self.assertRaisesRegex(MODULE.TransitionQCError, "refusing to overwrite"):
                MODULE.generate_transition_qc(run_dir, "human_approved", context_ms=30)

        with tempfile.TemporaryDirectory() as temp:
            run_dir = make_run(Path(temp))
            manifest_path = run_dir / "render_human_approved/render_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_edl_sha256"] = "0" * 64
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(MODULE.TransitionQCError, "source EDL SHA"):
                MODULE.generate_transition_qc(run_dir, "human_approved", context_ms=30)

    def test_refuses_to_write_into_a_completed_delivery_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = make_run(Path(temp))
            state_path = run_dir / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["state"] = "DELIVERY_DECISION_RECORDED"
            write_json(state_path, state)
            with self.assertRaisesRegex(MODULE.TransitionQCError, "completed or review-pending runs are read-only"):
                MODULE.generate_transition_qc(run_dir, "human_approved", context_ms=30)


if __name__ == "__main__":
    unittest.main()
