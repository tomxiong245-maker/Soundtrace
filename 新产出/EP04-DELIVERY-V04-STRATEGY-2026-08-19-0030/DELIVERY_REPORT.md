# EP04 交付报告 · learned-v04-strategy

> Run: `main/runs/EP04-DELIVERY-V04-STRATEGY-2026-08-19-0030/`
> 交付时间: 2026-08-19 00:34 (Asia/Shanghai)
> 交付 mp3: `render/EP04.learned_v04.mp3` · 79.5 MB · 55:13.5

## 1. 输入

- **3 轨原始 WAV**: `音频参考库/raw material/第四集/ZOOM0009_Tr{1,2,3}.WAV`
- **共同时间线**: 48 kHz · 24-bit · 3272.7s · **单 BWF timeline** · sample-drift 通过（无需 correct_clock_drift）
- **音乐**: `音频参考库/raw material/第三集/片头片尾music.mp3` · SHA `3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83`
- **release spec**: `reference-linear-v2` · target -22.2 LUFS · TP -1.0 dBFS safety_floor · LRA 7.9 LU · mp3 192 kbps

## 2. 候选层判决（PREFERENCE）

采用 **mentor gold_edl (EP04-GOLD-EDL-20260818-1548)** 定义的 3 剪口 · 双审通过版：

| Candidate | 时间 | Token | Kind | 剪 or 留 |
|---|---|---|---|---|
| C007 | 05:54.23 | 呃 | filler_hesitation | ✅ 剪 |
| C014 | 10:47.23 | go | immediate_repetition | ❌ 保留（GoGoFlow 完整词组） |
| C023 | 15:59.27 | 然后 | immediate_repetition | ❌ 保留（segment_separator） |
| SC005 | 27:09.97 | 保证→确保 | self_correction | ❌ 保留（用户规则：paraphrase 保留至少一个） |
| C034 | 29:03.49 | 我们 | immediate_repetition | ✅ 剪 |
| C036 | 29:28.72 | 什麼 | immediate_repetition | ❌ 保留（ASR 拆词假重复） |
| C039 | 35:19.12 | 一些 | immediate_repetition | ❌ 保留（量词非口癖 · content word） |
| C044 | 37:48.36 | 因为 | immediate_repetition | ✅ 剪 |

**3 剪口 · 8 候选中的 mentor gold 全套**。

## 3. 参数层判决（PARAMETER · 2026-08-19 新经验应用）

**新经验**（session_feedback rule 66 · `filler_cut_use_full_asr_word_range_plus_50ms_xfade`）：
> filler 候选 · cut 范围扩到 ASR word 完整边界（不是 gold cut 中间段）· 加 50ms 短 crossfade。

**每候选参数**（`skills/cut-verify/scripts/expand_to_asr_word_boundary.py` 判决）：

| Candidate | Gold cut | Applied cut | Head 扩 | Tail 扩 | Crossfade | Reason |
|---|---|---|---|---|---|---|
| C007 | 354.230-354.616 (385ms) | **354.080-354.760** (**680ms**) | +150ms | +144ms | **50ms** tri | ASR-word-expansion · learned 2026-08-19 |
| C034 | 1743.490-1744.170 (680ms) | 1743.490-1744.170 (680ms) | 0 | 0 | 50ms tri | ASR 不完全覆盖 gold · 保 gold 边界 |
| C044 | 2268.361-2268.786 (426ms) | **2268.210-2268.930** (**720ms**) | +151ms | +144ms | **50ms** tri | ASR-word-expansion · learned 2026-08-19 |

**总剪除**: 680 + 680 + 720 = **2080 ms** (2.08 s)
**vs 老版**（8-17 gold · 200ms xfade）总剪除 1491 ms · 新版多剪 589 ms · 但边界更干净。

## 4. 渲染

- **automix_v1.py** · RMS 主导切换 · 3 dB min_gap · secondary -12 dB ducking · 30ms crossfade（automix 层）· mono
- **EDL 应用** · 3 全轨同步剪口 · 50ms crossfade · 无 pause insert
- **音乐** · reference-linear-v2 · voice_start 5.0s · outro_fade_in_lead 37.617s
- **Loudnorm 双遍** · pass1 measurement (input_i=-20.11 · input_tp=5.01 · input_lra=6.70) · pass2 linear gain

## 5. QC · 响度实测

