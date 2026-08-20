#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_by_mos.py · Challenger nisqa-cutverify-v1 · SKELETON.

依据 NISQA MOS 分（absolute 或 delta）产出 Check 5 的最终 verdict。
本文件是 Challenger 骨架 · 判决函数体 raise NotImplementedError。

判决表（Check 5 独立层 · 骨架期不实现真实计算）：
    ┌──────────────────────────────┬───────────────────┐
    │  条件                        │  verdict          │
    ├──────────────────────────────┼───────────────────┤
    │  absolute_mos < 3.0          │  HUMAN_REVIEW     │
    │  delta_mos < -0.5            │  REJECT           │
    │  其它                        │  PASS             │
    └──────────────────────────────┴───────────────────┘

阈值来源：
    - absolute 3.0 · NISQA 论文 5 档语义 "Fair" 下限
    - delta   -0.5 · NISQA test-retest 95% CI ≈ ±0.3 + 0.2 缓冲

关系说明（相对 skills/cut-verify Champion 的 4 项 check）：
    Check 1..4 verdict 已 REJECT_*        → Check 5 不执行 · 沿用原判决
    Check 1..4 verdict 全 PASS
        + Check 5 = PASS                  → 保持原判决
        + Check 5 = HUMAN_REVIEW          → 升级为 NEEDS_HUMAN_REVIEW（不否决剪辑）
        + Check 5 = REJECT                → 升级为 REJECT_QUALITY_REGRESSION
    ⚠️ **补充非替代** —— Check 5 永不擅自 override 前 4 项的 REJECT。

调用契约：
    python route_by_mos.py \
        --mos-json <path/to/nisqa_result.json> \
        --out-json <path/to/verdict.json>

    输入 JSON（overall 或 delta 二选一 · 由上游脚本产出）：
        overall 模式: {"mos": <float>, ...}                          → 走 absolute 判决
        delta   模式: {"before_mos": <float>, "after_mos": <float>, "delta": <float>} → 走 delta 判决

输出 JSON schema（骨架期 · 字段占位为 null）：
    {
      "verdict": "PASS" | "HUMAN_REVIEW" | "REJECT" | null,
      "reason":  <str | null>,
      "source":  "absolute" | "delta",
      "engine":  "nisqa-2.0",
      "status":  "SKELETON"
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict


ENGINE_ID = "nisqa-2.0"

ABSOLUTE_HUMAN_REVIEW_THRESHOLD = 3.0    # mos < 3.0 → HUMAN_REVIEW
DELTA_REJECT_THRESHOLD = -0.5            # delta < -0.5 → REJECT

VERDICTS = ("PASS", "HUMAN_REVIEW", "REJECT")


def route_absolute(mos: float) -> Dict[str, Any]:
    """absolute 模式判决 · 骨架期占位。

    真实规则：
        if mos < 3.0 : HUMAN_REVIEW
        else         : PASS
    """
    raise NotImplementedError("route_absolute · skeleton only")


def route_delta(delta: float) -> Dict[str, Any]:
    """delta 模式判决 · 骨架期占位。

    真实规则：
        if delta < -0.5 : REJECT
        else            : PASS
    """
    raise NotImplementedError("route_delta · skeleton only")


def route_by_mos(mos_result: Dict[str, Any]) -> Dict[str, Any]:
    """入口 · 依据输入 JSON 结构自动选择 absolute / delta 分支 · 骨架期占位。"""
    raise NotImplementedError("route_by_mos · skeleton only")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check 5 · NISQA MOS-based verdict routing · SKELETON",
    )
    parser.add_argument("--mos-json", required=True, help="NISQA result JSON (absolute or delta)")
    parser.add_argument("--out-json", required=True, help="verdict JSON output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with open(args.mos_json, "r", encoding="utf-8") as f:
        mos_result = json.load(f)
    verdict = route_by_mos(mos_result)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
