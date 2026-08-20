---
name: skills-index
description: minglue 剪辑项目所有 skill 的顶层导航 + CLAUDE.md 硬边界归属映射。**不再直接执行任何决策**——任何具体任务都必须路由到下方 7 个 active skill 之一。触发关键词：skills 目录、skill 全景、找 skill、去哪个 skill、CLAUDE.md § 归属、职能索引。
status: index
owner: champion
entry_tool: null
related_tools: []
preconditions:
  - "剪辑项目根 /Users/renting/Desktop/minglue/剪辑项目/ 已挂载"
  - "已读 CLAUDE.md 与 统筹全局/当前项目上下文.md"
postconditions:
  - "任务已被路由到具体的下游 skill，或明标'不激活 · 停下问人'"
---

# skills index · 顶层导航

> **本文件是索引，不是执行 skill**。所有具体决策链都在下方 7 个 active skill 里，按"用户视角的独立决策链"合并（2026-08-18）。原顶层 `audio-clips-orchestration`（占位声明版）已 deprecated · 其职能拆到 `episode-triage-and-plan` + `input-triage` 收编到 s1。

## 1. 为什么会有这份文件

原顶层 `SKILL.md`（audio-clips-orchestration）自认"占位声明版" · `related_tools=[]` · 只提名 2 个下游 skill；14 个 pipeline 决策点里只覆盖 1 个。2026-08-18 用户裁定：把碎片化的 15 个 skill 合并按"用户视角的一条不可打断决策链"重组为 6 大 skill；另加 1 个另一窗口做的 `feedback-engine`（反馈闭环）合计 7 个 active。

## 2. 现役 skill（7 个 active）

| # | skill 目录 | 入口 tool | 职能一句话 | 硬化 CLAUDE.md |
|---|---|---|---|---|
| L0 | [`skills/episode-triage-and-plan/`](skills/episode-triage-and-plan/SKILL.md) | `inspect_audio` | 拿到音频 → 判 episode_type + 建 run + 冻结 plan.json + 声明 speaker_map | §1, §12, FR-01/02/03 |
| L1 | [`skills/feedback-engine/`](skills/feedback-engine/SKILL.md) | `feedback_engine` | 决策前 retrieve、决策后 analyze；用户反馈处理器（另一窗口 v220.merged） | §14, §18, §20 |
| L2 | [`skills/candidate-generation-and-gate/`](skills/candidate-generation-and-gate/SKILL.md) | `build_candidate_family_bundle` | 候选生成 → 长停顿跨轨 gate → MFA + neighbor + onset 边界精修 → autocut 7 门（一条不可打断链） | §6.9, §8, §11, §12, §13, §14, §17, §18, §20, FR-04/05 |
| L2 | [`skills/cut-verify/`](skills/cut-verify/SKILL.md) | `verify_cut_plan` | **剪口干净度 4 项 check**（幻觉 · 静音位置 · 节奏 · 拼接路由）· 用现装开源工具（faster-whisper word.probability + pydub.silence + cut_parameters + policy）· 输 verified_edl.json 侧车 · 2026-08-19 EP04 首跑 4/7 与 mentor gold 一致 | §8, §11, §15, §16, §17, §22 |
| L2 | [`skills/audition-and-delivery/`](skills/audition-and-delivery/SKILL.md) | `automix_render_speech` | automix 全片 → A/B clip（单一版本）→ 人审 → render → transition/loudness QC → DELIVERY_MANIFEST | §9, §11, §13, §15, §16, §17, §20, F06, FR-06/07 |
| L3 | [`skills/learning-and-experience/`](skills/learning-and-experience/SKILL.md) | `label_learning_driver` | 三合一学习层：单期偏好学习 + save 后 online refresh + 多期案例蒸馏 | §10, F08 §120-127 |
| L4 | [`skills/governance-and-tool-registry/`](skills/governance-and-tool-registry/SKILL.md) | `validate_integration_governance` | 新能力接入门 + tool 登记 + audit + installed-tools-first 扫描 | §6.6, §11, §15, §18 |

**分层含义**：
- L0 = 前置门（不做完 pipeline 不启动）
- L1 = 每次决策前后必调（横切）
- L2 = 主 pipeline 决策链（候选→交付）
- L3 = 学习闭环（读历史 · 不改当期）
- L4 = 治理层（不管交付语义）

