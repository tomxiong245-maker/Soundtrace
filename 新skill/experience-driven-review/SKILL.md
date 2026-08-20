---
name: experience-driven-review
description: 用 LLM 软判**剪辑质量**（不是剪哪些）。对每条候选：量化 pipeline 剪法 vs mentor gold vs session_feedback 里 PARAMETER 规则 → 输出 quantified cut plan diff + 具体调整建议（cut_duration_ms / pause_ms / crossfade_ms / boundary_offset）+ cited case_ids。**候选是否剪由 gate v3 决定 · 本 skill 只优化怎么剪**。触发词：剪得不够好、cut quality、boundary 精修、参数量化、cut plan diff、how to cut。
status: active
owner: challenger
entry_tool: analyze_cut_plans
related_tools:
  - feedback_engine
  - retrieve_before_decision
  - load_cut_parameters
  - mfa_align_and_extract_boundaries
preconditions:
  - "run 目录有 machine_assisted_draft.edl.json 或 candidate_source.json（本 skill 只读现有 cut plan · 不生成）"
  - "main/knowledge/session_feedback/current.session_feedback.jsonl 存在 · 内含 knowledge_category=PARAMETER 的规则"
  - "main/knowledge/cut_parameters.json 存在或 feedback_engine.load_cut_parameters() 可用"
  - "存在 EP04-GOLD-EDL-* 之类 gold_edl.json（提供 mentor 实测剪法基线 · 若无也可跑但没参照）"
  - "3 轨原始 WAV 存在（可选 · 用于精修实测 RMS / gap_before / gap_after · 若无则跳过声学层量化）"
postconditions:
  - "写 <run>/cut_plan_diff.json（schema cut-plan-diff-v1）"
  - "每条候选记 {candidate_id, current_plan{duration_ms/pause_ms/crossfade_ms/gap_before/gap_after/boundary_offset}, gold_reference (若匹配), applied_rules[], gap_analysis{}, recommended_plan{}, confidence, reasoning, cited_case_ids[]}"
  - "不写 EDL · 不改 EDL · 不改 gate 判决 · 不改 session_feedback"
covers_decision_points:
  - quantify_cut_plan_vs_gold
  - retrieve_parameter_rules_for_boundary
  - recommend_adjustments_with_cited_evidence
covers_claude_md_rules:
  - "§14"
  - "§17"
  - "§18"
  - "§19"
  - "§20"
  - "§21"
pre_flight_check: null
---

# experience-driven-review

## 1. 定位

**LLM 参与剪辑质量优化的软 gate**。焦点是 **PARAMETER 层（怎么剪）**，不是 PREFERENCE 层（剪哪些）。用户 2026-08-18 明确反馈：候选找对了 · 剪得不够好 · 想把经验规范化为 skill 让 LLM 量化参与。

**做什么**：
- 对每条候选（已被 gate v3 或人工提议要剪的）
- 量化提取当前 cut plan 的 6 个 PARAMETER 指标
- 与 mentor gold（若匹配）+ session_feedback PARAMETER 规则 + cut_parameters.json 对齐
- LLM 输出 recommended_plan · 引用具体 case_id · 给自然语言 reasoning

**不做**：
- ❌ 不判"该不该剪"（PREFERENCE 层 · 走 gate v3 / feedback_engine.is_never_cut）
- ❌ 不写 EDL / 不改 EDL / 不改 gate 判决 / 不改 session_feedback
- ❌ 不自动应用 recommended_plan · 只输出建议 · 由人或另一 skill 决定应用

## 2. 何时激活

- 用户/agent 说：**"剪得不够好"** / cut quality / 参数量化 / boundary 精修 / cut plan diff / 与 gold 对比
- run 目录已有 EDL 或候选 · 想 LLM 复审每条剪口的**具体怎么剪**
- 反馈驱动的迭代：user 抱怨某个剪口"痕迹明显 / 吃了词 / pause 不自然" · 要精确定位是哪个 PARAMETER 出问题

## 3. 输入 · 6 个量化 PARAMETER 指标

Per candidate（从 EDL + candidate_source + 3 轨 ASR + audio wav 提取）：

