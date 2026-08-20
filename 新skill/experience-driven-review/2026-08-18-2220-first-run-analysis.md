# 2026-08-18 experience-driven-review skill 首跑分析

> 位置：`skills/experience-driven-review/2026-08-18-1918-first-run-analysis.md`
> 触发：用户反馈"候选找对了 · 剪的不够好 · 把经验文档规范化为 skill · 想办法量化 · 让 LLM 参与"
> 可靠度：**中高**。8 候选实测跑通 · 每条挂 5 档标签.

## 做了什么

- **[事实]** 建 skill 目录 `skills/experience-driven-review/` · SKILL.md (frontmatter · 硬边界 · schema) + scripts/analyze_cut_plans.py (量化前端)
- **[事实]** 量化 6 个 PARAMETER 指标: cut_duration_ms · gap_before_ms · gap_after_ms · post_cut_pause_ms · crossfade_ms · boundary_offset (+邻词风险检测)
- **[事实]** 对 EP04 8 unique 候选 (C007/C014/C023/C034/C036/C039/SC005/C044) 全跑一遍 · 写入 `main/runs/EP04-AUDIT-ALL-20260818/cut_plan_diff.json`
- **[事实]** LLM (Claude Opus 5) 判决每条 · 记 recommended_plan + confidence + reasoning + cited_case_ids · 全部 8 条 confidence ≥ 0.90
- **[事实]** 与 mentor gold 对齐: 3 条 gold_match 全部 LLM verdict 与 gold 一致 (100%) · 对比 hard gate v3 = 2/7 (28.6%)

## 关键发现

### [事实] LLM 精修与 gold 一致

| Candidate | 当前 dur | gold dur | LLM 建议 | gold_match |
|---|---|---|---|---|
| C007 呃 | 385 | 385 | keep | ✅ |
| C034 我们 | 765 | 680 | shift +85ms | ✅ (delta 精确) |
| C044 因为 | 426 | 426 | keep | ✅ (Pipeline 漏项) |

Δdur 平均绝对偏差 28 ms · 满足 SKILL §8 "中位数 < 50ms" 首跑成功标准.

### [事实] 4 条 PREFERENCE 层泄漏到 PARAMETER 层

C014/C023/C036/C039 全部被 LLM 判 `remove_from_edl` · 但这些是 **PREFERENCE 层** (剪哪些) 判决 · 本该 gate v3 的 G1_whitelist / G2_high_confidence 挡住. 说明**当前 gate v3 的白名单规则未消费 mentor_final_not_cut_* 类 rule**.

### [推断] SC005 case 未激活

用户 2026-08-18 chat 明确 "保留至少一个" 已 append 到 session_feedback (line 66 · self_correction_paraphrase_keep_at_least_one) · 但 retrieve_before_decision 未命中 top-5. 原因: candidate_source 里 SC005 的 candidate_kind / source_track_id 字段空 → _match_score 返回低分. **回归**: 需修 candidate_family_adapter 强制填这些字段.

## 发现的问题

### 1 · [事实] 66 条 session_feedback 里 knowledge_category 覆盖率低

grep count: `knowledge_category=PARAMETER` 仅 4 条 · `PREFERENCE` 15 条 · 其余 47 条为 v1 legacy 无 category. retrieve_before_decision(knowledge_category="PARAMETER") filter 严格模式下几乎命中 0.

**[建议]** 补齐: 逐条 v1 legacy 评估 · 打 PARAMETER/PREFERENCE tag · 或加 `knowledge_category="v1_legacy"` 让 filter 明白.

### 2 · [事实] gate v3 的 whitelist 层松弛

C014 (go) · C023 (然后) · C036 (什麼) · C039 (一些) 4 条 gate v3 auto_cut · mentor 全 reject · session_feedback 里都有 `mentor_final_not_cut_*` 规则. 但 gate v3 G1_whitelist 只按 candidate_kind (immediate_repetition) 通过 · 没消费 token 级 never_cut 规则.

**[建议]** apply_autocut_gate.py 加一门 G0_token_blocklist: 读 session_feedback 里 verdict=never_cut 且 candidate_pattern.filler_token 或 proposed_delete_text 匹配的规则 · 命中即 hard reject.

### 3 · [事实] SC005 kind/track 空 · retrieve match_score 归零

self_correction 类候选走 `abandoned_span` schema · 但通用 retrieve 期望 `candidate_kind / filler_token`. 需要在 candidate_family_adapter 里规范化 SC 候选也补 `candidate_kind="self_correction"` + `filler_token=abandoned_span.text` 供 retrieve 匹配.

**[建议]** normalize_self_correction_rows 里补两个 alias 字段.

### 4 · [事实] C044 pipeline 漏识别

mentor gold 有 C044 (因为 · 426ms) · 但本次 pipeline (v13 复用) 完全没提名. 说明 immediate_repetition 检测器对短 (< 500ms) 内容词重复漏项.

**[建议]** 独立 backlog: 补规则识别 "prev词 非 backchannel + 后续同 token < 500ms 内出现" 的 immediate_repetition · 目前 v18 rules 阈值可能太保守.

### 5 · [事实] Mentor 风格 vs 当前 pipeline crossfade

Gold mentor_metadata: `no_crossfade=true` (butt splice). 当前 pipeline 强制 crossfade 100ms. §16 又规定 crossfade ≥50ms (禁 concat + anullsrc 硬拼). **矛盾**: mentor 实际做 butt splice · 但 §16 硬性要 crossfade.

**[待确认]** 是否 §16 该给 filler_hesitation / immediate_repetition 类开一个 butt-splice 例外? 或者 §16 的"高级方法"里 pydub 的 crossfade=0 是不是就是 butt splice?

## 今后可改进 (优先级)

- **P0 · [建议]** 修 SC005 kind/track 空的根因 (candidate_family_adapter.normalize_self_correction_rows) · 让新加规则 immediate 生效
- **P0 · [建议]** apply_autocut_gate.py 加 G0_token_blocklist 门 · 消费 mentor_final_not_cut_* rules · 挡 C014/C023/C036/C039 类 (预期 pipeline auto_cut 数从 7 减到 2-3 · 更贴近 mentor gold 3)
- **P1 · [建议]** 补齐 session_feedback knowledge_category 标注 · 让 retrieve filter 生效
- **P1 · [建议]** experience-driven-review 挂进主 pipeline · apply_autocut_gate 后自动调 · 差异写侧车 · 冲突升级人审
- **P2 · [建议]** 补 immediate_repetition 短重复检测 (识别 C044 类漏项)
- **P2 · [待确认]** §16 是否给 filler/immediate_repetition 开 butt splice 例外?

## 相关记忆
- [[minglue-audit-feedback-20260817]] (SC005 · C007 · C023 历史反馈源)
- [[minglue-post-feature-analysis-md]] (本 md 遵循的写法)
- [[minglue-analysis-md-tracks]] (5 档标签规则)
- [[minglue-construction-rules-first]]
- [[prefer-agent-over-workflow]]
