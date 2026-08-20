# Agent SOP · 跨模型稳定契约

> 版本：`agent-sop-v1`
> 更新时间：2026-08-15
> 作用：让任何 AI Agent（Claude Opus 5 / Sonnet 5 / GPT-5.x / 未来其它模型）在本项目上接手都能得到**一致、可预测**的产出。
> 前置读物：`统筹全局/全局统筹记忆.md`、`统筹全局/当前项目进度.md`、`CLAUDE.md`、`AGENTS.md`、`统筹全局/版本同步与交付事实门.md`。
> 配套：`统筹全局/Preflight-checklist-与今日踩坑清单.md`（每次开工前跑一遍）。

## 0. 为什么写这份

项目已经把**数据流**锁得很紧（状态机、SHA、双 EDL、run identity）。跨模型的差异不来自数据流，而来自：

- 何时问用户 vs 何时自决
- 环境依赖缺失时的补救策略
- 出错时的措辞和 fail-closed 时机
- 对文档层自然语言的解读差异

这份 SOP 只解决这四类"策略/风格"层面的差异。数据流的强约束已经在 `Agent交付流程-从音频到成片.md`、F04-F10、`版本同步与交付事实门.md` 里。

## 1. 强制约定（任何模型都必须遵守）

### 1.1 开工前的三次读

任何一次新会话第一件事：

1. 读 `统筹全局/全局统筹记忆.md`（长期产品与架构）
2. 读 `统筹全局/当前项目进度.md`（当前状态；含 `CURRENT_DELIVERY_FACTS` 结构化块）
3. 跑 `python3 main/orchestrator/check_current_delivery_sync.py --check`
   - 通过 → 继续
   - FAIL → 先诊断差异；若能只修正文档、检查器或新增证据且不触碰活动审核包、候选、音频或决定，就先安全修复并复查；若需要刷新活动审核包、改音频/边界、代填决定或扩大授权，必须停下报告具体阻塞，不能靠改 SHA 或文字掩盖。

### 1.2 目录写权限白名单

**可写**：任务书 / CLAUDE.md 指定的 challenger 目录、对应新 run 目录、`/private/tmp/`（缓存）、`~/.cache/huggingface/`（权重）。

**只读、不可覆盖**：
- 原始 WAV（`音频参考库/raw material/**`）
- `mentor的成果/**`
- Champion（`稳定生产/scripts/**`、`稳定生产/rules/**`）
- 已生成的 human_approved run
- 其它 run 里已冻结的 EDL / 决定 / 转写

**碰其他 Agent 未提交的改动前必须先 `git status --short`**；用户还在跑的会话里的文件不清理、不 revert。

### 1.3 SHA 校验先于一切

任何一步开始前，先验证输入的 SHA 与前一步 manifest 声明的一致。不一致 → `BLOCKED: RUN_IDENTITY_MISMATCH` / `BLOCKED: SHA_MISMATCH`，不继续。

### 1.4 输出必须落到明确的新目录

结果写到 `main/runs/<EP>/<新 run_id>/`，命名 `<EP>-<版本或用途>-<YYYYMMDD-HHMM>`。**绝不覆盖历史 run**。旧 FAILED run 保留作证据。

## 2. 何时问用户 vs 何时自决

### 2.1 必须问用户（人工闸门 + 语义决定）

1. **CALIBRATION_REVIEW_REQUIRED** 阶段的候选 accept/reject
2. **FINAL_QC_REQUIRED** 阶段的整片决定（继续 / 返工 / hold）
3. reviewer 姓名（永远不代填）
4. autocut_policy 授权（永远不代签）
5. 需要动语义边界（离题裁剪、章节重排、增加旁白）但没有政策授权时
6. 用户明确没授权的动作（如上传原始素材、把机器决定写成人工标签）

### 2.2 必须自决（不问用户）

以下情况**立刻做**，做完再报告：

1. **补装依赖到隔离环境**（`/private/tmp/venv-*` 或 `稳定生产/challengers/*/environment/venv-*`）；不允许污染系统 Python
2. **建 run 目录**（首次运行时按命名规范建）
3. **删除自己刚创建的临时缓存**（用户表达"没空间"时立刻做）
4. **失败后写 `BLOCKED` 到 state.json 并保留最后完整产物**
5. **修显然的错字/路径拼写**（如 `serve` vs `serve-review`）
6. **提交用户已经明确留下决定的草稿**（比如 `review_draft.json` 完整且用户明说"审完了"时，可代按 `/api/submit`——但**不代填内容**）
7. **跑 preflight 校验**（每次开工前必跑，不问）
8. **发出中间进度报告**（不阻塞主线）

### 2.3 灰色地带（问一次即可，之后不再问）

- 磁盘释放：第一次问选哪个删；获得授权后同类操作直接做
- 依赖版本 pin：如果 requirements.txt 里写了 `<pin_on_M3>` 未填，第一次询问，之后按当次决定
- 使用哪个 python：首次询问；之后按 `--python` 参数或 CLAUDE.md 记录

## 3. 会话内每一步的操作规矩

### 3.1 命令三段式

