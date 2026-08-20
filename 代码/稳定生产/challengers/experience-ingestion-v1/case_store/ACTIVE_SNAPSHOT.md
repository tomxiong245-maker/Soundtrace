# experience-ingestion-v1：当前有效快照

当前用于案例检索、Skill/规则分析的快照是：

```text
case_store/two-state-v1-20260812-1627/
```

对应的真实运行和报告是：

```text
main/runs/EXPERIENCE-INGESTION-v1-20260812-1627/
```

## 当前结论

- 27 条逐项真人二态案例：EP03 11、EP04 16；accept 12、reject 15。
- 26 条 EP03 bulk accept 明确排除；quarantine 为 0。
- 27/27 状态都是 `eligible_rule_only`：允许案例检索、Skill/规则分析；禁止训练模型、改生产规则或批准 EDL。
- `review_mode` 只是兼容元数据；`adjust` 当前不做。这两项不会阻塞本快照。
- training readiness 仍为 `NOT_READY`，原因是数据量、节目期数、独立审核人、冻结 benchmark、独立复核和回滚演练不足。

## 为什么没有覆盖根目录旧产物

`case_store/` 根的 `cases/`、`reports/` 是 2026-08-12 14:46 的首轮历史产物，其中的
`pending_review_mode` 文字已经不符合当前二态产品决定。它们保留为可追溯证据，不作为当前事实。

第一次重跑 `main/runs/EXPERIENCE-INGESTION-v1-20260812-1612/` 检出 EP04 filler/long-pause 的
`human_decisions.json` 和 `review_session_metrics.json` 相对旧 SHA 发生变化，并正确 fail-closed。
经逐字段复核三条决定的语义投影未变后，当前快照使用
`baseline/source_shas.two-state-v1.json` 单独冻结；旧 baseline 保持不动。
