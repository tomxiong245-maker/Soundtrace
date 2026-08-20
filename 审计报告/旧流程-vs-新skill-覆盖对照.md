# 旧流程 vs 新 6 skill · 12 环节覆盖对照

**对照日期**：2026-08-18
**对照对象**：用户提供的旧流程图（4 大区块 · 12 环节）vs 新 6 skill 体系
**目的**：确认没有旧流程环节被新体系排斥在外；接入本轮新增（CLAUDE.md §21 参数/偏好分家 · 学习流选择器 · 三顿悟 · gold-EDL 产物）后再核一遍。

---

## 一、旧流程 12 环节清单（按图从上到下、大列 → 外部循环 → 内部循环 → tool 注册表）

| # | 旧环节名称 | 旧位置（图中） |
|---|---|---|
| 1 | 新音频输入 | 中间大列顶部 |
| 2 | 不可修改的安全规则 | 中间大列顶部（并列） |
| 3 | 认知层 · 类型判断 + 路由 | 中间大列 · 顶层 SKILL.md |
| 4 | 统筹 Agent · 7 状态状态机 | 中间大列 · orchestrator.py |
| 5 | 冻结本期执行方案 | 中间大列 · plan.json |
| 6 | 稳定音频 Skill · scripts + rules | 中间大列 · 稳定生产 |
| 7 | 分层人工审核 | 中间大列 · 审核前端 |
| 8 | 最终成片 | 中间大列底部 · 14_final |
| 9 | feedback_bundle 累积 | 中间大列尾部 |
| 10 | 外部知识循环 · 异步 | 右上区块 |
| 11 | 内部经验循环 · 未启 | 右下区块 |
| 12 | Tool 注册表 · v0.1 未真调 | 左侧区块 |

---

## 二、逐条对照

### 环节 1 · 新音频输入

- **归属新 skill**：s1 `episode-triage-and-plan`
- **覆盖状态**：✅ 完全覆盖 · **已增强**
- **变化**：旧流程只是"新音频进来"一个盒子；新 s1 里明确了：拆音频、跑 inspect / measure_loudness / estimate_sync 三个 tool、算噪声底和 drift、判 5 类输入信号（当前只允许"中文对谈"通行、其他停下问人）
- **相关 CLAUDE.md**：§1（原始只读）、§12（speaker_map 必备）、FR-01/02/03

### 环节 2 · 不可修改的安全规则

- **归属新 skill**：跨 s1 / s6
  - s6 `governance-and-tool-registry` 负责规则本体的**变更管控**（哪条规则能改、谁改、留 SHA）
  - s1 `episode-triage-and-plan` 负责**每期开工前引用一遍**（把 rules_version + snapshot_version 冻结进 plan.json）
- **覆盖状态**：✅ 完全覆盖 · **拆得更清**
- **变化**：旧图里"安全规则"是一个静态盒子；新体系里被**升级为跨 skill 的读写协议**（谁能读、谁能改、改动如何登记 audit）
- **相关 CLAUDE.md**：§1-§7、§11、§15、§6.6

### 环节 3 · 认知层 · 类型判断 + 路由

- **归属新 skill**：主要 s1 · 顶层 SKILL.md 只做 index（不做执行）
- **覆盖状态**：✅ 完全覆盖 · **主体从顶层下沉到 s1**
- **变化**：旧图里"认知层"是顶层 SKILL.md（占位声明版）；本轮把类型判断 5 信号、路由决策**主体下沉到 s1**，顶层只剩 skills index（导航 + CLAUDE.md § 归属 + 决策点归属表）
- **相关 CLAUDE.md**：§12、FR-02、FR-03

### 环节 4 · 统筹 Agent · 7 状态状态机

**旧状态机**：`CREATED → PLANNED → PREPARED → WAITING_FOR_HUMAN_REVIEW → APPROVED → FINALIZED → ARCHIVED`

- **归属新 skill**：**分散到 3 个 skill 各自负责一段** —— 这是最需要说清的接缝
- **覆盖状态**：⚠️ **覆盖但接缝散** · **需要在顶层 skills-index 补一张状态机接缝图**（本次 backlog）

