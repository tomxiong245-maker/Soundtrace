# 2026-08-19 · cut-verify skill 落地与 EP04 交付攻坚

> 位置：`skills/cut-verify/2026-08-19-0040-cut-verify-landing-and-EP04-delivery.md`
> 触发：2026-08-18 深夜到 2026-08-19 凌晨 · 用户"一周了 · 每一版都不干净 · 要被辞退" · Path B "开发 4 个开源工具集成" 一夜攻坚
> 可靠度：**中高** · 事实全部有 SHA + run 目录支撑 · 结论/建议明标档次

## 做了什么

- **[事实]** 新建 skill `skills/cut-verify/` · SKILL.md + audits/cut-verify-v1.md + 6 个脚本
- **[事实]** 6 个脚本 · 全部现装开源工具 · 无新依赖：
  - `check_hallucination.py` · faster-whisper `word.probability` < 0.6 判 filler 幻觉
  - `check_silence_location.py` · pydub.silence.detect_silence 判剪口是否落静音段
  - `check_rhythm_gap.py` · cut_parameters.json target_range 阈值判剪后 gap
  - `route_crossfade_strategy.py` · P1-P7 policy 路由 · butt splice vs crossfade
  - `verify_cut_plan.py` · entry_tool · 一次跑完 4 项 check · 写 verified_edl.json 侧车
  - `expand_to_asr_word_boundary.py` · 应用 2026-08-19 新学到的经验 · filler ASR-word 边界扩展
- **[事实]** tools.json 51 → 57 项 · 6 项新登记
- **[事实]** CLAUDE.md 加 §22 硬边界 · 剪口干净度 4 项 check + filler ASR-word 扩展 + 50ms xfade
- **[事实]** SKILL.md (顶层) 7 skill → 8 skill · 加 cut-verify (L2)
- **[事实]** session_feedback.jsonl 65 → 66 行 · +1 条规则 `filler_cut_use_full_asr_word_range_plus_50ms_xfade`
- **[事实]** EP04 7 候选实测 (main/runs/EP04-CUT-VERIFY-2026-8-19/verified_edl.json) · 与 mentor gold 一致率 4/7
- **[事实]** C007 A/B 4 版对比 (main/runs/EP04-C007-AB-COMPARE-2026-8-19/) · 用户 confirm v04 最干净 = ASR word 边界 (354.08-354.76) + 50ms xfade
- **[事实]** C044 A/B 验证 (main/runs/EP04-DELIVERY-V04-STRATEGY-2026-08-19-0030/ab_compare/) · 用户 confirm "做得太好了" · 同规则从 gold 426ms 扩展到 720ms
- **[事实]** EP04 最终交付版 mp3 · `main/runs/EP04-DELIVERY-V04-STRATEGY-2026-08-19-0030/render/EP04.learned_v04.mp3` · 79.5MB · 55:13 · -22.77 LUFS · TP -1.29 dBFS · 3 剪口应用新经验（C007 扩展 · C034 保 gold · C044 扩展）

## 关键发现

### [事实] 一周不干净的**结构性**根因

老 pipeline 缺 4 个 check · 所有版本都在同一个坑里：

1. **缺 filler 幻觉检测** → C007 呃 (prob 0.488) 一直被当"有内容"剪 · 实际是幻觉
2. **缺剪口位置校验** → gold cut 位置常"跨越内容-静音边界" · 200ms crossfade 把内容尾巴糊过来 = ghost
3. **缺节奏跳变指标** → cut 385ms 静音 · post_cut_gap 计算未 gate · 有时抢话
4. **一刀切 crossfade 参数** → 所有剪口 200ms · 静音段被糊 · 内容段又不够 mask

**[事实]** 新工具解决前 4 项 · EP04 一晚跑通。

### [事实] 学到的具体经验 (session_feedback rule 66)

C007 A/B 4 版对比 · 用户 confirm v04：
- 老版：gold cut (354.23-354.62) + 200ms xfade · **呃头尾残留 + xfade 糊** = 不干净
- 新版：**ASR word 边界 (354.08-354.76) + 50ms xfade** = **干净**

**规则文本**（append 到 session_feedback）：
> filler 候选剪切 · 用 ASR word 完整边界（不是 gold cut 中间段）+ 50ms crossfade · 呃头尾彻底消失。

**迁移**：同规则应用到 C044 因为 (gold 426ms → ASR 720ms) · 用户 confirm 同样干净。**规则一晚验证了 2/3 个 mentor gold cut**（C034 因 ASR 不完全覆盖 gold · 保 gold + 50ms xfade）。

### [事实] EP04 8 unique 候选跨 run 汇总

