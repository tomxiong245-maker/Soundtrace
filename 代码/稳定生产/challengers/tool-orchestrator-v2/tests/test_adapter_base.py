"""Contract tests for tool-orchestrator-v2 AdapterBase.

These tests exercise the abstract base with a fake in-memory adapter subclass
that echoes inputs to a JSON file. No real tool script is called.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))

import _adapter_base as ab  # noqa: E402


class _EchoAdapter(ab.AdapterBase):
    """Fake adapter that writes {"input_dict": inputs} to output.json."""

    contract = {
        "adapter_id": "echo-adapter-v1",
        "tool_name": "echo_tool",
        "adapter_version": "v1",
        "wraps_script": "TESTS_FAKE_SCRIPT.py",  # will be created by tests
        "reads_only": True,
        "inputs_schema": {"required": ["message", "output_json"]},
        "outputs_schema": {},
        "provenance_fields": ["exit_code"],
        "timeout_seconds": 10,
    }

    def __init__(self, script_path: Path):
        self._script = script_path

    def _resolve_script_path(self) -> Path:
        return self._script

    def _build_command(self, inputs, run_dir):
        return [sys.executable, str(self._script), inputs["message"], inputs["output_json"]]

    def _expected_outputs(self, inputs, run_dir):
        return {"echo": Path(inputs["output_json"])}


class _WriteAdapter(_EchoAdapter):
    contract = {
        **_EchoAdapter.contract,
        "adapter_id": "write-adapter-v1",
        "reads_only": False,
        "write_policy": {
            "policy_id": "test-write-policy-v1",
            "allowed_output_roots": ["ignored-because-requires-run-dir"],
            "requires_run_dir": True,
        },
    }


class AdapterBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        # Create fake echo script
        self.script = self.tmp_path / "TESTS_FAKE_SCRIPT.py"
        self.script.write_text(
            "import sys, json, pathlib\n"
            "msg=sys.argv[1]; out=sys.argv[2]\n"
            "pathlib.Path(out).write_text(json.dumps({'input': msg}))\n"
        )
        self.run_dir = self.tmp_path / "run"
        self.run_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_returns_plan_without_execution(self):
        adapter = _EchoAdapter(self.script)
        out_json = self.run_dir / "out.json"
        plan = adapter.dry_run_plan({"message": "hi", "output_json": str(out_json)}, self.run_dir)
        self.assertEqual(plan["adapter_id"], "echo-adapter-v1")
        self.assertEqual(plan["tool_name"], "echo_tool")
        self.assertTrue(plan["reads_only"])
        self.assertIn("hi", plan["command"])
        self.assertEqual(plan["expected_outputs"]["echo"], str(out_json))
        # Dry run must not have created the output
        self.assertFalse(out_json.exists())

    def test_invoke_success_writes_provenance(self):
        adapter = _EchoAdapter(self.script)
        out_json = self.run_dir / "out.json"
        prov = adapter.invoke({"message": "hello", "output_json": str(out_json)}, self.run_dir)
        self.assertEqual(prov.exit_code, 0)
        self.assertIsNone(prov.error)
        self.assertEqual(prov.tool_name, "echo_tool")
        self.assertTrue(out_json.exists())
        self.assertIn("echo", prov.output_sha_map)
        self.assertNotEqual(prov.output_sha_map["echo"], "")
        prov_path = self.run_dir / "echo-adapter-v1.provenance.json"
        self.assertTrue(prov_path.exists())
        prov_json = json.loads(prov_path.read_text())
        self.assertEqual(prov_json["exit_code"], 0)

    def test_missing_required_input_raises(self):
        adapter = _EchoAdapter(self.script)
        with self.assertRaises(ab.InputsValidationError):
            adapter.validate_inputs({"message": "hi"})

    def test_wraps_script_sha_drift_is_fail_closed(self):
        adapter = _EchoAdapter(self.script)
        # Compute current SHA and mutate contract to declare a wrong one
        adapter.contract = dict(adapter.contract)
        adapter.contract["wraps_script_sha256"] = "0" * 64
        with self.assertRaises(ab.AdapterError):
            adapter.invoke(
                {"message": "hi", "output_json": str(self.run_dir / "out.json")},
                self.run_dir,
            )

    def test_write_adapter_requires_policy_and_scope(self):
        adapter = _WriteAdapter(self.script)
        out_json = self.run_dir / "out.json"
        with self.assertRaises(ab.WritesPolicyError):
            adapter.invoke({"message": "hi", "output_json": str(out_json)}, self.run_dir)
        # With correct policy + scope hash it should proceed
        scope = ab.compute_writes_scope_hash([str(self.run_dir)])
        prov = adapter.invoke(
            {"message": "hi", "output_json": str(out_json)},
            self.run_dir,
            writes_policy_id="test-write-policy-v1",
            writes_scope_hash=scope,
        )
        self.assertEqual(prov.exit_code, 0)

    def test_write_adapter_wrong_scope_hash_fails(self):
        adapter = _WriteAdapter(self.script)
        with self.assertRaises(ab.WritesPolicyError):
            adapter.invoke(
                {"message": "hi", "output_json": str(self.run_dir / "out.json")},
                self.run_dir,
                writes_policy_id="test-write-policy-v1",
                writes_scope_hash="deadbeef" * 8,
            )

    def test_verify_outputs_catches_missing_output(self):
        adapter = _EchoAdapter(self.script)
        # Replace the fake script with one that writes nothing
        broken = self.tmp_path / "BROKEN.py"
        broken.write_text("import sys; sys.exit(0)\n")
        broken_adapter = _EchoAdapter(broken)
        prov = broken_adapter.invoke(
            {"message": "hi", "output_json": str(self.run_dir / "never.json")},
            self.run_dir,
        )
        self.assertIsNotNone(prov.error)
        self.assertIn("verify_outputs failed", prov.error)

    def test_verify_outputs_catches_nonzero_exit(self):
        broken = self.tmp_path / "FAIL.py"
        broken.write_text("import sys; sys.exit(7)\n")
        adapter = _EchoAdapter(broken)
        prov = adapter.invoke(
            {"message": "hi", "output_json": str(self.run_dir / "out.json")},
            self.run_dir,
        )
        self.assertEqual(prov.exit_code, 7)
        self.assertIn("exited 7", prov.error)


if __name__ == "__main__":
    unittest.main()
