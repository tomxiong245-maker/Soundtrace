#!/usr/bin/env python3
"""tool-orchestrator-v1 runner.

Bridges `main/tools/tools.json` and a per-episode plan to real script calls,
stops at HUMAN_REVIEW_REQUIRED and never runs post-review tools.

See SCHEMA.md for episode config and plan fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from registry_validator import validate_registry  # type: ignore

RUNNER_VERSION = "tool-orchestrator-v1/0.2"
STATES = {
    "CREATED",
    "PLAN_FROZEN",
    "RUNNING",
    "HUMAN_REVIEW_REQUIRED",
    "STOPPED_AT_CHECKPOINT",
    "COMPLETED_PRE_REVIEW",
    "FAILED",
}


class RunnerError(RuntimeError):
    pass


# ---------------- utilities ----------------

def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_under(path: Path, parent: Path, label: str) -> Path:
    """Resolve a path and reject lexical or symlink escapes from parent."""
    resolved = path.resolve()
    root = parent.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RunnerError(f"{label} escapes required root {root}: {resolved}") from exc
    return resolved


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_json(p: Path) -> Any:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_atomic(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, p)


def _project_root_from(registry_path: Path) -> Path:
    # Registry canonical layout: <project>/main/tools/tools.json → parents[2].
    # If someone points at a challenger-local registry, we cannot infer the
    # project root reliably; callers must pass --project-root explicitly.
    return registry_path.resolve().parents[2]


# ---------------- config / plan ----------------

def load_and_validate_config(config_path: Path) -> dict[str, Any]:
    cfg = _load_json(config_path)
    for k in ("episode_id", "tracks", "steps", "human_review_after"):
        if k not in cfg:
            raise RunnerError(f"config missing required key: {k}")
    if not cfg["tracks"]:
        raise RunnerError("config.tracks must be non-empty")
    if not cfg["steps"]:
        raise RunnerError("config.steps must be non-empty")
    track_ids = set()
    for t in cfg["tracks"]:
        for k in ("track_id", "label", "input_path", "sample_rate", "channel_count", "duration_seconds", "sha256"):
            if k not in t:
                raise RunnerError(f"track missing key: {k}")
        if t["track_id"] in track_ids:
            raise RunnerError(f"duplicate track_id: {t['track_id']}")
        track_ids.add(t["track_id"])
    step_ids = set()
    for s in cfg["steps"]:
        for k in ("step_id", "tool", "phase", "params"):
            if k not in s:
                raise RunnerError(f"step missing key: {k}")
        if s["step_id"] in step_ids:
            raise RunnerError(f"duplicate step_id: {s['step_id']}")
        step_ids.add(s["step_id"])
        if s["phase"] not in {"pre_review"}:
            raise RunnerError(
                f"step {s['step_id']}: phase={s['phase']!r} not allowed; only pre_review is enabled in this challenger"
            )
    hr = cfg["human_review_after"]
    if not isinstance(hr, str) or not hr:
        raise RunnerError("human_review_after must name a pre_review step; this challenger cannot complete without a human gate")
    if hr not in step_ids:
        raise RunnerError(f"human_review_after={hr!r} does not match any step_id")
    return cfg


def _resolve_track_field(cfg: dict, ref: str) -> Any:
    # @track:<track_id>.<field>
    body = ref[len("@track:"):]
    tid, _, field = body.partition(".")
    for t in cfg["tracks"]:
        if t["track_id"] == tid:
            if field not in t:
                raise RunnerError(f"track {tid!r} has no field {field!r}")
            return t[field]
    raise RunnerError(f"unknown track_id in reference: {tid!r}")


def _resolve_param(cfg: dict, run_dir: Path, project_root: Path, value: Any) -> Any:
    if isinstance(value, list):
        return [_resolve_param(cfg, run_dir, project_root, v) for v in value]
    if not isinstance(value, str):
        raise RunnerError(f"param value must be string or list of strings, got {type(value).__name__}")
    if value.startswith("@track:"):
        resolved = _resolve_track_field(cfg, value)
        if not isinstance(resolved, str):
            raise RunnerError(f"track field {value!r} is not a string")
        p = Path(resolved)
        if not p.is_absolute():
            p = (project_root / p).resolve()
        return str(p)
    if value.startswith("run:"):
        sub = value[len("run:"):]
        if not sub or Path(sub).is_absolute() or ".." in Path(sub).parts:
            raise RunnerError(f"run: subpath must be safe relative, got {sub!r}")
        return str(_require_under(run_dir / sub, run_dir, "run: path"))
    if value.startswith("abs:"):
        raw = value[len("abs:"):]
        p = Path(raw).resolve()
        return str(_require_under(p, project_root, "abs: path"))
    # literal
    return value


def _load_registry_frozen(registry_path: Path, project_root: Path) -> tuple[dict, str]:
    reg = _load_json(registry_path)
    report = validate_registry(reg, project_root=project_root, require_scripts=True)
    if not report["ok"]:
        raise RunnerError(
            "registry failed static validation: " + "; ".join(report["errors"])
        )
    sha = _sha256_of(registry_path)
    tools_by_name = {t["name"]: t for t in reg["tools"]}
    reg["_tools_by_name"] = tools_by_name
    return reg, sha


def build_plan(
    cfg: dict[str, Any],
    registry_path: Path,
    run_dir: Path,
    project_root: Path | None = None,
) -> dict[str, Any]:
    if project_root is None:
        project_root = _project_root_from(registry_path)
    project_root = project_root.resolve()
    reg, registry_sha = _load_registry_frozen(registry_path, project_root)

    # Verify each track's SHA (if input_path exists).
    resolved_tracks_sha = {}
    for t in cfg["tracks"]:
        p = Path(t["input_path"])
        if not p.is_absolute():
            p = project_root / p
        if not p.exists():
            raise RunnerError(f"track {t['track_id']!r} input not found: {p}")
        measured = _sha256_of(p)
        if measured != t["sha256"]:
            raise RunnerError(
                f"track {t['track_id']!r} sha mismatch: expected {t['sha256']} got {measured}"
            )
        resolved_tracks_sha[t["track_id"]] = measured

    scripts_root = _require_under(project_root / reg["scripts_root"], project_root, "scripts_root")

    plan_steps = []
    for s in cfg["steps"]:
        tool = reg["_tools_by_name"].get(s["tool"])
        if tool is None:
            raise RunnerError(f"step {s['step_id']}: unknown tool {s['tool']!r}")
        if not bool(tool.get("reads_only", False)):
            raise RunnerError(
                f"step {s['step_id']}: tool {tool['name']!r} is not reads_only; "
                "this challenger only permits safe pre-review tools"
            )
        declared_params = list(tool["params"])
        supplied = s["params"]
        # Every supplied key must be declared; missing keys are allowed only if
        # the tool truly doesn't need them (rare) — we conservatively require all.
        for key in supplied:
            if key not in declared_params:
                raise RunnerError(
                    f"step {s['step_id']}: param {key!r} not declared by tool {tool['name']!r}"
                )
        missing = [k for k in declared_params if k not in supplied]
        if missing:
            raise RunnerError(
                f"step {s['step_id']}: missing params {missing}"
            )
        script_path = _require_under(scripts_root / tool["script"], scripts_root, f"tool {tool['name']!r} script")
        if not script_path.is_file():
            raise RunnerError(f"tool {tool['name']!r} script missing: {script_path}")
        resolved_params = {
            k: _resolve_param(cfg, run_dir, project_root, v) for k, v in supplied.items()
        }
        for key, raw_value in supplied.items():
            if key.startswith("output"):
                if not isinstance(raw_value, str) or not raw_value.startswith("run:"):
                    raise RunnerError(
                        f"step {s['step_id']}: output param {key!r} must use a run: path"
                    )
                _require_under(Path(resolved_params[key]), run_dir, f"step {s['step_id']} output {key!r}")
        plan_steps.append(
            {
                "step_id": s["step_id"],
                "tool": tool["name"],
                "reads_only": bool(tool.get("reads_only", False)),
                "phase": s["phase"],
                "script": tool["script"],
                "script_path": str(script_path),
                "script_sha256": _sha256_of(script_path),
                "resolved_params": resolved_params,
                "raw_params": dict(supplied),
            }
        )

    plan = {
        "episode_id": cfg["episode_id"],
        "created_at": _now(),
        "runner_version": RUNNER_VERSION,
        "registry_path": str(registry_path),
        "registry_sha256": registry_sha,
        "project_root": str(project_root),
        "scripts_root": reg["scripts_root"],
        "tracks": cfg["tracks"],
        "resolved_tracks_sha256": resolved_tracks_sha,
        "steps": plan_steps,
        "human_review_after": cfg.get("human_review_after"),
    }
    return plan


# ---------------- state ----------------

def _load_state(run_dir: Path) -> dict[str, Any]:
    p = run_dir / "state.json"
    if not p.exists():
        return {}
    return _load_json(p)


def _save_state(run_dir: Path, state: dict[str, Any]) -> None:
    _save_json_atomic(run_dir / "state.json", state)


def _transition(state: dict, target: str, note: str = "") -> None:
    prev = state.get("state")
    if target not in STATES:
        raise RunnerError(f"unknown target state: {target}")
    state["state"] = target
    state.setdefault("history", []).append({"from": prev, "to": target, "at": _now(), "note": note})


# ---------------- commands ----------------

def cmd_create(config_path: Path, run_dir: Path, registry_path: Path, project_root: Path | None) -> int:
    if run_dir.exists() and any(run_dir.iterdir()):
        raise RunnerError(f"run_dir already exists and is not empty: {run_dir}")
    cfg = load_and_validate_config(config_path)
    plan = build_plan(cfg, registry_path, run_dir, project_root=project_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    _save_json_atomic(run_dir / "plan.json", plan)
    # persist original config alongside plan for audit
    _save_json_atomic(run_dir / "episode_config.snapshot.json", cfg)
    state = {
        "state": "CREATED",
        "episode_id": cfg["episode_id"],
        "run_dir": str(run_dir),
        "completed_step_ids": [],
        "history": [],
    }
    _transition(state, "PLAN_FROZEN", f"frozen with registry_sha256={plan['registry_sha256'][:12]}")
    _save_state(run_dir, state)
    print(f"[PLAN_FROZEN] episode={cfg['episode_id']} steps={len(plan['steps'])} run_dir={run_dir}")
    return 0


def _run_one_step(run_dir: Path, step: dict, dry_run: bool) -> dict:
    logs_dir = run_dir / "logs"
    stdout_path = logs_dir / f"{step['step_id']}.stdout.txt"
    stderr_path = logs_dir / f"{step['step_id']}.stderr.txt"
    cmd = [sys.executable, step["script_path"]]
    for k, v in step["resolved_params"].items():
        flag = f"--{k.replace('_', '-')}"
        if isinstance(v, list):
            for item in v:
                cmd.extend([flag, item])
        else:
            cmd.extend([flag, v])
    record = {
        "step_id": step["step_id"],
        "tool": step["tool"],
        "cmd": cmd,
        "started_at": _now(),
        "dry_run": dry_run,
    }
    if dry_run:
        record.update({"finished_at": _now(), "returncode": None, "duration_ms": 0})
        return record
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Ensure output parents only for a real subprocess. A dry run must not
    # create output directories or mark the episode as partly processed.
    for k, v in step["resolved_params"].items():
        if isinstance(v, list):
            continue
        if k.endswith("_json") or k.endswith("_wav") or k == "output_dir":
            outp = Path(v)
            if k == "output_dir":
                outp.mkdir(parents=True, exist_ok=True)
            else:
                outp.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
        )
    except FileNotFoundError as exc:
        record.update({
            "finished_at": _now(),
            "returncode": -1,
            "duration_ms": int((time.time() - t0) * 1000),
            "error": f"exec failed: {exc}",
        })
        return record
    except subprocess.TimeoutExpired as exc:
        record.update({
            "finished_at": _now(),
            "returncode": -2,
            "duration_ms": int((time.time() - t0) * 1000),
            "error": f"timeout after 600s: {exc}",
        })
        return record
    stdout_path.write_bytes(proc.stdout)
    stderr_path.write_bytes(proc.stderr)
    record.update({
        "finished_at": _now(),
        "returncode": proc.returncode,
        "duration_ms": int((time.time() - t0) * 1000),
        "stdout_bytes": len(proc.stdout),
        "stderr_bytes": len(proc.stderr),
        "stdout_sha256": _sha256_of_bytes(proc.stdout),
        "stderr_sha256": _sha256_of_bytes(proc.stderr),
    })
    return record


def _write_call_record(run_dir: Path, record: dict) -> None:
    filename = "dry_run_tool_calls.jsonl" if record.get("dry_run") else "tool_calls.jsonl"
    with (run_dir / filename).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def cmd_run(run_dir: Path, dry_run: bool, stop_at: str | None) -> int:
    state = _load_state(run_dir)
    if not state:
        raise RunnerError(f"no state.json in {run_dir}; call `create` first")
    plan_path = run_dir / "plan.json"
    plan = _load_json(plan_path)
    if state["state"] not in {"PLAN_FROZEN", "RUNNING", "FAILED", "HUMAN_REVIEW_REQUIRED", "STOPPED_AT_CHECKPOINT"}:
        raise RunnerError(f"cannot run from state {state['state']}")
    if state["state"] == "HUMAN_REVIEW_REQUIRED":
        print("[HUMAN_REVIEW_REQUIRED] awaiting human decision; runner will not run or advance automatically")
        return 0

    # Registry SHA drift check: if changed since plan freeze, refuse.
    registry_path = Path(plan["registry_path"])
    current_registry_sha = _sha256_of(registry_path)
    if current_registry_sha != plan["registry_sha256"]:
        raise RunnerError(
            f"registry SHA changed since plan freeze; open a new run_dir. plan={plan['registry_sha256']} now={current_registry_sha}"
        )

    if stop_at and not any(s["step_id"] == stop_at for s in plan["steps"]):
        raise RunnerError(f"--stop-at {stop_at!r} does not match any step")

    if not dry_run:
        _transition(state, "RUNNING", "start run")
        _save_state(run_dir, state)

    completed = set(state.get("completed_step_ids", []))
    hr_after = plan.get("human_review_after")

    for step in plan["steps"]:
        if step["step_id"] in completed:
            continue
        # Guard: never execute a phase != pre_review in this challenger.
        if step["phase"] != "pre_review":
            if not dry_run:
                _transition(state, "FAILED", f"step {step['step_id']}: phase={step['phase']} not allowed")
                _save_state(run_dir, state)
            raise RunnerError(f"phase {step['phase']!r} not permitted in this challenger")
        if not step.get("reads_only", False):
            if not dry_run:
                _transition(state, "FAILED", f"step {step['step_id']}: non-read-only tool")
                _save_state(run_dir, state)
            raise RunnerError(f"step {step['step_id']}: non-read-only tool is not permitted")
        plan_root = Path(plan["project_root"])
        plan_scripts_root = _require_under(
            plan_root / plan["scripts_root"], plan_root, "frozen plan scripts_root"
        )
        _require_under(Path(step["script_path"]), plan_scripts_root, f"step {step['step_id']} script")
        for key, value in step["resolved_params"].items():
            if key.startswith("output"):
                if isinstance(value, list):
                    raise RunnerError(f"step {step['step_id']}: output param {key!r} may not be a list")
                _require_under(Path(value), run_dir, f"step {step['step_id']} output {key!r}")
        # Verify script SHA still matches plan-frozen SHA.
        current_script_sha = _sha256_of(Path(step["script_path"]))
        if current_script_sha != step["script_sha256"]:
            if not dry_run:
                _transition(state, "FAILED", f"step {step['step_id']}: script SHA drift")
                _save_state(run_dir, state)
            raise RunnerError(
                f"script {step['script']} SHA changed since plan freeze; open a new run"
            )
        record = _run_one_step(run_dir, step, dry_run)
        _write_call_record(run_dir, record)
        rc = record.get("returncode")
        if rc not in (0, None):  # 0=ok, None=dry_run
            if not dry_run:
                state["failed_step_id"] = step["step_id"]
                _transition(state, "FAILED", f"step {step['step_id']} rc={rc}")
                _save_state(run_dir, state)
            raise RunnerError(f"tool call failed rc={rc} step={step['step_id']}")
        if not dry_run:
            completed.add(step["step_id"])
            state["completed_step_ids"] = sorted(completed)
            _save_state(run_dir, state)
        if stop_at and step["step_id"] == stop_at:
            if dry_run:
                print(f"[DRY_RUN_STOPPED] stopped at {stop_at}; state remains {state['state']}")
                return 0
            _transition(state, "STOPPED_AT_CHECKPOINT", f"stopped at --stop-at={stop_at}")
            _save_state(run_dir, state)
            _write_manifest(run_dir, state, plan)
            print(f"[STOPPED_AT_CHECKPOINT] stopped at {stop_at}")
            return 0
        if hr_after and step["step_id"] == hr_after:
            if dry_run:
                print(f"[DRY_RUN_HUMAN_REVIEW_BOUNDARY] after step {hr_after}; state remains {state['state']}")
                return 0
            _transition(state, "HUMAN_REVIEW_REQUIRED", "reached human_review_after")
            _save_state(run_dir, state)
            _write_manifest(run_dir, state, plan)
            print(f"[HUMAN_REVIEW_REQUIRED] after step {hr_after}. Runner will not continue automatically.")
            return 0

    if dry_run:
        print(f"[DRY_RUN_COMPLETE] all pre-review steps validated; state remains {state['state']}")
        return 0
    _transition(state, "COMPLETED_PRE_REVIEW", "all pre-review steps done")
    _save_state(run_dir, state)
    _write_manifest(run_dir, state, plan)
    print("[COMPLETED_PRE_REVIEW] all steps done (no human_review_after configured)")
    return 0


def _write_manifest(run_dir: Path, state: dict, plan: dict) -> None:
    outputs = []
    for s in plan["steps"]:
        for k, v in s["resolved_params"].items():
            if not k.startswith("output"):
                continue
            p = Path(v)
            if p.exists() and p.is_file():
                outputs.append(
                    {"step_id": s["step_id"], "param": k, "path": str(p), "sha256": _sha256_of(p)}
                )
            elif p.exists() and p.is_dir():
                # Directory output; record the directory only.
                outputs.append(
                    {"step_id": s["step_id"], "param": k, "path": str(p), "is_dir": True}
                )
    manifest = {
        "episode_id": plan["episode_id"],
        "state": state["state"],
        "runner_version": RUNNER_VERSION,
        "completed_step_ids": state.get("completed_step_ids", []),
        "outputs": outputs,
        "written_at": _now(),
    }
    _save_json_atomic(run_dir / "run_manifest.json", manifest)


def cmd_status(run_dir: Path) -> int:
    state = _load_state(run_dir)
    if not state:
        print(f"no state.json in {run_dir}")
        return 1
    plan = _load_json(run_dir / "plan.json")
    print(f"episode : {state.get('episode_id')}")
    print(f"state   : {state.get('state')}")
    print(f"completed_step_ids : {state.get('completed_step_ids')}")
    print(f"human_review_after : {plan.get('human_review_after')}")
    return 0


def cmd_resume(run_dir: Path, stop_at: str | None) -> int:
    state = _load_state(run_dir)
    if not state:
        raise RunnerError(f"no state.json in {run_dir}")
    if state["state"] == "COMPLETED_PRE_REVIEW":
        print("[COMPLETED_PRE_REVIEW] nothing to resume")
        return 0
    if state["state"] == "HUMAN_REVIEW_REQUIRED":
        # Resume from a review pause must NOT auto-advance without human
        # decisions. All pre-review steps are already complete.
        print("[HUMAN_REVIEW_REQUIRED] awaiting human decision; runner will not resume automatically")
        return 0
    return cmd_run(run_dir, dry_run=False, stop_at=stop_at)


# ---------------- entrypoint ----------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("--config", required=True, type=Path)
    p_create.add_argument("--run-dir", required=True, type=Path)
    p_create.add_argument("--registry", type=Path, required=True)
    p_create.add_argument("--project-root", type=Path, default=None,
                          help="Override project_root (default: registry_path.parents[2])")

    p_run = sub.add_parser("run")
    p_run.add_argument("--run-dir", required=True, type=Path)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--stop-at", default=None)

    p_res = sub.add_parser("resume")
    p_res.add_argument("--run-dir", required=True, type=Path)
    p_res.add_argument("--stop-at", default=None)

    p_st = sub.add_parser("status")
    p_st.add_argument("--run-dir", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "create":
            return cmd_create(args.config, args.run_dir, args.registry, args.project_root)
        if args.cmd == "run":
            return cmd_run(args.run_dir, args.dry_run, args.stop_at)
        if args.cmd == "resume":
            return cmd_resume(args.run_dir, args.stop_at)
        if args.cmd == "status":
            return cmd_status(args.run_dir)
    except RunnerError as exc:
        print(f"RUNNER_ERROR: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
