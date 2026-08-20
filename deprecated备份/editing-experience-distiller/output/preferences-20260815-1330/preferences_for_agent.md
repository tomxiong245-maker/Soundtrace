# Mentor 偏好 · Agent 操作清单 · v1

> 生成时间：2026-08-15
> 来源：65 条真人二态决定（`aggregated.json` / `preferences.md`）
> 目的：**给下一个 Agent 读**。每条指令都以"遇到 X → 做 Y"形式，可以直接影响候选生成、打分或阻断。
> 边界：这份文件是**建议来源**，不是自动执行契约。Agent 必须先在 Challenger 目录下将它转成候选规则 JSON，跑 fixture + 真实节目 benchmark 通过，独立复核，才能进入生产。

---

## 使用方式

1. Agent 每次生成候选后、送人审前，按下列 P-XX 规则逐一评估。
2. 命中"绝对不提名 / 强 reject"→ 从候选池移除，写入 blocked_candidates 记原因。
3. 命中"强 accept / 优先 auto_cut_eligible"→ 提升到 high tier；仍需 stratum unanimous 或 autocut_policy 授权才自动剪。
4. 命中"边界 / 需人审"→ 保留为 human_review_required。
5. **所有命中都必须写 provenance**：case_ids=[...]，方便 mentor 追溯"这次判断来自哪几条历史决定"。

---

## P-01 · filler_hesitation 词内匹配阻断（词内首字误伤）

**证据**：`E301`(EP04) "额度" 里的 `额`、`E302`(EP04) "word" 里的 `er`、`E303`(EP04) "额度" 里的 `额` —— **3/3 reject**，mentor 明确 "额度是一个词" / "一个完整的单词" / "这是一个词啊"。

**规则**：candidate.reason_key == `filler_hesitation` 且 candidate.filler_token 命中 `strong_tokens` 时：
- 检查该 token 在原始转写里的**下一个字符**
- 如果构成已知实词（"额度"/"额外"/"呃对"/"哦哦不"/...）→ **绝对不提名**
- 词表见 `next_char_blocklist.yml`（TODO：从偏好卡自动生成）

**已在 v14 D4 部分实现**（`next_char_blocklist`），但没覆盖英文 "er/erm/uh/um" 命中英语单词首字（"er" in "word"）—— **需要扩英文**。

---

## P-02 · "呃" clause-tail/head 明显犹豫音 → 强 accept 候选

**证据**：`C003`(EP04 v20) 拟删 "呃"、clause-tail、confidence high → mentor accept + 反馈 "很好"；`C002`(EP04) 拟删 "呃" → accept；`C010`(EP04) 拟删 "对对" → accept。

**规则**：candidate.filler_token in `["呃","唔"]` 且 candidate.clause_position in `["clause-tail","clause-head","clause-boundary"]` 且 duration 100ms-1s：
- **默认 accept 倾向**（confidence high）
- 若同一 stratum 内 sample 全 accept → **自动 propagate 到 machine_proposed_accept**
- 不做 D6 强阻断（allow_positions 已含 clause-tail/head/boundary）

---

## P-03 · "对/然后/这个" 单个出现在句中 → 绝对不提名

**证据**：`C001`(EP04) 拟删 "对"、track_02 → reject；`N002`/`N003`(EP04) filler_strong clause-mid/boundary → 全 reject，明确 "不要剪（要保证完整性，这个是在句中）"、"剪辑痕迹明显"；`E305`(EP04) 拟删 filler_weak 单词 → reject "不是口癖啊，是完整的词"。

**规则**：candidate.filler_token in `weak_tokens`（"对/然后/这个/就是/那个"）且 candidate.clause_position in `["clause-mid","cross-clause","unknown"]`：
- **绝对不提名**
- 即使 stutter_signature 命中也不提名（对话连接词有天然重复）

**已在 v16 D6 sentence_position_gate 实现**，但要**扩展到 immediate_repetition 是否也应用 D6** —— 参见 P-05。

---

## P-04 · "嗯" 作 backchannel 全保留

