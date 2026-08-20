#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compute_mos_delta.py · Challenger nisqa-cutverify-v1 · SKELETON.

计算「剪前 vs 剪后」同一片段的 NISQA MOS 差分。
本文件是 Challenger 骨架 · 所有真正推理路径均 raise NotImplementedError。

关系说明（相对 skills/cut-verify Champion 的 4 项 check）：
    Check 5 · MOS delta 是 Check 5 的**delta mode 编排层**，负责：
      1. 对 --before-clip 与 --after-clip 分别调用 check_nisqa_mos（overall 或 delta 均可）
      2. 计算 delta = after_mos - before_mos
      3. 输出统一 JSON 给下游 route_by_mos.py

    ⚠️ **补充非替代**：此计算完全独立于 Check 1/2/3/4，仅在前 4 项 verdict 均通过时被调用。
    ⚠️ 骨架期不做任何真实推理。

调用契约：
    python compute_mos_delta.py \
        --before-clip <path/to/before.wav> \
        --after-clip  <path/to/after.wav> \
        --out-json    <path/to/delta.json>

输出 JSON schema（骨架期 · 字段占位为 null）：
    {
      "before_clip": "<str>",
      "after_clip":  "<str>",
      "before_mos":  <float | null>,
      "after_mos":   <float | null>,
      "delta":       <float | null>,   // = after_mos - before_mos
      "engine":      "nisqa-2.0",
      "status":      "SKELETON"
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict


ENGINE_ID = "nisqa-2.0"


def score_clip(clip_path: str) -> Dict[str, float]:
    """给单条 clip 打 MOS · 内部委托 check_nisqa_mos.predict_mos（占位）。

    真实实现（sandbox 阶段）会 import check_nisqa_mos.predict_mos。
    """
    raise NotImplementedError("score_clip · skeleton only")


def compute_mos_delta(before_clip: str, after_clip: str) -> Dict[str, Any]:
    """入口 · 前后 clip 差分 · 骨架期占位。

    Delta 语义：
        delta = after_mos - before_mos
        delta < 0  → 剪后质量下降
        delta ≥ 0  → 剪后质量不劣化甚至改善（e.g., 剪掉了噪声段）
    """
    raise NotImplementedError("compute_mos_delta · skeleton only")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check 5 · Before/After NISQA MOS delta · SKELETON",
    )
    parser.add_argument("--before-clip", required=True, help="pre-cut WAV clip path")
    parser.add_argument("--after-clip", required=True, help="post-cut/rendered WAV clip path")
    parser.add_argument("--out-json", required=True, help="output JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = compute_mos_delta(before_clip=args.before_clip, after_clip=args.after_clip)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