| 旧状态 | 新负责方 | 新 skill 内部对应状态（若有） |
|---|---|---|
| CREATED | s1 | `RECEIVED` |
| PLANNED | s1 | `plan.json 冻结完成` |
| PREPARED | s1 → s2 交接 | `INPUT_VALIDATED → TIMELINE_READY`；候选生成中 |
| WAITING_FOR_HUMAN_REVIEW | s2 交给 s3 | s3 `AB_LISTENING_SENT` |
| APPROVED | s3 | `DELIVERY_DECISION_RECORDED`（含 mentor + project_owner 双审） |
| FINALIZED | s3 | render + QC 通过 → 允许标 `publish_candidate` |
| ARCHIVED | s3 + s6 | s3 关闭 delivery run；s6 冻结 integration_governance registry |

- **Gap**：**没有一个 skill 拥有整条状态机的全局视图**。旧 `orchestrator.py` 是单点负责的，拆到 3 个 skill 后**接缝没画清**。
- **补法**（backlog）：顶层 skills-index 加一张"状态机跨 skill 接缝图"，标每次交接的 postcondition 必须字段
- **相关 CLAUDE.md**：无直接边界，是流程的元规则

### 环节 5 · 冻结本期执行方案

- **归属新 skill**：s1
- **覆盖状态**：✅ 完全覆盖 · **字段更细**
- **变化**：旧 plan.json 主要含 `rules_version + snapshot_version`；新 s1 冻结的 plan.json 至少含 `episode_id / run_id / contract_version / rules_version+sha / preference_profile_id+sha / music_template_id+sha / candidate_rules_version + sha / editing_policy_relpath+sha / experience_snapshot_id`（照抄 EP04-v26 实测 schema）
- **PARAMETER 关联**（§21 新增）：plan.json 冻结时**还要读一次 PARAMETER 参数文件**（crossfade / gap / boundary_offset / RMS thresholds / asymmetric_head_pad），把参数快照进本期 run —— **顿悟 2：per-episode constant · 不动态调制**
- **相关 CLAUDE.md**：§21、FR-06

### 环节 6 · 稳定音频 Skill · scripts + rules

- **归属新 skill**：**分家到 s2 + s3**
  - **rules（决定"剪哪些"）→ s2 candidate-generation-and-gate**（filler-global-pause v18 · MFA · autocut gate 规则）
  - **scripts（决定"怎么剪"）→ s3 audition-and-delivery**（automix_v1 · generate_ab_clip · make_edl_ab_clips · transition_qc）
- **覆盖状态**：✅ 完全覆盖 · **按 §21 分家**
- **变化**：旧图里"稳定音频 Skill"是一个大盒子（含 scripts + rules）；本轮按 CLAUDE.md §21 分家为 PARAMETER / PREFERENCE 两块，两块彻底解耦（消费方不同、变更频率不同、硬边界不同）
- **相关 CLAUDE.md**：§8、§11、§13、§14、§16、§17、§18、§19、§20、§21

### 环节 7 · 分层人工审核

- **归属新 skill**：s3 audition-and-delivery
- **覆盖状态**：✅ 完全覆盖 · **无变化 · 前端产物名保持**
- **变化**：审核前端 index.html + 启动审核.command → human_decisions.json 路径**不动**；新 s3 只是把它挂在 postcondition 里明说
- **相关 CLAUDE.md**：§2（语义删剪必须真人）、FR-04

### 环节 8 · 最终成片

- **归属新 skill**：s3 audition-and-delivery
- **覆盖状态**：✅ 完全覆盖 · **已增强**
- **变化**：旧图 `main/runs/<EP>/14_final/master.approved.wav + mp3 + QC` 是一个盒子；新 s3 拆成 5 段产物：
  1. Automix 全片（`speech.mono.wav` + `automix_manifest.json`）
  2. A/B clip 单一目录（`current_audit_clips/` · `comprehensive_cut-v219` manifest）
  3. Render 成片（mp3/wav + `qc/loudness_report.json` + `qc/transition_qc.json`）
  4. Delivery（`DELIVERY_MANIFEST.json` · approval_chain 必须含 mentor + project_owner）
  5. Delivery Report + state.json
- **相关 CLAUDE.md**：§9、§15、§16、§17、§19、§20、F06、FR-06、FR-07

