---
name: editing-experience-distiller
description: 从已经逐项完成真人审核的多轨播客剪辑案例中提炼可复用的 Skill 经验、规则假设和下一轮 Challenger 任务。适用于"审核已结束、想知道系统从中学到什么""为口癖、重复、停顿、串音或说错重来总结经验"以及"需要把历史案例交给统筹 Agent 检索"时；绝不用于自动批准删剪、修改生产规则、训练模型或覆盖已有产物。触发词：案例记忆、case memory、editing experience、preference snapshot、经验蒸馏、historical case lookup、reason_key 相似案例、audit history、labels lake 消费、experience_context、preference_learning。
status: deprecated
deprecated_at: "2026-08-18"
superseded_by:
  - learning-and-experience
owner: challenger:experience-ingestion-v1
entry_tool: experience_consumer_adapter
related_tools:
  - apply_preference_snapshot
preconditions:
  - "已有逐项完成真人审核的 run（如 EP03 / EP04 human_approved）"
  - "case-store 快照可读（main/runs/LABEL-LEARNING-v3-*/preference_snapshot/）"
postconditions:
  - "产出经验条目 / 规则假设 / 下一轮 challenger 任务书（写入独立 run 目录，不覆盖生产规则）"
---

# 案例经验蒸馏（DEPRECATED）

> ⚠️ **本 skill 已停用**（deprecated_at 见 frontmatter）。
> **继任 skill**：`learning-and-experience` · `apply_preference_snapshot`
> **不要**在新任务里激活本 skill；如触发词命中，直接改激活继任 skill。
> 原正文与详细流程见 2026-08-18 之前的 git 历史或 `editing-experience-distiller/flow_boundary.md`（若存在，仅作历史参考）。
