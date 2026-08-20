# TASK CONTRACT · learning-pattern-from-case-v1

## Preconditions (读)
1. `learned_examples_EP03_MENTOR.md` 存在于 knowledge-dir (若缺 · skip 该源)
2. `case_store/cases/EP*.jsonl` 存在 (若缺 · skip 该源) · EP*.FROZEN 排除
3. `human_decisions*.json` 存在 (若缺 · skip 该源 · 只读语义字段)

## Postconditions (写 · 3 条)
1. **只写** `output/pattern_summary.md` · 不动 case_store · 不动 knowledge/ · 不动 pipeline 代码
2. 输出 markdown **无数字 · 无阈值 · 无百分比** · 全语义模式
3. 每段带 `[source=mentor|user|human_decisions]` 与 `[weight=高|中|低]` 标签

## Fail-closed
- 任一数据源缺 · skip 该源 · 继续
- 全部数据源缺 · 生成空 markdown (含"无数据源可用"说明) · **exit 0** · 不 break pipeline
- 输出目录不存在 · 自动 mkdir
- 严禁抛未捕获异常终止调用方 pipeline

## 严禁
- 不 wire 进 pipeline (llm_full_pipeline.py / run_end_to_end.py)
- 不改 knowledge/*.md
- 不改 case_store/**
- 不写数字 / 阈值 / 百分比
- 不做 embedding retrieval (Q3 未解锁)

## Wiring
**首版不 wire** · 未来若接入 · 由 pipeline 末尾调用一次 · 幂等覆盖 `output/pattern_summary.md`.