### 环节 9 · feedback_bundle 累积

- **归属新 skill**：**拆成 3 份 · 更细化**
  - **session_feedback**（用户反馈的原始记录 · 64 条 · v2 schema）→ **feedback-engine** 消费读、s2/s3 决策时被查
  - **labels_lake**（accept/reject 决定聚合 · 33 决定）→ **s5 learning-and-experience** 消费
  - **case_store**（案例卡 · 65 records）→ **s5** 案例蒸馏消费
- **覆盖状态**：✅ 完全覆盖 · **旧的 feedback_bundle.json 单一大 json 拆成三份细化协议**
- **变化**：旧图里 feedback_bundle 是一个盒子（原音频/人工决定/最终成片打包）；新体系按用途拆成 3 份不同的读写协议，各自单一 SOT（§20）、各自变更频率、各自消费者
- **相关 CLAUDE.md**：§14、§18、§20

### 环节 10 · 外部知识循环 · 异步

**旧图内容**：视频、官方文档（`从视频学习经验/references/` 10 份 · `端到端学习剪辑/skill/*/references/`）→ 外部学习 Agent（人工已跑 · YT-02~YT-05 5 视频）→ 知识卡快照（`main/knowledge/external_snapshot/index.json` · v1-2026-08-10 frozen）→ 下期喂给认知层

- **归属新 skill**：s5 learning-and-experience（外部知识循环段 · 本次 Edit 补进 §8）
- **覆盖状态**：✅ **本次 Edit 已补上** · 之前一版有 gap · 现已修
- **变化**：本次接入前 s5 只讲了三段（单期偏好学习 + online refresh + 案例蒸馏），没提外部知识循环 · **本轮 Edit 已补一段：s5 只读外部快照，不改；快照更新走人工离线路径**
- **相关 CLAUDE.md**：F08 · 外部知识与监督学习

### 环节 11 · 内部经验循环 · 未启

**旧图内容**：feedback_bundle → 经验学习 Agent（**代码不存在**）→ 案例库空（`cases=[]`）→ 下期喂给认知层

- **归属新 skill**：s5 learning-and-experience（三段全部覆盖：单期偏好学习 + online refresh + 多期案例蒸馏）
- **覆盖状态**：✅ 完全覆盖 · **已从"代码不存在"升级到"三段真跑通"**
- **变化**：
  - 旧图明标"经验学习 Agent 代码不存在"；本轮 s5 实测已跑通：`label_learning_driver.py`（driver.py 内 prohibited scope 段行号 L1-20/L79-86/L611-617/L794-801/L951/972/L1108-1114）
  - 案例库从 `cases=[]` 升级到 65 records（EP03.jsonl + EP04.jsonl · 24 accept / 41 reject / 37 quarantine · 20 policy_cards）
  - online refresh 闭环真实跑过（LABEL-LEARNING-AUTO-HUMAN-BACKFILL-20260817-001）
- **未闭环**：跨节目泛化 backtest = `INSUFFICIENT_DATA_FOR_CROSS_EPISODE_GENERALIZATION`（2 期 / 1 审核人 · 需 4-5 期）
- **相关 CLAUDE.md**：§10、F08 §120-127

### 环节 12 · Tool 注册表 · v0.1 未真调

**旧图内容**：`main/tools/tools.json` 19 个 tool 名字/说明/参数/脚本路径 · v0.1 只登记 · **未被 orchestrator 真调**

- **归属新 skill**：s6 governance-and-tool-registry
- **覆盖状态**：⚠️ **部分覆盖 · 结构在 · 内容严重欠账**
- **变化**：
  - Tool 数从 19 涨到 48（`tools.json` 实测行数）
  - `tool_lookup.py::script_for(name)` 6 项 API 已实作 · orchestrator 部分 subprocess 走它（`delivery_orchestrator.py` 17 处 `_script_for(...)` + `run_end_to_end.py` 2 处 · 其他仍硬编码）
  - **audit 覆盖率 1/48**（只有 ffmpeg）—— s6 的**首要 backlog**
  - **runtime_dependencies 覆盖率 3/48** —— s6 的次要 backlog
  - `extract_gold_cut_features.py`（本轮 2026-08-18 新加）尚未登记 —— s6 立即待办
  - `tool-orchestrator-v2` 22 项契约测试通过但**未晋升**·主流程仍走 v1 硬编码路径