| 指标 | 单位 | 计算 | 目标区间（cut_parameters.json / §21）|
|---|---|---|---|
| **cut_duration_ms** | ms | `(end_sample - start_sample) / sr * 1000` | 依 kind 而定 · filler_hesitation ~200-500 · immediate_repetition ~150-800 |
| **prev_word_end_to_cut_start_ms** (gap_before) | ms | ASR 里 cut_start 之前最近词的 end · 差值 | target [120, 300] · hard_reject < 50 unless in silence |
| **cut_end_to_next_word_start_ms** (gap_after) | ms | ASR 里 cut_end 之后最近词的 start · 差值 | target [120, 450] · prefer tail_heavier (gap_after ≥ 0.9 × gap_before) |
| **boundary_offset_from_silence_edge_ms** | ms | 如果 cut 落静音段 · cut_start 距离静音段起始的距离 | target 300 · prefer min 76 |
| **crossfade_ms** | ms | render_sync_cuts[i].crossfade_samples / sr * 1000 | mentor gold=butt splice (0ms) · 老规则=50-100ms |
| **post_cut_pause_ms** | ms | render_sync_cuts[i].insert_silence_samples / sr * 1000 | mentor gold=0 · segment_separator=300-500 · 其他 40-60 |

补充（若能计算）：
- **rms_at_boundaries_db** · cut_start / cut_end / middle 三点 20 ms RMS · 差值应 ≤ 15 dB soft / 25 dB hard
- **librosa_onset_of_next_kept_word_ms** · 若下一保留词是内容词 · cut_end 必须 ≤ onset - 30 ms（§17 保护）
- **kind_specific_flags** · filler_boundary_edge_extend / segment_separator_pause_preserve / self_correction_all_or_none

## 4. Mentor Gold 匹配规则

对每条候选 · 在 `main/runs/EP04-GOLD-EDL-*/gold_edl.json` 里找匹配的 `gold_cuts[]`：
- 同 `candidate_id`（本 pipeline 沿用 ID 时）
- 或 |start_seconds| 差 < 3s 且同 kind

匹配到就把 gold 的 `duration_ms` / `pause_ms_in_gold` / `mentor_metadata` 作 reference：
- gold.duration_ms 与 pipeline duration_ms diff · 记 `duration_delta_ms`（正=过剪 · 负=欠剪）
- gold.pause_ms_in_gold vs pipeline post_cut_pause_ms · 记 `pause_delta_ms`

## 5. 输出 · cut_plan_diff.json

```json
{
  "schema_version": "cut-plan-diff-v1",
  "run_dir": "main/runs/EP04-...",
  "episode_id": "EP04",
  "cut_parameters_source": "main/knowledge/cut_parameters.json",
  "session_feedback_lines_used": 66,
  "gold_edl_source": "main/runs/EP04-GOLD-EDL-20260818-1548/gold_edl.json",
  "mentor_metadata": {"no_pause_insert": true, "no_crossfade": true, "prefers_content_word_only": true},
  "candidates": [
    {
      "candidate_id": "C007",
      "kind": "filler_hesitation",
      "track": "track_01",
      "current_plan": {
        "start_seconds": 354.08,
        "end_seconds": 354.76,
        "cut_duration_ms": 680,
        "gap_before_ms": null,
        "gap_after_ms": null,
        "crossfade_ms": 100,
        "post_cut_pause_ms": 40
      },
      "gold_reference": {
        "matched_by": "candidate_id",
        "gold_cut_id": "GOLD_C007",
        "duration_ms": 385,
        "pause_ms_in_gold": 0,
        "duration_delta_ms": 295,
        "pause_delta_ms": 40
      },
      "applied_rules": [
        {"kind": "gold_synth_boundary_offset_zone_and_tie_breakers", "match_score": 2, "note": "..."}
      ],
      "gap_analysis": {
        "boundary_over_cuts_by_ms": 295,
        "pause_extra_ms": 40,
        "crossfade_extra_ms": 100,
        "estimated_kept_word_at_risk": false
      },
      "recommended_plan": {
        "cut_duration_ms": 385,
        "post_cut_pause_ms": 0,
        "crossfade_ms": 30,
        "start_seconds_adjust_ms": +150,
        "end_seconds_adjust_ms": -145
      },
      "confidence": 0.85,
      "reasoning": "Mentor gold 实际只剪 385 ms + 无 pause + butt splice · 当前 pipeline 剪了 680 ms · 多剪了 295 ms · 可能吃了前后保护词. 40ms pause 是 v20 filler_hesitation 默认 · 但 gold_edl 显示 pause_ms_in_gold=0 · 建议移除",
      "cited_case_ids": ["GOLD_C007", "gold_synth_boundary_offset_zone_and_tie_breakers", "prefers_content_word_only"]
    }
  ]
}
```

