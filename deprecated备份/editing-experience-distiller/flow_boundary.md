# editing-experience-distiller · flow boundary

## ✅ 本 skill 允许做的

- 从 `LABEL-LEARNING-*/preference_snapshot/aggregated.json` (65 records) 检索历史相似案例
- 每候选按 `reason_key` + `filler_token` 匹配 → 输出 `experience_context` 字段
- 提炼 5-10 条可复用 Skill 经验（如"C036 什麼类边界扩 100ms 用户 accept"）
- 生成 `case_memory.pre_review.json` + `review_bundle/case_memory.json` 侧车

## ❌ 本 skill 禁止做的

- **绝不自动批准删剪**（case memory 只是 signal，不是决定）
- **绝不修改生产规则**（观察不改）
- **绝不训练模型**（本项目无 LLM/DNN 训练）
- **绝不覆盖已有 case_store 或 preference_snapshot**
- **绝不把本轮 audit 加入 case memory**（防泄漏 —— 只用**上一期**结束的案例）
- **绝不改活跃 preference snapshot 指针**（那是 label-learning-driver 的职责）

## 依赖的工具（tools.json 已登记）

- `consume_experience_cases`（scripts/consume_experience_cases.py）
- `experience_consumer_adapter`（scripts/experience_consumer_adapter.py）
- `case_memory`（main/orchestrator/case_memory.py，codex 加）
- `build_case_memory`（tools.json 第 39 项，codex 加）

## 输出 schema

```json
{
  "reason_key_matches": <int>,
  "reason_key_accept_count": <int>,
  "reason_key_reject_count": <int>,
  "exact_token_matches": <int>,
  "exact_token_accept_count": <int>,
  "exact_token_reject_count": <int>,
  "case_store_relpath": "main/runs/LABEL-LEARNING-*/preference_snapshot/aggregated.json",
  "top_case_ids": ["EP03-...", ...]
}
```

## 违反本边界的证据

- （无历史违反 —— skill 是 read-only）