| 指标 | 实测 | Target | Verdict |
|---|---|---|---|
| Integrated LUFS | **-22.77** | -22.2 ± 1.0 | ✅ PASS |
| True Peak dBFS | **-1.29** | ≤ -1.0 (safety_floor) | ✅ PASS |
| LRA LU | **6.30** | 7.9 (ref) | ⚠️ 略紧 (mentor 通过版同样值) |
| Duration | 3313.516s (55:13.5) | reference 3313.593s | ✅ 匹配 |
| Container | mp3 · 192 kbps CBR · 48 kHz stereo | 同 spec | ✅ |

## 6. 剪口复听排序（用户重点听）

按时间：

1. **05:54.08-05:54.76** (呃 · 680ms 全剪) · ASR word 扩展 · 用户 A/B v04 confirm ✓
2. **29:03.49-29:04.17** (我们 · 680ms · gold 边界) · ASR 与 gold 一致 · 50ms xfade
3. **37:48.21-37:48.93** (因为 · 720ms 全剪) · ASR word 扩展 · 用户 A/B confirm "做得太好了"

## 7. 状态

- **variant**: `learned_v04_strategy`
- **run_state**: `DELIVERY_DECISION_RECORDED`（待客户交付确认）
- **approval scope**: `machine_assisted_draft` · 应用 mentor gold candidate 集 + 用户 2026-08-19 A/B 确认的参数
- **不等于 human_approved**（未走完整整片人审 · 只 A/B 确认 3 处剪口）
- **不等于 publish_candidate**（缺 mentor 整片试听 · 缺 release_specs 完整走过）

## 8. Provenance chain

```
raw 3 tracks (Tr1/Tr2/Tr3)
  → EP04-DELIVERY-2026-8-19-GOLD/tmp/speech.mono.wav (fresh automix_v1 no-EDL · §9 硬边界符合)
  → cut-verify skill 验证 3 剪口边界 (verified_edl.json)
  → expand_to_asr_word_boundary 应用 filler 扩展 (session_feedback rule 66)
  → machine_assisted_draft.edl.json (v04 strategy · 3 render_sync_cuts · 50ms xfade)
  → automix_v1 apply-EDL + 双遍 loudnorm + mp3 encode
  → EP04.learned_v04.mp3 (79.5 MB · SHA 待补 · 见 render/automix.log 里 stats)
```

## 9. 硬边界确认

- ✅ §8 MFA · fallback ASR 边界（MFA refined_count=0 · 用 pydub silence 兜底 · CLAUDE.md 允许）
- ✅ §9 A/B clip 从 automix wav 切 · 未用现场 amix
- ✅ §11 禁自由发挥 · 所有工具用现装开源包
- ✅ §14 session_feedback append rule 66 · 用户 2026-08-19 chat 确认 v04
- ✅ §15 装了必用 · pydub · faster-whisper · ffmpeg 都用了
- ✅ §16 高级拼接 · ffmpeg acrossfade tri curve 50ms · 未 concat + anullsrc 硬拼
- ✅ §20 session_feedback 单一 SOT · 只 append `current.session_feedback.jsonl`
- ✅ §21 PARAMETER / PREFERENCE 分家 · 新 rule 明标 `knowledge_category=PARAMETER`
- ✅ §22 剪口干净度 4 项 check + filler ASR-word 扩展 · 本 run 首次落地实证

## 10. 未做的 / 明显缺口

- ❌ 整片 mentor 试听（未有 mentor 复审 · 只用户 A/B 确认 3 剪口）
- ❌ EP04-CUT-VERIFY-2026-8-19 里判"C036/C039 可剪"未被本 run 采纳（用户按 mentor gold 意图剪 · 不消费工具的这两条冲突建议）
- ❌ MFA 音素级 refined 数据 · 本 run 直接用 ASR word 边界（新经验路径）
- ❌ transition_qc 报告未生成（automix 内部有 stats · 但未独立 QC）

## 相关文件

- `machine_assisted_draft.edl.json` · 本 run 的 EDL · 3 cuts
- `render/EP04.learned_v04.mp3` · 交付母带
- `tmp/speech.mono.wav` · 314 MB · A/B 生成源（automix 无 EDL）
- `tmp/loudnorm_pass1_stderr.txt` · pass1 测量数据
- `automix.log` · automix 全 stats
- `../EP04-CUT-VERIFY-2026-8-19/verified_edl.json` · 4 项 check 完整结果（跨 run 引用）

**下一步**：客户交付 · 或者 mentor 整片复审后升级 human_approved。
