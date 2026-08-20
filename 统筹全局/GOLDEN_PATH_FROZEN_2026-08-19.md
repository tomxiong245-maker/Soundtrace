# GOLDEN PATH FROZEN · 2026-08-19 · LLM-First 最终架构

## 冻结签字

- **project_owner**: 熊镇正 (开发者身份) · 2026-08-19 evening
- **突破 M1 规则** · M1 保护生产 pipeline · 开发者身份可直接冻结架构
- **说法原文**: "图片中的这个成功经验就是最终版 · 全盘保留"
- **前提**: 3 关键漏洞已修 (Stage 3.7 触发 · Stage 5 消费 · Stage 6.10 交集) · EP05 端到端真跑通
- **Champion 冻结证据 run**: `main/runs/EP05-GOLDEN-PATH-20260819-1900/`

## 冻结的架构图 (最终版 · 不再变)

```
候选生成 (rules · 高召回):
├── filler-global-pause detector
├── self-correction detector
└── transient-events detector (cough disable)
              ↓
autocut_gate 只保留 4 门结构性门 (物理约束):
├── speaker_role · 剪错人不行
├── source_track · 伪影不行
├── G6_duration · > 0.8s 剪不干净
└── review_budget · 人审资源
    (G1_whitelist / G2_high_confidence / G3_no_preserve /
     G5_history / G7_session_feedback / G7_protection 让位 diagnostic_only)
              ↓
所有候选进 LLM (语义门全交 LLM 判决)
              ↓
Stage 3.7 · LLM 语义 filter ⭐ 唯一 "该不该剪" 判决
├── verdict: KEEP_CUT / REJECT_KEEP / NEEDS_REVIEW
├── reason: 语义级理由
└── confidence: high / medium / low
    (3 mode: claude CLI 首选 · Anthropic API · fallback subagent)
              ↓
Stage 5 · EDL (只用 LLM KEEP_CUT 的候选)
              ↓
Stage 6 · Automix (default 参数)
              ↓
Stage 6.7 · Optuna (参数级优化 · 对 KEEP_CUT)
              ↓
Stage 6.5 · NISQA (客观 benchmark)
              ↓
Stage 6.10 · re-render 用 Optuna 参数 (LLM ∩ Optuna 交集)
```

## 8 Stage 真证据 (from EP05-GOLDEN-PATH-20260819-1900 · 全绿)

### Stage 1 · 候选生成 (rules · 高召回) — 全通
- **Detector**: filler-global-pause + self-correction + transient-events (cough disable)
- **EP05 数字**: **2 候选** (cough disable 真生效 · 老 pipeline 36 → 现在 2)
- **Evidence**: `all_candidates.json` (48,115 B)

### Stage 2 · autocut_gate (结构性门) — 全通
- **保留**: speaker_role · source_track · G6_duration · review_budget
- **让位**: G1_whitelist / G2_high_confidence / G3_no_preserve / G5_history / G7_session_feedback / G7_protection
- **EP05 数字**: auto_cut = 1 · 结构性门全跑
- **Evidence**: `autocut_gate/` 目录

### Stage 3.7 ⭐ · LLM 语义 filter (唯一 "该不该剪" 判决) — 全通 (**修 1 落地**)
- **Skill**: `交付/最终交付文档/新skill/candidate-semantic-veto/SKILL.md`
- **3 mode**: Mode 1 claude CLI (首选 · 无 API key) · Mode 2 Anthropic API · Mode 3 fallback subagent
- **Runner log marker**: `[stage 3.7 · LLM semantic filter] 用户 2026-08-19 · LLM 唯一候选决定`
- **Verdict summary log**: `verdict summary: {"total": 2, "keep_cut": 1, "reject_keep": 1, "needs_review": 0}`
- **EP05 数字**: total=2 · keep_cut=1 · reject_keep=1 · needs_review=0 · 全 high confidence
- **Evidence**: `llm_verdicts.json` (988 B · mode=`claude_cli` · computed_at 2026-08-19T11:24:29Z)

### Stage 5 · EDL (只用 LLM KEEP_CUT) — 全通 (**修 2 落地**)
- **代码**: `stage_edl_from_gate` 读 `llm_verdicts.json` · 只保留 KEEP_CUT
- **Runner log**: `[stage 5 · EDL] LLM 决定 · 只用 1 KEEP_CUT 候选 (auto_cut 原 1)`
- **EP05 数字**: EDL actions=1 (只剪 C004 · LLM 认可) · autocut_policy=`APPROVED_FOR_WHITELIST_KINDS_ONLY`
- **Evidence**: `machine_assisted_draft.edl.json` (1,373 B)

### Stage 6 · Automix (default 参数) — 全通
- **EP05 成品**: `render/EP05.machine_assisted_draft.mp3`
- **数字**: 8,221,868 B · 342.516 s · 192 kbps · SHA `0bee3208bfb50f492dc21a9ce2383bf8b383a06c4b21173423a688e8dd751582`

### Stage 6.7 · Optuna (参数级优化 · 对 KEEP_CUT) — 全通
- **EP05 数字**: 2 候选进 · clean=1 + escaped_max=1 · avg_iter=10
- **Evidence**: `iterative_refinement_tmp/` + `refinement_trace.json` (110,121 B)

