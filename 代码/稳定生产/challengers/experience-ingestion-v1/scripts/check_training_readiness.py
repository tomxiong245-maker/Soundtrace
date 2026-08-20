#!/usr/bin/env python3
"""experience-ingestion-v1: 训练准备度门禁。

只做门禁检查，不训练模型。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from consume_experience_cases import iter_cases


def build_readiness(case_store: Path) -> dict[str, Any]:
    cases = iter_cases(case_store)
    n = len(cases)
    episodes = sorted({c["episode_id"] for c in cases})
    reviewers = sorted({c["label"]["reviewer"] for c in cases if c["label"].get("reviewer")})
    adjusts = sum(1 for c in cases if c["label"]["decision"] == "adjust")
    rejects = sum(1 for c in cases if c["label"]["decision"] == "reject")
    reject_plus_adjust_ratio = ((rejects + adjusts) / n) if n else 0.0

    has_independent_benchmark = False
    has_independent_review = False
    has_rollback_drill = False

    reasons: list[str] = []
    if n < 500:
        reasons.append(f"有效数据量不足：{n} < 500")
    # NOTE 2026-08-17: 已按用户指令移除 episodes >= 10 与 reviewers >= 2 门。
    if reject_plus_adjust_ratio < 0.2:
        reasons.append(
            f"reject+adjust 比例过低：{reject_plus_adjust_ratio:.2%} < 20%"
        )
    if not has_independent_benchmark:
        reasons.append("没有冻结独立 benchmark")
    if not has_independent_review:
        reasons.append("没有独立复核")
    if not has_rollback_drill:
        reasons.append("没有回滚演练")

    return {
        "schema_version": "training-readiness-v1",
        "status": "NOT_READY",
        "model_trained": False,
        "checks": {
            "valid_episodes": len(episodes),
            "valid_cases": n,
            "adjust_count": adjusts,
            "reject_plus_adjust_ratio": reject_plus_adjust_ratio,
            "review_mode_is_optional": True,
            "reviewer_count": len(reviewers),
            "reviewers": reviewers,
            "has_independent_benchmark": has_independent_benchmark,
            "has_independent_review": has_independent_review,
            "has_rollback_drill": has_rollback_drill,
        },
        "reasons": reasons,
        "prohibited_actions": [
            "train_model",
            "write_model_weights",
            "modify_production_rules",
            "auto_approve_edl",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-store", required=True)
    ap.add_argument("--reports-dir", required=True)
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args(argv)

    case_store = Path(args.case_store).resolve()
    reports_dir = Path(args.reports_dir).resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)

    doc = build_readiness(case_store)
    (reports_dir / "training_readiness.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "training_readiness.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps({
        "status": doc["status"],
        "reasons": doc["reasons"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    # 允许作为脚本导入 consume_experience_cases
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
