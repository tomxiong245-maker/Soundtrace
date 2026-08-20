#!/usr/bin/env python3
"""Refresh the media-free development benchmark evidence for one delivery run.

This is a small lifecycle wrapper around the three existing benchmark tools.
It never creates a semantic edit, EDL, or human decision.  It deliberately
keeps benchmark readiness separate from delivery ``verify``: a missing human
QA result must remain visible as ``NOT_MEASURED``, not block an otherwise
valid human-review or render stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark" / "editing-e2e-v1"
AUDIT_SCRIPT = BENCHMARK_ROOT / "sample_no_candidate_windows.py"
SCORECARD_SCRIPT = BENCHMARK_ROOT / "build_scorecard.py"
FEEDBACK_SCRIPT = BENCHMARK_ROOT / "mentor-feedback-regression-v1" / "build_catalog.py"

AUDIT_COUNT = 8
AUDIT_WINDOW_SECONDS = "25"
AUDIT_HANDLE_SECONDS = "5"
EVIDENCE_FILENAME = "benchmark_evidence.json"


class BenchmarkLifecycleError(RuntimeError):
    """A benchmark evidence error; it never authorizes or changes an edit."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkLifecycleError(f"invalid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise BenchmarkLifecycleError(f"JSON object required: {path}")
    return value


def project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError as error:
        raise BenchmarkLifecycleError(f"path must stay inside project root: {path}") from error


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def require_run(run_dir: Path) -> tuple[dict[str, Any], str]:
    run_dir = run_dir.expanduser().resolve()
    try:
        run_dir.relative_to(PROJECT_ROOT.resolve())
    except ValueError as error:
        raise BenchmarkLifecycleError("run_dir must stay inside the local project") from error
    identity_path = run_dir / "run_identity.json"
    state_path = run_dir / "state.json"
    if not identity_path.is_file() or not state_path.is_file():
        raise BenchmarkLifecycleError("run_dir must contain run_identity.json and state.json")
    identity = read_object(identity_path)
    run_id = identity.get("run_id")
    episode_id = identity.get("episode_id")
    if not isinstance(run_id, str) or not run_id or not isinstance(episode_id, str) or not episode_id:
        raise BenchmarkLifecycleError("run_identity.json must contain non-empty episode_id and run_id")
    return identity, sha256_file(identity_path)


