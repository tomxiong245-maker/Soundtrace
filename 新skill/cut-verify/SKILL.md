---
name: cut-verify
description: 剪口"干净度"验证 skill · 4 个独立 check · 用现装开源工具（faster-whisper word.probability + pydub.silence + cut_parameters.json 阈值 + policy 路由）· 输入 EDL/candidate · 输出 verified_edl.json（每候选带 cut_cleanliness 评级 + 建议参数）· **不改 EDL / 不改音频 / 不改 session_feedback**。触发词：剪口干净度、cut cleanliness、幻觉检测、静音段校验、butt splice、节奏跳变、rhythm gap、剪辑残留。
status: active
owner: challenger
entry_tool: verify_cut_plan
related_tools:
  - check_hallucination
  - check_silence_location
  - check_rhythm_gap
  - route_crossfade_strategy
  - verify_cut_plan
preconditions:
  - "候选 candidate_source.json 或 machine_assisted_draft.edl.json 存在"
  - "对应的 ASR 转写在 analysis/track_*.transcript.json（含 words[].probability 字段 · faster-whisper 输出）"
  - "raw 三轨 WAV 可读（用于 pydub.silence.detect_silence）"
  - "main/knowledge/cut_parameters.json 存在（gap target_range 阈值来源）"
postconditions:
  - "写 <run>/verified_edl.json（schema cut-verify-v1）· 每候选带 4 项 check 结果 + overall_verdict"
  - "verdict ∈ {clean_cut_ok · butt_splice_recommended · crossfade_50ms · needs_human_review · reject_hallucination · reject_rhythm_broken}"
  - "不改 EDL / candidate / 音频 / session_feedback · 只写侧车"
covers_decision_points:
  - filler_hallucination_gate
  - cut_boundary_in_silence_check
  - post_cut_rhythm_gap_check
  - crossfade_vs_butt_splice_routing
covers_claude_md_rules:
  - "§8"
  - "§11"
  - "§15"
  - "§16"
  - "§17"
  - "§22"
pre_flight_check:
  parameter_source: "main/knowledge/cut_parameters.json#cut_verify_thresholds"
  required_thresholds:
    - check1.prob_threshold=0.6
    - check2.silence_thresh_db=-40.0
    - check2.min_silence_len_ms=100
    - check2.context_window_s=1.5
    - check3.gap_target_min_ms=120
    - check3.gap_target_max_ms=450
    - check4.butt_splice_crossfade_ms=0
    - check4.boundary_crossfade_ms=50
    - check4.room_tone_pad_ms=10
    - filler_asr_word_expansion.post_expansion_crossfade_ms=50
---

# cut-verify skill

## 1. 定位

**剪口"干净度"的 4 项独立验证** · 用现装开源工具 · 不引入新依赖 · 不改主 pipeline。

**做什么**：
- 对每条候选 · 跑 4 个 check：
  - **点 1 · 幻觉检测**（faster-whisper `word.probability` · Whisper 教科书用法）
  - **点 2 · 静音段位置校验**（pydub.silence.detect_silence）
  - **点 3 · 节奏跳变检测**（cut_parameters.json 阈值比对 · 纯 Python）
  - **点 4 · 拼接策略路由**（butt_splice vs crossfade · 纯 policy）
- 输出 verified_edl.json 侧车 · 每候选带 overall_verdict + 建议参数

**不做**：
- ❌ 不改 EDL / 不生成候选 / 不改音频 / 不改 session_feedback / 不改 cut_parameters.json
- ❌ 不做 LLM 判决（这是 4 个确定性 check · 不涉及推理）

## 2. 4 个 check 详解

### Check 1 · 幻觉检测（`check_hallucination`）

**问题**：Whisper 在低能量段会幻觉出高频词（"呃/嗯/啊/是"）· 反馈到候选层就是 "filler_token 是 ASR 幻觉"。

**开源方案**：faster-whisper 输出每个词的 `probability` 字段（0-1 · 正常语音 > 0.9 · 低置信 < 0.6）。

**规则**：
```
if candidate.candidate_kind in ("filler_hesitation", "immediate_repetition") 
   and asr_word.probability < 0.6:
    verdict = REJECT_LOW_PROB_HALLUCINATION
```

**阈值来源**：EP04 实测 · 正常"它"prob=0.75 · "就是"prob=0.77 · 幻觉"呃"prob=0.49 · 明显分层。阈值 0.6 保守。

### Check 2 · 静音段位置校验（`check_silence_location`）

**问题**：cut boundary 落在**内容-静音过渡边界**上 · 200ms crossfade 就把内容尾巴糊过剪口 · 产生 ghost。落在**纯静音段内部**则 butt splice 干净。

**开源方案**：`pydub.silence.detect_silence(audio, min_silence_len=100ms, silence_thresh=-40dB)` 返回所有静音区间 `[(start_ms, end_ms), ...]`。（参数与 `check_silence_location.py` 常量 `DEFAULT_MIN_SILENCE_LEN_MS=100` 一致 · 与 CLAUDE.md §22 Check 2 一致 · 旧版误写 200ms 已更正 2026-08-19）

