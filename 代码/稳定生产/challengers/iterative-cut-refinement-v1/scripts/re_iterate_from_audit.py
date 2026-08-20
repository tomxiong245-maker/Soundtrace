#!/usr/bin/env python3
"""re_iterate_from_audit · 人审 REJECTED 后的第二轮 Optuna 迭代 · 直到 benchmark 通过.

**用户 2026-08-19 明确要求**:
  "如果人不通过，再用 optuna 跑，直到 benchmark 通过 (也是 10 次)"

**触发路径 · 集成到主 pipeline**:
  1. 主 pipeline 第一轮跑完 · 生成 audit_report + audit_verdicts.json (人审待填)
  2. 真人在 audit_verdicts.json 里标 candidate 为 verdict="REJECTED" (加 reason 可选)
  3. 用户 rerun pipeline (或独立跑本 script)
  4. run_end_to_end.py Stage 6.9 检测到 audit_verdicts.json 存在且有 REJECTED · 调本 script
  5. 本 script 对每个 REJECTED 候选跑第二轮 Optuna 10 iter (**换 seed · 跳过 warm_start · 探索新区**)
  6. 若收敛 · 更新 verified_edl.json 对应 candidate params + verdict=SECOND_ROUND_PASSED
  7. 若 10 iter 仍 escape · verdict=SECOND_ROUND_ESCAPED · 走 M3 兜底人审

**为什么第二轮换配置**:
  - 第一轮 warm_start 已被人拒 (虽然 NISQA 判 PASS) · 再用同一 warm_start 会重复失败
  - 换 seed (42 → 43) + skip_warm_start · 让 TPE 冷启动 · 探索第一轮未采样的参数区
  - **objective 不变** (5.0 - discontinuity + verify penalty) · 因为人拒可能不在 NISQA 感知范围
    真正的 "人品味 objective" 无法自动学 · 只能通过参数空间多样性提高命中率

**M3 元规则兜底**:
  - 若 10 iter 仍 escape · 该候选 verdict=SECOND_ROUND_ESCAPED
  - 不假装完成 · 交人工手改 或 丢弃该 cut

**输入**:
  --run-dir            已完成第一轮 pipeline 的 run 目录 (含 audit_verdicts.json + verified_edl.json)
  --nisqa-python       NISQA venv python (与主 pipeline 同一份)
  --project-root       (optional) 项目根 · 默认从 script path 推

**输出**:
  <run-dir>/second_round_candidates.json    - 筛出的 REJECTED 候选
  <run-dir>/second_round_policy.json        - 第二轮专用 policy (max=10 · skip_warm · seed=43)
  <run-dir>/second_round_trace.json         - 每候选 10 iter 完整轨迹 (Optuna trials)
  <run-dir>/verified_edl.json               - 就地更新 · REJECTED 候选写入新 params + 新 verdict

**exit code**:
  0    - 全部 REJECTED 候选二轮 PASSED (可 rerun render)
  1    - 至少 1 个 SECOND_ROUND_ESCAPED (仍需人审)
  2    - 无 REJECTED 候选 · 或 audit_verdicts.json 不存在 (nothing to do)
  100  - 数据文件缺失 · 阻塞失败
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_second_round_policy(base_policy: dict) -> dict:
    """从第一轮 policy 派生第二轮 policy · 加 second_round 标记.

    optuna_refine.py 读 policy["second_round_mode"] · 若 True · skip_warm_start + seed 用 override.
    max_iterations 用 base_policy 里的 second_round.max_iterations (default 10).
    """
    p2 = deepcopy(base_policy)
    sr = p2.get("second_round", {}) or {}
    p2["second_round_mode"] = True
    p2["max_iterations"] = int(sr.get("max_iterations", 10))
    p2["seed_override"] = int(sr.get("seed_override", 43))
    p2["skip_warm_start"] = bool(sr.get("skip_warm_start", True))
    p2["_derived_from"] = "first_round_policy · second_round_mode=true"
    p2["_derived_at_utc"] = _utc_now()
    return p2


def collect_rejected(audit_verdicts: dict) -> list[str]:
    """Extract candidate_ids with verdict='REJECTED' (or any non-APPROVED · non-empty)."""
    out = []
    for c in audit_verdicts.get("candidates", []):
        cid = c.get("candidate_id")
        v = (c.get("verdict") or "").upper()
        if cid and v in ("REJECTED", "REJECT", "REDO", "NEEDS_REROLL"):
            out.append(cid)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True, type=Path,
                    help="已完成第一轮的 run 目录 (含 audit_verdicts.json + verified_edl.json)")
    ap.add_argument("--nisqa-python", required=False, type=Path, default=None,
                    help="NISQA venv python · 用于第二轮 iter_until_clean 的 --nisqa-venv-python")
    ap.add_argument("--project-root", type=Path, default=None,
                    help="项目根 · 用于定位 iterate_until_clean.py 脚本 · 默认从本脚本 path 推")
    args = ap.parse_args(argv)

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        print(f"[re_iterate] ERROR: run-dir 不存在: {run_dir}", file=sys.stderr)
        return 100

    audit_path = run_dir / "audit_verdicts.json"
    verified_path = run_dir / "verified_edl.json"

    if not audit_path.is_file():
        print(f"[re_iterate] audit_verdicts.json 不存在 · nothing to do: {audit_path}")
        return 2
    if not verified_path.is_file():
        print(f"[re_iterate] ERROR: verified_edl.json 不存在: {verified_path}", file=sys.stderr)
        return 100

    audit = _load_json(audit_path)
    verified = _load_json(verified_path)

    rejected_ids = collect_rejected(audit)
    if not rejected_ids:
        print(f"[re_iterate] 无 REJECTED 候选 · nothing to do (人审全 APPROVED)")
        return 2

    print(f"[re_iterate] 检测到 {len(rejected_ids)} 个 REJECTED 候选: {rejected_ids}")

    # 筛出 REJECTED candidates 完整数据
    all_cands = verified.get("candidates", [])
    rejected_cands = [c for c in all_cands if c.get("candidate_id") in rejected_ids]
    if not rejected_cands:
        print(f"[re_iterate] WARN: audit 里说 REJECTED 但 verified_edl 里找不到 · 数据不一致 · skip")
        return 100

    # Locate iterate_until_clean.py + policy
    if args.project_root:
        project_root = args.project_root.resolve()
    else:
        # 从本 script path 推 · 兼容活代码 + 最终交付两种 layout
        script_path = Path(__file__).resolve()
        # 活代码: <root>/稳定生产/challengers/iterative-cut-refinement-v1/scripts/re_iterate_from_audit.py
        # 最终交付: <root>/交付/最终交付文档/代码/稳定生产/challengers/iterative-cut-refinement-v1/scripts/re_iterate_from_audit.py
        # 找到含 "稳定生产/challengers/iterative-cut-refinement-v1" 的祖先
        parts = script_path.parts
        try:
            idx = parts.index("iterative-cut-refinement-v1")
            # 活代码 layout: idx-2 == "challengers" · idx-3 == "稳定生产" · idx-4 == project root
            # 最终交付 layout 更深 · 但工具能在同一 tree 找到即可
            project_root = Path(*parts[: idx - 2])  # to "稳定生产" 上一层
        except ValueError:
            project_root = script_path.parents[4]  # fallback

    iterate_script = None
    for candidate in [
        project_root / "稳定生产/challengers/iterative-cut-refinement-v1/scripts/iterate_until_clean.py",
        project_root / "交付/最终交付文档/代码/稳定生产/challengers/iterative-cut-refinement-v1/scripts/iterate_until_clean.py",
    ]:
        if candidate.is_file():
            iterate_script = candidate
            break
    if iterate_script is None:
        print(f"[re_iterate] ERROR: 找不到 iterate_until_clean.py", file=sys.stderr)
        return 100

    base_policy_path = iterate_script.parent.parent / "rules/refinement-policy-v1.json"
    if not base_policy_path.is_file():
        print(f"[re_iterate] ERROR: policy 不存在: {base_policy_path}", file=sys.stderr)
        return 100
    base_policy = _load_json(base_policy_path)

    # 写第二轮候选 + 策略
    sr_cands_path = run_dir / "second_round_candidates.json"
    sr_policy_path = run_dir / "second_round_policy.json"
    sr_trace_path = run_dir / "second_round_trace.json"

    _write_json(sr_cands_path, {"candidates": rejected_cands,
                                "note": "REJECTED 由人审标记 · re_iterate_from_audit 派生",
                                "derived_from": str(verified_path.name),
                                "derived_at_utc": _utc_now()})
    _write_json(sr_policy_path, build_second_round_policy(base_policy))
    print(f"[re_iterate] 写 {sr_cands_path.name} ({len(rejected_cands)} candidates)")
    print(f"[re_iterate] 写 {sr_policy_path.name} (max_iter=10 · skip_warm_start · seed=43)")

    # 读 run 相关元数据构造 iterate_until_clean.py 需要的 args
    transcript_dir = run_dir / "analysis"
    if not transcript_dir.is_dir():
        print(f"[re_iterate] ERROR: transcript-dir 不存在: {transcript_dir}", file=sys.stderr)
        return 100

    # raw_track_map · 从 processing_manifest.json / run_identity.json 读
    raw_track_map: dict[str, str] = {}
    manifest_path = run_dir / "processing_manifest.json"
    if manifest_path.is_file():
        m = _load_json(manifest_path)
        for t in m.get("tracks", []) or []:
            tid = t.get("track_id")
            wav = t.get("source_audio_path") or t.get("audio_path")
            if tid and wav:
                raw_track_map[tid] = str(wav)
    if not raw_track_map:
        # 兜底 · 从 input_manifest.json 读
        input_manifest = run_dir / "input_manifest.json"
        if input_manifest.is_file():
            im = _load_json(input_manifest)
            for t in im.get("tracks", []) or []:
                tid = t.get("track_id")
                wav = t.get("audio_path") or t.get("source_audio_path")
                if tid and wav:
                    raw_track_map[tid] = str(wav)
    if not raw_track_map:
        print(f"[re_iterate] ERROR: raw_track_map 无法从 run 元数据构造", file=sys.stderr)
        return 100

    render_root = run_dir / "second_round_clips"
    render_root.mkdir(exist_ok=True)

    cmd = [
        sys.executable, str(iterate_script),
        "--candidate-json", str(sr_cands_path),
        "--transcript-dir", str(transcript_dir),
        "--raw-track-map", json.dumps(raw_track_map, ensure_ascii=False),
        "--policy-json", str(sr_policy_path),
        "--use-optuna",
        "--render-root", str(render_root),
        "--out", str(sr_trace_path),
    ]
    if args.nisqa_python:
        cmd += ["--nisqa-venv-python", str(args.nisqa_python)]

    print(f"[re_iterate] cmd: {' '.join(cmd[:6])} ... --use-optuna")
    proc = subprocess.run(cmd, check=False)
    print(f"[re_iterate] iterate_until_clean exit_code={proc.returncode}")

    if not sr_trace_path.is_file():
        print(f"[re_iterate] ERROR: trace 没生成: {sr_trace_path}", file=sys.stderr)
        return 100

    trace = _load_json(sr_trace_path)
    # 消费 trace · 更新 verified_edl.json 里 REJECTED 候选的 params + verdict
    updated = 0
    still_escaped = 0
    trace_by_cid = {r.get("candidate_id"): r for r in trace.get("results", [])}
    for c in all_cands:
        cid = c.get("candidate_id")
        if cid not in rejected_ids:
            continue
        r = trace_by_cid.get(cid)
        if not r:
            continue
        if r.get("converged"):
            fp = r.get("final_params") or {}
            for k, v in fp.items():
                c[k] = v
            c["verdict"] = "SECOND_ROUND_PASSED"
            c["second_round_iterations_used"] = r.get("iterations_used")
            updated += 1
        else:
            c["verdict"] = "SECOND_ROUND_ESCAPED"
            c["second_round_iterations_used"] = r.get("iterations_used")
            c["second_round_escape_reason"] = r.get("final_verdict")
            still_escaped += 1

    verified["_second_round_at_utc"] = _utc_now()
    verified["_second_round_summary"] = {
        "total_rejected": len(rejected_ids),
        "second_round_passed": updated,
        "second_round_escaped": still_escaped,
    }
    _write_json(verified_path, verified)

    summary = {
        "run_dir": str(run_dir),
        "rejected_count": len(rejected_ids),
        "second_round_passed": updated,
        "second_round_escaped": still_escaped,
        "trace_path": str(sr_trace_path),
        "second_round_candidates_json": str(sr_cands_path),
        "second_round_policy_json": str(sr_policy_path),
    }
    _write_json(run_dir / "second_round_summary.json", summary)
    print(f"[re_iterate] SUMMARY: {json.dumps(summary, ensure_ascii=False)}")
    return 0 if still_escaped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