## 3. Deprecated skill（6 个 · 保留作历史证据）

| deprecated 目录 | 迁到 | 保留原因（历史证据） |
|---|---|---|
| `skills/podcast-editing-orchestrator/` | s1 + s2 + s3 | 20-pack 事件、v207→v217 迭代、EP04-DELIVERY 双审流程 |
| `skills/candidate-family-integration/` | s2 | §13 cough_like 只 mute、mixed-14 3/3 误报证据 |
| `skills/label-learning-driver/` | s5 | driver.py 内 prohibited scope 行号、v3 案例集提名过程 |
| `skills/editing-experience-distiller/` | s5 | 3 份 preferences 快照、11 条 P-XX 规则迭代路径 |
| `skills/integration-governance/` | s6 | 两道门叙述、6 类 evidence label、attestation 硬约束 |
| `SKILL.md`（原 audio-clips-orchestration 版） | 本文件（重写为 index）| 5 类输入信号、5 条红线、4 步冻结方案 |

**规则**：deprecated skill 目录**不删**、frontmatter `status: deprecated` + 顶部 banner 指向新 skill。新任务禁止再从 deprecated 目录进入。

## 4. CLAUDE.md §1-§20 硬边界 → skill 归属

（对照 Plan 防丢失审计 · 每条边界都有唯一 owner skill；§4/§5 半落地必须补）

| § | 边界摘要 | 主 owner skill | 备注 |
|---|---|---|---|
| §1 | 原始 WAV / Mentor / Champion / 已哈希产物只读 | **s1** | s6 也守望 |
| §2 | 语义删剪必须真人 or autocut_policy 授权 | **s3** | s6 拒改 policy |
| §3 | EDL 用整数 sample、批准区间同步全轨 | **s2 + s3** | s2 出 EDL、s3 消费 |
| §4 | 公司音频/转写/候选不外传（本地推理） | **s6** | ⚠️ **半落地** · 无系统级 tool 拦截 |
| §5 | 不 curl|sh / 不覆盖系统 Python / 不改全局 Skill | **s6** | ⚠️ **半落地** · 只 Preflight 人肉版 |
| §6 | 外部工具先审 URL/版本/SHA/依赖/遥测/数据流 | **s6** | audit 覆盖率 1/48 |
| §6.6 | 新工具审计门（登记 tools.json + audit） | **s6** | pre_flight §7 门 4/5 |
| §6.9 | 长停顿跨轨静默 | **s2** | v207 LG48/51/56 事件 |
| §7 | Challenger 晋升要冻结 benchmark + 独立复核 + 回滚 | **s6** | 4 项 blocker 未过 |
| §8 | 候选边界精修必须 MFA · OOV 走人审 | **s2** | boundary_refinement 段 |
| §9 | A/B clip 必须先跑 automix 全片 | **s3** | 用户明说 4 次 |
| §10 | 每次 save 后触发 online 学习闭环 | **s5** | evolution path 1 |
| §11 | 禁自由发挥 · 做过的都是工具 | **s6** | 20-pack 事件 |
| §12 | speaker_role_gate · 主持人 backchannel 不进候选 | **s1 + s2** | s1 声明、s2 消费 |
| §13 | source_track_gate · cough_like 只 mute 不全轨 cut | **s2** | mixed-14 3/3 |
| §14 | 备注记忆 · session_feedback append + G7 消费 | **s1-feedback + s2** | s2 gate 消费 |
| §15 | 装了的开源包必须优先用 | **s6** | verify.sh 未落地 |
| §16 | 剪口拼接必须高级方法（pydub / acrossfade / room tone） | **s3** | v208 硬拼事件 |
| §17 | librosa onset 保护 · 保留词不被 crossfade 吞 | **s2 + s3** | s2 候选层、s3 clip 层 |
| §18 | Feedback-First Retrieval 决策前必调 | **s1-feedback** | v215/v216 违反 |
| §19 | 相邻词保护 · edge_extend 不吃 prev/next word | **s2 + s3 + cut-verify** | c007/c034 相邻词事件 · cut-verify Check 4 P2 吃邻词→REMOVE_FROM_EDL |
| §20 | session_feedback 单一 SOT · A/B clip 单一目录 | **s1-feedback + s3** | current.session_feedback.jsonl |
| §22 | 剪口干净度 4 项 check + filler ASR-word 扩展 | **cut-verify** | 2026-08-19 新增 · entry=verify_cut_plan · EP04 7 候选 4/7 与 mentor gold 一致 · session_feedback rule 66 |

