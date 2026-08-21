# Claude Code 项目入口

你正在处理当前仓库根目录。这是本地、可追溯、由真人掌握最终语义决定的多轨播客后期助手。拿到新一期音频时，先按 `统筹全局/Agent交付流程-从音频到成片.md` 建立一套新 run；用户不应被要求手工拼接脚本。系统可以输出机器辅助试听草稿，但不是无人审核发布器。旧 EP03 以双轨为主；2026-08-11 起输入、审核 EDL 和渲染按任意 N 条对齐 mono 轨设计，当前重点为三轨。

协作主目录固定为：`<PROJECT_ROOT>/`。`<HOME>/Documents/ChatGPT/制作音视频剪辑工作流/` 只是历史/迁移来源；除非任务明确要求追溯，新的代码、run、审核决定和文档不得写回临时工作区。

## 必读顺序（轻量上下文）

1. 本文件的 **六条元规则**（下方）——项目 canonical，一次读完
2. `docs/domain-rules.md` —— §1-§22 具体条款（只在触及相关领域时读对应 § 编号）
3. `统筹全局/当前状态摘要.md` —— 只读视图，由 `CURRENT_DELIVERY_FACTS` 自动生成
4. 与任务对应的 **一份** Fxx；涉及当前版本 / 审核 / ASR / 音乐 / 渲染 / 哈希时，加读 `版本同步与交付事实门.md`

不要每次全文读取全局记忆、当前进度、Agent 流程、全部 Fxx 或旧会话；读取路由见 `统筹全局/上下文读取索引.md`，操作步骤见 `统筹全局/Agent交付流程-从音频到成片.md`。

## 最高规则 · M0 · 开发者模式 (覆盖 M1-M6)

**触发词**: 用户输入包含 "开发者模式" 或 "developer mode" (中英不敏感 · 精确匹配)

**效果**:
- 触发后此对话全程 · **突破 M1-M6 六条元规则**
- Champion 只读 · 突破 · 可直接改
- 人签字 · 突破 · 开发者作为 stand-in
- 契约先行 · 突破 · 未登记 tool 可先测试再登记

**语义**: M0 = 开发者调试特权 · 生产 pipeline 保护 (M1-M6) 是给终端用户的 · 开发者可临时绕过

**不可突破的** (物理/数据安全约束):
- ❌ 公司音频不出本地 (M2 数据安全部分)
- ❌ 原始素材只读 (M2 原始数据保护部分)
- ❌ EDL 整数 sample (M4 物理约束)

**用法**:
- 每次触发 "开发者模式" · 当前对话生效
- 新对话不继承 · 需再次触发
- 每次冻结架构后 · 未来若需改动 · 需明确触发 "开发者模式"

**记录**: 每次触发 · 需在对话结束时**手动记录到** 统筹全局/DEVELOPER_MODE_LOG.md

## 六条元规则（本项目 canonical · 违反 = 项目本身不成立）

**M1 · 分层**
Champion（`稳定生产/scripts/*` · `端到端学习剪辑/代码/*` · 已哈希 run 产物）只读；Challenger（`稳定生产/challengers/<name>/`）隔离实验；run-local（`main/runs/<id>/`）只写自己 run 目录。未经人工晋升不改 Champion。无冻结 benchmark / 独立复核 / 回滚，不得晋升 Challenger。

**M2 · 只读**
原始素材 · Mentor 成果 · Champion · 已哈希 run 产物默认只读。公司音频、转写、内部资料默认不离开本机；不 `curl | sh`、不覆盖系统 Python、不修改全局 Skill。

**M3 · 人签字**
语义删剪必须真人明确批准，或有负责人签署、版本化 `autocut_policy` 授权的低风险自动剪。禁自批准、超时批准、把机器预测伪装成人审。

**M4 · EDL 整数 sample**
批准区间同步作用于全部语音轨；不容许 sub-sample 或单轨偏移。

**M5 · 契约先行**
每个 tool 必须登记 `main/tools/tools.json` + 有 v2 adapter；未登记即：
- `verify.sh` Layer 21 FAIL（`main/orchestrator/*.py` 静态扫描）
- `executor_v2.execute_plan` `unknown adapter` 拒跑
- `_adapter_base.dry_run_plan` `wraps_script not found` 拒 plan

外部依赖必须记录官方 URL / 版本 / 许可证 / 权重 SHA / 依赖 / 遥测 / 数据流。

**M6 · 报告纪律**
三档措辞：**已验证事实 / 已决定的方向 / 待验证假设**。无真实证据时禁用 "完成 / 已跑通 / 风险为零 / 识别更准 / 可发布"。见文末 "完成报告"。

## 域规则（§1-§22）

**具体条款（何时用什么工具、什么参数、什么门槛）不住本文件**。权威源：[`docs/domain-rules.md`](docs/domain-rules.md)。