def command_result(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def run_required(command: list[str], label: str) -> dict[str, Any]:
    result = command_result(command)
    if result["returncode"]:
        tail = (result["stderr"] or result["stdout"])[-1600:].strip()
        raise BenchmarkLifecycleError(f"{label} failed: {tail}")
    return result


def paths_for(run_dir: Path, run_id: str) -> dict[str, Path]:
    audit_dir = BENCHMARK_ROOT / "audits" / f"{run_id}.no-candidate-audit-v1"
    return {
        "audit_dir": audit_dir,
        "audit_json": audit_dir / "no_candidate_windows.json",
        "scorecard_dir": BENCHMARK_ROOT / "scorecards" / f"{run_id}.current",
        "scorecard_json": BENCHMARK_ROOT / "scorecards" / f"{run_id}.current" / "scorecard.json",
        "scorecard_markdown": BENCHMARK_ROOT / "scorecards" / f"{run_id}.current" / "SCORECARD.md",
        "catalog": BENCHMARK_ROOT / "mentor-feedback-regression-v1" / "catalog.json",
        "evidence": run_dir / EVIDENCE_FILENAME,
    }


def existing_audit_parameters(
    audit_path: Path,
    *,
    run_id: str,
    run_identity_sha256: str,
) -> tuple[str, int, str, str]:
    """Read the frozen request parameters of an existing audit without changing it.

    The audit sampler intentionally allows its seed to be chosen by the caller.
    A later lifecycle refresh must therefore use the parameters that created the
    already-existing evidence rather than silently deriving a new seed from a
    newer run-id naming convention.  ``--check`` below then re-derives the
    complete invariant document and proves that these parameters still bind to
    the current run inputs.
    """

    document = read_object(audit_path)
    if document.get("schema_version") != "no-candidate-window-audit-v1":
        raise BenchmarkLifecycleError(
            "existing no-candidate audit schema is unsupported; refusing to replace it"
        )
    provenance = document.get("provenance")
    parameters = document.get("parameters")
    if not isinstance(provenance, dict) or not isinstance(parameters, dict):
        raise BenchmarkLifecycleError(
            "existing no-candidate audit must contain provenance and parameters objects"
        )
    if provenance.get("run_id") != run_id:
        raise BenchmarkLifecycleError(
            "existing no-candidate audit belongs to a different run; refusing to replace it"
        )
    if provenance.get("run_identity_sha256") != run_identity_sha256:
        raise BenchmarkLifecycleError(
            "existing no-candidate audit run identity SHA differs; refusing to replace it"
        )

    seed = parameters.get("seed")
    count = parameters.get("count")
    window_seconds = parameters.get("window_seconds")
    handle_seconds = parameters.get("candidate_handle_seconds")
    if not isinstance(seed, str) or not seed:
        raise BenchmarkLifecycleError("existing no-candidate audit has invalid parameters.seed")
    if type(count) is not int:
        raise BenchmarkLifecycleError("existing no-candidate audit has invalid parameters.count")
    if not isinstance(window_seconds, str) or not window_seconds:
        raise BenchmarkLifecycleError(
            "existing no-candidate audit has invalid parameters.window_seconds"
        )
    if not isinstance(handle_seconds, str) or not handle_seconds:
        raise BenchmarkLifecycleError(
            "existing no-candidate audit has invalid parameters.candidate_handle_seconds"
        )
    return seed, count, window_seconds, handle_seconds


def refresh(run_dir: Path, *, phase: str, python: str) -> dict[str, Any]:
    """Create or strictly validate an audit, then refresh the derived scorecard.

    Existing audit files are never recreated: their deterministic identity is
    validated while the three human-result fields remain untouched.
    """

    identity, identity_sha = require_run(run_dir)
    run_dir = run_dir.expanduser().resolve()
    run_id = str(identity["run_id"])
    episode_id = str(identity["episode_id"])
    paths = paths_for(run_dir, run_id)
    default_seed = f"{run_id}-no-candidate-audit-v1"

    try:
        for required in (AUDIT_SCRIPT, SCORECARD_SCRIPT, FEEDBACK_SCRIPT):
            if not required.is_file():
                raise BenchmarkLifecycleError(f"required local benchmark tool is missing: {required}")

        feedback = run_required(
            [python, str(FEEDBACK_SCRIPT), "--check"],
            "pinned Mentor feedback regression validation",
        )

        if paths["audit_json"].is_file():
            seed, audit_count, audit_window_seconds, audit_handle_seconds = existing_audit_parameters(
                paths["audit_json"],
                run_id=run_id,
                run_identity_sha256=identity_sha,
            )
        else:
            seed = default_seed
            audit_count = AUDIT_COUNT
            audit_window_seconds = AUDIT_WINDOW_SECONDS
            audit_handle_seconds = AUDIT_HANDLE_SECONDS

        audit_base = [
            python,
            str(AUDIT_SCRIPT),
            "--run-dir",
            str(run_dir),
            "--output-dir",
            str(paths["audit_dir"]),
            "--seed",
            seed,
            "--count",
            str(audit_count),
            "--window-seconds",
            audit_window_seconds,
            "--handle-seconds",
            audit_handle_seconds,
        ]
        if paths["audit_json"].is_file():
            audit = run_required(audit_base + ["--check"], "existing no-candidate audit validation")
            audit_status = "VALIDATED_EXISTING_HUMAN_RESULTS_PRESERVED"
        elif paths["audit_dir"].exists():
            raise BenchmarkLifecycleError(
                "no-candidate audit directory exists without its required JSON evidence; refusing to overwrite it"
            )
        else:
            audit = run_required(audit_base, "no-candidate audit creation")
            audit_status = "CREATED_PENDING_HUMAN_LISTENING"

        scorecard = run_required(
            [
                python,
                str(SCORECARD_SCRIPT),
                "--build",
                "--replace",
                "--run-dir",
                str(run_dir),
                "--no-candidate-audit",
                str(paths["audit_json"]),
                "--feedback-catalog",
                str(paths["catalog"]),
                "--output-dir",
                str(paths["scorecard_dir"]),
            ],
            "development scorecard refresh",
        )
        scorecard_document = read_object(paths["scorecard_json"])
        scorecard_status = (scorecard_document.get("scorecard_status") or {}).get("status")
        if not isinstance(scorecard_status, str) or not scorecard_status:
            raise BenchmarkLifecycleError("refreshed scorecard has no explicit scorecard status")

        evidence = {
            "schema_version": "delivery-development-benchmark-evidence-v1",
            "episode_id": episode_id,
            "run_id": run_id,
            "run_identity_sha256": identity_sha,
            "refreshed_at": utc_now(),
            "phase": phase,
            "status": "PASS",
            "delivery_effect": "NON_BLOCKING_DEVELOPMENT_EVIDENCE_ONLY",
            "human_decision_authority": "none; this artifact cannot create accept/reject, EDL, automatic deletion, Champion promotion, or release approval",
            "media_boundary": "this wrapper calls only JSON/Markdown benchmark tools; it does not open, decode, copy, hash, or upload source or preview media",
            "audit": {
                "status": audit_status,
                "relpath": project_relative(paths["audit_json"]),
                "sha256": sha256_file(paths["audit_json"]),
                "seed": seed,
                "count": audit_count,
                "window_seconds": audit_window_seconds,
                "handle_seconds": audit_handle_seconds,
                "command_result": audit,
            },
            "feedback_regression": {
                "status": "VALIDATED_PINNED_DEVELOPMENT_CATALOG",
                "relpath": project_relative(paths["catalog"]),
                "sha256": sha256_file(paths["catalog"]),
                "command_result": feedback,
            },
            "scorecard": {
                "status": scorecard_status,
                "quality_pass": bool((scorecard_document.get("scorecard_status") or {}).get("quality_pass")),
                "json_relpath": project_relative(paths["scorecard_json"]),
                "json_sha256": sha256_file(paths["scorecard_json"]),
                "markdown_relpath": project_relative(paths["scorecard_markdown"]),
                "markdown_sha256": sha256_file(paths["scorecard_markdown"]),
                "command_result": scorecard,
            },
            "next_rule": "NOT_MEASURED never means no problem, pass, or permission to further reduce human review",
        }
    except Exception as error:
        evidence = {
            "schema_version": "delivery-development-benchmark-evidence-v1",
            "episode_id": episode_id,
            "run_id": run_id,
            "run_identity_sha256": identity_sha,
            "refreshed_at": utc_now(),
            "phase": phase,
            "status": "BENCHMARK_EVIDENCE_UNAVAILABLE",
            "delivery_effect": "NON_BLOCKING_DEVELOPMENT_EVIDENCE_ONLY",
            "error": str(error),
            "next_rule": "fix benchmark evidence separately; do not invent human results or change an EDL to make this pass",
        }
        atomic_write_json(paths["evidence"], evidence)
        if isinstance(error, BenchmarkLifecycleError):
            raise
        raise BenchmarkLifecycleError(str(error)) from error

    atomic_write_json(paths["evidence"], evidence)
    return evidence


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-dir", type=Path, required=True)
    result.add_argument(
        "--phase",
        default="manual",
        choices=("candidate_frozen", "review_package_refreshed", "post_render", "final_decision", "manual"),
        help="where in the delivery lifecycle this non-blocking evidence refresh occurred",
    )
    result.add_argument("--python", default=sys.executable, help="Python used for the local JSON-only benchmark tools")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        evidence = refresh(args.run_dir, phase=args.phase, python=args.python)
    except BenchmarkLifecycleError as error:
        print(f"BENCHMARK_EVIDENCE_UNAVAILABLE: {error}", file=sys.stderr)
        return 2
    scorecard = evidence["scorecard"]
    print(
        "PASS: refreshed non-blocking development benchmark "
        f"(run={evidence['run_id']}; phase={evidence['phase']}; "
        f"scorecard={scorecard['status']}; quality_pass={scorecard['quality_pass']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
