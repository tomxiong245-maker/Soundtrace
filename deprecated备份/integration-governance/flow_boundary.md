# integration-governance · flow boundary

## ✅ 本 skill 允许做的

- 维护 `main/knowledge/integration_governance/owner_attested_mainline.v1.json`（能力接入登记）
- 校验新 run 是否只调用已 `OWNER_ATTESTED_INTEGRATE` 的能力
- 冻结每次新 run 的接入 registry SHA 到 `run_identity.json`
- 记录 evidence labels：`OWNER_ATTESTED_INTEGRATE` / `EVIDENCE_VERIFIED_INTEGRATE` / `INTEGRATED_PENDING_REAL_RUN` / `REOPENED_ON_ISSUE` / `ISOLATED_NOT_MAINLINE` / `DEFERRED`
- 缺陷重开时**只影响相关能力**，不改历史 run 证据

## ❌ 本 skill 禁止做的（**严格分离**）

- **`OWNER_ATTESTED_INTEGRATE` ≠ `human_accept`**（组件接入 ≠ 语义批准）
- **`OWNER_ATTESTED_INTEGRATE` ≠ 独立复核结果**（复核是**接入后**做的）
- **`OWNER_ATTESTED_INTEGRATE` ≠ Champion 晋升**（Champion 需要独立 benchmark + rollback 演练）
- **`OWNER_ATTESTED_INTEGRATE` ≠ 发布授权**（发布需要整片试听 + QC）
- **绝不签发 `human_approved`**（只有真人整片试听能）
- **绝不签发 `autocut_policy = APPROVED`**（policy 晋升需 policy_promotion.py 独立验证）
- **绝不删除已有 registry 条目**（只能标 `DEFERRED` 或 `REOPENED_ON_ISSUE`）

## 依赖的工具（tools.json 已登记）

- `validate_integration_governance`（main/orchestrator/integration_governance.py，codex 加）
- `policy_promotion`（main/orchestrator/policy_promotion.py，独立门）

## 关键 registry 字段

```json
{
  "capability_id": "automix_adapter",
  "status": "OWNER_ATTESTED_INTEGRATE",
  "attested_at": "2026-08-18",
  "attestation_scope": "可以进入未来主线 run，但不代表语义批准或独立复核",
  "verification_pending": ["independent audit", "cross-episode benchmark", "rollback rehearsal"],
  "reopen_on_issue": "只重开本能力，不改历史 run 证据"
}
```

## 违反本边界的证据

- （无历史违反 —— codex 一开始就把 governance 做得很严）
