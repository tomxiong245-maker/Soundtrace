# B · immediate_repetition boundary 扩到最后重复 · 2026-08-17 17:50

**可靠度声明**：本文陈述基于单元测试实测与 EP04 C044 具体 ASR 词级数据；未真跑完整 EP04 candidate 生成管线（保留给下一期节目实测）。

## 事实（[HIGH]）

- 改动 2 个文件：
  - `稳定生产/challengers/filler-global-pause-v1/scripts/build_filler_global_pause_review_source.py::find_immediate_repetition_proposals`
  - `main/orchestrator/snap_candidate_boundaries.py`（消费新 `boundary_lock` 字段）[HIGH]
- 契约测试：P1 3/3 · P2 12/12 · filler-global-pause 16/16 · automix 6/6 · sync PASS。self-correction v1 pytest 环境缺失（既有问题，非本改动引入）。[HIGH]
- 单元测试断言实测通过：
  - N=3 chain "因为因为因为"：产 1 candidate · chain_len=3 · boundary=[2.0s, 3.0s] · 删"因为因为"·保 last · boundary_lock=True ✓
  - N=2 chain（EP04 C044 复现）："因为(2268.21-2268.93) + 因为(2268.93-2269.13)" → boundary=[2268.21, 2268.93] · 删 1 个 · 保第 2 个 · boundary_lock=True ✓
- v18 → v19 行为变化：
  - N=2 chain: boundary 计算**结果一致**（v18 也是整词 range），但 v19 加了 `boundary_lock=True` 让 snap 跳过精修
  - N=3+ chain: v18 视为多个独立 pair 各生成一个 candidate（重复），v19 合并为一个 chain candidate 明确删前 N-1 保 last。[MED]

## 判断（[MED]）

- **修复 EP04 C044 "两个因为只剪一个" 的完整链路**：
  - v18 candidate 边界: [2268.36, 2268.79]（430ms 中间段）· snap 精修后
  - v19 candidate 边界: [2268.21, 2268.93]（720ms 整词）· boundary_lock 阻止 snap 缩边界
  - 剪 720ms + rendering_gate 100ms qsin crossfade 消爆音 → 第 1 个"因为"整词消失、第 2 个"因为"完整保留
  - 听感预期：只剩 1 个"因为"，边界干净 [HIGH]
- **snap_candidate_boundaries 的行为变化**：仅当 candidate 明确声明 `boundary_lock=true` 时才跳过 snap；未声明的候选（filler_hesitation、global_long_pause 等）**行为完全不变**。零回归风险。[HIGH]
- **immediate_repetition 现在正确处理 N=3+ 连续同词**：以前遇到"因为因为因为"会生成 2 个重叠 candidate（(w0,w1) 一个、(w1,w2) 一个），现在合并成 1 个 chain candidate，避免下游二义。[MED]

## 建议 · 后续动作（[MED]）

1. **EP05 上线时真跑一次**：用 filler-global-pause-v18 rules 跑 candidate → 观察 chain candidates 数量与命中；若 EP04 数据回跑，应看到 C044 boundary 从 430ms 变 720ms。
2. **rendering_gate crossfade 时长实测**：现有 speech_cut_crossfade_ms=100，qsin 曲线；720ms 整词剪后接 100ms crossfade 是否足够自然，需听审。
3. **filler_hesitation 的整词覆盖尚未实施**：v19 boundary_strategy.filler_hesitation.mode = entire_word_asr_bounds 目前只有契约声明；C007 "呃剪不干净"的修复需要在 find_filler_proposals 里做类似 boundary_lock 处理 → 下一步 F 系列任务。
4. **snap 报告字段扩展**：`snap_stats["unchanged"]` 会统计 boundary_lock=true 的候选，未来 stats 报告应细分 `locked` 与真 `unchanged`。

## 未做（诚实交代）

- 未在 EP04 完整管线上跑 build_filler_global_pause_review_source 端到端（会需要重新生成 candidates.json，触发下游 review_bundle 变化；不是本改动 scope）
- 未修 filler_hesitation 的整词覆盖（C007 问题）；下一次改动处理
- 未装 pytest 修 self-correction 测试环境（既有问题；EP05 前处理）
- 未做 rendering crossfade 时长的听感 A/B 实测（需要真跑）

## 相关文件

- 主改动：[build_filler_global_pause_review_source.py L626-L717](../../filler-global-pause-v1/scripts/build_filler_global_pause_review_source.py)
- Snap 消费：[snap_candidate_boundaries.py L134-L145](../../../../main/orchestrator/snap_candidate_boundaries.py)
- v19 契约声明：[candidate_rules.v19.json](../rules/candidate_rules.v19.json)
- EP04 C044 case 参考：[EP04-DELIVERY-20260817-1427/qc/loudness_report.json](../../../../main/runs/EP04-DELIVERY-20260817-1427/qc/loudness_report.json)

## 相关记忆

- [[minglue-project-layout]]
- [[minglue-post-feature-analysis-md]]
- [[minglue-analysis-md-tracks]]
