---
name: podcast-editing-orchestrator
description: 多轨播客后期候选生成 + 真人校准 + 机器辅助试听编排（Challenger）。给定 N 轨对齐 mono WAV 与词级转写，生成全量候选、按风险挑选高风险全审与低风险代表样本，审核后生成机器预测和实验性试听草稿；不修改生产规则、Champion 与审核前端；一切写在独立 run 目录。触发词：多轨播客、剪辑 pipeline、machine_assisted_draft、候选生成、audit review、EDL 生成、automix、zero-touch、run_end_to_end、EP0X 剪辑、audio clips、三轨对齐、MFA 精修、autocut gate。
status: deprecated
deprecated_at: "2026-08-18"
superseded_by:
  - episode-triage-and-plan
  - candidate-generation-and-gate
  - audition-and-delivery
owner: champion
entry_tool: build_review_package
related_tools:
  - build_priority_review_page
  - build_semantic_transcript
  - build_filler_global_pause_candidates
  - create_aligned_ab_previews
  - approve_review_candidates
  - render_approved_edl
  - assemble_program
  - finish_approved_project
  - serve_review_ui
  - analyze_transition_qc
  - snap_candidate_boundaries
  - predict_cut_artifact
  - review_event_routes
preconditions:
  - "N 轨对齐 mono WAV + 词级 canonical 转写已就绪"
  - "手工 plan.json（研发前置条件）已写好"
postconditions:
  - "独立 run 目录内产出候选/审核包/双 EDL/双渲染；不改 Champion 与前端"
---

# 多轨播客编排（DEPRECATED）

> ⚠️ **本 skill 已停用**（deprecated_at 见 frontmatter）。
> **继任 skill**：`episode-triage-and-plan` · `candidate-generation-and-gate` · `audition-and-delivery` · `build_priority_review_page` · `build_semantic_transcript` · `build_filler_global_pause_candidates` · `create_aligned_ab_previews` · `approve_review_candidates` · `render_approved_edl` · `assemble_program` · `finish_approved_project` · `serve_review_ui` · `analyze_transition_qc` · `snap_candidate_boundaries` · `predict_cut_artifact` · `review_event_routes`
> **不要**在新任务里激活本 skill；如触发词命中，直接改激活继任 skill。
> 原正文与详细流程见 2026-08-18 之前的 git 历史或 `podcast-editing-orchestrator/flow_boundary.md`（若存在，仅作历史参考）。
