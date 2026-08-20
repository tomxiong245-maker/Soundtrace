# Plan 防丢失审计报告

**审计时间**：2026-08-18
**审计范围**：`/Users/renting/Desktop/minglue/剪辑项目/`
**审计目标**：为 6→6 skill 合并盘出**所有**可能被漏掉的历史资产
**审计执行**：Plan agent（Read-only · Explore 广度 max thorough）

---

## 关键发现

1. **`skills/feedback-first-retrieval/` 已不存在**（另一窗口 2026-08-18 合并进 `skills/feedback-engine/` · v220.merged）
2. **CLAUDE.md 实际到 §20**（多出 §19 相邻词保护 + §20 session_feedback 单一 SOT）
3. **4 项仍开放 kind**：C-26 distiller_after_review / C-29 historical_case_note_reject / C-41 "一些" 白名单未落地 / C-42 c034_cut_too_much 内容词 chain 边界未落地
4. **§4 §5 半落地**（不上传 / 不 curl|sh）—— 无系统级 tool 拦截，只 Preflight 人肉版
5. **legacy alias 必须保**：`feedback_first_retrieval` / `user_feedback_analyzer`
6. **2 个 orphan orchestrator .py**：G-15 `generate_comprehensive_cut.py` / G-19 `orchestrator.py`
7. **verify.sh 项目内不存在**（CLAUDE.md §15 承诺的第 18 层扫描器还没落地）
8. **EP04-DELIVERY-20260817-1427 未跑 `write_delivery_report`**（目录无 DELIVERY_REPORT.md · 单点通过流程未沉淀）

---

## A. 每个现存 skill 的"实际动作清单"

### A1. `skills/feedback-engine/`（合并版 · v220.merged · 2026-08-18 · 另一窗口做）

| 编号 | 内容 | 状态 | 归属新 skill |
|---|---|---|---|
| A1-1 | 决策前 `retrieve_before_decision(candidate, decision_type, episode_id, max_return=5)` 读 current.session_feedback.jsonl + labels_lake.feedback[] · 按 verdict priority + match score + timestamp 排序返回 top-N | active · 硬规则 §18 | **user-feedback-loop → feedback-engine** |
| A1-2 | 决策后 `analyze_feedback(candidate, verdict, note, episode_id)` 四步链：Parse → TOOL_APPLY(0.9) → DOC_REFERENCE(0.7) → SESSION_FEEDBACK_PATCH(0.5, 最后手段) | active | feedback-engine |
| A1-3 | Verdict 优先级表：never_cut/forbidden(10) > needs_extension(8) > cut_scope_too_wide(8) > policy/pause(4-6) > accept(3) | active | feedback-engine |
| A1-4 | CLI 双入口：`feedback_engine.py {retrieve\|analyze}` | active | feedback-engine |
| A1-5 | Legacy alias：`feedback_first_retrieval` / `user_feedback_analyzer` 保持 import 兼容 | active | feedback-engine（保留） |
| A1-6 | Precondition：`current.session_feedback.jsonl` 存在（§20 单一 SOT） | active | feedback-engine |
| A1-7 | Postcondition：SESSION_FEEDBACK_PATCH 需 escalation flag，不能作默认路径 | active | feedback-engine |
| A1-8 | 边界：绝不改代码、绝不改 tools.json、绝不改 labels_lake；决策必带 reasoning_chain | active | feedback-engine |
| A1-9 | 历史 bug 证据：v215 tool 不查反馈直接用 EDL 窄边界 C007/C034/C039 剪不干净；v218-219 每次反馈都直接 append 导致规则库膨胀 | 已修 | feedback-engine（保留 flow_boundary） |
| A1-10 | 依赖文档：YouTube学习总结.md / Preflight-checklist.md / mentor-briefing.md（供 DOC_REFERENCE 步骤检索） | active | feedback-engine + learning-and-experience |

### A2. `skills/podcast-editing-orchestrator/`（已 deprecated · 拆到 s1+s2+s3）