**⚠️ 未闭环项**（Plan 报告 C 节仍开放 kind）：
- **C-26** `distiller_after_review` → s5（SOP 违规判据 · 无自动拦截）
- **C-29 / OPT-017** 剪辑痕迹 mentor 复听 → s3
- **C-41** `never_cut_yixie`（"一些" 白名单未落地）→ s2
- **C-42** `c034_cut_too_much`（内容词 chain 边界规则未落地）→ s2

## 4a. CLAUDE.md §21 · PARAMETER vs PREFERENCE 分家（2026-08-18 新增）

用户 2026-08-18 明确把"知识"拆成两块：**PARAMETER 决定"怎么剪"**、**PREFERENCE 决定"剪哪些"**。两块彻底分家、消费方不同、变更频率不同。

| 维度 | PARAMETER（怎么剪） | PREFERENCE（剪哪些） |
|---|---|---|
| 存放位置 | 单一参数文件 | session_feedback 单一 SOT（64 条 · v2 schema） |
| 关键内容 | crossfade 默认 50ms · long_pause 200ms · gap_before target [120,300]ms · gap_after target [120,450]ms · boundary_offset 深植 300ms min 76ms · RMS soft 15dB / hard 25dB · asymmetric_head_pad 按 class（filler 头 210 尾 110 / pause 头 100 尾 120 / boundary 头 180 尾 300）· **cut_verify_thresholds（2026-08-19 落地 · 值以 `skills/cut-verify/2026-08-19-0040-cut-verify-landing-and-EP04-delivery.md` 为唯一权威 · 数值本身镜像见 cut_parameters.json + cut-verify SKILL.md §7）** | semantic_boundary_primary_target（**主战场 71%**）· 修辞重复 never_cut · pure_filler_isolated 硬拒绝 · self_correction 全或无 duration ≥ 4000ms · immediate_rep_in_speech_allowed（11/12 mentor 不在静音）· host-silent gate / cross_track_speaking 重定义 · **filler_cut_use_full_asr_word_range_plus_50ms_xfade（session_feedback rule 66 · 2026-08-19 C007/C044 accept · 权威见落地报告）** |
| 主消费者 | **s2 边界精修段**（asymmetric_head_pad 决定候选头尾扩多少）+ **s3**（crossfade / gap / boundary_offset 决定 A/B clip 与成片拼接） | **s2 候选家族筛选 + gate G7** · 通过 feedback-engine.retrieve_before_decision 读取 |
| 变更频率 | 每期 constant（顿悟 2：crossfade per-episode 常数 · 不动态调制） | append-only · 每次人审后追加 |
| 硬边界 | 变更走 s6 governance · 每次改动记 SHA | 单一 SOT（§20）· §18 决策前必调查 |

### 三个关键顿悟（本轮 workflow 挖出来 · 已归属到具体 skill）

1. **mentor 剪 71% 是 semantic_boundary · pure_filler=0 · rhetorical=0** —— 系统"砍 filler"的直觉方向**反了**。s2 候选家族的重心应从 filler_hesitation 转向 semantic_boundary。**归属 s2**。
2. **crossfade per-episode constant · 不因音频局部特征调制** —— s3 A/B clip 与成片拼接的 crossfade 参数在 plan.json 冻结时读一次，不在候选级别动态改。**归属 s3**。
3. **cross_track_speaking 目前定义 59/59 假阳 · 需 speaker_map 差异化重写** —— s2 的跨轨长停顿 gate 目前用能量启发式，全期 59 个全假阳。需要改用 s1 已声明的 speaker_map 逐轨判定（host backchannel vs guest speaking 差异化）。**归属 s2**。

### 学习流选择器（本轮新增 · 元 skill 层）

