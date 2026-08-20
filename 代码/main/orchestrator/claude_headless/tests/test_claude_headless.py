from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "claude_headless.py"
SPEC = importlib.util.spec_from_file_location("claude_headless", SCRIPT)
assert SPEC and SPEC.loader
claude_headless = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = claude_headless
SPEC.loader.exec_module(claude_headless)


class ClaudeHeadlessTests(unittest.TestCase):
    def test_locate_claude_honors_explicit_binary(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            binary = Path(raw_dir) / "claude"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)
            self.assertEqual(claude_headless.locate_claude(str(binary)), binary.resolve())

    def test_full_profile_enables_all_tools_and_bypass(self) -> None:
        argv = claude_headless.build_run_argv(
            Path("/tmp/claude"),
            "test prompt",
            output_format="json",
            permission_profile="full",
            max_turns=3,
            max_budget_usd=0.25,
            model=None,
            persist_session=False,
            safe_mode=False,
            append_system_prompt=None,
        )
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertIn("bypassPermissions", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "default")
        self.assertIn("--no-session-persistence", argv)
        self.assertIn(claude_headless.PROJECT_GUARDRAILS, argv)

    def test_json_is_error_overrides_zero_exit_code(self) -> None:
        process = claude_headless.ProcessResult(
            argv=["claude"],
            returncode=0,
            stdout=json.dumps({"type": "result", "is_error": True}),
            stderr="",
        )
        self.assertTrue(claude_headless.result_failed(process, "json"))

    def test_missing_json_result_fails_closed(self) -> None:
        process = claude_headless.ProcessResult(
            argv=["claude"], returncode=0, stdout="", stderr=""
        )
        self.assertTrue(claude_headless.result_failed(process, "json"))

    def test_readonly_profile_disables_mutating_and_mcp_tools(self) -> None:
        argv = claude_headless.build_run_argv(
            Path("/tmp/claude"),
            "test prompt",
            output_format="json",
            permission_profile="readonly",
            max_turns=3,
            max_budget_usd=0.25,
            model=None,
            persist_session=False,
            safe_mode=False,
            append_system_prompt=None,
        )
        denied = argv[argv.index("--disallowedTools") + 1]
        self.assertIn("Bash", denied)
        self.assertIn("mcp__*", denied)
        self.assertIn("--strict-mcp-config", argv)

    def test_stream_json_uses_last_result_message(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps({"type": "result", "is_error": False, "result": "ok"}),
            ]
        )
        parsed = claude_headless.parse_result_message(stdout, "stream-json")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["result"], "ok")


if __name__ == "__main__":
    unittest.main()