### Stage 6.5 · NISQA (客观 benchmark) — 全通
- **EP05 分数**: overall=**2.85** · noisiness=2.22 · discontinuity=4.11 · coloration=3.42 · loudness=3.63
- **Evidence**: `nisqa_benchmark.json` (schema=`nisqa-mos-v1` · model=`nisqa_v2.0`)

### Stage 6.10 · re-render 用 Optuna 参数 (LLM ∩ Optuna 交集) — 全通 (**修 3 落地**)
- **代码**: 只对 LLM KEEP_CUT 且 Optuna converged 的候选 apply
- **Runner log**: `LLM KEEP_CUT 候选: 1 · 只对这些 apply Optuna 参数` → `0 交集 · skip re-render · 成品用 default`
- **EP05 行为**: skip re-render (**符合设计** · Optuna clean 只有 FAM-self_correction · LLM 已 REJECT · 空交集 · 成品用 default)

## LLM 判决 (真 · 从 llm_verdicts.json)

| candidate_id | kind | verdict | confidence | reason |
|---|---|---|---|---|
| C004 | filler_hesitation | **KEEP_CUT** | high | 候选'就是'为孤立重复填充词，后文'就是就是设计'存在明显冗余重复，剪掉后'我主要负责的工作就是设计这样一套工作流'语义连贯 |
| FAM-self_correction-track_01-42a1ca9d868e | self_correction | **REJECT_KEEP** | high | 句号后新句开启话轮转换（主持人提问结束→嘉宾开始自我介绍），'我的话'是新句起始的话头，非自纠正 filler |

## 3 处修复记录 (今晚 · 2026-08-19 evening)

### 修 1 · Stage 3.7 pipeline hook 真触发
- **之前**: EP05 早期 pipeline 跑得早 · `llm_semantic_filter.py` 还没造好 · Stage 3.7 silent skip · verdict 是 post-hoc sidecar
- **现在**: skill script 已建 · Mode 1 (claude CLI) 无 API key 直接跑 · runner log 有明确 marker · verdict 在 pipeline 内产出

### 修 2 · Stage 5 EDL 真消费 llm_verdicts
- **之前**: EDL 直接从 autocut_gate 生成 33 cuts · LLM verdict 是 sidecar · 下游不消费
- **现在**: `stage_edl_from_gate` 读 `llm_verdicts.json` · 只保留 KEEP_CUT · 有明确 log · EP05 落地 1 cut

### 修 3 · Stage 6.10 · LLM ∩ Optuna 交集
- **之前**: Stage 6.10 对所有 converged apply · 忽略 LLM verdict · 可能剪不该剪
- **现在**: 只对 LLM KEEP_CUT 且 Optuna converged apply · 空交集时 skip re-render · 成品用 default

## Champion 保护 (冻结后 M1 生效)

- 图片架构 = Champion 最终版
- 未来任何改动 (开发者也一样) 必须走: 冻结 benchmark + 独立复核 + 回滚方案 + 人工签字
- opt-out flag 保留 (向后兼容):
  - `--no-auto-llm-semantic-filter` (Stage 3.7 opt-out)
  - `MINGLUE_LLM_TAKEOVER=off` (语义门不让位)
  - `MINGLUE_G5_DISABLED_WHEN_LLM=off` (G5 恢复)

## 冻结的东西 (Champion · 不再变)

- 候选生成 · 3 detector (filler-global-pause + self-correction + transient-events 但 cough disable)
- autocut_gate 4 门结构性门 (speaker_role / source_track / G6_duration / review_budget)
- Stage 3.7 · LLM 语义 filter (唯一 "该不该剪" 判决)
- Stage 5 · EDL 只从 llm_verdicts KEEP_CUT
- Stage 6.7 · Optuna 参数级
- Stage 6.5 · NISQA benchmark
- Stage 6.10 · re-render 用 LLM ∩ Optuna 交集

## 让位的东西 (diagnostic only · 不决定 EDL)

- autocut_gate G3_no_preserve / G5_history / G7_session_feedback (never_cut 保留 hard override) / G7_protection
- cut-verify 前 4 项 verdict 决定 EDL (改为 side-write params)
- Check 5 NISQA gate (关 · 只做 benchmark)

## 保留但降级 (不删代码 · 未来若 LLM 挂可回退)

- cut-verify 4 项 check 保留 · 生成 recommended_params 供参考
- Optuna warm_start_by_kind · mentor gold + YouTube learning
- case_embedding · sidecar (未接 G8 gate · 27 case FAISS 已 build)

## 未来 (不属于本冻结版)

- audit_verdicts.template.json · 每期人审填 → Stage 6.9 二轮 Optuna
- case_embedding 接 G8 gate (当前 sidecar)
- pyannote speaker_turnover_guard (代码已加 · env 未落地)
- WhisperX 词级 forced alignment (可选升级)

## 上台一句话

> "5 门语义规则让位 · 4 门结构性门保留 · LLM 唯一决定候选 · Optuna+NISQA 只做参数优化 · EP05 端到端真跑通 · 8 stage 全绿 · 从 rules 老 pipeline 33 candidates 33 cuts · 到 LLM-first 2 candidates 1 cut · 规则不死 · 让位而已"