| ID | Token | Mentor Gold | 今晚交付版 | 新工具 verdict |
|---|---|---|---|---|
| C007 | 呃 | ✅ accept | ✅ 剪 · ASR 扩 680ms + 50xf | REJECT_HALLUCINATION（工具 vs mentor 冲突 · 用户拍板剪） |
| C014 | go | ❌ reject | ❌ 不剪 | REJECT_HALLUCINATION ✓ |
| C023 | 然后 | ❌ reject | ❌ 不剪 | NEEDS_HUMAN_REVIEW ✓ |
| SC005 | 保证→确保 | ❌ reject | ❌ 不剪 | 不 apply（用户"保留至少一个"规则） |
| C034 | 我们 | ✅ accept | ✅ 剪 · gold 680ms + 50xf | CLEAN_SHORT_CROSSFADE ✓ |
| C036 | 什麼 | ❌ reject | ❌ 不剪 | CLEAN_BUTT_SPLICE（工具 vs mentor 冲突 · 缺 false-repetition 检测） |
| C039 | 一些 | ❌ reject | ❌ 不剪 | CLEAN_SHORT_CROSSFADE（工具 vs mentor 冲突 · 缺内容词保护） |
| C044 | 因为 | ✅ accept | ✅ 剪 · ASR 扩 720ms + 50xf | CLEAN_BUTT_SPLICE ✓ |

## 发现的问题（今夜暴露的）

### 1 · [事实] cut-verify 与 mentor gold 冲突 3 处

- **C007** · 工具判 REJECT_HALLUCINATION (prob 0.488) · mentor 是"剪静音压缩节奏" · 两个视角。**用户最终按 mentor 意图剪 · 但用 ASR word 边界扩展 · 效果更好**
- **C036 什麼** · 工具判 CLEAN_BUTT_SPLICE · mentor 拒（假重复） · **缺 false-repetition 检测器** · backlog
- **C039 一些** · 工具判 CLEAN_SHORT_CROSSFADE · mentor 拒（量词非口癖）· **缺内容词保护** · backlog

### 2 · [事实] MFA refined_count = 0 · 未消费

**触发**：v13 复用的 candidate_source 里 filler_token 字段与 mandarin_mfa 词典不匹配 · MFA 全 skip。**后果**：本轮所有剪口边界仍用 ASR 词级（Whisper 100-150ms 偏移未修）· 只靠 pydub silence + gap 阈值兜底。**新经验**（ASR word 扩展 + 50ms xfade）**间接绕过**了 MFA 缺失。

### 3 · [事实] 一晚多次绕圈的复盘

**耗时最多的错误**：
- 早期把 audit A/B (`make_edl_ab_clips.py`) 和交付 render (`automix_v1.py`) 混用 · 参数不一致导致听感反复
- v13 mode 1 复用因为 candidates 里 filler_token 空 · retrieval 失效
- 100ms vs 200ms crossfade 参数反复 · 因为没有量化指标 · 全靠猜
- Mentor gold 位置 `354.23-354.62` 被以为是"正确剪辑边界" · 实际只是"mentor 保守剪法" · 用户耳朵更严

**根本教训**：**没有"剪口干净度定量指标"就是**一周不干净的**底层根因**。今晚 4 项 check 补上后 · 一次 A/B 对比就找到答案。

## 今后可改进（优先级）

- **P0 · [建议]** 补 false-repetition 检测器（C036 什麼类）· 用 MFA 音素级 equivalence 或 spaCy tokenizer 判 "你 什麼 什麼" vs "什麼 什麼"
- **P0 · [建议]** 补内容词白名单（C039 一些类）· spaCy POS tag + never_cut_list（session_feedback 已有 never_cut_yixie 规则 · 但 gate 未消费）
- **P1 · [建议]** MFA 集成 · 让 refined_count > 0 · 消费 mfa_boundaries.json 供 check_silence_location 更准
- **P1 · [建议]** 多期泛化验证 · EP05 上线时跑 cut-verify · 看 prob 0.6 / silence -40dB 阈值是否稳定
- **P2 · [建议]** verify_cut_plan 挂主 pipeline · run_end_to_end.py Stage 3.7 自动跑 · 输出侧车 · gate 消费

## 相关记忆

- [[minglue-audit-feedback-20260817]]（C007 · C014 · C023 · C034 · C036 · C039 · SC005 历史反馈源）
- [[minglue-post-feature-analysis-md]]（本 md 遵循的写法）
- [[minglue-analysis-md-tracks]]（5 档标签规则）
- [[minglue-construction-rules-first]]（先读施工规则）
- [[prefer-agent-over-workflow]]（一晚单进程 · 未用 Workflow）

## 硬约束确认

- ✅ 所有新工具用现装开源包（faster-whisper / pydub / json）· 无新依赖 · 符合 §11 + §15
- ✅ 不写 EDL · 不改音频 · 不改 session_feedback · 只写侧车 → 符合 §14 · §20
- ✅ session_feedback 单一 SOT · +1 行 append · 未新建文件 → 符合 §20
- ✅ 4 项 check 全部确定性 · 无 LLM 判决 · 符合 §11
- ⚠️ MFA 未成功 refine · 但 CLAUDE.md §8 允许 fallback（OOV 走人审） · 未违反
- ✅ 交付 mp3 走 automix_v1 · loudnorm -22.2 · TP -1.29 · 符合 §9 · §16