| 编号 | 内容 | 归属新 skill |
|---|---|---|
| A2-1 | 入口一条命令 `scripts/run_end_to_end.py --episode-id --from-raw-wav --tracks-for-automix --out-dir` | s1（入口）+ s2 + s3 |
| A2-2 | Pipeline 11 步（**顺序不可打乱**）：denoise / ASR / 候选 / 3.1 auto_speaker_role / 3.2 spacy_semantic / 3.3 session_feedback lookup / 3.4 speaker_role_filter / 3.5 MFA / experience_lookup / autocut_gate / EDL+automix | 分片：1-3.1-3.4 → s2；3.3 → feedback-engine；3.5 MFA → s2；experience_lookup → s5；EDL+automix → s3 |
| A2-3 | Precondition：N 轨对齐 mono WAV + 词级 canonical 转写 + 手工 plan.json | s1 |
| A2-4 | Postcondition：run 目录内产出候选/审核包/双 EDL/双渲染；不改 Champion 与前端 | 全部（跨 skill 边界） |
| A2-5 | 每期节目上线前 `speaker_maps/<EP0X>.speaker_map.json`（若不建 Stage 3.1 自动跑 auto_speaker_role.py） | s1 |
| A2-6 | 每次用户 chat 反馈必须主动 append `session_feedback/<EP0X>.session_feedback.jsonl` | feedback-engine |
| A2-7 | G7 hard reject：候选命中 `previous_user_feedback[].verdict == "never_cut"` 自动不进 auto_cut | s2 + feedback-engine |
| A2-8 | 禁止自由发挥；候选生成 / boundary 精修 / gate / mix / clip 生成必用系统 tool | s6 |
| A2-9 | `autocut_policy` 白名单只允许 filler_hesitation / immediate_repetition / global_long_pause / self_correction | s2 |
| A2-10 | 输出永远是 `machine_assisted_draft`，不能标 human_approved | s3 |
| A2-11 | 不写 `human_decisions.json`（只有真人前端能写） | s3 |
| A2-12 | 每次跑新一期新时间戳，不覆盖历史 run | s1 |
| A2-13 | 输出目录约定（run_identity.json / analysis/ / all_candidates.json / mfa_boundaries.json / experience_context.json / autocut_gate/ / machine_assisted_draft.edl.json / render/ / audit_report.md / regate_diff.json） | s1 + 全部 |
| A2-14 | 历史违反证据：2026-08-18 20-pack 事件（agent 绕过 pipeline，自写候选扫描 → 全部 reject） | s2 + s6 |
| A2-15 | A/B clip 硬规则：必须从 automix_v1.py 输出的 automix.wav/mp3 切；禁止 ffmpeg amix 现场叠加 | s3 |
| A2-16 | related_tools 列表：build_priority_review_page / build_semantic_transcript / build_filler_global_pause_candidates / create_aligned_ab_previews / approve_review_candidates / render_approved_edl / assemble_program / finish_approved_project / serve_review_ui / analyze_transition_qc / snap_candidate_boundaries / predict_cut_artifact / review_event_routes | 分散到 s2 + s3 |
| A2-17 | 关联的 Challenger：orchestrator-e2e-v1 / experience-ingestion-v1 / crosstalk-candidate-v1 / transient-events-v1 / self-correction-v1 | s6（登记 challenger→skill 映射） |
| A2-18 | 已知局限：self-correction v1 用"句间前缀匹配"粒度太粗（漏检）；primary/bleed/ambiguous 是能量启发式；阈值建议 <10 样本时 INSUFFICIENT_DATA | s2（记入 backlog） |

### A3-A7. 其他 4 份 deprecated + 顶层

见完整 Plan 报告（已在 workflow 归档 · 详细内容太长这里省略）。关键归属：

- **A3 candidate-family-integration** → s2（cough_like 只 mute · self_correction 规范化 · mixed-14 3/3 误报证据）
- **A4 label-learning-driver** → s5（driver.py L1-20/L79-86/L611-617/L794-801/L951/972/L1108-1114 prohibited scope 段行号 · `LABEL-LEARNING-v3-20260816` 案例集 65 records）
- **A5 editing-experience-distiller** → s5（owner=challenger:experience-ingestion-v1 修正为 champion · 3 份 preferences 快照 · 11 条 P-XX 规则）
- **A6 integration-governance** → s6（两道门叙述 · OWNER_ATTESTED_INTEGRATE 6 类 evidence label · registry 硬约束）
- **A7 顶层 SKILL.md** → s1（5 类输入信号 · 5 条红线 · 4 步冻结方案 · EP04 v12 双审证据）

