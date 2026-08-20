---
name: candidate-family-integration
description: 将负责人批准接入的咳嗽与说错重来检测器规范化为 canonical N 轨审核候选；不产生真人决定、EDL 或自动语义删剪。触发词：候选家族、candidate family、self_correction 接入、cough_like、咳嗽接线、说错重来、canonical review source、候选规范化、adapter、integration。
status: deprecated
deprecated_at: "2026-08-18"
superseded_by:
  - candidate-generation-and-gate
owner: champion
entry_tool: build_candidate_family_bundle
related_tools:
  - build_candidate_family_bundle
  - detect_self_correction_wordlevel
  - detect_transient_events
preconditions:
  - "run 已有有效 P0 词级转写和 run-local 音频"
  - "integration governance registry 已冻结并允许对应 capability"
postconditions:
  - "candidate_source.json 保持 canonical schema，并记录 detector/rules/input SHA"
  - "self_correction 与 cough_like 全部标为 human_review_required"
---

# 候选家族接入（DEPRECATED）

> ⚠️ **本 skill 已停用**（deprecated_at 见 frontmatter）。
> **继任 skill**：`candidate-generation-and-gate` · `build_candidate_family_bundle` · `detect_self_correction_wordlevel` · `detect_transient_events`
> **不要**在新任务里激活本 skill；如触发词命中，直接改激活继任 skill。
> 原正文与详细流程见 2026-08-18 之前的 git 历史或 `candidate-family-integration/flow_boundary.md`（若存在，仅作历史参考）。