**证据**：`N004`(EP04) `filler_ack_long_single` 拟删 "嗯"、时长 1-2s → reject；`N005`/`N006`/`N007`(EP04) 全 reject；`N007` 明确 "保留一些活人感（表示对别人的认可）"、`N008` "这也是表示对别人的认可" —— **4/4 reject**。

**规则**：candidate.filler_token in `["嗯"]` 且 candidate.candidate_kind == `filler_ack_*`：
- **绝对不提名**（无论时长、无论密集与否）
- 例外条件：**必须有可靠 speaker attribution 明确判为自己犹豫**（当前没有，所以永远不例外）

**已在 v16 D5 实现**（`acknowledgement_handling: auto_preserve_without_reliable_speaker_attribution`）。

---

## P-05 · immediate_repetition clause-mid 允许，但对称重复需 stutter signature

**证据**：`N012`(EP04) 拟删 "这个/这个" clause-mid → **accept "很好"**；`N014`(EP04) 拟删 "一些/一些" clause-mid → **accept "很好"**。同时 `S038`(EP04) "报告报告" → reject "很明显的剪辑痕迹，而且不需要剪"；`S056`(EP04) "我自己我自己" → reject。

**规则**：candidate.candidate_kind == `immediate_repetition`：
- **可以命中 clause-mid**（D8 免除 D6）
- 但**如果两次重复 duration 对称（asymmetry < 30%）且没有犹豫音标记（呃/额/嗯 邻近）**：
  - 拟删词是**功能词**（"这个/一些/我们/然后"）→ 保留为候选（mid tier）
  - 拟删词是**实词**（"报告/我自己/工具"）→ **降为 low tier 或不提名**

**已在 v15 D7 实现 stutter_signature 门槛**（`duration_asymmetry_ratio >= 0.3` 或 `very_tight_gap_seconds < 0.15` 或 markers 邻近）。但**没区分功能词 vs 实词**，导致 "报告报告"/"我自己我自己" 会被误提名。**建议加 `functional_word_whitelist` 和 `content_word_blocklist`**。

---

## P-06 · cough_like / mic_bump_like 家族完全撤销

**证据**：`S025`/`S081`(EP04) cough_like → reject "没有咳嗽"、"声音很轻 为什么要见"；`S082`(EP04) mic_bump_like → reject "根本没有" —— **3/3 全假警报**。

**规则**：candidate.candidate_kind in `["cough_like","mic_bump_like"]`：
- **完全禁用该家族**（当前实现严重误报）
- 未来若要重启，必须先在 fixture 上验证误报率 < 10%

**已在 v14 D1/D2 实现**（`disabled_candidate_families`）。

---

## P-07 · filler_immediate_repetition 家族全撤销

**证据**：`S038`(EP04) "报告报告" reject "很明显的剪辑痕迹"、`S056`(EP04) "我自己我自己" reject —— **2/2 全 reject**。

**规则**：candidate.candidate_kind == `filler_immediate_repetition`（内容词的连续重复）：
- **禁用**（P-05 已经用 immediate_repetition + stutter signature 覆盖了合理场景）
- 该家族与 `immediate_repetition` 重叠且更宽松，只制造误报

**建议在 v19 中禁用**。

---

## P-08 · long_pause 400ms-1s 可 accept；长于 5s 更倾向 accept

**证据**：`S022`(EP04) 400ms-1s track_02 → accept；`S047`(EP04) 5s+ track_01 → accept；`C032`(EP04) global_long_pause 5s+ track_01 → accept。1 条 reject（`S032`）明确因 "剪辑痕迹明显"，是 rendering 问题不是规则问题。

**规则**：candidate.candidate_kind in `["long_pause","global_long_pause"]` 且 duration >= 1s：
- **倾向 accept**（confidence high）
- rendering_gate 必须 >= 100ms 才允许提名（否则剪辑痕迹重）
- 必须听 A/B 才最终决定

**已在 v16 global_long_pause 实现**（`min_silence_seconds: 1.5`；v18 降到 1.10）。

---

## P-09 · 所有 REAUDIT 样本 → 遵循 round 1 结论，不复剪