## 6. 决策链

```
Per candidate:
  1. 提取 current_plan 6 指标 (从 EDL + ASR + 可选 audio)
  2. 查 gold_reference (若匹配)
  3. retrieve_before_decision(cand, decision_type="cut_boundary", ep_id, knowledge_category="PARAMETER", k=5)
     拿 top-5 PARAMETER 规则
  4. LLM 读:
       current_plan
       gold_reference (若有)
       top-5 rules (kind/verdict/note)
       mentor_metadata (no_pause_insert / no_crossfade / prefers_content_word_only)
       cut_parameters.json defaults
  5. LLM 输出:
       gap_analysis (量化 delta)
       recommended_plan (量化调整值)
       confidence (0-1)
       cited_case_ids
       reasoning
```

## 7. 硬边界

- **绝不**写 EDL / autocut_policy / session_feedback / cut_parameters.json
- **绝不**说 "auto-apply recommended_plan" · 输出永远是**建议** · 由用户/另 skill 决定
- **§17 硬约束**：若 recommended_plan 的 cut_end 会吃到下一保留内容词 onset - 30 ms · verdict 强制 "needs_human_review"
- **§19 硬约束**：若 recommended_plan 的 cut_start < prev_word.end + 20 ms 或 cut_end > next_word.start - 20 ms · 同上
- Mentor gold `pause_ms_in_gold=0` 是 **观测** 而非 **规则**；若 candidate 是 segment_separator（然后/首先/第三...）则遵守 `segment_separator_pause_preserve` (300-500 ms) 覆盖 mentor 默认

## 7.1 参数取值层次 · 硬约束（2026-08-18 补 · CLAUDE.md §11 禁自由发挥）

recommended_plan 里**每个数值字段**必须带 `param_source` 字段 · 值从下表**从上到下**逐层查 · 找到就停：

| 优先级 | 来源 | 举例 | 何时用 |
|---|---|---|---|
| **1** | mentor gold_edl 对应字段 | GOLD_C007.duration_ms=385 | 有 gold_reference 且字段直接映射 |
| **2** | cut_parameters.json `override_by_semantic_class[kind]` | `crossfade_ms.override.filler_hesitation=50` | kind 匹配的 override 存在 |
| **3** | cut_parameters.json `asymmetric_head_pad_by_class[kind]` | `filler_hesitation={gap_before:210, gap_after:110}` | 特殊 pad 需要 |
| **4** | cut_parameters.json 默认值 | `crossfade_ms.default=50` / `pause_ms_after_cut=0` | 无 override |
| **5** | session_feedback verdict_priority≥7 的规则数值 | `segment_separator_pause_preserve` 300-500 | 特殊 verdict 需要 |
| **6** | session_feedback verdict_priority<7 | | 弱证据 |
| **7** | LLM 推导（必须给算式） | 例：`start_seconds_adjust_ms = gold_dur - current_dur = -85` | 前 6 层无覆盖时 · 且 must show math |

**LLM_invented / made_up 是显式错误** · 出口 lint 会拒。若字段值 = "编的" · 必须写 `param_source: "LLM_derived_from_math"` + `derivation` 字段展示算式。

## 7.2 剪辑干净门 · 硬约束（2026-08-18 补 · 用户明确 "要删掉的词首先得剪辑干净"）

每条候选**必须**先过 cut_cleanliness_status 硬门 · 才允许进 recommended_plan 层：

| status | 含义 | verdict 上限 | confidence 上限 |
|---|---|---|---|
| `mfa_refined` | MFA 音素级对齐成功 · 边界误差 ≤ 20 ms | 任意 | 1.0 |
| `boundary_lock_asr_word_bound` | ASR 整词边界 · 有 boundary_lock=True · 短 filler 类可接受 | 任意 | 0.9 |
| `snap_only` | 仅 zero-crossing / RMS 精修 · 无音素级验证 | `human_review` | 0.7 |
| `asr_only` | 只有 ASR word timestamp · 无任何精修 | **`human_review` 强制** | **≤ 0.5** |
| `neighbor_word_risk` | gap_before < 50 或 gap_after < 20 或 cut 端在保留词 onset-30ms 内 | **`human_review` 强制** | **≤ 0.4** |

