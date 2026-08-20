# Development 剪辑 Benchmark Scorecard

- run: `EP04-v26-20260815-1650` / `EP04`
- 交付状态：`CALIBRATION_REVIEW_REQUIRED`
- scorecard：`INCOMPLETE_HUMAN_REVIEW_REQUIRED`
- 质量通过：**否**（本文件不产生发布、Champion、自动删剪或 accept/reject 授权）

> run is still at CALIBRATION_REVIEW_REQUIRED; no human decision, EDL, render, or transition-QC result exists

## 候选负担（可测，但不是质量）

- 时间线：3272.700 秒 / 0.909083 小时；对齐轨道：3。
- 全部候选：12（13.200 条/节目小时）；审核包：11（12.100 条/节目小时）。
- 候选类别：`{"filler_hesitation": 3, "immediate_repetition": 9}`；风险：`{"low": 12}`。
- 审核预算 / 剩余：`20` / `9`；安全阻断：`35`。
- 限制：Candidate volume measures reviewer workload only. A lower number is not evidence of better recall, safer edits, or better audio.

## 当前真人审核

- 状态：`PENDING_HUMAN_REVIEW`；正式决定：0 / 11。
- accept/reject 观察值：`NOT_MEASURED`；备注：`NOT_MEASURED`。
- 草稿存在但不计入：`False`。

## 历史 Mentor 备注回归集

- 状态：`AVAILABLE_DEVELOPMENT_ONLY`；历史决定：32（accept 8 / reject 24），有备注 23 条。
- 限制：historical development regression only; it does not label current-run candidates, authorize edits, or form a frozen benchmark

## 无候选区抽查

- 状态：`NOT_MEASURED`。
- 原因：the plan exists, but not every sampled window has a recognized real-human outcome

## 渲染后剪口复听排序（transition QC）

- 客观排序：`NOT_MEASURED`；人耳自然度：`NOT_MEASURED`。
- 原因：this run is pre-render, so transition QC is not generated yet

## 仍未测量的质量门

| 指标 | 状态 | 原因 |
| --- | --- | --- |
| 候选召回（对人工 edit map） | `NOT_MEASURED` | no validated human edit map/reference EDL is part of this development scorecard |
| 无候选区漏检信号 | `NOT_MEASURED` | no fully completed human no-candidate audit sample exists |
| 渲染剪口自然度 | `NOT_MEASURED` | transition_qc ranks objective anomalies for re-listening; it never measures naturalness, semantic correctness, or an automatic pass |
| 严重语义误删 | `NOT_MEASURED` | requires explicit human semantic/whole-episode evaluation; candidate count or acceptance rate cannot substitute |
| 净节省时间 | `NOT_MEASURED` | real review, rework, maintenance, and baseline manual time have not been recorded together |

## 下一道门

- 真实审核人完成当前 review package 的每一条 accept/reject；草稿不算决定。
- 真人同步试听所有固定无候选窗口，并以规定 finding 写入结果；发现问题先形成新候选。
- 正常 resume 后生成两份 transition_qc，再把其优先项纳入人耳复听。
- 单独记录整片语义/听感、返工与工时；本 scorecard 不产生发布或 Champion 晋升结论。

## 重要解释

- NOT_MEASURED means evidence is absent, incomplete, invalid, or outside this scorecard; it never means zero problems or pass.
- Candidate burden is workload metadata, not a proxy for recall, semantic safety, or audio quality.
- Historical Mentor feedback is development-only regression context and never becomes a current-run decision or automatic policy.
- No-candidate windows require real human listening; an unlistened plan cannot support a missed-edit conclusion.
- Transition QC is an objective priority ranking only. It does not hear naturalness, validate meaning, or authorize edits.