- **Gap**：verify.sh 第 18 层（CLAUDE.md §15 承诺的 installed-vs-used 扫描器）**项目内根本不存在**
- **相关 CLAUDE.md**：§6、§6.6、§11、§15、§18

---

## 三、本轮新增（CLAUDE.md §21 分家 · 学习流选择器 · 三顿悟 · gold-EDL 产物）接入情况

| 新增项 | 归属 skill | 接入位置 | 状态 |
|---|---|---|---|
| CLAUDE.md §21 · PARAMETER 参数文件 | s2（消费）+ s3（消费）+ s6（变更管控） | 顶层 SKILL.md §4a · s2 反馈证据 · s3 反馈证据 | ✅ 接入 |
| CLAUDE.md §21 · PREFERENCE 单一 SOT | s2（G7 消费）+ feedback-engine（retrieve） | 顶层 SKILL.md §4a · s2 硬化条款 | ✅ 接入 |
| 顿悟 1 · mentor 剪 71% semantic_boundary | s2 | s2 §8 反馈证据 | ✅ 接入 |
| 顿悟 2 · crossfade per-episode constant | s3 | s3 §8 反馈证据 | ✅ 接入 |
| 顿悟 3 · cross_track_speaking 59/59 假阳 | s2 | s2 §8 反馈证据 + s2 §7 pre_flight_check backlog | ✅ 接入（修正待落地） |
| `docs/learning-flow-selector.md`（决策树 + 补丁滥用防线） | s5 | s5 §8 反馈证据 | ✅ 接入 |
| `extract_gold_cut_features.py`（新 tool） | s6（登记）+ s5（消费） | s6 §8 反馈证据 + 顶层 SKILL.md §4a | ✅ 归属明确、**登记待办** |
| EP04-GOLD-EDL-20260818-1548（分轨可靠度分析 md + synthesis.json） | s5 案例蒸馏产物 | s5 §8 反馈证据 | ✅ 接入 |
| EP04-COMPREHENSIVE-20260818-1730/current_audit_clips/（8 A/B mp3） | s3 试听产物 | s3 §8 反馈证据 | ✅ 接入 · 符合"单一目录"契约 |
| 全部 sync 到 `交付-2026-8-17`（2.7GB） | 上一轮交付归档（与本轮 skill 重构包并列） | 顶层 SKILL.md §4a 表格 | ✅ 归档 |

---

## 四、结论：**没有旧流程环节被排斥在外**

| 覆盖状态 | 环节数 | 说明 |
|---|---|---|
| ✅ 完全覆盖 · 无变化 | 1 | 分层人工审核 |
| ✅ 完全覆盖 · 已增强 | 6 | 新音频输入 / 安全规则 / 认知层 / 冻结方案 / 最终成片 / 内部经验循环 |
| ✅ 完全覆盖 · 按 §21 分家 | 2 | 稳定音频 rules+scripts / feedback_bundle 拆三份 |
| ✅ 完全覆盖 · 本次 Edit 补上 | 1 | 外部知识循环（原 gap · 已修） |
| ⚠️ 覆盖但接缝散 | 1 | 7 状态状态机（**需在顶层加接缝图** · backlog） |
| ⚠️ 部分覆盖 · 结构在内容欠 | 1 | Tool 注册表（audit 1/48 · runtime_deps 3/48 · verify.sh 不存在） |

**关键 gap（需在下一轮处理）**：

1. **7 状态状态机接缝**：拆到 3 个 skill 后没有单一视图。**backlog**：顶层 skills-index 加一张接缝图。
2. **Tool 注册表内容欠账**：audit 1/48 · runtime_deps 3/48 · verify.sh 未落地。**backlog**：s6 首要任务。
3. **`extract_gold_cut_features.py` 登记**：本轮新 tool 未登记 tools.json + 无 audit。**backlog**：s6 立即待办。
4. **cross_track_speaking 定义 59/59 假阳**：s2 未真正落地修正，仍用能量启发式作兜底。**backlog**：改用 s1 speaker_map 逐轨判定。

**没有一个旧环节完全没被新体系吸收。**