**规则**：
```
silences = detect_silence(track_wav)
cut_fully_in_silence = any(s <= cut_start_ms and cut_end_ms <= e for s, e in silences)
cut_spans_boundary = not cut_fully_in_silence
```

### Check 3 · 节奏跳变检测（`check_rhythm_gap`）

**问题**：剪 385ms 静音 · 原 gap 500ms 变 115ms · 低于 cut_parameters.json 里 `gap_before target_range=[120,300]` 的下限 · 说话人"抢话"感 · 用户觉得"不干净"。

**开源方案**：无需工具 · 纯 Python 计算。从 cut_parameters.json 读阈值 · 与实际 gap 对比。

**规则**：
```
raw_gap_between_prev_and_next = next_word.start - prev_word.end
post_cut_gap = raw_gap_between_prev_and_next - cut_duration_ms
target_min = cut_parameters["how_to_cut_defaults"]["gap_before_ms"]["target_range"][0]  # 120
target_max = cut_parameters["how_to_cut_defaults"]["gap_after_ms"]["target_range"][1]   # 450

if post_cut_gap < target_min:
    verdict = REJECT_RHYTHM_TOO_TIGHT  # 抢话
elif post_cut_gap > target_max:
    verdict = WARN_RHYTHM_TOO_LOOSE    # 太拖沓
else:
    verdict = RHYTHM_OK
```

### Check 4 · 拼接策略路由（`route_crossfade_strategy`）

**问题**：所有剪口硬编码 200ms crossfade · 静音段被糊 · 内容段被 mask。

**开源方案**：无需工具 · 消费 Check 2 结果。

**规则**：
```
if check_2.cut_fully_in_silence:
    strategy = BUTT_SPLICE       # crossfade=0 · 硬切 · 无 ghost
    room_tone_pad_ms = 10        # 极短 room tone 掩盖切换点
elif check_2.cut_spans_boundary:
    strategy = CROSSFADE_50MS    # 短 xfade 平滑过渡
else:
    strategy = HUMAN_REVIEW      # 边界模糊 · 人耳判
```

## 3. 输出 · verified_edl.json

```json
{
  "schema_version": "cut-verify-v1",
  "run_dir": "main/runs/EP04-...",
  "cut_parameters_source": "main/knowledge/cut_parameters.json",
  "verified_at": "2026-08-19T00:xx:xxZ",
  "candidates": [
    {
      "candidate_id": "C007",
      "current_cut": {
        "start_seconds": 354.230, "end_seconds": 354.616,
        "track_id": "track_01", "kind": "filler_hesitation", "token": "呃"
      },
      "checks": {
        "hallucination": {
          "asr_word_probability": 0.4876,
          "threshold": 0.6,
          "verdict": "REJECT_LOW_PROB_HALLUCINATION"
        },
        "silence_location": {
          "silence_intervals_nearby": [[354.10, 354.60]],
          "cut_fully_in_silence": true,
          "cut_spans_boundary": false
        },
        "rhythm_gap": {
          "prev_word_end_s": 354.076, "next_word_start_s": 354.900,
          "raw_gap_ms": 824,
          "cut_duration_ms": 386,
          "post_cut_gap_ms": 438,
          "target_min_ms": 120, "target_max_ms": 450,
          "verdict": "RHYTHM_OK"
        },
        "crossfade_strategy": {
          "strategy": "BUTT_SPLICE",
          "recommended_crossfade_ms": 0,
          "recommended_room_tone_pad_ms": 10
        }
      },
      "overall_verdict": "REJECT_HALLUCINATION_DESPITE_CLEAN_CUT",
      "reasoning": "Check 1 认定 filler_token '呃' 是 ASR 幻觉 (prob 0.49 < 0.6 阈值). 即使剪口位置在纯静音段 (Check 2 通过) · 节奏 OK (Check 3 通过) · 拼接可用 butt splice (Check 4) · 但候选本身不该剪. 建议 remove_from_edl."
    }
  ]
}
```

## 4. 硬边界

- **绝不**改 EDL / 候选 / 音频 / session_feedback / cut_parameters.json
- **绝不**做 LLM 判决 · 4 个 check 全部确定性
- **绝不**跳过 CLAUDE.md §9（A/B clip 必须先跑 automix）· §17（librosa onset 保护）· §19（邻词保护）
- Check 1 阈值 0.6 可通过 CLI `--prob-threshold` 覆盖 · 但默认值来自 EP04 实测

## 5. 覆盖 tool（4 个新脚本）