- **决策树**：什么时候用参数学习流（gold-EDL 特征提取）/ 什么时候用偏好学习流（session_feedback）/ 什么时候用案例蒸馏
- **补丁滥用防线**：任何反馈**不许**直接 append session_feedback（那是最后一步），必须先经 feedback-engine 四步链（Parse → 优先用工具 → 借鉴知识沉淀 → 最后才 append）
- **归属 s5**（`learning-and-experience`）· 作为案例蒸馏段的决策入口

### 本轮新产物（gold-cut 特征提取 · 2026-08-18）

| 产物 | 归属 skill | 用途 |
|---|---|---|
| `extract_gold_cut_features.py`（新 tool） | **s6 待登记 tools.json** | 从人工 gold EDL 反向提取 PARAMETER + PREFERENCE 特征 |
| `EP04-GOLD-EDL-20260818-1548` 目录（含分轨可靠度分析 + workflow 综合 json） | **s5 案例蒸馏产物** | 分轨可靠度 + 顿悟 1/2/3 的证据 |
| `EP04-COMPREHENSIVE-20260818-1730/current_audit_clips/`（8 段 A/B mp3） | **s3 试听产物**（符合"单一目录"契约） | mentor gold cut A/B 对照 |
| 全部 sync 到 `交付-2026-8-17`（2.7GB） | 与本轮 skill 重构包并列的**上一轮交付** | 归档 |

## 4b. 7 状态状态机跨 skill 接缝（补齐 · 消除接缝散）

旧 `orchestrator.py` 单点掌管的 7 状态状态机拆到 3 个 skill 后，需要一张接缝图讲清每次交接。**任何 skill 不允许在没满足 postcondition 的情况下把控制权交给下一个 skill**。

### 状态转换图

```
[外部输入 · 音频文件到位]
      ↓
┌── s1 episode-triage-and-plan ──────────────────┐
│  RECEIVED                                      │
│    ↓ (输入检查/降噪/时间线/speaker_map 完成)     │
│  INPUT_VALIDATED  ← 旧 CREATED + PLANNED 合并   │
│    ↓ (plan.json 冻结含 rules/preference/PARAMETER 全部 sha) │
│    ↓ (**主导轨已合成 · 2026-08-18 前置** speech.mono.wav) │
│  TIMELINE_READY   ← 旧 PREPARED                │
└────────────── 交接点 1 ─────────────────────────┘
      ↓
┌── s2 candidate-generation-and-gate ────────────┐
│  CANDIDATES_GENERATED                          │
│    ↓ (五族候选 + 长停顿跨轨 gate + MFA/snap/onset 边界精修 + 7 门 gate 判决完成) │
│  GATED_READY_FOR_REVIEW  ← 旧 WAITING_FOR_HUMAN_REVIEW │
└────────────── 交接点 2 ─────────────────────────┘
      ↓
┌── s3 audition-and-delivery ────────────────────┐
│  AB_LISTENING_SENT                             │
│    ↓ (人审 save → 反馈闭环触发 s5 online refresh) │
│  HUMAN_APPROVED  ← 旧 APPROVED                 │
│    ↓ (automix 全片 → render → transition + loudness QC 通过) │
│  DELIVERY_DECISION_RECORDED  ← 旧 FINALIZED    │
│    ↓ (DELIVERY_MANIFEST.approval_chain 含 mentor + project_owner 双审) │
│  PUBLISH_CANDIDATE 或 MACHINE_ASSISTED_DRAFT   │
└────────────── 交接点 3 ─────────────────────────┘
      ↓
┌── s6 governance-and-tool-registry ─────────────┐
│  ARCHIVED  ← 旧 ARCHIVED                       │
│    (integration_governance registry 冻结, run 只读) │
└────────────────────────────────────────────────┘

(横切) s5 learning-and-experience 在每个交接点后异步触发：
   交接点 3 后 · 人审 save 触发 → online refresh + labels_lake 增量 + regate
   多期完成后 · 案例蒸馏（离线批处理）

(横切) feedback-engine 在每次决策前必调（跨所有 s2/s3 内部动作）
```

### 每次交接的 postcondition 必须字段（不满足 = 下一 skill 拒开工）

