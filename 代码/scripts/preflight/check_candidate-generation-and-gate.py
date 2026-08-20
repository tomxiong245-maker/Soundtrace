#!/usr/bin/env python3
"""check_candidate-generation-and-gate — pre_flight_check (skills/candidate-generation-and-gate/SKILL.md §7).

用途
----
把 SKILL.md §7.1–§7.13 十三条 preflight 命令做成可运行脚本。

CLI
---
  --check <name>        跑单条（可多次）
  --all                 跑全部（默认）
  --project-root PATH   改写项目根
  --json                JSON 输出汇总
  --verbose

退出码
------
  0  全部 PASS
  1  至少一条 FAIL
  2  BLOCKED（governance / MFA / SOT 依赖缺失）

Check 覆盖
----------
governance_attested          · 7.1
tools_json_related_registered · 7.2
mfa_binary_present            · 7.3
cough_like_scope_action       · 7.4
no_mic_bump_or_thump_kind     · 7.5
self_correction_normalized    · 7.6
mfa_boundaries_schema         · 7.7
boundary_snap_summary_present · 7.8
gate_summary_schema           · 7.9
never_cut_leak_in_auto_cut    · 7.10
session_feedback_sot          · 7.11
labels_lake_schema            · 7.12
policy_not_approved_zero_auto · 7.13
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable


NEED_TOOLS = {
    "build_filler_global_pause_candidates",
    "build_candidate_family_bundle",
    "detect_self_correction_wordlevel",
    "detect_transient_events",
    "mfa_align_and_extract_boundaries",
    "snap_candidate_boundaries",
    "apply_autocut_gate",
    "build_case_memory",
    "build_labels_lake",
    "spacy_semantic_transcript",
    "build_semantic_transcript",
    "p0_transcribe_mvp",
}


def _project_root_default() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


# --- individual checks ------------------------------------------------------

def check_governance_attested(root: Path) -> tuple[str, str]:
    p = root / "main/knowledge/integration_governance/owner_attested_mainline.v1.json"
    if not p.is_file():
        return "BLOCK", f"missing: {p}"
    text = p.read_text(encoding="utf-8")
    if "candidate_family_adapter" not in text:
        return "FAIL", "candidate_family_adapter not present"
    if "OWNER_ATTESTED_INTEGRATE" not in text:
        return "FAIL", "not OWNER_ATTESTED_INTEGRATE"
    return "PASS", "candidate_family_adapter attested"


def check_tools_json_related_registered(root: Path) -> tuple[str, str]:
    p = root / "main/tools/tools.json"
    if not p.is_file():
        return "BLOCK", f"missing: {p}"
    doc = _load_json(p)
    tools = doc.get("tools") or []
    names: set[str] = set()
    for t in tools:
        n = t.get("tool_name") or t.get("name")
        if n:
            names.add(n)
    missing = NEED_TOOLS - names
    if missing:
        return "FAIL", f"tools missing: {sorted(missing)}"
    return "PASS", f"12 required tools present ({len(names)} total)"


def check_mfa_binary_present(root: Path) -> tuple[str, str]:
    # 允许 ~/miniforge3/bin/mfa 或 which mfa
    candidates = [
        Path.home() / "miniforge3/bin/mfa",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return "PASS", str(c)
    found = shutil.which("mfa")
    if found:
        return "PASS", found
    return "BLOCK", "mfa binary not found (~/miniforge3/bin/mfa or PATH)"


def check_cough_like_scope_action(root: Path) -> tuple[str, str]:
    offenders: list[str] = []
    checked = 0
    for p in glob.glob(str(root / "main/runs/*/candidate_source.json")):
        try:
            doc = _load_json(Path(p))
        except Exception:
            continue
        checked += 1
        for c in doc.get("candidates") or []:
            if c.get("reason_key") == "cough_like":
                if c.get("cut_scope") != "source_track_gate_only":
                    offenders.append(f"{p} cid={c.get('candidate_id')} cut_scope={c.get('cut_scope')}")
                if c.get("action_type") != "source_track_gate":
                    offenders.append(f"{p} cid={c.get('candidate_id')} action_type={c.get('action_type')}")
    if offenders:
        return "FAIL", f"cough_like scope/action wrong (sample: {offenders[:3]})"
    return "PASS", f"checked {checked} candidate_source.json"


def check_no_mic_bump_or_thump_kind(root: Path) -> tuple[str, str]:
    pat = re.compile(r'"reason_key"\s*:\s*"(mic_bump_like|thump_like)"')
    hits: list[str] = []
    for p in glob.glob(str(root / "main/runs/*/candidate_source.json")):
        try:
            text = Path(p).read_text(encoding="utf-8")
        except Exception:
            continue
        if pat.search(text):
            hits.append(p)
    if hits:
        return "FAIL", f"mic_bump_like/thump_like leaked (sample: {hits[:3]})"
    return "PASS", "no mic_bump_like/thump_like in candidate pools"


def check_self_correction_normalized(root: Path) -> tuple[str, str]:
    offenders: list[str] = []
    for p in glob.glob(str(root / "main/runs/*/candidate_source.json")):
        try:
            doc = _load_json(Path(p))
        except Exception:
            continue
        for c in doc.get("candidates") or []:
            if c.get("candidate_kind") != "self_correction":
                continue
            if c.get("cut_scope") != "abandoned_span_only":
                offenders.append(f"{p} cid={c.get('candidate_id')} cut_scope={c.get('cut_scope')}")
            if c.get("boundary_lock") is not True:
                offenders.append(f"{p} cid={c.get('candidate_id')} boundary_lock={c.get('boundary_lock')}")
            if c.get("policy") != "review_only_no_automatic_accept":
                offenders.append(f"{p} cid={c.get('candidate_id')} policy={c.get('policy')}")
    if offenders:
        return "FAIL", f"self_correction not normalized (sample: {offenders[:3]})"
    return "PASS", "self_correction candidates normalized"


def check_mfa_boundaries_schema(root: Path) -> tuple[str, str]:
    found = 0
    bad: list[str] = []
    for p in glob.glob(str(root / "main/runs/*/mfa_boundaries.json")):
        try:
            doc = _load_json(Path(p))
        except Exception:
            bad.append(p)
            continue
        if doc.get("schema_version") != "mfa-boundaries-v1":
            bad.append(f"{p} schema={doc.get('schema_version')}")
        found += 1
    if bad:
        return "FAIL", f"mfa-boundaries schema mismatch (sample: {bad[:3]})"
    if found == 0:
        return "BLOCK", "no mfa_boundaries.json under main/runs/*"
    return "PASS", f"{found} mfa_boundaries.json PASS"


def check_boundary_snap_summary_present(root: Path) -> tuple[str, str]:
    missing: list[str] = []
    checked = 0
    for p in glob.glob(str(root / "main/runs/*/candidate_source.json")):
        try:
            doc = _load_json(Path(p))
        except Exception:
            continue
        checked += 1
        if "boundary_snap_summary" not in doc:
            missing.append(f"{p} missing boundary_snap_summary")
        if "candidate_source_sha256_before_boundary_snap" not in doc:
            missing.append(f"{p} missing candidate_source_sha256_before_boundary_snap")
    if missing:
        return "FAIL", f"missing snap fields (sample: {missing[:3]})"
    return "PASS", f"checked {checked} candidate_source.json"


def check_gate_summary_schema(root: Path) -> tuple[str, str]:
    bad: list[str] = []
    checked = 0
    for p in glob.glob(str(root / "main/runs/*/autocut_gate/summary.json")):
        try:
            doc = _load_json(Path(p))
        except Exception:
            bad.append(p)
            continue
        checked += 1
        if doc.get("schema_version") != "autocut-gate-v1-run-v1":
            bad.append(f"{p} schema={doc.get('schema_version')}")
        summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else doc
        for k in ("auto_cut_eligible_count", "human_review_required_count"):
            if k not in summary and k not in doc:
                bad.append(f"{p} missing {k}")
    if bad:
        return "FAIL", f"gate summary schema issues (sample: {bad[:3]})"
    return "PASS", f"checked {checked} summary.json"


def check_never_cut_leak_in_auto_cut(root: Path) -> tuple[str, str]:
    offenders: list[str] = []
    for p in glob.glob(str(root / "main/runs/*/autocut_gate/gate_report.json")):
        try:
            doc = _load_json(Path(p))
        except Exception:
            continue
        for row in doc.get("per_candidate") or []:
            if not row.get("all_gates_passed"):
                continue
            for fb in row.get("previous_user_feedback") or []:
                if fb.get("verdict") == "never_cut":
                    offenders.append(f"{p} cid={row.get('candidate_id')}")
    if offenders:
        return "FAIL", f"never_cut leaked into auto_cut (sample: {offenders[:3]})"
    return "PASS", "no never_cut leak"


def check_session_feedback_sot(root: Path) -> tuple[str, str]:
    p = root / "main/knowledge/session_feedback/current.session_feedback.jsonl"
    if not p.is_file():
        return "FAIL", f"§20 SOT missing: {p}"
    return "PASS", str(p.relative_to(root))


def check_labels_lake_schema(root: Path) -> tuple[str, str]:
    p = root / "main/knowledge/labels_lake.json"
    if not p.is_file():
        return "BLOCK", f"missing: {p}"
    try:
        doc = _load_json(p)
    except Exception as exc:
        return "FAIL", f"unreadable: {exc}"
    if doc.get("schema_version") != "labels-lake-v2":
        return "FAIL", f"schema={doc.get('schema_version')}"
    if "by_reason_key" not in doc:
        return "FAIL", "by_reason_key missing"
    return "PASS", "labels-lake-v2 · by_reason_key present"


def check_policy_not_approved_zero_auto(root: Path) -> tuple[str, str]:
    offenders: list[str] = []
    for p in glob.glob(str(root / "main/runs/*/autocut_gate/summary.json")):
        try:
            doc = _load_json(Path(p))
        except Exception:
            continue
        if doc.get("policy_status") == "NOT_APPROVED":
            summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else doc
            n = summary.get("auto_cut_eligible_count", doc.get("auto_cut_eligible_count", 0))
            if int(n or 0) > 0:
                offenders.append(f"{p} auto_cut_eligible={n}")
    if offenders:
        return "FAIL", f"NOT_APPROVED but auto_cut>0 (sample: {offenders[:3]})"
    return "PASS", "policy=NOT_APPROVED enforced (or absent)"


# --- registry ---------------------------------------------------------------

CHECKS: dict[str, Callable[[Path], tuple[str, str]]] = {
    "governance_attested": check_governance_attested,
    "tools_json_related_registered": check_tools_json_related_registered,
    "mfa_binary_present": check_mfa_binary_present,
    "cough_like_scope_action": check_cough_like_scope_action,
    "no_mic_bump_or_thump_kind": check_no_mic_bump_or_thump_kind,
    "self_correction_normalized": check_self_correction_normalized,
    "mfa_boundaries_schema": check_mfa_boundaries_schema,
    "boundary_snap_summary_present": check_boundary_snap_summary_present,
    "gate_summary_schema": check_gate_summary_schema,
    "never_cut_leak_in_auto_cut": check_never_cut_leak_in_auto_cut,
    "session_feedback_sot": check_session_feedback_sot,
    "labels_lake_schema": check_labels_lake_schema,
    "policy_not_approved_zero_auto": check_policy_not_approved_zero_auto,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="check_candidate-generation-and-gate",
        description="pre_flight_check for skills/candidate-generation-and-gate/SKILL.md §7",
    )
    p.add_argument("--check", action="append", default=[],
                   help=f"check name to run (可多次); options: {sorted(CHECKS)}")
    p.add_argument("--all", action="store_true", help="run all checks (default)")
    p.add_argument("--project-root", type=Path, default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.project_root or _project_root_default()).resolve()
    if not (root / "main" / "runs").is_dir():
        print(f"[preflight] not a project root (no main/runs): {root}", file=sys.stderr)
        return 2

    if args.check:
        names = list(args.check)
        for n in names:
            if n not in CHECKS:
                print(f"[preflight] unknown check: {n}", file=sys.stderr)
                return 2
    else:
        names = list(CHECKS.keys())

    results = []
    worst = "PASS"
    for name in names:
        try:
            status, detail = CHECKS[name](root)
        except Exception as exc:
            status, detail = "FAIL", f"exception: {exc!r}"
        results.append({"check": name, "status": status, "detail": detail})
        if status == "FAIL":
            worst = "FAIL"
        elif status == "BLOCK" and worst != "FAIL":
            worst = "BLOCK"
        if args.verbose or not args.json:
            print(f"[{status:5s}] {name} · {detail}")

    if args.json:
        print(json.dumps({"root": str(root), "results": results,
                          "overall": worst}, ensure_ascii=False, indent=2))
    if worst == "FAIL":
        return 1
    if worst == "BLOCK":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
