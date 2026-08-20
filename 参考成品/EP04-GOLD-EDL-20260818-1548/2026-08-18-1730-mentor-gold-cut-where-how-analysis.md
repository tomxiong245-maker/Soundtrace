# 2026-08-18-1730 · Mentor Gold Cut 分析 · Where / How 双轨学习

**可靠度声明**:样本量 EP03=56 + EP04=3 (共 59 条 mentor gold cut),EP04 样本过小 · EP03 主导所有分布结论 · 跨集泛化能力**中等偏低** · 需要 ≥3 集额外 gold 数据校准。

---

## 用户明确要求(2026-08-18 · 本轮)

> "现有的知识应该分两块:
> 1. **参数** (决定怎么剪辑) 基础的参数用工具的
> 2. **偏好** (决定剪哪些)
> 今后有新的可以作为答案出现的东西 · 进行学习 (三个学习流自己找应该用哪个) · 沉淀进来"

据此重构知识库:
- `main/knowledge/cut_parameters.json` (PARAMETER · 决定怎么剪 · 工具直接消费)
- `main/knowledge/session_feedback/current.session_feedback.jsonl` (PREFERENCE · 决定剪哪些 · retrieve_before_decision 消费)
- `docs/learning-flow-selector.md` (三学习流选择器 · 新答案怎么进来)

---

## 已验证事实 (实测 · 59 条 gold 特征统计)

| 维度 | 中位数 | p25 | p75 | 分类差异 |
|---|---|---|---|---|
| gap_before_ms (刀前离词尾) | 180 [实测] | 120 | 260 | boundary_review 180 · pause 100 · filler 210 |
| gap_after_ms  (次词离刀后) | 280 [实测] | 120 | 440 | boundary_review 300 · pause 120 · filler 110 |
| cut_lands_in_silence_gap | 46.6% (27/58) [实测] | - | - | boundary_review 65% · pause 0% · filler 33% |
| boundary_offset_from_silence_edge_ms | 300 [实测 · 顶 300 上限] | 76 | 300 | 14/27 顶到 cap |
| \|rms_before - rms_after\| dB | 9.0 [实测] | 2.9 | 22.5 | 能量跳变 >10dB 占 48% |
| mentor_crossfade_ms | 50 (EP03) / 200 (EP04) [实测] | - | - | 全期 constant · 无 per-cut 调制 |
| gold_category 占比 | boundary_review 71% · pause 22% · filler 10% [实测] | - | - | pure_filler=0 · rhetorical=0 |

**能量匹配非硬约束** [推断]:mentor 不严格匹配剪口能量;50 vs 200ms crossfade 是 per-episode 决策,不因音频局部特征调整。

**cross_track_speaking 目前定义失效** [实测]:59/59 都是 True · 三轨录音双麦 bleed 导致所有相邻轨都有 "primary" · 需引入 speaker_map 差异化重定义才能作为判别信号。

---

## 已决定的方向

### 参数 · 决定怎么剪 → `cut_parameters.json`

**crossfade**:默认 50ms · long_pause 类 200ms · 短 cut window clamp `min(target, dur-128 samples)` [推断 · 基于 workflow synthesis 与 gold 全期一致性]

**gap_before**:target [120, 300] ms · **gap_before < 50ms 且不在 silence gap 时硬拒绝** [推断 · gb<50 只 3/58 案例]

**gap_after**:target [120, 450] ms · 优先 tail-heavier `ga ≥ 0.9 × gb` [实测 · 28/58 尾长 vs 20 头长]

**boundary_offset**:落静音时 ≥76ms · 目标 300ms 深植 [实测 · median 300 顶上限 14/27]

**RMS**:soft ≤15dB / hard ≤25dB · middle hump 只在非静音时预警 [推断 · workflow LENS4]

### 偏好 · 决定剪哪些 → `current.session_feedback.jsonl`

**never_cut**(硬负样本):
1. `emphasis_repetition` 修辞性重复 (特别特别/非常非常/真的真的) [实测 · EP03 C021 C022 熊镇正 reject]
2. `pure_filler_isolated` 孤立呃/嗯/啊/额/那个/这个/就是 无重复无自纠 [推断 · 59 gold 中 pure_filler=0 · 强负样本]

