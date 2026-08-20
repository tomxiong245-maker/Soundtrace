#!/usr/bin/env python3
"""Generate a small, read-only context view from the canonical delivery facts.

The long Markdown files remain evidence and policy documents.  This script is
the cheap L0 context layer that an agent reads at the start of an ordinary
turn.  It never invents a current run: the source is the
CURRENT_DELIVERY_FACTS block in 当前项目进度.md.

Usage:
  python3 main/orchestrator/context_checkpoint.py --write
  python3 main/orchestrator/context_checkpoint.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUS_MD = PROJECT_ROOT / "统筹全局/当前项目进度.md"
SUMMARY_MD = PROJECT_ROOT / "统筹全局/当前状态摘要.md"
CHECKPOINT_JSON = PROJECT_ROOT / "统筹全局/当前交接检查点.json"
MARKER = re.compile(
    r"<!-- CURRENT_DELIVERY_FACTS:start -->\s*```json\s*(\{.*?\})\s*```\s*<!-- CURRENT_DELIVERY_FACTS:end -->",
    re.DOTALL,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_facts() -> tuple[dict[str, Any], str]:
    content = STATUS_MD.read_text(encoding="utf-8")
    match = MARKER.search(content)
    if not match:
        raise ValueError("CURRENT_DELIVERY_FACTS block is missing")
    facts = json.loads(match.group(1))
    if facts.get("schema_version") != "current-delivery-facts-v1":
        raise ValueError("CURRENT_DELIVERY_FACTS has an unsupported schema")
    return facts, sha256_file(STATUS_MD)


def _ids(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "无"
    return ", ".join(str(value) for value in values)


def build_summary(facts: dict[str, Any], source_sha: str) -> str:
    review = facts.get("current_review") or {}
    best = facts.get("best_local_delivery") or {}
    asr = facts.get("asr") or {}
    music = facts.get("music") or {}
    benchmark = facts.get("development_benchmark") or {}
    learning = facts.get("learning_loop") or {}
    snapshot = learning.get("snapshot") or {}
    guards = learning.get("production_guard_policy") or {}
    promotion = learning.get("production_promotion") or {}
    return f"""# 当前状态摘要（自动生成，只读）

> 这是 L0 上下文视图，不是新的事实来源。唯一事实来源是
> `统筹全局/当前项目进度.md` 中的 `CURRENT_DELIVERY_FACTS`。
> 生成时间：{datetime.now(timezone.utc).isoformat()}
> source_sha256：`{source_sha}`

## 当前审核 run

- run：`{review.get('run_id', 'UNKNOWN')}`
- 状态：`{review.get('state', 'UNKNOWN')}`
- 候选：全部 `{review.get('candidate_count', '?')}`；审核包 `{review.get('review_package_candidate_count', '?')}`；前端可见 `{review.get('frontend_visible_candidate_count', '?')}`
- 规则：`{review.get('candidate_rules_version', 'UNKNOWN')}`
- ASR/语义复用：`{review.get('reuse_analysis_from', 'UNKNOWN')}` / `{review.get('reuse_semantic_from', 'UNKNOWN')}`
- 经验快照：`{review.get('experience_snapshot_id', '未记录')}`（只影响排序/提示，不产生决定）

## 已批准的本地交付

- run：`{best.get('run_id', 'UNKNOWN')}`
- 状态：`{best.get('state', 'UNKNOWN')}`
- 批准方式：`{best.get('approval_mode', 'UNKNOWN')}`

## 固定硬要求

- ASR：外部 `{asr.get('display_name', 'UNKNOWN')}`；复用源 `{asr.get('reused_from_run', 'UNKNOWN')}`。
- 音乐：`{music.get('template_id', 'UNKNOWN')}`；人声在 `{music.get('voice_start_seconds', '?')} s` 进入；片头淡出至 `{music.get('intro_fade_out_end_seconds', '?')} s`。
- 片尾：人声结束前 `{music.get('outro_fade_in_lead_seconds', '?')} s` 淡入，尾乐约 `{music.get('outro_music_tail_seconds', '?')} s`。
- 备注：每项最多 500 字；草稿不等于正式决定；机器预测不得伪装成人工标签。
- 当前不做：自动渲染/发布、未接候选家族的静默删剪、自动晋升 Champion。

## 生产规则与学习闭环

- 学习证据：`{learning.get('evidence_run_relpath', '未记录')}`；独立事件 `{snapshot.get('records', '?')}` 条，政策卡 `{snapshot.get('policy_cards', '?')}` 张。
- 活跃保护规则：`{guards.get('policy_id', '未记录')}` / `{guards.get('status', '未记录')}`；只会保留已知误报或升级人工审核。
- 自动语义删剪：`{learning.get('autocut_policy', 'NOT_APPROVED')}`；当前没有任何候选可因这套规则直接变成剪口。
- 晋升证据：`{promotion.get('evidence_run_relpath', promotion.get('report_relpath', '未记录'))}` / `{promotion.get('report_status', '未记录')}`。

