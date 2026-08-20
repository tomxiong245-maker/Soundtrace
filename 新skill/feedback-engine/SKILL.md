---
name: feedback-engine
description: 反馈闭环唯一入口 · 读 + 写完整合并 · CLAUDE.md §18 硬规则. **读方向** (决策前): retrieve_before_decision(candidate, decision_type, episode_id) 检索 current.session_feedback.jsonl + labels_lake.feedback[] 里最匹配规则 (verdict priority DESC + match score DESC + timestamp DESC). **写方向** (决策后): analyze_feedback(candidate, verdict, note) 决策链 TOOL_APPLY (查 tools.json 48 项) → DOC_REFERENCE (查 YouTube 学习总结/Preflight/mentor briefing/功能说明) → SESSION_FEEDBACK_PATCH (最后手段 · append). 合并 v20.9 feedback-first-retrieval + v20.11 user-feedback-analyzer. 触发词: 反馈闭环, feedback engine, 决策前查反馈, 决策后路由, retrieve before decide, analyze after review.
status: active
owner: champion
entry_tool: feedback_engine
related_tools:
  - feedback_engine  # 主入口 (retrieve + analyze 两个 mode)
  - load_session_feedback
  - build_labels_lake
  - generate_comprehensive_cut
  - user_feedback_analyzer  # legacy alias · 同 feedback_engine analyze
  - feedback_first_retrieval  # legacy alias · 同 feedback_engine retrieve
preconditions:
  - "candidate 有 filler_token / reason_key / candidate_kind 之一 (retrieve)"
  - "有 user verdict + note 备注 (analyze)"
  - "current.session_feedback.jsonl 存在 (§20 单一 SOT)"
postconditions:
  - "retrieve: 返回 top-N 反馈规则供 apply"
  - "analyze: 输出 action_type (TOOL_APPLY | DOC_REFERENCE | SESSION_FEEDBACK_PATCH) + reasoning_chain"
  - "SESSION_FEEDBACK_PATCH 是最后手段 · 需 escalation flag"
---

# feedback-engine · Skill (合并版)

**版本**：v220.merged（2026-08-18）· CLAUDE.md §18 硬规则 · 用户"两个 skill 需要合并"

## 一句话

**反馈闭环唯一 skill** —— 决策前 retrieve，决策后 analyze，读+写全部合并。

## 用户明确要求（2026-08-18）

- （前）"上线一个规则甚至 skill · 同一个东西先从就近反馈检索"
- （后）"新建一个 skill · 拿到用户反馈, 先分析原因, 找原因后先看已有工具, 没有借鉴知识沉淀文档, 最后才补丁"
- （合并）"这两个 skill 需要合并"

## 决策链

### 读方向（决策前 · retrieve）

```
retrieve_before_decision(candidate, decision_type, episode_id, max_return=5)
    ↓
读 current.session_feedback.jsonl (§20 单一 SOT) + labels_lake.feedback[]
    ↓
匹配候选 pattern → 打分 (exact filler_token 10 > reason_key 5 > context 3 > any 1)
    ↓
排序 (verdict_priority DESC · match_score DESC · timestamp DESC)
    ↓
返回 top-N 供调用方 apply
```

**Verdict 优先级**：
`never_cut/forbidden (10)` > `needs_extension (8)` > `cut_scope_too_wide (8)` > `policy/pause (4-6)` > `accept (3)`

### 写方向（决策后 · analyze）

```
analyze_feedback(candidate, user_verdict, user_note, episode_id)
    ↓
STEP 1 · Parse note → root_cause 关键词
    ↓
STEP 2 · TOOL_APPLY (confidence 0.9)  ← 查 tools.json 48 项
    ↓ 无
STEP 3 · DOC_REFERENCE (confidence 0.7) ← 查 YouTube § 1-5 / Preflight / mentor briefing
    ↓ 无
STEP 4 · SESSION_FEEDBACK_PATCH (confidence 0.5) ← 最后手段 · append current.jsonl
```

## CLI 用法

```bash
# 读
python feedback_engine.py retrieve \
    --candidate-json '{"filler_token":"呃","reason_key":"filler_hesitation"}' \
    --decision-type cut_boundary

# 写
python feedback_engine.py analyze \
    --candidate-json '{"filler_token":"一些","reason_key":"immediate_repetition"}' \
    --verdict "never_cut" \
    --note "一些不应该剪 内容词" \
    --apply
```

## Python API

```python
from feedback_engine import (
    retrieve_before_decision, is_never_cut,   # 读
    analyze_feedback, apply_decision,          # 写
)

# 决策前
fb_list = retrieve_before_decision(candidate, "cut_boundary", "EP04")
if fb_list and fb_list[0]["verdict"] == "never_cut":
    return SKIP
elif fb_list:
    apply_rule(fb_list[0])
else:
    apply_default()

# 决策后
decision = analyze_feedback(candidate, "never_cut", "一些不该剪")
result = apply_decision(decision, dry_run=False)
```

## 违反 = 破坏契约

- **决策前不调 retrieve** → CLAUDE.md §18 违反
- **决策后跳过 STEP 2/3 直接 patch** → 补丁滥用
- **不产 reasoning_chain** → 不可追溯

## Legacy 兼容

老代码 import 保持工作：

```python
# 旧 (仍支持)
from feedback_first_retrieval import retrieve_before_decision
from user_feedback_analyzer import analyze_feedback

# 新 (推荐)
from feedback_engine import retrieve_before_decision, analyze_feedback
```