任何有副作用的动作（写文件、跑 orchestrator、装包、删目录）必须三段式：

```
[意图] 一句话说要干什么
[命令] 具体命令
[后果] 会创建/修改/删除的路径 + 大概占用空间 + 是否可回滚
```

用户可以在 [后果] 之后打断。

### 3.2 报告只从磁盘现读

对用户口头汇报的每一个：
- 路径存在与否
- 文件 SHA
- 文件大小
- run state.json 的当前 state
- history 的最新 entry
- 磁盘剩余

都必须**当场 ls / cat / hashlib** 出来，不从自己上一段话或"我记得"里抄。

### 3.3 长任务后台化

任何超过 30 秒的任务（DeepFilterNet 降噪、ASR、大文件 ffmpeg 渲染）必须 `run_in_background=true`。等 task-notification，不 sleep 轮询。

### 3.4 状态过渡记录

每一个 state 变化后立刻读 `state.json` 报告最新 state 和 note，不预测下一个 state。

## 4. 失败模式与固定应对

| 症状 | 固定应对 | 不做什么 |
|---|---|---|
| 缺 python 包 | 在**隔离 venv** 装；venv 位置见 preflight 文档；`--user` 只作 fallback | 不 `pip install` 到系统 Python |
| 权重缺失 | 从官方源下（记 URL / SHA / 许可证）；下载中就报进度；下完 verify SHA | 不 `curl \| sh`；不从不可信镜像拉 |
| 磁盘满 | 立刻停手 → 列可释放清单 → 等用户点头 → 删 → 报释放量 | 不擅自删任何目录 |
| sync check FAIL | 先诊断具体哪个字段不一致 → 给用户 A/B 方案 → 由用户决定"先动文档还是先动数据" | 不静默改 SHA 让它对上 |
| run identity mismatch | 停到 `BLOCKED: RUN_IDENTITY_MISMATCH`；保留产物；报差异 | 不改 run_identity.json 让它匹配 |
| 前一个 run FAILED | 保留失败 run 作证据（不删）；新起 run；在新 run 的 note 里引用失败原因 | 不覆盖失败 run；不删失败 run 除非用户明说 |
| 依赖冲突（如 wave 不认某格式） | 换 Python 版本或换实现库；隔离 venv | 不改 Champion 脚本迁就自己环境 |
| 权重许可证不明 | 记录并停在 Challenger 层；不上生产 | 不假装"许可证已审计" |
| 用户说"没空间" / "停一下" | **立刻** kill 所有后台任务 + 释放我自己创建的缓存；报磁盘状况；等指令 | 不继续跑；不问"要不要停"，直接停 |

## 5. 数据边界（永远不做的事）

1. 语义删剪未经真人批准 → 不写 human_approved.edl.json
2. 机器预测 → 不写成 human_accept / 不写成 reviewer 名字
3. 原始 WAV / Champion / mentor 产物 → 只读
4. 上传真实音频 / 转写 / 内部资料到外部服务 → 禁
5. `curl | sh`、不透明安装脚本、覆盖系统 Python、修改全局 Skill → 禁
6. Challenger 直接改 Champion → 禁；必须走"冻结 benchmark + 独立复核 + 人工晋升"

## 6. 会话结束前必查

无论何时会话即将结束（用户说"停"、任务完成、时间到），做一遍：

1. `git status --short` 确认没意外改动
2. state.json 是否停在**语义正确**的状态（不是半成品；FAILED / BLOCKED / *_REQUIRED / DELIVERY_DECISION_RECORDED 都算正常终止）
3. 后台任务全部收尾（kill 或标记完成）
4. 后台任务留下的临时缓存清理或明确保留（并告诉用户在哪）
5. 一句话总结：这次做了什么、结果在哪、下一次接手要读什么

## 7. 与其他文档的关系

- **数据流硬约束**：`Agent交付流程-从音频到成片.md`、`版本同步与交付事实门.md`、F04-F10。本 SOP 不重复它们。
- **具体环境依赖**：`Preflight-checklist-与今日踩坑清单.md`。本 SOP 只引用，不复制。
- **产品目标 & 长期决策**：`全局统筹记忆.md`。本 SOP 不改动这些方向。
- **当前状态**：`当前项目进度.md` 的 `CURRENT_DELIVERY_FACTS` 块。SOP 不写状态，只要求"改动后必须同步"。

## 8. 修订规则

本 SOP 修订必须满足：

- 说明为什么改（引用具体案例 / 会话记录）
- 变更范围（哪一节）
- 谁改（人 / 哪个模型）
- 兼容性影响（旧行为是否被禁）
- 通过 `check_current_delivery_sync.py --check`

不允许在会话中口头修改本 SOP；必须写进这份文档。

## 9. AI 反思学习循环（2026-08-15 加入）

**核心原则**：每条 mentor 标签都必须进入可读、可回滚的学习证据链，绝不能只停留在“1/2/3 分数”或聊天记忆里。学习不是在线改规则：它输出 Markdown/JSON 和 Challenger 证据，不是黑盒模型或隐式自动删剪。未来新 run 的历史事件**展示**可由 orchestrator 生成侧车，但不会写入当前决定。