---

## B. CLAUDE.md §1-§20 边界当前拦截点（20 条 · 每条唯一 owner）

| § | 边界原文摘要 | 当前拦截点 | 归属新 skill |
|---|---|---|---|
| §1 | 原始 WAV / Mentor / Champion / 已哈希产物只读 | AUDIO-CLEANUP-20260814/20260817 README + delivery_orchestrator start SHA 校验 + integration_governance registry | **s6**（s1 也守望） |
| §2 | 语义删剪必须真人 or autocut_policy | policy_promotion.py (NOT_APPROVED) + editing_policy.guards-v1.json + integration-governance flow_boundary | **s6 + s3** |
| §3 | EDL 用整数 sample · 批准区间同步全轨 | make_edl_ab_clips.py + delivery_orchestrator + snap_candidate_boundaries + candidate_family_adapter cough_like scope | **s2 + s3** |
| §4 | 公司音频/转写/候选不外传 | ⚠️ **半落地** · 无系统级 tool 拦截，只 CLAUDE.md 声明 + flow_boundary "绝不联网" 语句 | **s6** |
| §5 | 不 curl|sh / 不透明 inference.sh / 不覆盖系统 Python / 不改全局 Skill | ⚠️ **半落地** · Preflight §1-§5 venv 隔离 + 版本检查，人肉版 | **s6** |
| §6 | 外部工具先审 URL/版本/SHA/依赖/遥测/数据流 | `main/tools/audits/` + tools.json runtime_dependencies + verify.sh 检查 | **s6** |
| §6.6 | 新工具审计门 | 同上 | **s6** |
| §6.9 | 长停顿跨轨静默 | v207 LG48/51/56 事件（当前**未完全闭环**） | **s2** |
| §7 | Challenger 晋升要冻结 benchmark + 独立复核 + 回滚 | policy_promotion.py 独立门 + benchmark/editing-e2e-v1/ 契约 | **s6** |
| §8 | 候选边界精修必须 MFA · OOV 走人审 | run_end_to_end.py Stage 3.5 `mfa_align_and_extract_boundaries` + mfa-alignment-v1 Challenger | **s2** |
| §9 | A/B clip 必须先跑 automix_v1 完整并轨 | make_edl_ab_clips.py + automix_v1.py + automix_adapter.py · 用户 4 次强调 | **s3** |
| §10 | 每次人审 save 后触发 online 学习闭环 | `/api/save` hook + refresh_label_learning_snapshot.py + refresh_lake_and_regate.py | **s5**（feedback-engine 触发） |
| §11 | 禁自由发挥 · 候选/边界/gate/mix/clip 必用系统工具 | Preflight §14 + verify.sh 12 层 tools.json full_path 检查 + 13 层 5 skills flow_boundary | **s6** |
| §12 | speaker_role_gate · 主持人 backchannel 不进候选池 | run_end_to_end.py Stage 3.4 stage_speaker_role_filter + auto_speaker_role.py + speaker_maps/*.speaker_map.json | **s1 + s2** |
| §13 | source_track_gate · cough_like 只 mute 不全轨 cut | candidate_family_adapter.py + candidate-family-integration flow_boundary + integration_governance registry | **s2 + s3** |
| §14 | 用户反馈必须落地 session_feedback + G7 消费 | run_end_to_end.py Stage 3.3 stage_feedback_lookup + feedback_engine.retrieve_before_decision + apply_autocut_gate G7 | **feedback-engine + s2** |
| §15 | 装了的开源包必须优先用 | verify.sh 第 18 层扫描 installed vs used（**verify.sh 项目内不存在**） | **s6** |
| §16 | 剪口拼接必须高级方法 | generate_ab_clip_learning_driven.py + session_feedback splice_must_be_pydub_or_ffmpeg_crossfade + YouTube §2/§5 学习总结 | **s3** |
| §17 | librosa onset 保护 | generate_ab_clip_learning_driven.py::safe_bounds + session_feedback cut_boundary_from_librosa_onset | **s3**（s2 候选层也拦） |
| §18 | Feedback-First Retrieval | skills/feedback-engine/ + feedback_engine.py（retrieve + analyze 双 mode） | **feedback-engine** |
| §19 | 相邻词保护 · edge_extend 不吃 prev/next word | generate_ab_clip_learning_driven.py::safe_bounds · c007/c034 相关反馈 | **s3**（s2 候选层也拦） |
| §20 | session_feedback 单一 SOT · A/B clip 单一目录 | current.session_feedback.jsonl + feedback_engine.retrieve 只读它 + main/runs/*/current_audit_clips/ | **feedback-engine + s3** |

