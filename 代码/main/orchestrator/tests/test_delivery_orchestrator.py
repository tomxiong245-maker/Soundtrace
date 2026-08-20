#!/usr/bin/env python3
"""Focused contract tests for the delivery orchestrator.

They deliberately use temporary PCM fixtures and never touch a real run or source
audio.  End-to-end ASR and full-program render remain separately evidenced by a
real run because they depend on local models and long audio.
"""

from __future__ import annotations

import array
import importlib.util
import json
import math
import shutil
import sys
import tempfile
import textwrap
import types
import unittest
import uuid
import wave
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "delivery_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("delivery_orchestrator", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_pcm_wav(
    path: Path,
    frames: int = 4800,
    tone_windows: tuple[tuple[float, float], ...] = (),
) -> None:
    """Write a small 48 kHz mono fixture with optional tone regions."""

    if tone_windows:
        samples = array.array("h")
        for frame in range(frames):
            second = frame / 48000
            audible = any(start <= second < end for start, end in tone_windows)
            value = round(8000 * math.sin(2 * math.pi * 440 * second)) if audible else 0
            samples.append(value)
        payload = samples.tobytes()
    else:
        payload = b"\0\0" * frames
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(payload)


class DeliveryOrchestratorTests(unittest.TestCase):
    def test_semantic_reuse_requires_explicit_choice_when_multiple_runs_match(self) -> None:
        """A reused ASR report cannot silently pick an arbitrary semantic layer."""

        with tempfile.TemporaryDirectory() as temp:
            episode_dir = Path(temp) / "EPTEST"
            source_run = episode_dir / "EPTEST-source"
            source_run.mkdir(parents=True)
            MODULE.write_json(
                source_run / "run_identity.json",
                {
                    "schema_version": "run-identity-v1",
                    "episode_id": "EPTEST",
                    "run_id": "EPTEST-source",
                    "contract_version": MODULE.CONTRACT_VERSION,
                    "run_dir_rel": "main/runs/EPTEST/EPTEST-source",
                },
            )
            report_sha = "a" * 64
            semantic_runs = []
            for suffix in ("one", "two"):
                semantic_run = episode_dir / f"EPTEST-semantic-transcript-v1-{suffix}"
                semantic_run.mkdir()
                MODULE.write_json(
                    semantic_run / "manifest.json",
                    {
                        "schema_version": "semantic-transcript-run-v1",
                        "episode_id": "EPTEST",
                        "run_id": semantic_run.name,
                        "source_run_id": "EPTEST-source",
                        "status": "PASS",
                        "input_report": {"sha256": report_sha},
                    },
                )
                semantic_runs.append(semantic_run)

            with self.assertRaisesRegex(MODULE.DeliveryError, "ambiguous semantic transcript"):
                MODULE._find_semantic_reuse_dir(source_run, report_sha)
            self.assertEqual(
                MODULE._find_semantic_reuse_dir(
                    source_run,
                    report_sha,
                    explicit_semantic_run=semantic_runs[1],
                ),
                semantic_runs[1].resolve(),
            )

    def test_reuse_rejects_new_asr_options_before_creating_a_run(self) -> None:
        args = types.SimpleNamespace(
            input_dir=Path("/definitely/not/read"),
            episode_id="EPTEST",
            run_id="EPTEST-conflict",
            reuse_analysis_run=Path("/unused/source"),
            reuse_semantic_run=None,
            model="small",
            context_prompt="",
            ffmpeg=None,
        )
        with self.assertRaisesRegex(MODULE.DeliveryError, "cannot be used with --reuse-analysis-run"):
            MODULE.cmd_start(args)

    def test_cli_defaults_to_automatic_development_benchmark_refresh(self) -> None:
        args = MODULE.parser().parse_args(["start", "--input-dir", "/does/not/need/to/exist/yet"])
        self.assertEqual(args.benchmark_mode, "auto")
        self.assertTrue(MODULE.benchmark_refresh_enabled(args))
        self.assertIsNone(args.experience_snapshot)
        # The effective default is resolved at start time so a newly submitted
        # human review can atomically update the active immutable snapshot.
        self.assertTrue(MODULE.resolve_default_experience_snapshot().is_file())

    def test_representative_selection_keeps_all_high_risk(self) -> None:
        candidates = [
            {
                "candidate_id": "H001",
                "candidate_kind": "global_long_pause",
                "duration_seconds": 1.2,
            }
        ]
        candidates += [
            {
                "candidate_id": f"L{index:03d}",
                "candidate_kind": "filler_hesitation",
                "reason_key": "filler_hesitation",
                "filler_subtype": "repeated_weak_filler",
                "source_track": "track_01",
                "duration_seconds": 0.12 + index / 1000,
            }
            for index in range(1, 31)
        ]
        first, report = MODULE.select_calibration_candidates(candidates, "stable-seed")
        second, _ = MODULE.select_calibration_candidates(candidates, "stable-seed")
        self.assertEqual(first, second)
        self.assertIn("H001", first)
        self.assertGreaterEqual(len(first), 4)
        self.assertEqual(report["high_risk"], ["H001"])

    def test_historic_case_memory_priority_claims_a_low_risk_review_slot(self) -> None:
        candidates = [
            {
                "candidate_id": f"C{index:02d}",
                "candidate_kind": "filler_hesitation",
                "reason_key": "filler_hesitation",
                "filler_subtype": "repeated_weak_filler",
                "source_track": "track_01",
                "duration_seconds": 0.10 + index / 100,
                "case_memory_signal": {
                    "review_priority": 3 if index == 10 else 0,
                    "policy": "reference_and_review_priority_only",
                },
            }
            for index in range(20)
        ]
        selected, report = MODULE.select_calibration_candidates(candidates, "stable-seed")
        stratum = next(iter(report["low_risk_strata"].values()))
        self.assertIn("C10", selected)
        self.assertEqual(stratum["priority_candidate_ids"], ["C10"])
        self.assertEqual(stratum["priority_selected_ids"], ["C10"])

    def test_base_run_records_run_local_symlink_not_external_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.wav"
            write_pcm_wav(source)
            previous = MODULE.RUNS_ROOT
            MODULE.RUNS_ROOT = root / "runs"
            try:
                run_dir, _, tracks = MODULE.make_base_run(
                    episode_id="EPTEST",
                    run_id="EPTEST-unit",
                    source_tracks=[("track_01", "unit", source)],
                    purpose="unit test",
                    music_template_id="reference-linear-v1",
                )
                self.assertTrue((run_dir / tracks[0]["input_relpath"]).is_file())
                self.assertTrue(tracks[0]["input_relpath"].startswith("inputs/"))
                plan = MODULE.read_json(run_dir / "plan.json")
                self.assertEqual(plan["denoise"]["backend"], "deepfilternet")
                self.assertEqual(plan["denoise"]["status"], "PENDING")
                self.assertEqual(plan["music"]["music_template_id"], "reference-linear-v1")
                self.assertEqual(plan["music"]["timing"]["voice_start_seconds"], 5.0)
                self.assertEqual(plan["music"]["timing"]["intro_fade_out_end_seconds"], 16.0)
                checkpoint = MODULE.read_json(run_dir / "requirements_checkpoint.json")
                self.assertEqual(checkpoint["music"]["template_id"], "reference-linear-v1")
                self.assertEqual(checkpoint["music"]["timing"]["voice_start_seconds"], 5.0)
                self.assertEqual(
                    checkpoint["music"]["timing_sha256"],
                    MODULE.sha256_bytes(checkpoint["music"]["timing"]),
                )
                self.assertEqual(MODULE.identity_errors(run_dir), [])
            finally:
                MODULE.RUNS_ROOT = previous

    def test_merge_actions_preserves_provenance_and_nonoverlap(self) -> None:
        merged = MODULE.merge_sync_actions(
            [
                {"action_id": "a", "start_sample": 1000, "end_sample": 2000},
                {"action_id": "b", "start_sample": 1800, "end_sample": 2400},
                {"action_id": "c", "start_sample": 5000, "end_sample": 5400},
            ],
            frame_count=10000,
            sample_rate=48000,
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["source_action_ids"], ["a", "b"])
        self.assertGreaterEqual(merged[0]["crossfade_samples"], 0)

    def test_source_track_gate_is_separate_from_global_cut_filter(self) -> None:
        gate_graph = MODULE.source_track_gate_filter(
            [
                {
                    "action_id": "gate-1",
                    "track_id": "track_01",
                    "start_sample": 100,
                    "end_sample": 200,
                }
            ],
            "track_01",
            1000,
        )
        self.assertIn("volume=0", gate_graph)
        no_gate_graph = MODULE.source_track_gate_filter([], "track_02", 1000)
        self.assertEqual(no_gate_graph, "[0:a]anull[gated]")

    def test_review_budget_never_drops_mandatory_high_risk(self) -> None:
        candidates = [
            {
                "candidate_id": f"H{index:03d}",
                "candidate_kind": "global_long_pause",
                "duration_seconds": 1.0,
            }
            for index in range(1, 4)
        ]
        with self.assertRaisesRegex(MODULE.DeliveryError, "mandatory high-risk"):
            MODULE.select_calibration_candidates(candidates, "stable", review_budget=2)

    def test_transition_qc_is_not_required_before_render_or_for_special_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            MODULE.write_json(run_dir / "state.json", {"state": "CALIBRATION_REVIEW_REQUIRED"})
            self.assertFalse(MODULE.transition_qc_required(run_dir))
            MODULE.write_json(run_dir / "state.json", {"state": "FINAL_QC_REQUIRED"})
            self.assertTrue(MODULE.transition_qc_required(run_dir))
            MODULE.write_json(run_dir / "human_approval_scope.json", {"approval_mode": "human_whole_episode_audition"})
            self.assertFalse(MODULE.transition_qc_required(run_dir))

    def test_benchmark_refresh_failure_is_nonblocking_and_leaves_an_honest_record(self) -> None:
        """Development diagnostics cannot change a delivery state or invent labels."""

        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "EPTEST" / "EPTEST-v1"
            run_dir.mkdir(parents=True)
            MODULE.write_json(
                run_dir / "run_identity.json",
                {
                    "schema_version": "run-identity-v1",
                    "episode_id": "EPTEST",
                    "run_id": "EPTEST-v1",
                },
            )
            MODULE.write_json(
                run_dir / "state.json",
                {"schema_version": "delivery-state-v1", "state": "FINAL_QC_REQUIRED"},
            )
            previous_script = MODULE.DEVELOPMENT_BENCHMARK_SCRIPT
            MODULE.DEVELOPMENT_BENCHMARK_SCRIPT = run_dir / "missing-wrapper.py"
            try:
                result = MODULE.refresh_development_benchmark_nonblocking(
                    run_dir,
                    phase="post_render",
                    python=sys.executable,
                )
            finally:
                MODULE.DEVELOPMENT_BENCHMARK_SCRIPT = previous_script
            self.assertEqual(result["status"], "BENCHMARK_EVIDENCE_UNAVAILABLE")
            self.assertEqual(MODULE.read_json(run_dir / "state.json")["state"], "FINAL_QC_REQUIRED")
            evidence = MODULE.read_json(run_dir / "benchmark_evidence.json")
            self.assertEqual(evidence["status"], "BENCHMARK_EVIDENCE_UNAVAILABLE")
            self.assertEqual(evidence["phase"], "post_render")
            self.assertFalse((run_dir / "human_decisions.json").exists())
            self.assertFalse((run_dir / "human_approved.edl.json").exists())

    def test_full_fixture_flow_reaches_delivery_decision_with_local_tool_contracts(self) -> None:
        """Exercise start -> review packet -> resume -> final decision without real audio.

        The fake denoiser and ASR are deliberately tiny local executables so the
        test proves orchestration, hashes, review, EDL render, music and QC rather
        than claiming a synthetic signal measures real model quality.
        """

        ffmpeg = Path(
            "/Applications/爱问云.app/Contents/Resources/app/node_modules/@plasosdk/plasoffmpeg/ffmpeg"
        )
        if not ffmpeg.is_file():
            self.skipTest("the locally audited ffmpeg fixture binary is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_dir = root / "input"
            input_dir.mkdir()
            frames = 30 * 48000
            for index in range(1, 4):
                write_pcm_wav(
                    input_dir / f"track_{index:02d}.wav",
                    frames=frames,
                    tone_windows=((9.8, 10.2), (13.2, 13.8)),
                )
            music = root / "fixture-music.wav"
            write_pcm_wav(music, frames=65 * 48000)

            fake_denoise = root / "fake_denoise.py"
            fake_denoise.write_text(
                textwrap.dedent(
                    """\
                    import argparse
                    import hashlib
                    import json
                    import shutil
                    from pathlib import Path

                    def sha256(path):
                        digest = hashlib.sha256()
                        with open(path, 'rb') as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                                digest.update(chunk)
                        return digest.hexdigest()

                    parser = argparse.ArgumentParser()
                    parser.add_argument('--output-dir', required=True, type=Path)
                    parser.add_argument('--ffmpeg')
                    parser.add_argument('--track', action='append', required=True)
                    args = parser.parse_args()
                    args.output_dir.mkdir(parents=True, exist_ok=False)
                    tracks = []
                    for value in args.track:
                        track_id, raw_path = value.split('=', 1)
                        source = Path(raw_path)
                        output = args.output_dir / f'{track_id}.deepfiltered.wav'
                        shutil.copy2(source, output)
                        tracks.append({'track_id': track_id, 'output_sha256': sha256(output)})
                    (args.output_dir / 'denoise_manifest.json').write_text(json.dumps({
                        'schema_version': 'fixture-deepfilternet-v1',
                        'status': 'USER_AUTHORIZED_DIRECT_INTEGRATION__SUBJECTIVE_REVIEW_PENDING',
                        'tracks': tracks,
                    }), encoding='utf-8')
                    """
                ),
                encoding="utf-8",
            )
            fake_p0 = root / "fake_p0.py"
            fake_p0.write_text(
                textwrap.dedent(
                    """\
                    import argparse
                    import hashlib
                    import json
                    import wave
                    from pathlib import Path

                    def sha256(path):
                        digest = hashlib.sha256()
                        with open(path, 'rb') as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                                digest.update(chunk)
                        return digest.hexdigest()

                    parser = argparse.ArgumentParser()
                    parser.add_argument('--manifest', required=True, type=Path)
                    parser.add_argument('--out', required=True, type=Path)
                    parser.add_argument('--model')
                    parser.add_argument('--context-prompt')
                    args = parser.parse_args()
                    source = json.loads(args.manifest.read_text(encoding='utf-8'))
                    first_audio = (args.manifest.parent / source['tracks'][0]['audio_path']).resolve()
                    with wave.open(str(first_audio), 'rb') as audio_info:
                        sample_rate_hz = audio_info.getframerate()
                        frame_count = audio_info.getnframes()
                    tracks = []
                    for item in source['tracks']:
                        audio = (args.manifest.parent / item['audio_path']).resolve()
                        transcript = args.out / f\"{item['track_id']}.transcript.json\"
                        transcript.write_text(json.dumps({
                            'schema_version': 'ntrack-transcript-v1',
                            'track_id': item['track_id'],
                            'label': item['label'],
                            'source_audio_path': str(audio),
                            'source_audio_sha256': sha256(audio),
                            'sample_rate_hz': sample_rate_hz,
                            'frame_count': frame_count,
                            'engine': 'fixture',
                            'model_ref': 'fixture',
                            'words': [
                                {'word_id': f\"{item['track_id']}:w1\", 'text': '前', 'start_seconds': 10.0, 'end_seconds': 10.2, 'probability': 0.99},
                                {'word_id': f\"{item['track_id']}:w2\", 'text': '后', 'start_seconds': 13.2, 'end_seconds': 13.4, 'probability': 0.99},
                            ],
                        }, ensure_ascii=False), encoding='utf-8')
                        tracks.append({'track_id': item['track_id'], 'label': item['label'], 'transcript_path': str(transcript), 'status': 'PASS'})
                    (args.out / 'p0_mvp_report.json').write_text(json.dumps({
                        'schema_version': 'p0-mvp-report-v1',
                        'track_count': len(tracks),
                        'sample_rate_hz': sample_rate_hz,
                        'frame_count': frame_count,
                        'engineering_gate': 'PASS',
                        'quality_gate': 'FIXTURE_ONLY',
                        'tracks': tracks,
                    }, ensure_ascii=False), encoding='utf-8')
                    """
                ),
                encoding="utf-8",
            )

            run_id = f"EPFIX-v13-fixture-{uuid.uuid4().hex[:10]}"
            real_runs_root = MODULE.PROJECT_ROOT / "main" / "runs"
            test_run_dir = real_runs_root / "EPFIX" / run_id
            previous = {
                "runs_root": MODULE.RUNS_ROOT,
                "music_source": MODULE.MUSIC_SOURCE,
                "music_sha": MODULE.MUSIC_SHA256,
                "denoise_script": MODULE.DEEPFILTER_DENOISE_SCRIPT,
                "p0_script": MODULE.P0_SCRIPT,
            }
            # The real review builder deliberately only accepts a run below the
            # project's canonical main/runs root.  This test uses a uniquely named
            # disposable run there and removes exactly that run in finally.
            MODULE.RUNS_ROOT = real_runs_root
            MODULE.MUSIC_SOURCE = music
            MODULE.MUSIC_SHA256 = MODULE.sha256_file(music)
            MODULE.DEEPFILTER_DENOISE_SCRIPT = fake_denoise
            MODULE.P0_SCRIPT = fake_p0
            try:
                args = types.SimpleNamespace(
                    input_dir=input_dir,
                    episode_id="EPFIX",
                    run_id=run_id,
                    model=None,
                    context_prompt="",
                    music_template="reference-linear-v1",
                    candidate_rules=MODULE.DEFAULT_CANDIDATE_RULES,
                    experience_snapshot=MODULE.DEFAULT_EXPERIENCE_SNAPSHOT,
                    review_budget=20,
                    python=sys.executable,
                    ffmpeg=str(ffmpeg),
                )
                self.assertEqual(MODULE.cmd_start(args), 0)
                run_dir = test_run_dir
                self.assertEqual(
                    MODULE.read_json(run_dir / "state.json")["state"],
                    "CALIBRATION_REVIEW_REQUIRED",
                )
                plan = MODULE.read_json(run_dir / "plan.json")
                self.assertEqual(
                    plan["integration_governance"]["registry_id"],
                    "owner-attested-mainline-20260818-v1",
                )
                self.assertTrue(
                    (run_dir / "frozen/integration_governance.json").is_file()
                )
                self.assertEqual(plan["editing_policy"]["id"], "editing-policy-guards-v1")
                self.assertEqual(plan["autocut_policy"]["status"], "NOT_APPROVED")
                policy_application = MODULE.read_json(run_dir / "policy_application.json")
                self.assertEqual(policy_application["policy_id"], "editing-policy-guards-v1")
                self.assertEqual(policy_application["summary"]["auto_cut_eligible"], 0)
                learning_pre = MODULE.read_json(run_dir / "label_learning_application.pre_review.json")
                learning_post = MODULE.read_json(
                    run_dir / "review_bundle/label_learning_application.post_boundary.json"
                )
                memory_pre = MODULE.read_json(run_dir / "case_memory.pre_review.json")
                memory_post = MODULE.read_json(run_dir / "review_bundle/case_memory.json")
                current_identity_sha = MODULE.sha256_file(run_dir / "run_identity.json")
                current_input_sha = MODULE.sha256_file(run_dir / "input_manifest.json")
                for application in (learning_pre, learning_post):
                    self.assertTrue(application["policy"]["never_creates_human_decision"])
                    self.assertTrue(application["policy"]["never_creates_edl"])
                    self.assertEqual(application["target_identity"]["run_identity_sha256"], current_identity_sha)
                    self.assertEqual(application["target_identity"]["input_manifest_sha256"], current_input_sha)
                self.assertEqual(
                    set(memory_pre["candidate_memory"]),
                    {row["candidate_id"] for row in MODULE.read_json(run_dir / "candidates/candidate_source.json")["candidates"]},
                )
                self.assertEqual(memory_post["source_review_manifest_sha256"], MODULE.read_json(run_dir / "review_bundle/review_package.json")["review_manifest_sha256"])
                for row in memory_post["candidate_memory"].values():
                    self.assertIsNone(row["current_decision"])
                    self.assertFalse(row["creates_edl_action"])
                    self.assertFalse(row["creates_autocut_permission"])
                revision = MODULE.refresh_review_package(
                    run_dir,
                    python=sys.executable,
                    ffmpeg=str(ffmpeg),
                    reason="fixture refresh validates case-memory rebinding",
                )
                refreshed_report = MODULE.read_json(run_dir / "preference_application_report.json")
                self.assertEqual(
                    refreshed_report["case_memory"]["review_bundle_sha256"],
                    MODULE.sha256_file(run_dir / "review_bundle/case_memory.json"),
                )
                self.assertEqual(
                    revision["current_case_memory_sha256"],
                    refreshed_report["case_memory"]["review_bundle_sha256"],
                )
                self.assertTrue((run_dir / "review_packet.md").is_file())
                self.assertTrue((run_dir / "review_decisions.template.json").is_file())
                package = MODULE.read_json(run_dir / "review_bundle/review_package.json")
                self.assertEqual(len(package["candidates"]), 1)
                candidate = package["candidates"][0]
                self.assertEqual(candidate["reason_key"], "global_long_pause")
                self.assertTrue((candidate["review_requirements"] or {})["must_listen_to"])
                MODULE.write_json(
                    run_dir / "human_decisions.json",
                    {
                        "schema_version": "human-decisions-mvp-v1",
                        "package_id": package["package_id"],
                        "review_manifest_sha256": package["review_manifest_sha256"],
                        "reviewer": "Fixture Human",
                        "decisions": [
                            {
                                "candidate_id": candidate["candidate_id"],
                                "candidate_semantic_sha256": candidate["semantic_sha256"],
                                "decision": "accept",
                                "reviewer": "Fixture Human",
                                "decided_at": "2026-08-13T12:00:00Z",
                                "review_basis": "text_and_audio",
                                "feedback": "Fixture 备注：需要保留这段上下文。",
                                "listened_previews": {
                                    "original_sha256": candidate["previews"]["original_sha256"],
                                    "original_listened_at": "2026-08-13T12:00:01Z",
                                    "proposed_cut_sha256": candidate["previews"]["proposed_cut_sha256"],
                                    "proposed_cut_listened_at": "2026-08-13T12:00:02Z",
                                },
                            }
                        ],
                    },
                )
                post_memory_path = run_dir / "review_bundle/case_memory.json"
                original_post_memory = post_memory_path.read_bytes()
                tampered_post_memory = MODULE.read_json(post_memory_path)
                first_memory = next(iter(tampered_post_memory["candidate_memory"].values()))
                first_memory["summary"] = "tampered but still schema-valid"
                MODULE.write_json(post_memory_path, tampered_post_memory)
                with self.assertRaisesRegex(MODULE.DeliveryError, "case memory sidecar SHA drifted"):
                    MODULE.resume_after_review(run_dir, ffmpeg=str(ffmpeg))
                post_memory_path.write_bytes(original_post_memory)
                MODULE.resume_after_review(run_dir, ffmpeg=str(ffmpeg))
                self.assertEqual(
                    MODULE.read_json(run_dir / "state.json")["state"], "FINAL_QC_REQUIRED"
                )
                music_manifest = MODULE.read_json(run_dir / "music_manifest.json")
                self.assertEqual(music_manifest["music_template_id"], "reference-linear-v1")
                self.assertEqual(music_manifest["timing"]["voice_start_seconds"], 5.0)
                self.assertEqual(
                    music_manifest["variants"]["human_approved"]["voice_start_sample"],
                    240000,
                )
                qc = MODULE.read_json(run_dir / "qc_report.json")
                self.assertTrue(qc["transition_qc"]["required"])
                self.assertEqual(qc["transition_qc"]["status"], "PASS")
                for variant in ("human_approved", "machine_assisted_draft"):
                    report_path = run_dir / f"render_{variant}/transition_qc.json"
                    self.assertTrue(report_path.is_file())
                    report = MODULE.read_json(report_path)
                    self.assertEqual(
                        report["status"],
                        "OBJECTIVE_ANOMALY_RANKING_SUBJECTIVE_LISTENING_REQUIRED",
                    )
                    self.assertEqual(
                        qc["transition_qc"]["reports"][variant]["sha256"],
                        MODULE.sha256_file(report_path),
                    )
                removed_report = run_dir / "render_human_approved/transition_qc.json"
                report_bytes = removed_report.read_bytes()
                removed_report.unlink()
                self.assertIn(
                    "missing human_approved transition QC report",
                    MODULE.delivery_artifact_errors(run_dir),
                )
                removed_report.write_bytes(report_bytes)
                self.assertEqual(MODULE.delivery_artifact_errors(run_dir), [])
                MODULE.record_final_decision(
                    run_dir,
                    decision="human_approved_delivery",
                    reviewer="Fixture Human",
                    note="Fixture only: a real reviewer must make this decision for a real episode.",
                )
                self.assertEqual(
                    MODULE.read_json(run_dir / "state.json")["state"],
                    "DELIVERY_DECISION_RECORDED",
                )
                normalized = MODULE.read_json(run_dir / "human_decisions.json")
                self.assertEqual(normalized["candidate_feedback_count"], 1)
                self.assertEqual(normalized["decisions"][0]["feedback"], "Fixture 备注：需要保留这段上下文。")
                feedback_bundle = MODULE.read_json(run_dir / "feedback_bundle.json")
                self.assertEqual(feedback_bundle["candidate_feedback_count"], 1)
                self.assertEqual(feedback_bundle["candidate_feedback"][0]["feedback"], "Fixture 备注：需要保留这段上下文。")
                self.assertEqual(MODULE.delivery_artifact_errors(run_dir), [])
            finally:
                MODULE.RUNS_ROOT = previous["runs_root"]
                MODULE.MUSIC_SOURCE = previous["music_source"]
                MODULE.MUSIC_SHA256 = previous["music_sha"]
                MODULE.DEEPFILTER_DENOISE_SCRIPT = previous["denoise_script"]
                MODULE.P0_SCRIPT = previous["p0_script"]
                if test_run_dir.is_dir() and test_run_dir.parent == real_runs_root / "EPFIX":
                    shutil.rmtree(test_run_dir)
                if test_run_dir.parent.is_dir() and not any(test_run_dir.parent.iterdir()):
                    test_run_dir.parent.rmdir()

    def test_v12_promotion_records_scope_without_fabricating_item_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_run = root / "source"
            source_run.mkdir()
            # 24 seconds of master and zero seconds of speech are enough to prove
            # the 15s/3s/15s timing arithmetic without allocating a real episode.
            speech = source_run / "EP04-v4.speech-only.wav"
            write_pcm_wav(speech, frames=0)
            master = source_run / "EP04-v12.master.wav"
            write_pcm_wav(master, frames=24 * 48000)
            mp3 = source_run / "EP04-v12.master.mp3"
            mp3.write_bytes(master.read_bytes())
            (source_run / "EP04-v4.edl.json").write_text(
                json.dumps(
                    {
                        "sync_cuts_merged": [{"start_sample": 1000, "end_sample": 2000}],
                        "gates_by_track": {"track_01": []},
                    }
                ),
                encoding="utf-8",
            )
            (source_run / "loudnorm_report.json").write_text(
                json.dumps({"actual_output_pass2": {"output_i": "-16.0", "output_tp": "-1.0"}}),
                encoding="utf-8",
            )
            raw = root / "raw.wav"
            write_pcm_wav(raw)
            tracks_manifest = root / "tracks.json"
            tracks_manifest.write_text(
                json.dumps({"tracks": [{"track_id": "track_01", "label": "unit", "audio_path": str(raw)}]}),
                encoding="utf-8",
            )
            previous = MODULE.RUNS_ROOT
            previous_probe = MODULE.audio_probe
            MODULE.RUNS_ROOT = root / "runs"
            MODULE.audio_probe = lambda path: {"probe_tool": "test", "path": path.name}
            try:
                run_dir = MODULE.promote_v12(
                    episode_id="EPTEST",
                    run_id="EPTEST-promoted",
                    source_run=source_run,
                    source_tracks_manifest=tracks_manifest,
                    ffmpeg=None,
                )
                scope = json.loads((run_dir / "human_approval_scope.json").read_text(encoding="utf-8"))
                edl = json.loads((run_dir / "human_approved.edl.json").read_text(encoding="utf-8"))
                self.assertEqual(scope["approval_mode"], "human_whole_episode_audition")
                self.assertEqual(edl["decision_summary"]["per_candidate_human_labels"], 0)
                self.assertEqual(MODULE.read_json(run_dir / "state.json")["state"], "DELIVERY_DECISION_RECORDED")
                self.assertEqual(MODULE.delivery_artifact_errors(run_dir), [])
            finally:
                MODULE.RUNS_ROOT = previous
                MODULE.audio_probe = previous_probe


if __name__ == "__main__":
    unittest.main()
