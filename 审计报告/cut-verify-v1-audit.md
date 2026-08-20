# cut-verify skill · 4 个开源工具集成 audit

> 状态：2026-08-19 · v1 · 落地 · EP04 7 条候选实测通过
> 位置：`skills/cut-verify/`

## 固定信息

| 项 | 值 |
|---|---|
| 脚本路径 | `skills/cut-verify/scripts/{check_hallucination,check_silence_location,check_rhythm_gap,route_crossfade_strategy,verify_cut_plan}.py` |
| 入口 | `verify_cut_plan.py` (一次跑完 4 项 check) |
| 依赖 Python | `python3.13` (系统) + `~/miniforge3/bin/python` (Check 2 需要 pydub) |
| 依赖 ffmpeg | `/opt/homebrew/bin/ffmpeg` (Check 2 抽段) |

## 4 项工具 · 开源依赖清单

### Check 1 · check_hallucination

- **开源工具**：`faster-whisper 1.2.1` (装在 py3.13 --user + miniforge3)
- **用什么字段**：`word.probability` (来自 faster-whisper `word_timestamps=True` 输出)
- **无需新装**：所有 EP04 transcript.json 已含此字段（grep 验证过）
- **阈值**：0.6（EP04 实测调 · 正常语音 >0.9 · 幻觉 <0.5）· CLI 可覆盖

### Check 2 · check_silence_location

- **开源工具**：`pydub.silence.detect_silence` (装在 `~/miniforge3` · `pydub 0.25.1`)
- **依赖**：ffmpeg (抽段) + soundfile / pydub 内置 wav 读取
- **默认参数**：`silence_thresh=-40dB · min_silence_len=100ms · context_window=1.5s`
- **等价替代（备胎）**：`librosa.effects.split` / `ffmpeg silencedetect=n=-40dB:d=0.1`

### Check 3 · check_rhythm_gap

- **开源工具**：无（纯 Python + `json`）
- **阈值来源**：`main/knowledge/cut_parameters.json`（mentor 59 gold cut 反推）
- **fallback 硬编码**：target [120, 450]ms · median 200ms（若 cut_parameters.json 缺失）

### Check 4 · route_crossfade_strategy

- **开源工具**：无（纯 Python policy 路由）
- **优先级 P1-P7**：hallucination > invalid_gap > tight_rhythm > silence_butt > boundary_xfade > content_review > fallback

## SHA-256（2026-08-19 版本）

```
待生成 (脚本落定后 shasum -a 256 skills/cut-verify/scripts/*.py)
```

## 数据流

```
输入：candidate_source.json + 三轨 transcript.json + 三轨 raw WAV + cut_parameters.json
    ↓
Check 1 (每候选) → hallucination.json
Check 2 (每候选 · 需 raw wav) → silence.json
Check 3 (每候选) → rhythm.json
Check 4 (读前 3 项) → route.json
    ↓
融合 overall_verdict + recommended_params → verified_edl.json
```

## 输出 schema · cut-verify-v1

```json
{
  "candidates": [{
    "candidate_id": "C007",
    "current_cut": {"start_seconds": 354.23, "end_seconds": 354.62, "track_id": "track_01", "kind": "filler_hesitation", "token": "呃"},
    "checks": {
      "hallucination": {"verdict": "REJECT_LOW_PROB_HALLUCINATION", "asr_word_probability": 0.4876},
      "silence_location": {"verdict": "CUT_IN_SILENCE_BUTT_SPLICE_OK", "cut_fully_in_silence": true},
      "rhythm_gap": {"verdict": "RHYTHM_OK", "post_cut_gap_ms": 295},
      "crossfade_strategy": {"strategy": "REMOVE_FROM_EDL", "priority_level": "P1"}
    },
    "overall_verdict": "REJECT_HALLUCINATION",
    "recommended_params": {"crossfade_ms": null, "strategy": "REMOVE_FROM_EDL"}
  }]
}
```

## 覆盖范围（EP04 7 候选实测）

| Candidate | Mentor Gold | cut-verify | 一致 |
|---|---|---|---|
| C007 | accept | REJECT_HALLUCINATION | ⚠️ (mentor 剪静音 · tool 判 token 幻觉) |
| C014 | reject | REJECT_HALLUCINATION | ✅ |
| C023 | reject | NEEDS_HUMAN_REVIEW | ✅ |
| C034 | accept | CLEAN_SHORT_CROSSFADE (50ms) | ✅ |
| C036 | reject | CLEAN_BUTT_SPLICE | ⚠️ (缺 false-repetition 检测) |
| C039 | reject | CLEAN_SHORT_CROSSFADE | ⚠️ (缺内容词保护) |
| C044 | accept | CLEAN_BUTT_SPLICE (0ms) | ✅ |

**一致率 4/7 = 57%** · 剩下 3 处冲突反映 skill 未覆盖的语义层（false-repetition · 内容词保护）· 独立 backlog。

## 硬边界

- 不写 EDL · 不改音频 · 不改 session_feedback · 不改 cut_parameters.json
- Check 1 / 2 / 3 完全**确定性**（数字 threshold + 布尔判决）· 不含 LLM
- Check 4 是 policy 路由 · 输出只是**建议** · 不 auto-apply

## 未覆盖 / 独立 backlog

- **False-repetition detection**（C036 什麼 · ASR 拆词假重复）· 需要 MFA 或 phoneme-level equivalence check
- **Content-word protection**（C039 一些 · 量词非口癖）· 需要 spaCy POS tag + 白名单
- **多期泛化**：EP04 单期证据 · 阈值 0.6 与静音 -40dB 需 EP05+ 验证
- **MFA 集成**：本 skill 未消费 MFA boundaries（refined_count=0 时 fallback ASR）· 独立 backlog

## 与 CLAUDE.md 边界对应

- §8 MFA 音素级精修 · 本 skill 未强制 · fallback ASR (需 backlog)
- §11 禁自由发挥 · 4 项 check 全用现装开源工具 · 无新造依赖
- §15 装了必用 · Check 1 用 word.probability (原本没消费) · Check 2 用 pydub.silence (原本没消费)
- §16 高级拼接 · Check 4 明确 butt splice / 50ms xfade / 100ms xfade 三档 · 禁 concat+anullsrc
- §17 保留词 onset 保护 · 本 skill Check 3 已用 gap 阈值间接覆盖 · 但未消费 librosa.onset (backlog)

## 三档诚实标注

**已验证事实**：
- 4 个 check 脚本在 EP04 7 候选上跑通 · 输出结构化 verdict
- 与 mentor gold 一致率 57% · 一致的 4 条覆盖真实场景（幻觉/边界/butt/xfade）
- 无新装依赖 · 全部现有工具

**已决定的方向**：
- Skill 只输出**建议侧车** · 不改主流水
- Check 4 优先级 P1-P7 硬编码 · 后续可通过 override 调
- 冲突的 3 条候选（C036/C039/mentor C007）不试图靠加规则解决 · 走 backlog

**待验证假设**：
- prob 阈值 0.6 · silence -40dB · 单期 EP04 证据 · 需 EP05+ 泛化
- pydub.silence 与 librosa.effects.split 是否输出一致（未 A/B 验）
- cut_parameters.json target_range [120, 450] 是否覆盖所有 kind（segment_separator 特殊场景需另处理）
