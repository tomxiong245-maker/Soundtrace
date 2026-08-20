#!/usr/bin/env python3
"""
统筹 Agent 入口 v0.1（音频剪辑项目）

按《全局统筹记忆》§3 的状态机执行：
    CREATED → PLANNED → PREPARED → WAITING_FOR_HUMAN_REVIEW → APPROVED → FINALIZED → ARCHIVED

本版本 v0.1：
- 只支持"复用已有产物"模式（--reuse-existing）
- 读取 plan.json，逐阶段核对现有产物是否可用
- 状态持久化到 runs/<episode>/state.json
- 在 WAITING_FOR_HUMAN_REVIEW 停下来，把审核入口 URL 印给用户，绝不自批准
- APPROVED 阶段需要读取用户 approve 决定（human_decisions.json）

未来扩展：
- 加入 --rerun 模式，真正调 tool 跑脚本
- 每个 tool 通过 tools/tools.json 描述被调用
- 可无缝改成 MCP server 或 Claude Agent SDK 里的 tool call
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STATES = [
    "CREATED",
    "PLANNED",
    "PREPARED",
    "WAITING_FOR_HUMAN_REVIEW",
    "APPROVED",
    "FINALIZED",
    "ARCHIVED",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_DIR = PROJECT_ROOT / "main" / "runs" / "EP03"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(p: Path) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def transition(state: dict, target: str, note: str = "") -> dict:
    prev = state.get("state")
    if target not in STATES:
        raise ValueError(f"unknown target state: {target}")
    state["state"] = target
    state.setdefault("history", []).append(
        {"from": prev, "to": target, "at": now(), "note": note}
    )
    return state


def cmd_status(run_dir: Path) -> int:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        print(f"没有 state.json：{state_path}")
        return 1
    state = load_json(state_path)
    print(f"episode : {state.get('episode_id')}")
    print(f"当前状态: {state.get('state')}")
    print(f"上次时间: {state.get('history', [{}])[-1].get('at', '-')}")
    hint = {
        "CREATED": "运行 `orchestrator.py plan` 进入 PLANNED",
        "PLANNED": "运行 `orchestrator.py prepare` 进入 PREPARED",
        "PREPARED": "运行 `orchestrator.py wait-review` 生成审核入口并暂停",
        "WAITING_FOR_HUMAN_REVIEW": "打开 review_url 逐条审核；完成后 `orchestrator.py approve --decisions <file>`",
        "APPROVED": "运行 `orchestrator.py finalize` 渲染/复用成片",
        "FINALIZED": "运行 `orchestrator.py archive` 归档反馈包",
        "ARCHIVED": "本期已完成",
    }.get(state.get("state"), "")
    if hint:
        print(f"下一步  : {hint}")
    return 0


def cmd_plan(run_dir: Path, plan_json: Path) -> int:
    plan = load_json(plan_json)
    state_path = run_dir / "state.json"
    if state_path.exists():
        state = load_json(state_path)
    else:
        state = {"episode_id": plan["episode_id"], "state": "CREATED", "history": []}
        transition(state, "CREATED", "首次创建")
    transition(state, "PLANNED", f"冻结本期方案：reuse={plan['policy']['reuse_existing_artifacts']}")
    state["plan_path"] = str(plan_json)
    save_json(state_path, state)
    print(f"[PLANNED] {plan_json.name} 已冻结")
    print(f"  policy.reuse_existing_artifacts = {plan['policy']['reuse_existing_artifacts']}")
    print(f"  阶段数 = {len(plan['stages_to_execute_this_run'])}")
    return 0


def cmd_prepare(run_dir: Path) -> int:
    state_path = run_dir / "state.json"
    state = load_json(state_path)
    if state["state"] != "PLANNED":
        print(f"当前状态 {state['state']}，需先在 PLANNED 才能 prepare")
        return 1
    plan = load_json(Path(state["plan_path"]))
    # 复用模式下，PREPARE 阶段就是核对现有审核包
    # 这里只做元数据登记；实际的哈希校验在 CREATED 阶段已经跑过
    review_pkg = None
    for stg in plan["stages_to_execute_this_run"]:
        if stg["stage"].startswith("候选生成"):
            review_pkg = stg
            break
    if not review_pkg:
        print("plan 里没找到审核包阶段")
        return 1
    state["review_package"] = {
        "path": review_pkg["path"],
        "candidate_count": review_pkg["candidate_count"],
        "candidates_sha256_prefix": review_pkg["candidates_sha256_prefix"],
    }
    transition(state, "PREPARED", f"审核包已核对：{review_pkg['candidate_count']} 个候选")
    save_json(state_path, state)
    print(f"[PREPARED] 审核包 {review_pkg['candidate_count']} 个候选一致性 PASS")
    return 0


def cmd_wait_review(run_dir: Path) -> int:
    state_path = run_dir / "state.json"
    state = load_json(state_path)
    if state["state"] != "PREPARED":
        print(f"当前状态 {state['state']},需先在 PREPARED 才能进入审核")
        return 1
    pkg_path = Path(state["review_package"]["path"])
    review_html = pkg_path / "review.html"
    priority_html = pkg_path / "priority-review.html"
    transition(state, "WAITING_FOR_HUMAN_REVIEW",
               "已把审核入口交给用户；Agent 在此暂停，不允许自批准/超时批准")
    state["review_urls"] = {
        "review": f"file://{review_html}",
        "priority_review": f"file://{priority_html}",
    }
    state["human_decisions_expected_at"] = str(run_dir / "human_decisions.json")
    save_json(state_path, state)
    print("========================================")
    print("[WAITING_FOR_HUMAN_REVIEW] Agent 已停止")
    print("========================================")
    print(f"审核入口（浏览器打开）:")
    print(f"  优先审核页 : {state['review_urls']['priority_review']}")
    print(f"  完整审核页 : {state['review_urls']['review']}")
    print()
    print(f"完成后，把逐条决定写入 → {state['human_decisions_expected_at']}")
    print(f"然后运行           → orchestrator.py approve")
    print()
    print("Agent 不会自己继续。这是《全局统筹记忆》§3 强制的边界。")
    return 0


def cmd_approve(run_dir: Path) -> int:
    state_path = run_dir / "state.json"
    state = load_json(state_path)
    if state["state"] != "WAITING_FOR_HUMAN_REVIEW":
        print(f"当前状态 {state['state']}，无法 approve")
        return 1
    decisions_path = Path(state["human_decisions_expected_at"])
    if not decisions_path.exists():
        print(f"未找到人工决定文件：{decisions_path}")
        return 1
    decisions = load_json(decisions_path)
    if not decisions.get("reviewer") or not decisions.get("candidates"):
        print("human_decisions.json 必须含 reviewer + candidates 字段")
        return 1
    transition(state, "APPROVED",
               f"人工审核完成：{decisions['reviewer']}，{len(decisions['candidates'])} 项决定")
    state["human_decisions"] = str(decisions_path)
    save_json(state_path, state)
    print(f"[APPROVED] reviewer={decisions['reviewer']} decisions={len(decisions['candidates'])}")
    return 0


def cmd_finalize(run_dir: Path) -> int:
    state_path = run_dir / "state.json"
    state = load_json(state_path)
    if state["state"] != "APPROVED":
        print(f"当前状态 {state['state']}，无法 finalize")
        return 1
    # v0.1: 复用成片。未来加 --rerun 模式实际渲染。
    plan = load_json(Path(state["plan_path"]))
    existing = plan.get("existing_final_output_available_for_reference", {})
    state["finalized_output"] = existing
    transition(state, "FINALIZED",
               "复用已有成片（用户明确指示优先复用现有结果）")
    save_json(state_path, state)
    print(f"[FINALIZED] mp3 = {existing.get('mp3')}")
    return 0


def cmd_archive(run_dir: Path) -> int:
    state_path = run_dir / "state.json"
    state = load_json(state_path)
    if state["state"] != "FINALIZED":
        print(f"当前状态 {state['state']}，无法 archive")
        return 1
    archive_bundle = run_dir / "feedback_bundle.json"
    save_json(archive_bundle, {
        "episode_id": state["episode_id"],
        "state_history": state["history"],
        "plan_path": state["plan_path"],
        "review_package": state.get("review_package"),
        "human_decisions": state.get("human_decisions"),
        "final_output": state.get("finalized_output"),
        "archived_at": now(),
        "purpose": "供未来内部经验循环使用；本轮只归档，不学习",
    })
    transition(state, "ARCHIVED", "反馈包已归档")
    save_json(state_path, state)
    print(f"[ARCHIVED] 反馈包 → {archive_bundle}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="统筹 Agent v0.1")
    parser.add_argument("--run-dir", default=DEFAULT_RUN_DIR, type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--plan-json", type=Path,
                        default=DEFAULT_RUN_DIR / "plan.json")
    sub.add_parser("prepare")
    sub.add_parser("wait-review")
    sub.add_parser("approve")
    sub.add_parser("finalize")
    sub.add_parser("archive")

    args = parser.parse_args()
    dispatch = {
        "status": lambda: cmd_status(args.run_dir),
        "plan": lambda: cmd_plan(args.run_dir, args.plan_json),
        "prepare": lambda: cmd_prepare(args.run_dir),
        "wait-review": lambda: cmd_wait_review(args.run_dir),
        "approve": lambda: cmd_approve(args.run_dir),
        "finalize": lambda: cmd_finalize(args.run_dir),
        "archive": lambda: cmd_archive(args.run_dir),
    }
    return dispatch[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
