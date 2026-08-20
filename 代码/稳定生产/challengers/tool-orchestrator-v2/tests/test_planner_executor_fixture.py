"""End-to-end fixture test for tool-orchestrator-v2 planner + executor.

Uses a synthetic tiny WAV + fake tool scripts so the flow can be verified without
touching real audio or Champion scripts. Every wrapped tool runs successfully;
provenance and execution manifest are written to a temp run_dir.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/orchestrator_patch"))
sys.path.insert(0, str(PROJECT_ROOT / "稳定生产/challengers/tool-orchestrator-v2/adapters"))

import planner_v2  # noqa: E402
import executor_v2  # noqa: E402
import _adapter_base as ab  # noqa: E402
from generic_script_adapter import load_registry  # noqa: E402


def _write_tiny_wav(path: Path, seconds: float = 0.1, rate: int = 48000):
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % n, *([0] * n)))


def _fake_registry(tmp_root: Path) -> Path:
    """Build a fake registry.json that wraps two harmless echo scripts:
    a fake inspect_audio and a fake estimate_sync. Both write JSON output.
    """
    fake_dir = tmp_root / "fake_scripts"
    fake_dir.mkdir()
    inspect = fake_dir / "fake_inspect.py"
    inspect.write_text(
        "import sys, json, pathlib, wave\n"
        "wav_path=sys.argv[1]; out=sys.argv[2]\n"
        "pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)\n"
        "with wave.open(wav_path) as w:\n"
        "  info={'channels':w.getnchannels(),'rate':w.getframerate(),'frames':w.getnframes()}\n"
        "pathlib.Path(out).write_text(json.dumps(info))\n",
        encoding="utf-8",
    )
    sync = fake_dir / "fake_sync.py"
    sync.write_text(
        "import sys, json, pathlib\n"
        "a=sys.argv[1]; b=sys.argv[2]; out=sys.argv[3]\n"
        "pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)\n"
        "pathlib.Path(out).write_text(json.dumps({'a':a,'b':b,'offset_ms':0.0}))\n",
        encoding="utf-8",
    )

    scope = ab.compute_writes_scope_hash([str(tmp_root)])
    registry = {
        "schema_version": "tool-orchestrator-v2.registry.v1",
        "adapters": [
            {
                "contract": {
                    "adapter_id": "inspect-audio-v2",
                    "tool_name": "inspect_audio",
                    "adapter_version": "v2",
                    "wraps_script": str(inspect),
                    "reads_only": True,
                    "inputs_schema": {"required": ["input_wav", "output_json"]},
                    "outputs_schema": {"required": ["inspection"]},
                    "provenance_fields": ["exit_code"],
                    "timeout_seconds": 60,
                },
                "command_template": ["python3", "{script}", "{input_wav}", "{output_json}"],
                "outputs_template": {"inspection": "{output_json}"},
            },
            {
                "contract": {
                    "adapter_id": "estimate-sync-v2",
                    "tool_name": "estimate_sync",
                    "adapter_version": "v2",
                    "wraps_script": str(sync),
                    "reads_only": True,
                    "inputs_schema": {"required": ["track_a", "track_b", "output_json"]},
                    "outputs_schema": {"required": ["sync"]},
                    "provenance_fields": ["exit_code"],
                    "timeout_seconds": 60,
                },
                "command_template": ["python3", "{script}", "{track_a}", "{track_b}", "{output_json}"],
                "outputs_template": {"sync": "{output_json}"},
            },
        ],
    }
    path = tmp_root / "fake_registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    return path


class PlannerExecutorFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "runs" / "EPFIX-tool-orch-v2"
        self.run_dir.mkdir(parents=True)

        # Create 3 tiny WAV tracks and record their SHAs for the episode config
        self.track_paths = []
        for i in range(3):
            p = self.root / f"track_{i+1}.wav"
            _write_tiny_wav(p)
            self.track_paths.append(p)

        self.registry = _fake_registry(self.root)

        self.episode_config = {
            "episode_id": "EPFIX",
            "tracks": [
                {"relpath": str(p.relative_to(self.root.parent)), "sha256": ab.sha256_file(p)}
                for p in self.track_paths
            ],
        }
        self.policy_bindings = {
            "music_template_id": "reference-linear-v1",
            "release_spec_id": "reference-linear-v1",
            "candidate_rules_id": "filler-global-pause-v18",
            "editing_preference_id": "editing-preference-profile-v15-draft",
            "guards_policy_id": "editing-policy-guards-v1",
            "autocut_policy_id": "NOT_APPROVED",
            "signed_authorization": None,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _build_plan(self):
        plan = planner_v2.build_plan(
            self.episode_config,
            self.policy_bindings,
            self.registry,
            plan_id="plan-EPFIX-test",
            run_id=self.run_dir.name,
            run_dir=self.run_dir,
        )
        # Rewrite absolute paths in step.inputs to use our synthetic tracks
        for step in plan["steps"]:
            if "input_wav" in step["inputs"]:
                idx = int(step["step_id"].split("-")[-1])
                step["inputs"]["input_wav"] = str(self.track_paths[idx - 1])
            if "track_a" in step["inputs"]:
                step["inputs"]["track_a"] = str(self.track_paths[0])
            if "track_b" in step["inputs"]:
                step["inputs"]["track_b"] = str(self.track_paths[1])
        (self.run_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan

    def test_planner_emits_expected_step_count(self):
        plan = self._build_plan()
        # 3 inspect steps + 1 sync step
        self.assertEqual(len(plan["steps"]), 4)
        self.assertEqual(plan["schema_version"], "delivery-plan-v1")
        self.assertIn("planner_source_sha256", plan["created_by"])

    def test_executor_dry_run_produces_plans_no_outputs(self):
        self._build_plan()
        manifest = executor_v2.execute_plan(
            self.run_dir / "plan.json",
            run_dir=self.run_dir,
            registry_path=self.registry,
            dry_run=True,
        )
        for r in manifest["results"]:
            self.assertEqual(r["status"], "DRY_RUN")
        # No inspection outputs created
        self.assertFalse((self.run_dir / "01_inspect/track_01.inspection.json").exists())

    def test_executor_runs_full_chain_writes_provenance(self):
        self._build_plan()
        manifest = executor_v2.execute_plan(
            self.run_dir / "plan.json",
            run_dir=self.run_dir,
            registry_path=self.registry,
            dry_run=False,
        )
        statuses = [r["status"] for r in manifest["results"]]
        self.assertTrue(all(s == "OK" for s in statuses), f"statuses={statuses}, manifest={manifest}")
        for i in range(1, 4):
            self.assertTrue((self.run_dir / f"01_inspect/track_{i:02d}.inspection.json").exists())
        self.assertTrue((self.run_dir / "02_sync/sync.json").exists())
        # provenance files exist
        prov1 = self.run_dir / "inspect-audio-v2.provenance.json"
        self.assertTrue(prov1.exists())
        prov1_data = json.loads(prov1.read_text())
        self.assertEqual(prov1_data["exit_code"], 0)

    def test_executor_stops_at_first_failure(self):
        plan = self._build_plan()
        # Sabotage first inspect step to point at nonexistent wav
        plan["steps"][0]["inputs"]["input_wav"] = "/no/such/track.wav"
        (self.run_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        manifest = executor_v2.execute_plan(
            self.run_dir / "plan.json",
            run_dir=self.run_dir,
            registry_path=self.registry,
            dry_run=False,
        )
        # First step fails; remaining steps not executed
        self.assertEqual(manifest["results"][0]["status"], "FAILED")
        self.assertEqual(len(manifest["results"]), 1, f"expected fail-fast, got {manifest}")

    def test_topological_order_detects_cycle(self):
        with self.assertRaises(ValueError):
            executor_v2.topological_order([
                {"step_id": "A", "depends_on": ["B"]},
                {"step_id": "B", "depends_on": ["A"]},
            ])


if __name__ == "__main__":
    unittest.main()
