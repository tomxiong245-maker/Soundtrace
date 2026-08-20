# feedback-engine · flow_boundary

## ✅ 本 skill 允许做的

- **读**（retrieve）：从 `current.session_feedback.jsonl` + `labels_lake.feedback[]` 检索匹配规则
- **写**（analyze）：解析 verdict+note → 路由到 TOOL_APPLY / DOC_REFERENCE / PATCH
- 提供 helper：`is_never_cut` / `analyze_feedback` / `apply_decision`
- CLI：`python feedback_engine.py {retrieve|analyze} ...`

## ❌ 本 skill 禁止做的

- **绝不改代码** · 只返回决策，调用方按 fix_plan 应用
- **绝不修改** `current.session_feedback.jsonl` 内容 · 只 append (且是 STEP 4 最后手段)
- **绝不改 tools.json** / `labels_lake`
- **决策必带 reasoning_chain** · 不可追溯的决策拒绝输出
- **补丁不是默认路径** · TOOL_APPLY > DOC_REFERENCE > PATCH 严格顺序

## 依赖

- `session_feedback/current.session_feedback.jsonl` (§20)
- `labels_lake.json`
- `tools.json` (供 TOOL_APPLY 关键词 map 引用)
- `YouTube学习总结.md` / `Preflight-checklist.md` / `mentor-briefing.md` (供 DOC_REFERENCE)

## 违反本边界的历史证据

- v215 tool 不查反馈直接用 EDL 窄边界 · C007/C034/C039 剪不干净
- v218-v219 每次反馈都直接 append session_feedback · 规则库膨胀（本 skill 修的问题）
