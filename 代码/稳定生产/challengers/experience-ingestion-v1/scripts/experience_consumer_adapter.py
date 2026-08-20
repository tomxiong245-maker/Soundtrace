#!/usr/bin/env python3
"""experience-ingestion-v1: 未来统筹 Agent 的只读入口。

给定 case_store（已产出），返回：
- 当前经验摘要
- 相关历史案例（按 episode_id / reason_key 过滤）
- 规则建议
- 训练准备度
- 明确的禁止动作

它是一个确定性 Python consumer，不需要 LLM。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 允许作为脚本或模块使用
sys.path.insert(0, str(Path(__file__).resolve().parent))

from consume_experience_cases import (
    build_summary, build_rule_recommendations, iter_cases,
    load_exclusions, load_quarantine,
)
from check_training_readiness import build_readiness


PROHIBITED = {
    "can_change_production_rules": False,
    "can_approve_edl": False,
    "can_train_model": False,
    "can_read_cases": True,
}


def query(case_store: Path, *,
          episode_id: str | None = None,
          reason_key: str | None = None,
          max_examples: int = 20) -> dict[str, Any]:
    cases = iter_cases(case_store)
    exclusions = load_exclusions(case_store)
    quarantine = load_quarantine(case_store)

    filtered = cases
    if episode_id:
        filtered = [c for c in filtered if c["episode_id"] == episode_id]
    if reason_key:
        filtered = [c for c in filtered if c["candidate"]["reason_key"] == reason_key]

    return {
        "schema_version": "experience-adapter-v1",
        "capabilities": PROHIBITED,
        "summary": build_summary(cases, exclusions, quarantine),
        "matched_cases_count": len(filtered),
        "matched_cases": filtered[:max_examples],
        "recommendations": build_rule_recommendations(cases),
        "training_readiness": build_readiness(case_store),
        "prohibited_actions": [
            "modify_production_rules",
            "approve_edl",
            "train_model",
            "write_model_weights",
        ],
        "notes": (
            "只读适配器：不修改任何生产规则、不批准 EDL、不训练模型。"
            "统筹 Agent 若需变更，必须走 Challenger + 冻结 benchmark + 独立复核 + 人工晋升。"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-store", required=True)
    ap.add_argument("--episode-id", default=None)
    ap.add_argument("--reason-key", default=None)
    ap.add_argument("--max-examples", type=int, default=20)
    ap.add_argument("--out", default=None, help="输出 JSON 文件（可选）")
    args = ap.parse_args(argv)

    doc = query(
        Path(args.case_store).resolve(),
        episode_id=args.episode_id,
        reason_key=args.reason_key,
        max_examples=args.max_examples,
    )

    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