- **Cut-verify 域 · 最高优先级 · 一切规则遇到冲突向 cut-verify 让步**：§8 §11 §14 §15 §16 §17 §19 §20 §22（内容不动 · 由 `skills/cut-verify/SKILL.md` frontmatter `covers_claude_md_rules:` 引用；字段名沿用历史命名 · 语义指的是本文件与 `docs/domain-rules.md` 里的对应 § anchor）
- **已进代码强制**：§9（automix）· §10（online 学习闭环）· §11（未登记即报错）· §12（speaker_role_gate fail-closed）· §13（source_track_gate）· §18（feedback-engine 单入口）
- **数据模型（无法完全代码化）**：§20（session_feedback 单 SOT）· §21（PARAMETER vs PREFERENCE 知识分块）
- **元规则展开**：§1-§7（M1-M6 对应关系见 domain-rules.md 每条尾注）

## 当前关键事实

- 底层音频执行链路有较强工程证据；EP04 已跑到真人审核后的三轨技术试听版，但发布产品闭环未验收。
- P1 三轨工程 E2E 已实际通过；EP03 11 项与 EP04 13 项真人双态审核均已保存，EP04 已渲染 3 stems 和 speech mix；主麦 automix、整片听审和发布验收未完成。
- `tools.json` 58 项（2026-08-21 实测 · 从 57 瘦身 · 2 项 LEGACY SHIM 已并入 feedback_engine 并删除 · +1 automix_2track challenger · 后续增补至 58）；v2 orchestrator adapter 覆盖 52/58（未覆盖 6: `load_session_feedback / generate_ab_clip_learning_driven / iterate_until_clean / render_with_refinement / optuna_refine / run_versioning_guard`）。详见 `项目主文档/系统架构白皮书-2026-08-21.md § 6`。
- v2 orchestrator 处于 Champion 并联 Challenger 状态 · 22 项契约测试通过 · Session 3 才晋升顶替 v1。
- 当前 activity 是能量启发式，不是男/女声模型；EP0X 必须先建 `main/knowledge/speaker_maps/<episode>.speaker_map.json` 声明每轨角色。
- 2026-08-19 剪口干净度 4 项 check 已落地（skills/cut-verify），是 cut 相关参数的最高优先级。
- 2026-08-19: **4 个 Challenger 晋升到 Champion pipeline**·全 default ON · pyannote-audio 4.0.7 (Stage 3.4) · NISQA Check 5 (Stage 4.5) · NISQA benchmark (Stage 6.5) · Optuna TPE iterative refinement (Stage 6.7) · case embedding retrieval (Stage 6.8) · 详见 统筹全局/PROMOTION_MANIFEST_2026-08-19.md
- 2026-08-19 evening: **LLM-first 架构落地** · Stage 3.7 · LLM 语义 filter · 唯一候选决定者 · 3 mode (claude CLI 首选) · G5_history 让位 diagnostic_only · autocut_gate 语义门让位 · Optuna+NISQA 只做参数级优化 · 详见 统筹全局/EVENING_MANIFEST_2026-08-19.md
- **2026-08-19 evening · GOLDEN PATH FROZEN**: 图片架构 = Champion 最终版 · 开发者身份突破 M1 · 冻结 8-stage LLM-first 流水 · EP05 GOLDEN-PATH-20260819-1900 端到端真跑通 · runner log 有 [stage 3.7] marker · 1 LLM KEEP + 1 REJECT · EDL 1 cut · NISQA overall 2.85 · 详见 统筹全局/GOLDEN_PATH_FROZEN_2026-08-19.md

最新状态始终以 `统筹全局/当前项目进度.md` 为准，不在本文件继续追加运行历史。

## 目录所有权

- 新工具、新规则和模型只写任务书指定的 `稳定生产/challengers/<name>/` 与对应新 run。
- `稳定生产/scripts/`、`稳定生产/rules/` 是 Champion，未经人工晋升不得修改。
- P0 与 P1 的当日 N 轨小闭环已在 Challenger 内集成验证；不得据此覆盖 Champion，正式集成和晋升仍另开任务。
- 工作树已有用户和其他 Agent 的改动；开工前运行 `git status --short`，不得清理、重置或覆盖无关文件。

## 施工方式

1. 先复现任务书基线；对不上就停止，不改 benchmark 迎合实现。
2. 先写失败测试，再改实现。
3. 原始输出与 normalized 输出同时保存，不丢词级/sample 时间信息。
4. 所有结果写新目录，记录命令、环境、配置、模型/依赖、输入输出 SHA。
5. 静态就绪、自动测试、真实数据运行、真人审核和发布验收分别报告。
6. 机器辅助版必须与 `human_approved` 版分开保存；人只审核高风险/代表性样本时，机器结论使用 `machine_proposed_*`、`auto_cut_eligible` / `human_review_required` 字段。
7. 完成后更新 `统筹全局/当前项目进度.md` 与对应 Fxx 功能文档；详细日志留在 run/Challenger，不新建会话长摘要。

## 完成报告

必须列出：实际修改、实际命令、基线与 Champion SHA、自动测试、真实运行、浏览器/人工证据、失败/降级、尚未完成和下一道门。

结论使用"已验证事实 / 已决定的方向 / 待验证假设"。没有真实证据时禁止使用"完成、已跑通、风险为零、识别更准、可发布"。