| 交接点 | 上游 skill | 下游 skill | 必须已存在的字段 |
|---|---|---|---|
| **1**（s1 → s2） | episode-triage-and-plan | candidate-generation-and-gate | `run_identity.json`（schema=run-identity-v1）· `plan.json`（schema=delivery-plan-v1，含 `rules_version+sha` / `preference_profile_id+sha` / **cut_parameters_sha256**（§21 PARAMETER 快照）/ `experience_snapshot_id`）· `input_manifest.json`（`source_access` 字面串以 "raw sources are read only" 结尾）· `speaker_maps/<ep>.speaker_map*.json`（至少一 track `role="host"`）· `01_inspect/inspection.json` · `02_loudness/loudness_raw.json` · `03_sync/sync_report.json` · **`render_prep/speech.mono.wav` + `speech.mono.manifest.json`（2026-08-18 起 · 主导轨前置合成 · 下游候选生成与 A/B clip 都从这条轨切 · safety.edl_mutation=false）** |
| **2**（s2 → s3） | candidate-generation-and-gate | audition-and-delivery | `candidate_source.json`（含 candidate_family_integration 侧车 · self_correction `cut_scope="abandoned_span_only"` · cough_like `cut_scope="source_track_gate_only"`）· `mfa_boundaries.json`（`schema=mfa-boundaries-v1`）· `boundary_snap_summary` 完整 · `autocut_gate/summary.json`（`schema=autocut-gate-v1-run-v1`）· `autocut_gate/verdicts.jsonl` · 若 `autocut_policy=NOT_APPROVED` 则 `auto_cut_eligible_count == 0` · long_pause 候选全部经跨轨静默判定 |
| **3.1**（s3 内 · AB_LISTENING_SENT → HUMAN_APPROVED） | audition-and-delivery | 同 skill（人审 save 后触发 s5） | `automix_full/speech.mono.wav` + 其 sha256 一致 · `current_audit_clips/`（唯一目录 · 无 v20X_* 累积）· `current_audit_clips/*.manifest.json` 全部 `tools_used_all=true` · 人审 `human_decisions.json` 已保存 → 反馈闭环 hook 已调 s5 |
| **3.2**（s3 内 · HUMAN_APPROVED → DELIVERY_DECISION_RECORDED） | audition-and-delivery | 同 skill | render mp3/wav 已产出 · `qc/loudness_report.json.verdict == "PASS_ALL_TARGETS"` · `qc/transition_qc.json` 存在 · `DELIVERY_MANIFEST.approval_chain[]` 已含 role=mentor + role=project_owner 两条 verdict=approved |
| **4**（s3 → s6） | audition-and-delivery | governance-and-tool-registry | delivery run 内已冻结 `integration_governance.json`（source_sha256 + frozen_sha256）· state.json 推进到 `DELIVERY_DECISION_RECORDED` · `write_delivery_report` 已产出 `DELIVERY_REPORT.md` |

### 横切 skill 的介入点

- **feedback-engine**：s2 每个候选做 gate 判决前 + s3 每个候选做拼接决策前，必须调 `retrieve_before_decision`；人审 save 后调 `analyze_feedback` 处理新反馈
- **learning-and-experience**：交接点 3.1 后 `/api/save` hook 自动触发 online refresh（s5 段 B）；多期累积后另开批处理跑案例蒸馏（s5 段 C）

### 违反 postcondition = fail closed

任何下游 skill 检查上游 postcondition 缺失 → 状态机**停在上游状态**、写 `triage_notes.md` 或 `gate_report.md` 说明缺什么 → 不允许"半推进"。这是把旧 orchestrator.py 单点门禁**下沉到每个交接点**的关键契约。

## 5. Pipeline 决策点 · skill 归属全表

（21 处决策点 · 每处唯一归属 · 消除孤儿）

