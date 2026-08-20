# ERRATA · cross-track-safety-v1

> 新增时间：2026-08-11 · P1 新窗口
> 用途：**只新增**勘误，不修改历史 `run_manifest.json`、`before_metrics.json`、`after_metrics.json`、`safe_candidates.json`、`blocked_candidates.json` 或其它已哈希产物。所有历史 SHA-256 保持不变。

---

## E-001 · "跨轨误删风险为 0" 表述过度

### 涉及的旧表述

重构前历史报告 `与AI的上下文/归档/2026-08-11-文档重构前/2026-08-11-跨轨安全修复与ASR基准.md` §3 结论：

> "跨轨误删风险候选**从 56 中的 27+（约 48%）降到 SAFE 集里的 0**。"

同一表述在部分历史沟通中被简化为"跨轨误删风险从约 48% 降到 0"。

### 为何需要勘误

上述表述省略了当前证据的成立范围，实际证据支持的只是：

**在 Challenger `cross-track-safety-v1` 定义的守卫规则内，且在当前 segment 级两轨 RMS 能量差启发式所生成的 `primary/bleed/ambiguous` 标签下，11 条 SAFE 候选均未违反已定义的跨轨守卫（如 `SOURCE_NOT_PRIMARY`、`OTHER_TRACK_PRIMARY_SPEECH`、`AMBIGUOUS` 等），14 条候选被这些守卫 BLOCK。**

它不能直接推出真实声学层面的跨轨误删风险为 0，原因：

1. `primary/bleed/ambiguous` 是 segment 级能量启发式复制到词级，不是男/女声模型，也不是 speaker ground truth。它可能整体标错（例如把主讲误标为 bleed，或反之），此时规则守卫内的"违规数=0"与真实误删无必然关系。
2. ASR mini gold 仍为 `WAITING_FOR_HUMAN_GOLD`，尚无人工基准；`false-safe`（应删却过关）与 `false-block`（不该删却被 BLOCK）的真实数量未知。
3. 11 SAFE 候选未经真人逐项精审 accept/reject/adjust；bulk 层面的"未违规"不是"内容审核通过"。
4. T05 fixture 的修复只证明 fixture 与任务书原意一致，不证明真实音频标签正确。

### 准确表述（应替换任何后续引用）

- **可以说**："在当前启发式标签与本 Challenger 守卫定义下，11 条 SAFE 候选未违反已定义的跨轨规则；14 条被 BLOCK。"
- **可以说**："`cross-track-safety-v1` 阻止了旧候选生成器中一批由单轨视角产生的明显危险候选（如 `long_pause` 在另一轨有 primary 语音的时段）。"
- **不可以说**："跨轨误删风险为 0" / "真实误删风险降至 0" / "跨轨安全已解决"。
- **不可以说**："已验证 ASR 更准" / "审核已完成" / "已可发布"。

### 有效证据边界（fact-scoped）

| 证据类型 | 是否已完成 | 说明 |
| --- | --- | --- |
| 规则 fixture（12/12） | 已完成 | 见 `tests/fixtures.json` 与 `test_results.txt` |
| 幂等性（重跑 semantic SHA 一致） | 已完成 | 见 `normalized_output_sha256.json` |
| Champion 未变（12 项 baseline SHA） | 已完成 | 见 `before_metrics.json` |
| 真人逐项精审（11 SAFE） | **未完成** | 待 P1 review-product-v1 交付后由人执行 |
| 真实声学 false-safe / false-block | **未完成** | 待人工 speaker gold 才能测量 |
| ASR 准确率变化 | **未完成** | P0 ASR mini gold 未填 |

### 传播修复

以下位置在下一次编辑该文件时应引用本勘误或更改表述（本 ERRATA 不代为修改）：

- `与AI的上下文/归档/2026-08-11-文档重构前/2026-08-11-跨轨安全修复与ASR基准.md` §3 "结论"一句
- 当前对外口径见 `统筹全局/功能说明/F04-候选生成与跨轨安全.md`
- 任何后续从该报告复制"48% → 0"的沟通材料

历史 manifest / run 产物**不动**（保持哈希）。ERRATA 是**新增文件**，历史文件保持只读。