## Benchmark 与下一道门

- benchmark：`{benchmark.get('contract', 'UNKNOWN')}` / `{benchmark.get('scorecard_status', 'UNKNOWN')}`。
- 规则：`NOT_MEASURED` 不等于没有问题，也不等于允许减少人审。
- 下一道门：真人逐项审核当前审核包；若有决定，先保存 feedback，再由 Agent 生成校准/预测报告。没有明确人审前不得 `resume`。

## 读取规则

- 普通任务只读本摘要 + 一份对应 Fxx。
- 需要版本/审核/ASR/音乐/渲染时，再读《版本同步与交付事实门》并跑同步检查。
- 需要命令、SHA 或历史原因时，按 run ID 精确读取；归档默认不读。
"""


def build_checkpoint(facts: dict[str, Any], source_sha: str) -> dict[str, Any]:
    review = facts.get("current_review") or {}
    learning = facts.get("learning_loop") or {}
    guards = learning.get("production_guard_policy") or {}
    autocut_policy = learning.get("autocut_policy") or "NOT_APPROVED"
    return {
        "schema_version": "context-handoff-checkpoint-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "status_relpath": "统筹全局/当前项目进度.md",
            "status_sha256": source_sha,
            "facts_schema": facts.get("schema_version"),
        },
        "current_review": {
            "run_id": review.get("run_id"),
            "state": review.get("state"),
            "run_relpath": review.get("run_relpath"),
            "candidate_count": review.get("candidate_count"),
            "review_package_candidate_count": review.get("review_package_candidate_count"),
            "frontend_visible_candidate_count": review.get("frontend_visible_candidate_count"),
            "candidate_rules_version": review.get("candidate_rules_version"),
            "reuse_analysis_from": review.get("reuse_analysis_from"),
            "reuse_semantic_from": review.get("reuse_semantic_from"),
        },
        "hard_requirements": {
            "music_template_id": (facts.get("music") or {}).get("template_id"),
            "voice_start_seconds": (facts.get("music") or {}).get("voice_start_seconds"),
            "feedback_max_chars": 500,
            "human_decision_required": True,
            "autocut_policy": autocut_policy,
            "do_not_render_without_user_gate": True,
        },
        "learning_loop": {
            "status": learning.get("status"),
            "evidence_run_relpath": learning.get("evidence_run_relpath"),
            "evidence_manifest_sha256": learning.get("evidence_manifest_sha256"),
            "autocut_policy": autocut_policy,
            "production_guard_policy": {
                "policy_id": guards.get("policy_id"),
                "policy_sha256": guards.get("policy_sha256"),
                "status": guards.get("status"),
            },
        },
        "next_gate": "CALIBRATION_REVIEW_REQUIRED:真人决定和备注未完成前不得 resume",
        "provenance_policy": "历史证据保留；此检查点只作交接索引，不替代 run manifest 或 CURRENT_DELIVERY_FACTS",
    }


def write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_outputs() -> dict[str, Any]:
    facts, source_sha = read_facts()
    write_atomic(SUMMARY_MD, build_summary(facts, source_sha))
    checkpoint = build_checkpoint(facts, source_sha)
    write_atomic(CHECKPOINT_JSON, json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n")
    return {"status": "PASS", "source_sha256": source_sha, "summary": str(SUMMARY_MD), "checkpoint": str(CHECKPOINT_JSON)}


def check_outputs() -> list[str]:
    facts, source_sha = read_facts()
    errors: list[str] = []
    if not SUMMARY_MD.is_file():
        errors.append("当前状态摘要.md is missing")
    else:
        summary = SUMMARY_MD.read_text(encoding="utf-8")
        if f"source_sha256：`{source_sha}`" not in summary:
            errors.append("当前状态摘要.md source SHA is stale")
        current_run = str((facts.get("current_review") or {}).get("run_id") or "")
        if current_run and current_run not in summary:
            errors.append("当前状态摘要.md does not mention current run")
    if not CHECKPOINT_JSON.is_file():
        errors.append("当前交接检查点.json is missing")
    else:
        checkpoint = json.loads(CHECKPOINT_JSON.read_text(encoding="utf-8"))
        if (checkpoint.get("source") or {}).get("status_sha256") != source_sha:
            errors.append("当前交接检查点.json source SHA is stale")
        current_run = (facts.get("current_review") or {}).get("run_id")
        if (checkpoint.get("current_review") or {}).get("run_id") != current_run:
            errors.append("当前交接检查点.json current run is stale")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write:
        print(json.dumps(write_outputs(), ensure_ascii=False, indent=2))
        return 0
    errors = check_outputs()
    print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