---

## C. session_feedback jsonl 全部 42 kind 枚举 · 归属新 skill

（完整表见 workflow 归档 · 关键仍开放项在下方）

### 仍开放的 kind（4 项 · 必须在合并后 skill 明标）

| kind | verdict | 事件 | 归属 | 状态 |
|---|---|---|---|---|
| **C-26** `distiller_after_review` | policy | Preflight §14 · Agent-SOP §9 · 会话开始必读 preferences_for_agent.md · 用户新审核后跑 distill_preferences.py | s5 | ⚠️ **无自动拦截 · SOP 违规判据** |
| **C-29** `historical_case_note_reject` | never_cut | C026 "剪辑声音明显小了" / C028 "剪辑痕迹很重" · OPT-023/OPT-017 | s3 | ⚠️ **partial**（60ms→120ms 已改，mentor 复听未完成） |
| **C-41** `never_cut_yixie` | never_cut | C039 "一些" chain=1 内容词量词不是口癖 · 永远不进候选池 | s2 | ⚠️ **未闭环**（"一些" 白名单实施证据未见） |
| **C-42** `c034_cut_too_much` | cut_scope_too_wide | C034 "我们" chain=2 剪 543ms 太多 · 内容词 chain 边界规则 | s2 + s3 | ⚠️ **未闭环**（action_taken 规则未见实施证据） |

### 已闭环的关键 kind（38 项）

见 workflow 归档 · 全部有对应 tool / skill / rule 拦截。

---

## D. 待优化清单（OPT-001 至 OPT-030）· 归属新 skill

（仍开放项）

- **OPT-001** 审核效率（连续审核带 / 候选自动定位）→ s3
- **OPT-002** 无候选区随机抽查 → s3
- **OPT-003** 风险分级（数字/专名/否定/结论/重叠/短回应强制高风险）→ s2
- **OPT-004** ASR 热词独立实验臂 → s2 backlog
- **OPT-005** speaker profile + CAM++ 聚类映射 → s2 backlog
- **OPT-008** tool 注册表契约 / 调用器 / 重试 / 错误分级 → s6
- **OPT-009** 产品：一键创建本期/打开审核/恢复渲染/归档 → s3
- **OPT-010** GBDT 排序器（监督学习）→ s5 backlog
- **OPT-011** Loop：issue/人工闸门/artifact/晋升回滚 → s6 backlog
- **OPT-012** 输入类型：单轨/三人/采访/讲座新路由 → s1
- **OPT-014** 真实设备 clock drift 复验 → s1
- **OPT-017** 剪辑痕迹 rendering gate（partial · mentor 复听验证未完成）→ s3 ⚠️ open
- **OPT-018** 拆分 backchannel vs topic_connective 子字典 → s2
- **OPT-022** 审核前端保存路径可见性 → s3 ⚠️ open
- **OPT-024** stratum unanimous propagation 有效性 → s2 partial
- **OPT-025** 跨 episode 经验反馈到候选打分（case_memory 已接 · 排序未闭环）→ s5 partial
- **OPT-026** 每次改动同步四处文档 sync-check FAIL 不阻断 orchestrator → s6
- **OPT-030** 未接的三家族（crosstalk/semantic_duplicate/off_topic）→ s2

### 已知 gap（CURRENT_DELIVERY_FACTS + mentor-briefing）