| 决策点 | 归属 |
|---|---|
| episode_type 判定 | s1 |
| 噪声底路由 | s1 |
| drift_ppm 修正 | s1 |
| speaker_map host 声明 | s1 |
| 候选家族接线（filler / long_pause / repetition / self_correction / cough_like） | s2 |
| long_pause 跨轨静默 | s2 |
| MFA 边界精修 | s2 |
| snap 边界精修 | s2 |
| librosa onset 保护（候选层） | s2 |
| neighbor-word 保护（候选层） | s2 |
| autocut 7 门判决 | s2 |
| autocut_policy=NOT_APPROVED 强制 | s2 + s6 |
| Automix 全片（含双遍 loudnorm） | s3 |
| A/B clip 单一版本 | s3 |
| 拼接方法（pydub crossfade + room tone） | s3 |
| Render 双 EDL 变体 | s3 |
| transition/loudness QC | s3 |
| DELIVERY_MANIFEST 双审 + publish_candidate 判决 | s3 |
| 决策前必调 retrieve | s1-feedback |
| 决策后 analyze + 反馈落地 | s1-feedback |
| 单期偏好学习 shadow prediction | s5 |
| online refresh（save 后 lake 增量 + regate） | s5 |
| 案例蒸馏（多期离线） | s5 |
| 新 tool 登记 + audit | s6 |
| installed-tools-first 扫描 | s6 |

## 6. 保留的历史资产（原顶层 SKILL.md 未丢的内容）

原 SKILL.md 的下面几段**已迁移到具体 skill**，不在本索引重复：

- 第一步"5 类输入信号"（轨道数 / 时长 / 说话人 / 开场话术 / 背景噪声）→ [`skills/episode-triage-and-plan/`](skills/episode-triage-and-plan/SKILL.md) §4.8
- 第二步"当前只支持类型 A：中文对谈 podcast"路由 → s1 §2 + §7
- 第三步 5 条红线 → 每条都有 §1-§20 归属，见上表
- 第四步"冻结本期方案 8 个字段" → s1 §4.6 `plan.json` schema
- 诚实标注三档（已验证事实 / 已决定方向 / 待验证假设） → 每个 skill 末尾必备段落

原顶层 SKILL.md 内保留的完整"占位声明版"备份见 git 历史 `SKILL.md@2026-08-17`（本次 2026-08-18 提交将其重写为 index）。

## 7. 何时激活本文件

- Agent **迷路**了：拿到任务不知道该去哪个 skill → 读本索引找归属
- 用户问 "现在有哪些 skill / 各自做什么 / § 归属"
- 新加 skill 时：加一行到 §2，加决策点到 §5，加边界归属到 §4
- **不激活**：任何具体任务（候选、剪辑、审核、learning、governance）都必须直接进对应 skill，**不要**先跑本文件

## 8. 三档诚实标注

**已验证事实**：
- 剪辑项目 `skills/` 下现存 12 个目录：6 个新 active（episode-triage-and-plan / candidate-generation-and-gate / audition-and-delivery / learning-and-experience / governance-and-tool-registry + feedback-engine）+ 5 个 deprecated + 顶层 SKILL.md 本身
- feedback-engine 由另一个窗口 2026-08-18 完成，属 v220.merged 版
- 6 个新 skill 每份 SKILL.md 都严格实读了 EP03-freshrun-20260810-1730 / EP04-v26-20260815-1650 / EP04-AUTO-VERIFY-20260817-2200 / EP04-DELIVERY-20260817-1427 等真实 run 的 JSON schema

**已决定的方向**：
- Skill 数量从 15 压到 7（含 index）
- deprecated skill 保留内容作历史证据、不删
- 每条 CLAUDE.md §1-§20 有唯一 owner skill；未闭环项明标于 §4
- 顶层 SKILL.md 从执行 skill 降级为纯索引

**待验证假设**：
- 每个新 skill 的 `pre_flight_check` 脚本尚未落地（`scripts/preflight/check_<name>.py` 5 份全部待创建）
- CLAUDE.md `§6.6` / `§6.9` 是设计说明中的编号（子章节），需 grep CLAUDE.md 实际章节号对齐
- `tools.json` 里 7 个新增待登记 tool（`crosstrack_silence_check` / `neighbor_word_guard` / `librosa_onset_guard` / `run_versioning_guard` / `tool_registry_check` / `analyze_feedback_root_cause` / `match_feedback_to_existing_tools`）尚未真正登记
- 顶层 SKILL.md 作为 `skills-index`（不再是可激活的 skill）是本轮设计，harness / orchestrator 对 `status: index` 的兼容性未验证