### 每次会话必做的动作（不管是 Opus 5 / Sonnet 5 / GPT-5.x 接手）

**会话开始时**：
1. 读 `skills/editing-experience-distiller/output/preferences-20260815-1330/preferences_for_agent.md` 里最新的 11+ 条 P-XX 规则
2. 读 F04 "边界陷阱清单"章节
3. 才开始动候选/规则/审核

**会话进行中，用户提交新审核后**：
1. 只在真人正式提交后，运行 `main/orchestrator/build_label_learning_challenger.py` 生成一个新的、不可覆盖的学习证据 run；排除 bulk accept、机器预测和草稿。
2. 阅读事件路由、feedback 分类和政策卡，确认新案例属于 ASR、语义、执行或误报的哪一层。
3. 调用 `$label-learning-driver`：先跑按节目留出的回测，再对未来新候选或独立 shadow 输出机器建议。检查 `matched_cases`、`execution_warning`、`missing_features` 与 `data_quality.status`；不得把原始命中率当成泛化准确率。
4. 不得凭一次或少量反馈直接改 P-XX、阈值或生产候选规则；先将变化写成带反例和回滚信息的 `candidate/challenger` 政策卡。
5. 只有历史回归、独立样本和人工晋升通过后，才可提出对 `machine_assisted_draft` 的受限变更；`human_approved` 与 `autocut_policy` 永远不自动更新。

**会话结束前**：
1. 报告本次新建/更新了哪些事件、feedback 分类和政策卡，来自哪些 case_id。
2. 让用户看得懂“这条备注影响了什么候选提示/修复假设”，并明确它是否尚未晋升。
3. 保留旧政策与规则版本；不得覆盖历史 run 或把推荐直接写成生产默认。

### 2026-08-16 标签学习闭环补充

每次从真人审核学习时，先做事件级去重，再做反馈归类，不能直接按候选 ID 或“1/2/3 分数”学习：

1. 用输入/轨道、`match_text`、重叠时间窗和候选家族计算 `event_key`；精确重复只显示历史决定/备注参考，不能自动生成当前决定、EDL 或自动剪辑权限。
2. 若边界变化，最多复用旧语义线索，不是新边界的听感批准；必须标 `boundary_review_required`。
3. 备注分类为 `asr_error / semantic_keep / semantic_cut / execution_issue / false_positive / unknown`，保留原备注和 case_id。
4. 将稳定模式写成 Markdown/JSON 政策卡，包含条件、行动、案例、反例、版本、状态和回滚信息；初始只能是 Challenger。
5. 只有历史回归、独立样本和人工复核通过后，政策才可以影响 `machine_assisted_draft`；永远不能自动写入 `human_approved` 或 `autocut_policy`。

相关事实必须同步到《当前项目进度》、全局记忆、F03/F04/F05/F08 和本期 run 的检查点；同步检查失败时停止，不继续审核或渲染。

当前的可复用入口是：

```bash
python3 main/orchestrator/build_label_learning_challenger.py \
  --repo-root . \
  --out-dir main/runs/<NEW-LABEL-LEARNING-RUN> \
  --current-run main/runs/<EP>/<CURRENT-REVIEW-RUN> \
  --historical-run main/runs/<EP>/<HISTORICAL-HUMAN-REVIEW-RUN> \
  --canonical-case-store 稳定生产/challengers/experience-ingestion-v1/case_store/two-state-v1-20260812-1627
```

它输出事件路由、反馈分类和政策卡，但不能修改 `--current-run`。下一次新审核包通过 `delivery_orchestrator.py start --event-history-run <历史逐项人审 run>` 写入只读 `event_routes.json`；已有 `review_draft.json` 的包不得补写、刷新或手改。

### AI 反思学习 vs 流程学习（关键区别）

| | 流程内做的（orchestrator 自动） | AI 反思做的（Agent 主动） |
| --- | --- | --- |
| 边界精修 `snap_candidate_boundaries` | ✅ 每次 start 自动跑 | — |
| 剪口质量预测 `predict_cut_artifact` | ✅ 每次 start 自动跑 | — |
| 从新 mentor decisions 归纳偏好卡 | ❌ 不做 | ✅ AI 每次会话主动跑 distiller |
| 更新 P-XX 规则清单 | ❌ 不做 | ✅ AI 手工写进 preferences_for_agent.md |
| 更新边界陷阱阈值 | ❌ 不做 | ✅ AI 分析新 feedback 后手工改 F04 + 脚本 |
| 用偏好过滤/加分候选 | ✅ predict_cut_artifact 用 F04 阈值 | — |

**为什么这样分工**：
- 流程内的活是**执行**（拿规则跑候选）—— 稳定、快、可回放
- AI 反思的活是**学习**（把 feedback 变规则）—— 需要判断力，规则要人可读

### 违规判据

任何 AI 接手后跳过已冻结的案例/政策卡就动候选生成，或跳过 Challenger 证据链直接改 P-XX、阈值或生产规则，都是违规。会话结束前必须报告“读了什么、输出了什么、哪些仍未晋升”。
