#!/usr/bin/env python3
"""learning-pattern-from-case-v1 · 从 case 蒸馏模式 · 无量化.

用户 2026-08-19 明确:
- 数据源: learned_examples_*.md + case_store + human_decisions.json (人审)
- 权重: mentor > 用户人审 > case_store
- 每期 pipeline 完自动追加 (未来)
- 输出到 output/pattern_summary.md (无量化 · 无数字)

首版 · 骨架 · 未实现主逻辑 · 只保证:
- 参数解析
- 输出目录自动创建
- fail-closed (任何数据源缺 · skip · exit 0)
- 输出 markdown 明确标注"未实现"
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _probe(path: Path | None) -> str:
    if path is None:
        return "未提供路径"
    if not path.exists():
        return f"缺失 · skip (path={path})"
    return f"存在 (path={path})"


def main() -> int:
    ap = argparse.ArgumentParser(description="learning-pattern-from-case-v1 骨架")
    ap.add_argument("--knowledge-dir", required=True, type=Path,
                    help="最终交付 knowledge/ 目录 (含 learned_examples_*.md)")
    ap.add_argument("--case-store-dir", type=Path, default=None,
                    help="case_store 根目录 (可选)")
    ap.add_argument("--human-decisions-json", type=Path, default=None,
                    help="human_decisions.json 路径 (可选 · 逐项人审来源)")
    ap.add_argument("--out", required=True, type=Path,
                    help="输出 markdown 路径 · 通常 output/pattern_summary.md")
    args = ap.parse_args()

    # fail-closed · 输出目录自动创建
    args.out.parent.mkdir(parents=True, exist_ok=True)

    mentor_status = _probe(args.knowledge_dir)
    case_status = _probe(args.case_store_dir)
    human_status = _probe(args.human_decisions_json)

    body = f"""# Pattern Summary (SKELETON · 未实现主逻辑)

Generated: {_utc_now()}
Skill: learning-pattern-from-case-v1
Status: **骨架** · 首版只保证 fail-closed 与契约测试通过 · 主蒸馏逻辑待实装

## 数据源探测
- mentor (knowledge-dir · 权重 高): {mentor_status}
- 用户 case_store (权重 中): {case_status}
- human_decisions 人审 (权重 中): {human_status}

## Q3 未解锁
mentor gold case 未达 ≥ 100 · embedding retrieval 未启用 · 当前只做模式蒸馏.

## 输出格式 (未来主逻辑)
按类型分节: filler / rep / long_pause / self_corr / semantic.
每节内:
- 剪的模式 [source=mentor] [weight=高]
- 保留的模式 [source=mentor] [weight=高]
- 用户人审模式 [source=user] [weight=中]
- human_decisions [source=EP03-review-product-v1] [weight=中]
- 全语义 · 无数字 · 无阈值 · 无百分比

## Wiring
**未 wire 进 pipeline** · 用户明确 few-shot 先不塞 LLM · 不改 llm_full_pipeline.py · 不改 run_end_to_end.py.
"""
    args.out.write_text(body, encoding="utf-8")
    print(f"[extract_pattern] SKELETON · out={args.out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
