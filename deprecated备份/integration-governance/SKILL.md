---
name: integration-governance
description: 管理负责人确认的能力接入、独立验证、问题重开与回滚；把组件接入批准和语义删剪/发布授权严格分开。触发词：接入 governance、integration governance、OWNER_ATTESTED_INTEGRATE、能力接入登记、component adoption、独立复核、rollback、重开缺陷、SHA 冻结、mainline 接入、attestation。
status: deprecated
deprecated_at: "2026-08-18"
superseded_by:
  - governance-and-tool-registry
owner: champion
entry_tool: validate_integration_governance
related_tools:
  - validate_integration_governance
  - build_case_memory
  - automix_render_speech
preconditions:
  - "未来 run 尚未产生 reviewer 草稿或正式决定"
  - "integration registry 已绑定版本与 SHA"
postconditions:
  - "run 冻结 integration_governance.json，并记录负责人确认与独立验证状态"
  - "组件可进入主线，但不会生成 human_accept、EDL 或发布授权"
---

# 接入 governance（DEPRECATED）

> ⚠️ **本 skill 已停用**（deprecated_at 见 frontmatter）。
> **继任 skill**：`governance-and-tool-registry` · `validate_integration_governance` · `build_case_memory` · `automix_render_speech`
> **不要**在新任务里激活本 skill；如触发词命中，直接改激活继任 skill。
> 原正文与详细流程见 2026-08-18 之前的 git 历史或 `integration-governance/flow_boundary.md`（若存在，仅作历史参考）。