| tool | 输入 | 输出 |
|---|---|---|
| `check_hallucination` | candidate + ASR transcript | `{asr_word_probability, verdict}` |
| `check_silence_location` | candidate + raw wav | `{silence_intervals_nearby, cut_fully_in_silence}` |
| `check_rhythm_gap` | candidate + ASR + cut_parameters | `{post_cut_gap_ms, verdict}` |
| `route_crossfade_strategy` | 前 3 项结果 | `{strategy, recommended_crossfade_ms}` |
| `verify_cut_plan`（entry） | run_dir | 一次跑完 · 写 verified_edl.json |

## 6. 三档诚实标注

**已验证事实**：
- faster-whisper 转写文件确实带 `word.probability` 字段（grep EP04-v13/analysis/*.transcript.json）
- pydub / librosa / ffmpeg silencedetect 三个静音检测器都已装
- cut_parameters.json gap_before/after target_range 已明确写死
- C007 track_01 呃 prob=0.4876（低置信 · 幻觉证据）

**已决定的方向**：
- 4 个 check 独立跑 · 各自输出结构化 verdict
- 融合逻辑（overall_verdict）由 verify_cut_plan.py 编码 · 不用 LLM
- 输出只写侧车 · 不改主流水

**待验证假设**：
- 阈值 0.6 是否在其他期节目上仍合理（EP04 单期证据 · 需 EP05+ 补）
- pydub.silence 参数 (-40dB, 100ms) 是否需要按节目调
- Whisper "hallucination = low probability" 假设 · 长静音段的幻觉可能置信度未必都低（需更多样本）

## 7. 参数唯一权威口径

**本 skill 所有参数值 · 落地依据 · 冲突记录 · backlog · 硬约束确认 · 全部以下面这一份文档为唯一权威**：

> **`skills/cut-verify/2026-08-19-0040-cut-verify-landing-and-EP04-delivery.md`**（本目录同级）

该文档记录了一晚攻坚的完整实况：
- **落地的 4 项 check + 1 个 ASR word 扩展 + 1 条 rule 66**（session_feedback 65 → 66 行）
- **一周不干净的 4 个结构性根因**（缺 filler 幻觉检测 · 缺剪口位置校验 · 缺节奏跳变指标 · 一刀切 crossfade）
- **EP04 8 unique 候选跨 run 汇总**（C007/C014/C023/SC005/C034/C036/C039/C044 · 4/7 与 mentor gold 一致）
- **交付母带证据**（`main/runs/EP04-DELIVERY-V04-STRATEGY-2026-08-19-0030/render/EP04.learned_v04.mp3` · 55:13 · -22.77 LUFS · TP -1.29 dBFS）
- **冲突 3 处**（C007 gold vs REJECT_HALLUCINATION · C036 缺 false-repetition · C039 缺内容词保护）
- **MFA refined_count=0 未消费 · 新经验间接绕过**
- **一晚多次绕圈的复盘 + 根本教训**
- **今后可改进 P0/P1/P2 backlog**
- **硬约束确认（§11 · §14 · §15 · §16 · §20 · §8 fallback）**

**参数值列表**（作为落地报告的镜像 · 仅供快速查询 · 权威值以落地报告为准 · 任何差异以落地报告为准修正本表）：

| 参数 | 落地值 | 单位 | script 常量 |
|---|---|---|---|
| Check1 prob_threshold | 0.6 | prob | `check_hallucination.py::DEFAULT_PROB_THRESHOLD` |
| Check2 silence_thresh_db | -40.0 | dBFS | `check_silence_location.py::DEFAULT_SILENCE_THRESH_DB` |
| Check2 min_silence_len_ms | 100 | ms | `check_silence_location.py::DEFAULT_MIN_SILENCE_LEN_MS` |
| Check2 context_window_s | 1.5 | s | `check_silence_location.py::DEFAULT_CONTEXT_S` |
| Check3 target_min_ms | 120 | ms | `check_rhythm_gap.py::FALLBACK_TARGET_MIN_MS` |
| Check3 target_max_ms | 450 | ms | `check_rhythm_gap.py::FALLBACK_TARGET_MAX_MS` |
| Check4 butt_splice_xf_ms | 0 | ms | `route_crossfade_strategy.py::route` P4 |
| Check4 room_tone_pad_ms | 10 | ms | `route_crossfade_strategy.py::route` P4 |
| Check4 boundary_xf_ms | 50 | ms | `route_crossfade_strategy.py::route` P5/P7 |
| Check4 content_zone_xf_ms | 100 | ms | `route_crossfade_strategy.py::route` P6 |
| ASR-word 扩展 post_xf_ms | 50 | ms | `expand_to_asr_word_boundary.py` docstring |
| P1-P7 优先级路由 | 见落地报告 §关键发现 | — | `route_crossfade_strategy.py::route` |

**变更规则**：任何数值调整、任何新阈值加入、任何冲突项闭环、任何 backlog 落地 —— **都需要以新一份"XXXX-YYYY-cut-verify-XXX 落地"报告为凭据**，报告须包含新一次攻坚的具体证据（run 目录 + SHA + A/B 用户 accept）。本 SKILL.md §7 只作**镜像索引**，不做独立规范。