MFA `refined_count=0` 时 · **所有候选自动降级 asr_only** · 全部走 human_review。

## 7.3 冲突解决 · 硬编码优先级（2026-08-18 补 · 用户问 "冲突时如何选"）

top-K 检索里出现 verdict 矛盾时 · 按下表**从上到下**决议 · 高优 override 低优：

| 优先级 | 判决源 | 覆盖范围 | 举例 |
|---|---|---|---|
| **P1** | mentor gold_edl 匹配 | 整个 recommended_plan | GOLD_C044 accept · 即使 rule 说 emphasis_repetition_never_cut 也剪 |
| **P2** | verdict_priority=10 且 match_score≥10 (never_cut/forbidden 且 token 精确匹配) | verdict 层 · 整条候选是否该剪 | mentor_final_not_cut_然后 (score 15) → 强制 remove_from_edl |
| **P3** | §17/§19 邻词/onset 保护硬边界 | 邻词/onset 层 · 计算得出的物理不可行 | cut_start < prev_word.end+20ms → 强制 human_review |
| **P4** | cut_cleanliness_status（§7.2）| verdict/confidence 上限 | mfa_refined 缺失 → verdict ≤ human_review |
| **P5** | cut_parameters.json 里精确 override | recommended_plan 里数值字段 | crossfade_ms · pause_ms · gap 目标区间 |
| **P6** | session_feedback verdict_priority∈{6,7,8} | 数值调整 | segment_separator_pause_preserve → pause 300-500 |
| **P7** | session_feedback verdict_priority≤5 | 弱建议 · 可 override | policy/mixed/accept_pattern 类 |
| **P8** | LLM 推导 | 前 7 层未覆盖时 | 必须显式 `param_source:"LLM_derived_from_math"` + 展开算式 |

**冲突记录**：每条 recommendation 输出必须列 `conflicts_resolved: [{losing_rule_kind, losing_priority, winner_priority, why}]` · 让人审能追溯。

## 8. 反馈证据

- **触发**：2026-08-18 用户 chat "候选找对了 · 剪的不够好 · 把经验文档规范化为 skill · 想办法量化 · 让 llm 参与"
- **量化基线**：EP04 mentor gold 3 条 · 与当前 EP04-FULL-E2E-20260818-1836 pipeline 的 duration/pause/crossfade 对比 → duration_delta 平均 +295 ms（当前过剪）· pause_delta 平均 +40-200 ms（当前多插停顿）
- **PARAMETER 规则源**：session_feedback 里 knowledge_category=PARAMETER 的 4 条 (gold_synth_boundary_offset / gold_synth_boundary_rms / gold_synth_cross_track / cut_parameters.json 默认)
- **成功标准**：8 个候选跑完后 · recommended_plan 与 mentor gold duration_ms 的中位数偏差 < 50 ms

## 9. 三档诚实标注

**已验证事实**：
- `feedback_engine.retrieve_before_decision(knowledge_category="PARAMETER")` API 存在（v2 · line 118）
- `feedback_engine.load_cut_parameters()` API 存在
- mentor gold_edl.json 3 条 EP04 cuts 全部 pause_ms_in_gold=0
- cut_parameters.json 存在（若不在则用 hardcoded defaults）

**已决定的方向**：
- LLM 只做建议 · 不做 auto-apply
- 与 gate v3 / EDL / session_feedback 完全并联 · 不改主流水
- 优先引用 mentor gold（有则用）· 其次 session_feedback PARAMETER 规则 · 最后 cut_parameters defaults

**待验证假设**：
- 8 候选 recommended_plan 与 mentor 中位数偏差 < 50 ms（首跑 EP04 验证）
- LLM 引用的 case_id 是否真在 session_feedback 里（要 grep 验）
- 是否需要真跑 librosa onset detection 才能验证 §17（首跑先跳过 · 若发现风险再补）
