#!/usr/bin/env python3
"""Run Claude Code in non-interactive mode with a machine-readable contract."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PREFERRED_CLAUDE_PATHS = (
    Path("~/.npm-global/bin/claude").expanduser(),
    Path("~/.local/node/bin/claude").expanduser(),
    Path("~/.local/bin/claude").expanduser(),
)

PROJECT_GUARDRAILS = """
The user granted full Claude Code tool permissions for coding work in the current
workspace. This does not authorize access to or transmission of real audio,
transcripts, edit candidates, human review decisions, or other confidential
company materials. Do not overwrite original audio, Champion code, release
masters, or hashed historical outputs. Never approve semantic audio edits on a
human's behalf. Stay inside the requested workspace unless the user explicitly
names another non-sensitive path. Follow the workspace CLAUDE.md.
""".strip()


@dataclass
class ProcessResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def locate_claude(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    elif os.environ.get("CLAUDE_BIN"):
        candidates.append(Path(os.environ["CLAUDE_BIN"]).expanduser())
    else:
        candidates.extend(PREFERRED_CLAUDE_PATHS)
        discovered = shutil.which("claude")
        if discovered:
            candidates.append(Path(discovered))

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates) or "PATH"
    raise FileNotFoundError(f"Claude Code executable not found; searched: {searched}")


def stop_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def execute(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> ProcessResult:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        stdout, stderr = process.communicate()
        return ProcessResult(argv, 124, stdout, stderr, True)
    return ProcessResult(argv, process.returncode, stdout, stderr)


def parse_result_message(stdout: str, output_format: str) -> dict[str, Any] | None:
    if output_format == "text":
        return None
    if output_format == "json":
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    final: dict[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") == "result":
            final = value
    return final


def result_failed(process: ProcessResult, output_format: str) -> bool:
    if process.timed_out or process.returncode != 0:
        return True
    if output_format == "text":
        return False
    message = parse_result_message(process.stdout, output_format)
    return message is None or bool(message.get("is_error"))


def build_run_argv(
    claude: Path,
    prompt: str,
    *,
    output_format: str,
    permission_profile: str,
    max_turns: int,
    max_budget_usd: float,
    model: str | None,
    persist_session: bool,
    safe_mode: bool,
    append_system_prompt: str | None,
) -> list[str]:
    argv = [
        str(claude),
        "-p",
        prompt,
        "--output-format",
        output_format,
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(max_budget_usd),
        "--no-chrome",
    ]
    if permission_profile == "full":
        argv.extend(
            [
                "--permission-mode",
                "bypassPermissions",
                "--dangerously-skip-permissions",
                "--tools",
                "default",
            ]
        )
    elif permission_profile == "readonly":
        argv.extend(
            [
                "--permission-mode",
                "dontAsk",
                "--tools",
                "Read,Glob,Grep",
                "--allowedTools",
                "Read,Glob,Grep",
                "--disallowedTools",
                "Bash,Edit,Write,WebFetch,WebSearch,mcp__*",
                "--strict-mcp-config",
            ]
        )
    else:
        argv.extend(
            [
                "--permission-mode",
                "dontAsk",
                "--tools",
                "",
                "--disallowedTools",
                "mcp__*",
                "--strict-mcp-config",
            ]
        )

    combined_prompt = PROJECT_GUARDRAILS
    if append_system_prompt:
        combined_prompt = f"{combined_prompt}\n\n{append_system_prompt.strip()}"
    argv.extend(["--append-system-prompt", combined_prompt])

    if not persist_session:
        argv.append("--no-session-persistence")
    if safe_mode:
        argv.append("--safe-mode")
    if model:
        argv.extend(["--model", model])
    if output_format == "stream-json":
        argv.append("--verbose")
    return argv


def read_prompt(value: str | None) -> str:
    if value:
        return value
    if sys.stdin.isatty():
        raise ValueError("provide a prompt argument or pipe a prompt on stdin")
    prompt = sys.stdin.read()
    if not prompt.strip():
        raise ValueError("prompt is empty")
    return prompt


def command_status(args: argparse.Namespace) -> int:
    claude = locate_claude(args.claude_bin)
    cwd = Path(args.cwd).expanduser().resolve()
    version = execute([str(claude), "--version"], cwd=cwd, timeout_seconds=15)
    auth = execute(
        [str(claude), "auth", "status"], cwd=cwd, timeout_seconds=15
    )
    auth_payload: dict[str, Any] | None = None
    try:
        value = json.loads(auth.stdout)
        if isinstance(value, dict):
            auth_payload = {
                key: value.get(key)
                for key in ("loggedIn", "authMethod", "apiProvider")
            }
    except json.JSONDecodeError:
        pass
    payload = {
        "claude_bin": str(claude),
        "version": version.stdout.strip(),
        "version_exit_code": version.returncode,
        "auth": auth_payload,
        "auth_exit_code": auth.returncode,
        "cwd": str(cwd),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if version.returncode == 0 and auth.returncode == 0 else 1


def run_prompt(
    args: argparse.Namespace,
    *,
    prompt: str,
    cwd: Path,
    safe_mode: bool | None = None,
) -> ProcessResult:
    claude = locate_claude(args.claude_bin)
    argv = build_run_argv(
        claude,
        prompt,
        output_format=args.output_format,
        permission_profile=args.permission_profile,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        model=args.model,
        persist_session=args.persist_session,
        safe_mode=args.safe_mode if safe_mode is None else safe_mode,
        append_system_prompt=args.append_system_prompt,
    )
    return execute(argv, cwd=cwd, timeout_seconds=args.timeout_seconds)


def emit_process_result(process: ProcessResult) -> None:
    if process.stdout:
        sys.stdout.write(process.stdout)
        if not process.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if process.stderr:
        sys.stderr.write(process.stderr)
        if not process.stderr.endswith("\n"):
            sys.stderr.write("\n")
    if process.timed_out:
        print("Claude Code exceeded the host timeout", file=sys.stderr)


def command_run(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f"working directory does not exist: {cwd}")
    process = run_prompt(args, prompt=read_prompt(args.prompt), cwd=cwd)
    emit_process_result(process)
    return 1 if result_failed(process, args.output_format) else 0


def command_probe(args: argparse.Namespace) -> int:
    if args.permission_profile != "full":
        raise ValueError("the full-access probe requires --permission-profile full")
    if args.output_format != "json":
        raise ValueError("the full-access probe requires --output-format json")
    with tempfile.TemporaryDirectory(prefix="claude-headless-probe-") as raw_dir:
        cwd = Path(raw_dir)
        target = cwd / "claude_full_access_probe.txt"
        prompt = (
            "Use a tool to create claude_full_access_probe.txt in the current "
            "working directory with exactly FULL_ACCESS_OK followed by a newline. "
            "Read it back, then reply with exactly HEADLESS_FULL_ACCESS_OK."
        )
        process = run_prompt(args, prompt=prompt, cwd=cwd, safe_mode=True)
        message = parse_result_message(process.stdout, args.output_format)
        canary_ok = target.is_file() and target.read_text(encoding="utf-8") == "FULL_ACCESS_OK\n"
        response_ok = bool(
            message
            and isinstance(message.get("result"), str)
            and message["result"].strip() == "HEADLESS_FULL_ACCESS_OK"
        )
        payload = {
            "connected": not result_failed(process, args.output_format),
            "full_access_write_read": canary_ok,
            "response_verified": response_ok,
            "claude_exit_code": process.returncode,
            "timed_out": process.timed_out,
            "session_id": message.get("session_id") if message else None,
            "result": message.get("result") if message else None,
            "is_error": message.get("is_error") if message else None,
            "terminal_reason": message.get("terminal_reason") if message else None,
            "total_cost_usd": message.get("total_cost_usd") if message else None,
            "num_turns": message.get("num_turns") if message else None,
            "api_error_status": message.get("api_error_status") if message else None,
            "errors": message.get("errors") if message else None,
            "stderr": process.stderr.strip() or None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["connected"] and canary_ok and response_ok else 1


def add_shared_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--permission-profile",
        choices=("full", "readonly", "none"),
        default="full",
        help="Claude tool permission profile; default: full",
    )
    parser.add_argument("--output-format", choices=("text", "json", "stream-json"), default="json")
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-budget-usd", type=float, default=1.0)
    parser.add_argument("--model")
    parser.add_argument("--persist-session", action="store_true")
    parser.add_argument("--safe-mode", action="store_true")
    parser.add_argument("--append-system-prompt")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-bin", help="override the Claude Code executable")
    parser.add_argument("--cwd", default=os.getcwd(), help="working directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show version and redacted auth status")
    status.set_defaults(handler=command_status)

    run = subparsers.add_parser("run", help="run one non-interactive Claude turn")
    run.add_argument("prompt", nargs="?")
    add_shared_run_options(run)
    run.set_defaults(handler=command_run)

    probe = subparsers.add_parser("probe", help="verify model, command, write, and read access")
    add_shared_run_options(probe)
    probe.set_defaults(handler=command_probe)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
