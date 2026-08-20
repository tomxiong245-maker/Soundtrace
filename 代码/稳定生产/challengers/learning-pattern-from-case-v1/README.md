# learning-pattern-from-case-v1

**状态**: challenger · 骨架 · 未实现主逻辑 · **未 wire 进 pipeline**

## 一句话
从 mentor 反推位置 (56) + 用户人审 case + human_decisions.json (人审) 蒸馏"哪些词该剪 / 哪些不该剪"的**语义模式** · 给 LLM 参考 · 无量化 · 无数字.

## 用途
喂给 §21 / §14 相关 LLM prompt 段落作为背景知识 · **不作为 few-shot** (用户 2026-08-19 明确 few-shot 先不做).

## 数据源
| 来源 | 权重 | 说明 |
|---|---|---|
| learned_examples_EP03_MENTOR.md | 高 | mentor 反推位置 56 |
| case_store/cases/EP03.jsonl | 中 | 用户人审 11 case (EP04 FROZEN 排除) |
| human_decisions*.json (人审) | 中 | 逐项人审 · 只读语义字段 |

## 输入
- `--knowledge-dir` 最终交付 knowledge/ 目录
- `--case-store-dir` case_store 根 (可选)
- `--out` 输出 markdown 路径 (通常 `output/pattern_summary.md`)

## 输出
`output/pattern_summary.md` · 按类型 (filler / rep / long_pause / self_corr / semantic) 分节 · 每节列语义模式 · 附来源标签与权重 · 无数字.

## Q3 路径
mentor gold case ≥ 100 才解锁 embedding retrieval · 当前未达标.

## 位置
`交付/最终交付文档/代码/稳定生产/challengers/learning-pattern-from-case-v1/`

## 用户明确 (2026-08-19)
- Skill 名 `learning-pattern-from-case`
- challenger 隔离 M1
- 只专注最终交付
- few-shot section 先不开发
- 权重 mentor > 用户人审 > case_store
- 每期 pipeline 完自动追加 (未来)

### 接手者请先读 HANDOFF_ROADMAP.md · 定位 + 4 阶段路线图 + PARAMETER 硬边界
