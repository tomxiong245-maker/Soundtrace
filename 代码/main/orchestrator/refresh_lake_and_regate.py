#!/usr/bin/env python3
"""refresh_lake_and_regate — 一键 online 学习闭环 (evolution path 1 · v20.4)

**动机 (2026-08-17 用户明确方向)**:
> "我们之后主要的调整就可以基于偏好而非这种基本的东西了"

用户每次审完 auto-cut clip 后：
1. `human_decisions.json` 保存
2. `refresh_label_learning_snapshot.py` 已经自动跑（触发 preference snapshot rebuild）
3. **本脚本进一步**: rebuild `labels_lake.json` + 对指定 review run 重跑 `apply_autocut_gate`
   → 得新 gate_report，供下次人审对照

**流程**:
    save human_decisions.json
        ↓
    (existing hook) refresh_label_learning_snapshot
        ↓
    refresh_lake_and_regate.py --run <active_review_run>
        ↓
    build_labels_lake → labels_lake.json 增量
        ↓
    apply_autocut_gate --labels-lake <new> --candidates <run>/all_candidates.json
        ↓
    生成 gate_report.diff.json (new vs old)
        ↓
    人审看新一轮 auto_cut 是否更少 review_required

**用法**:
    python3 refresh_lake_and_regate.py --run main/runs/EP05-AUTO-XXX/
    python3 refresh_lake_and_regate.py  # 只 rebuild lake，不 regate
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, default=None,
                    help="active review run dir (has all_candidates.json + calibration_source.json). "
                         "If omitted, only rebuild lake.")
    ap.add_argument("--policy",
                    default="稳定生产/challengers/release-policy-v2/rules/editing_policy.guards-v2.json")
    ap.add_argument("--episode-duration-seconds", type=float, default=None,
                    help="Required if --run is set (or run_identity.json/input_manifest.json has frame_count)")
    args = ap.parse_args(argv)

    # 1. rebuild lake
    print("=== [1/3] rebuild labels_lake.json ===")
    lake = PROJECT_ROOT / "main/knowledge/labels_lake.json"
    lake_before_sha = lake.read_bytes()[:200] if lake.is_file() else b""
    subprocess.run([sys.executable, str(PROJECT_ROOT / "main/orchestrator/build_labels_lake.py"),
                    "--project-root", str(PROJECT_ROOT),
                    "--out", str(lake)], check=True, cwd=str(PROJECT_ROOT))
    lake_after_sha = lake.read_bytes()[:200]
    changed = lake_before_sha != lake_after_sha
    print(f"    lake changed: {changed}")

    if not args.run:
        print("=== done (lake only) ===")
        return 0

    run_dir = args.run.resolve()
    if not run_dir.is_dir():
        print(f"BLOCKED: run dir does not exist: {run_dir}", file=sys.stderr)
        return 2

    # 2. read episode_duration if needed
    ep_dur = args.episode_duration_seconds
    if ep_dur is None:
        for fname in ("run_identity.json", "input_manifest.json"):
            p = run_dir / fname
            if p.is_file():
                doc = json.loads(p.read_text(encoding="utf-8"))
                fc = int(doc.get("frame_count") or 0)
                sr = int(doc.get("sample_rate_hz") or 48000)
                if fc and sr:
                    ep_dur = fc / sr
                    break
        if ep_dur is None:
            print("BLOCKED: --episode-duration-seconds required", file=sys.stderr)
            return 2

    # 3. regate: apply_autocut_gate with new lake
    print(f"=== [2/3] regate {run_dir.name} with fresh lake ===")
    gate_tool = PROJECT_ROOT / "稳定生产/challengers/autocut-gate-v1/scripts/apply_autocut_gate.py"
    out_dir = run_dir / "autocut_gate_regated"
    prev_dir = run_dir / "autocut_gate"
    cmd = [sys.executable, str(gate_tool),
           "--candidates", str(run_dir / "all_candidates.json"),
           "--policy", str(PROJECT_ROOT / args.policy),
           "--labels-lake", str(lake),
           "--episode-duration-seconds", str(ep_dur),
           "--out", str(out_dir)]
    pa = run_dir / "policy_application.json"
    if pa.is_file():
        cmd += ["--policy-application", str(pa)]
    cal = run_dir / "calibration_source.json"
    if cal.is_file():
        cmd += ["--calibration-source", str(cal)]
    wl = run_dir / "self_correction_wordlevel.json"
    if wl.is_file():
        cmd += ["--extra-candidates", str(wl)]
    subprocess.run(cmd, check=True)

    # 4. Diff summary
    print(f"=== [3/3] diff old vs new gate ===")
    new_summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    prev_summary_path = prev_dir / "summary.json"
    diff = {
        "run": str(run_dir.relative_to(PROJECT_ROOT)),
        "lake_changed": changed,
        "new_gate_out": str(out_dir.relative_to(PROJECT_ROOT)),
        "new_summary": new_summary,
    }
    if prev_summary_path.is_file():
        prev_summary = json.loads(prev_summary_path.read_text(encoding="utf-8"))
        diff["prev_summary"] = prev_summary
        prev_ids = set(prev_summary.get("auto_cut_candidate_ids", []))
        new_ids = set(new_summary.get("auto_cut_candidate_ids", []))
        diff["auto_cut_added"] = sorted(new_ids - prev_ids)
        diff["auto_cut_removed"] = sorted(prev_ids - new_ids)
        diff["auto_cut_stable"] = sorted(new_ids & prev_ids)
    (run_dir / "regate_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "new_auto_cut": new_summary["summary"]["auto_cut_eligible_count"],
        "auto_cut_added": diff.get("auto_cut_added"),
        "auto_cut_removed": diff.get("auto_cut_removed"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
