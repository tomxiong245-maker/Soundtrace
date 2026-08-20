#!/usr/bin/env python3
"""Build an immutable, media-free label-learning Challenger evidence package.

The command joins two safe pieces of evidence:
1. a snapshot of itemized human decisions and feedback into classifications and
   policy cards; and
2. a metadata-only event route report between a new candidate run and a
   historical reviewed run.

It never opens audio, writes an EDL, changes a review bundle, creates a human
decision, or updates the Champion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPERIENCE_SCRIPTS = PROJECT_ROOT / "稳定生产" / "challengers" / "experience-ingestion-v1" / "scripts"
if str(EXPERIENCE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(EXPERIENCE_SCRIPTS))

import event_identity  # noqa: E402
from build_preference_snapshot import build_snapshot  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_package(*, repo_root: Path, out_dir: Path, current_run: Path,
                  historical_run: Path, canonical_case_store: Path | None) -> dict[str, Any]:
    if out_dir.exists():
        raise FileExistsError(f"refusing to overwrite immutable Challenger output: {out_dir}")
    if not current_run.is_dir() or not historical_run.is_dir():
        raise FileNotFoundError("current-run and historical-run must both be existing run directories")
    out_dir.mkdir(parents=True, exist_ok=False)
    snapshot_dir = out_dir / "preference_snapshot"
    try:
        snapshot = build_snapshot(repo_root, snapshot_dir, canonical_case_store)
        routing = event_identity.build_run_report(current_run, historical_run)
        routing_path = out_dir / "event_routes.json"
        write_json(routing_path, routing)
        identity = {
            "schema_version": "label-learning-challenger-run-v1",
            "run_id": out_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "CHALLENGER_EVIDENCE_ONLY",
            "purpose": "event-level review de-duplication, feedback classification and policy cards",
            "current_run_relpath": str(current_run.relative_to(repo_root)),
            "historical_run_relpath": str(historical_run.relative_to(repo_root)),
            "safety": {
                "reads_audio": False,
                "changes_audio": False,
                "changes_historical_decisions": False,
                "changes_current_review_bundle": False,
                "creates_edl": False,
                "creates_human_approved": False,
                "creates_autocut_policy": False,
            },
        }
        write_json(out_dir / "run_identity.json", identity)
        summary = routing.get("summary") or {}
        counts = snapshot.get("counts") or {}
        report_lines = [
            f"# 标签学习 Challenger：{out_dir.name}",
            "",
            "> 这是只读历史决定的 Challenger 证据包；它不改当前审核、音频、EDL、Champion 或自动剪辑政策。",
            "",
            "## 快照",
            "",
            f"- 有效记录：{counts.get('records', 0)}（accept={counts.get('accept', 0)} / reject={counts.get('reject', 0)}）",
            f"- Quarantine：{counts.get('quarantine', 0)}（不作为学习真值）",
            f"- 政策卡：{counts.get('policy_cards', 0)}；详见 `preference_snapshot/policies.md`。",
            "",
            "## 事件路由",
            "",
            f"- `already_reviewed_exact`：{summary.get('already_reviewed_exact', 0)}",
            f"- `semantic_reuse_boundary_review`：{summary.get('semantic_reuse_boundary_review', 0)}",
            f"- `rejected_false_positive`：{summary.get('rejected_false_positive', 0)}",
            f"- `rejected_execution_issue`：{summary.get('rejected_execution_issue', 0)}",
            f"- `new_event`：{summary.get('new_event', 0)}",
            "",
            "语义重用不等于复制旧听感批准；边界变化必须复核，执行问题不能误学成语义 reject。",
        ]
        (out_dir / "RUN_REPORT.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        artifacts = {
            name: sha256_file(out_dir / name)
            for name in ("run_identity.json", "event_routes.json", "RUN_REPORT.md")
        }
        artifacts["preference_snapshot/snapshot_manifest.json"] = sha256_file(snapshot_dir / "snapshot_manifest.json")
        manifest = {
            "schema_version": "label-learning-challenger-manifest-v1",
            "run_id": out_dir.name,
            "snapshot_counts": counts,
            "event_route_summary": summary,
            "artifacts": artifacts,
            "policy": "challenger-only; no human decision, EDL, render, Champion or autocut change",
        }
        write_json(out_dir / "manifest.json", manifest)
        return manifest
    except Exception:
        # Preserve incomplete output for diagnosis; it cannot be reused as a
        # successful evidence package because manifest.json is absent.
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--current-run", type=Path, required=True)
    parser.add_argument("--historical-run", type=Path, required=True)
    parser.add_argument("--canonical-case-store", type=Path)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    result = build_package(
        repo_root=repo_root,
        out_dir=args.out_dir.expanduser().resolve(),
        current_run=args.current_run.expanduser().resolve(),
        historical_run=args.historical_run.expanduser().resolve(),
        canonical_case_store=args.canonical_case_store.expanduser().resolve() if args.canonical_case_store else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