**证据**：Round 2 的 REAUDIT 系列（`R-S038`/`R-S032`/`R-E304`/`R-E306`）4 条 —— 2 accept 2 reject。说明 rendering 修 crossfade 后有些能过、有些还是过不去。

**规则**：candidate.reason_key.startswith(`REAUDIT_`)：
- 视为**验证轮**，不做新提名
- 仅确认之前的边界样本经过修 crossfade 后是否变可接受
- 如果 accept → mentor 明确此边界样本已修好；写进偏好库
- 如果 reject → 该边界样本永久不剪（`permanent_reject_stratum`）

**未实现**。建议在 v19 加 `permanent_reject_registry.json` 记录被 REAUDIT reject 的历史候选特征。

---

## P-10 · rendering artifact 是独立信号，不改规则改渲染

**证据**：多条 reject 备注含"剪辑痕迹明显 / 剪辑痕迹很重 / 剪辑的时候声音明显小了"：`R-E306`/`M001`/`N002`/`N009`/`S032`/`S038`/`C026`(v20)/`C028`(v20) —— **8 条以上**。

**规则**：这不是候选提名规则问题，是**渲染契约问题**：
- 修 `render_ntrack_preview`（build_mvp_package.py）不用 `amix=normalize=1`（-9.5 dB）
- Preview 混音方式必须与最终成片一致（P-11）
- crossfade curve 从 `qsin`（中点 -6dB）换到 `qsin2`/`hsin2`（中点 -3dB）或使用主麦 automix

**追踪**：OPT-023（preview 混音契约）、OPT-021（automix 落地）。

---

## P-11 · Preview 混音方式 = 最终成片混音方式（契约）

**证据**：`C026`(v20 也是 reject) 反馈 "剪辑的时候声音明显小了"—— 溯源 preview 用 amix normalize=1 + qsin -6dB，实际成片可能走不同混音路径。mentor 听 A/B 判断的音质**不是**成片会有的音质。

**规则**：任何候选 preview 生成必须：
- 使用与最终成片相同的混音链
- 相同的 crossfade curve
- 相同的 loudnorm 处理
- 不然 preview 判断作废，标 `preview_render_mismatch`

**未实现**。OPT-023。

---

## 全局倾向

从 65 条决定归纳：

- **接受率 37%**（24/65） → mentor 极度保守；宁可漏不误剪
- **feedback 里 12+ 次 "剪辑痕迹"** → 渲染质量 > 剪辑决定质量
- **feedback 里 5+ 次 "完整的词/单词/一个词"** → 词内匹配是最大误报源
- **feedback 里 3+ 次 "活人感/认可"** → "嗯" 保留是明确偏好
- **feedback 里 2+ 次 "在句中"** → 句中保护是明确偏好

**上层原则**（mentor 反馈归纳）：
1. **完整性优先** — 一段话能说清楚就不剪；句中不剪
2. **活人感优先** — "嗯/对" 作为背景应答是认可模板，保留
3. **剪辑质量优先** — 有剪辑痕迹宁可不剪
4. **保守默认** — 拿不准就不剪

---

## 用这份偏好清单能做什么

1. **给下一版规则修订**（v19）提供证据支持的建议：见 `rules_suggestions.md`（下一步生成）
2. **给候选生成器**加白名单/黑名单（例如 P-01 的 next_char_blocklist、P-05 的功能词/实词区分）
3. **给 orchestrator prediction manifest**打 provenance：每条 machine_proposed_accept 附上 "根据 P-XX 规则 + case_ids=[...]"，mentor 能追溯
4. **回归测试**：以后新规则不能推翻这 65 条决定的语义（`mentor-feedback-regression-v1` 已有骨架）

## 不能做什么

- **不能直接改 Champion 规则**——必须先在 Challenger 目录跑 fixture + 真实节目 benchmark 通过、独立复核
- **不能作为训练数据训模型**——65 条太少，且不同期节目分布不同
- **不能替代真人 A/B 试听**——高风险候选和长停顿仍要真人听
- **不能作为 autocut_policy**——policy 需要项目负责人签署