**needs_extension**(chain 场景):`然后/一些/因为/什麼/go` 保留最后 1 个 [实测 · EP04 mentor 未剪 · session_feedback mentor_final_not_cut_*]

**semantic_boundary_primary_target**(主战场):duration ≥ 600ms + 有实文本 + 落静音 → 优先接受 [推断 · workflow synthesis top-1]

**self_correction_all_or_none**:发现自纠正标记 (不是/不对/应该说/或者说) + duration ≥ 4000ms → 整段清除 [推断 · n=2 medium 置信]

**immediate_rep_in_speech_allowed**:immediate_rep 类不要求 lands_in_silence [实测 · 11/12 mentor immediate_rep 未落静音]

---

## 系统缺口 (workflow system_gaps · 10 条)

按影响力排序:

1. [高] `generate_comprehensive_cut.py` 缺 `semantic_boundary_primary_target` 通道 · 当前偏 "剪 filler" 直觉 · 与 mentor 剪 71% semantic_boundary 相反
2. [高] cross_track_speaking flag 定义错 · bleed 假阳掩盖真 overlap · 需 speaker_map 差异化重写
3. [高] 缺 host-silent gate · EP04 3/3 需 host `covered_word_texts==""` 且 gap ≥ 1.5s
4. [中] crossfade 单默认值 · 缺 per-episode-profile + per-category 双层查表
5. [中] boundary-offset 缺精细 zone · median 深植 300ms 缺硬约束
6. [中] immediate_rep 缺 in-speech 通道 · 强制 silence gap 会拒 11/12 gold
7. [中] self_correction 缺 "全或无 + duration ≥ 4000ms" 联合触发
8. [中] RMS 阈值缺配置 · soft 15dB / hard 25dB
9. [低] episode profile 概念缺失
10. [低] 分类启发式与 raw gold_category 冲突

---

## 待验证假设 (challenger 任务 · 10 条)

- 拉 ≥3 集额外 gold 验证 crossfade 是 per-episode 还是 per-project constant
- 构造 cross_track_speaking=false 样本 · 验证 flag 重定义后信号恢复
- 扩 self_correction 样本至 n≥10 验证 "全或无" 规则
- EP04 之外找 host+guest 真重叠 (非 bleed) 验证 host-silent gate 是硬 vs soft
- generate_comprehensive_cut.py vs mentor gold 混淆矩阵 · 量化 pure_filler 假阳率
- 42 条 semantic_boundary 二次拆分 · 主题分割 + 说话人转换
- gb<50ms 3 条紧贴样本逐一听审 · 硬拒绝规则是否误伤
- boundary_offset 300ms 顶格原因 · mentor 主观 vs 工具默认 padding?
- EP04 200ms xfade 与 long_pause 类还是三轨过渡强绑定?
- RMS 15/25dB 阈值 ROC 分析

---

## 产物索引

- `main/knowledge/cut_parameters.json` (新 · PARAMETER · 8 组参数默认)
- `main/knowledge/session_feedback/current.session_feedback.jsonl` (55→64 条 · v2 schema · +knowledge_category tag)
- `main/runs/EP04-GOLD-EDL-20260818-1548/gold_cut_features.jsonl` (EP04 3 条)
- `main/runs/EP04-GOLD-EDL-20260818-1548/gold_cut_features_ep03.jsonl` (EP03 56 条)
- `main/runs/EP04-GOLD-EDL-20260818-1548/ground_truth_stats.json` (Python 计算的分布)
- `main/runs/EP04-GOLD-EDL-20260818-1548/synthesis.json` (workflow 综合)
- `main/runs/EP04-GOLD-EDL-20260818-1548/patterns.workflow.json` (workflow raw)
- `docs/learning-flow-selector.md` (三学习流选择器)

---

## 三学习流选择器 (用户要求)

见 `docs/learning-flow-selector.md`。核心 rule of thumb:

| 新数据 shape | 选哪个 flow | 落地目标 |
|---|---|---|
| 单条真人反馈 + note | **feedback-engine analyze** | session_feedback append 补丁 |
| 多条 accept/reject 对 (≥5) | **label-learning-driver** | shadow prediction + preference_snapshot |
| Mentor gold EDL / 剪辑成品 | **editing-experience-distiller** | 经验卡 + Challenger 假设 |

