---
name: label-learning-driver
description: Turn itemized human podcast-editing labels and reviewer notes into hash-bound, explainable machine suggestions, a leakage-safe backtest, and a read-only shadow prediction. Use after new human-review labels arrive, before creating a future review package, or when an Agent needs to learn from prior accept/reject decisions without changing human decisions, EDLs, or audio. 触发词：偏好学习、label learning、labels lake、人审反馈回填、accept reject 学习、preference snapshot、shadow prediction、hash-bound backtest、learning driver、reviewer notes 训练、audit feedback ingestion、run refresh_lake_and_regate、online learning 闭环。
status: deprecated
deprecated_at: "2026-08-18"
superseded_by:
  - learning-and-experience
owner: champion
entry_tool: label_learning_driver
related_tools:
  - apply_preference_snapshot
  - run_development_benchmark
preconditions:
  - "已有 accept/reject 已保存的 human_decisions.json"
  - "配套 review_package.json 与 candidates.json 完整"
postconditions:
  - "在独立学习 run 目录产出 shadow 预测；不写 Champion、不写 EDL、不写音频"
---

# 标签学习驱动器（DEPRECATED）

> ⚠️ **本 skill 已停用**（deprecated_at 见 frontmatter）。
> **继任 skill**：`learning-and-experience` · `apply_preference_snapshot` · `run_development_benchmark`
> **不要**在新任务里激活本 skill；如触发词命中，直接改激活继任 skill。
> 原正文与详细流程见 2026-08-18 之前的 git 历史或 `label-learning-driver/flow_boundary.md`（若存在，仅作历史参考）。