- **D-gap-1** LABEL-LEARNING-DRIVER-v5 backtest = INSUFFICIENT_DATA_FOR_CROSS_EPISODE_GENERALIZATION（2 期 / 1 审核人 / 6-65 事件级音频身份 / 0-65 source bundle 身份） → s5 open
- **D-gap-2** autocut_policy = NOT_APPROVED · POLICY-PROMOTION-v1-20260816 4 项 blocker（独立 benchmark / 独立复核 / 回滚演练 / 明确签署低风险删剪范围）→ s6
- **D-gap-3 / 4** pyannote skeleton 未装 · speaker-diarization-v1 SKELETON_ONLY → s2 backlog
- **D-gap-8** tool-orchestrator-v2 22 项契约测试全过 · 未晋升 · 主流程仍 v1 → s6
- **D-gap-9** AUDIO-CLEANUP-20260817 副作用 · CURRENT_DELIVERY_FACTS mp3 relpath 未同步（历史证据保留）→ s1 + s3
- **D-gap-10** Preflight §12 遗留坑（Python 3.13 + audioop-lts + faster-whisper venv + ffmpeg 绝对路径 + huggingface 权重缓存 + 磁盘 15G 阈值 + 音乐 SHA + v20 上游依赖 + sync check）—— 只 preflight.sh 人肉版 → s1

---

## E-H 摘要（完整表见 workflow 归档）

- **E** SCORECARD / audit_report 遗留：EP04-AUTO-VERIFY-20260817-2200 有 20 个平行版本目录违反 §20 · 5 个 scorecard 质量门 NOT_MEASURED · EP04-DELIVERY-20260817-1427 未跑 write_delivery_report
- **F** 26 个 challenger status：11 个主线在用（Champion 级别）· 11 个 Challenger 未晋升 · 5 个状态不明
- **G** 33 个 orchestrator .py：2 个 orphan（generate_comprehensive_cut.py / orchestrator.py）· legacy alias 保留（feedback_first_retrieval.py / user_feedback_analyzer.py）
- **H** 顶层其他 md：README.md / AGENTS.md / PIPELINE_STEPS_v20.8.md / 25 份统筹全局/*.md / 5 份 benchmark/*.md · 每份归属已确定

---

## 核心防丢失清单（14 项 · 用户明说不能丢）

1. **CLAUDE.md §8-§20 全部 13 条硬规则** — 每条已由新 skill 承接（见 B 节）；§4/§5 补落地为 s6 backlog
2. **current.session_feedback.jsonl 42 条 kind** — 每条能被 feedback-engine 命中；4 项 open 已明标（C-26/29/41/42）
3. **11 stage pipeline 顺序不可打乱** — 顺序拆到 s1→s2→s3 · postcondition 契约保留
4. **OPT-017 / OPT-022 / OPT-024** — 三项 open backlog 继承到 s2/s3
5. **26 challenger 里 11 主线在用** — s6 registry.json 需明标 main_pipeline_dependency
6. **33 orchestrator .py 里 2 orphan** — G-15 / G-19 已明标 · 未处理
7. **feedback-engine legacy alias** — feedback_first_retrieval / user_feedback_analyzer 保留
8. **preferences-20260815-1330 / -label-loop-v1/v2 三份历史快照** — s5 保留触发点 · Preflight §14 SOP
9. **integration_governance registry** — main/knowledge/integration_governance/owner_attested_mainline.v1.json · s6 迁移承接
10. **AUDIO-CLEANUP-20260817 副作用** — mp3 relpath 未同步 · sync-check 挂时不得自行修补
11. **RELEASE-SPEC-FROM-EP03-20260817-1204** — EP03 发布规格 · s3 承接
12. **tool-orchestrator-v2 skeleton** — 22 项契约测试通过未晋升 · s6 保留 SKELETON 状态
13. **speaker_maps/<EP>.speaker_map.json 声明契约** — s1 承接每期上线前 P2 必做
14. **三条进化路径**（33 labels_lake + 65 case_memory + 42 session_feedback = 140 项历史知识）— 每条来源在合并 skill 里可溯源
