---
name: learning-pattern-from-case-v1
description: 从 case + 逐项人审中学 PREFERENCE 层 (剪哪些) · 输出 LLM 可读文件或直接指挥 LLM · 绝不触碰 PARAMETER 层 (怎么剪) · challenger 状态 · 未来取代 experience-ingestion PREFERENCE 层
status: challenger
entry_tool: scripts/extract_pattern_from_cases.py
covers_rules: [§21, §14]
related_skills: [learning-and-experience]
replaces_planned: experience-ingestion-v1-PREFERENCE-layer
layer_scope: PREFERENCE_only_never_PARAMETER
role: PREFERENCE 层学习总入口
upstream:
  - case_store/cases/EP*.jsonl (历史 case)
  - learned_examples_EP*_MENTOR.md (mentor 反推位置)
  - human_decisions*.json (逐项人审)
downstream:
  - output/pattern_summary.md (LLM 读的语义模式 md)
  - LLM Stage 3.5.5 prompt 直接注入 (阶段 3 激活后)
triggers:
  - PREFERENCE 学习
  - 从 case 和审查学
  - 给 LLM 的文件
  - 指挥 LLM
  - preference layer
  - 候选层学习入口
  - pattern 蒸馏
  - narrative 模式
---

## Purpose
从 learned_examples_EP03_MENTOR.md (56 mentor 反推位置) · 用户 case_store (EP03 11 case · EP04 已冻结) · human_decisions.json (逐项人审) · 蒸馏出"哪些词类型该剪 / 哪些不该剪"的**模式** · 给 LLM 参考 · 但不塞 few-shot (用户 2026-08-19 明确 few-shot 先不做).

## Preconditions
- learned_examples_EP03_MENTOR.md 存在
- case_store 里 EP03.jsonl 存在 (非 FROZEN)
- human_decisions*.json 存在 (可选 · 逐项人审来源)

## Postconditions
- 输出到 output/pattern_summary.md
- 无量化 · 无数字
- 每段附来源标签 (mentor / user / human_decisions)
- 每段附权重 (高 / 中 / 低)

## Hooks
- 触发词: "learning pattern from case" · "案例模式学习" · "从案例学模式"
- entry_tool: scripts/extract_pattern_from_cases.py

## Fail-closed
- 若 learned_examples_*.md 缺 · skip 该数据源
- 若 case_store 缺 · skip
- 全部缺 · output 为空 markdown · 不 break pipeline

## Q3 · 解锁条件 (未来)
- mentor gold case ≥ 100 才解锁 embedding retrieval
- 当前只用 mentor 反推位置 56 · 未达标 · 只做模式蒸馏 · 不做 embedding

## Data Weight
- mentor (未来若真拿到): 权重最高
- 用户人审 (human_decisions): 权重中
- case_store: 权重中

## Wiring 状态
- **NOT wired into pipeline** · 首版骨架 · 不改 llm_full_pipeline.py · 不改 run_end_to_end.py
- 未来每期 pipeline 完可自动追加调用 (待用户批准)

### PARAMETER 层硬边界 (2026-08-20)
用户明确要求参数与候选分离. 本 skill 只学 PREFERENCE (剪哪些) · **绝不触碰 PARAMETER (怎么剪)**. 完整 8 条 checklist 与被取代方证据见 HANDOFF_ROADMAP.md.
