# F07 统筹 Agent 与 Tool 注册表

> 当前状态指针：当前 run、工具调用阶段和阻塞点以 CURRENT_DELIVERY_FACTS/《当前状态摘要》为准；本文件只定义编排与 Tool 契约，不复制某一版 run ID。

## 2026-08-17 新增 · tool-orchestrator-v2 Challenger（用户点名 L2-8）

**用户 2026-08-17 明确指令**："L2-8 赶紧搭，我觉得这是很多时候反复做无用功的问题"。

`main/orchestrator/delivery_orchestrator.py` 已到 4640 行，每次接新能力都往里塞代码。`main/tools/tools.json` 有 18 项 tool 登记，但 orchestrator 并不通过它统一调用——每个 tool 各自 `subprocess.run` 直调脚本，参数/超时/日志/provenance/错误恢复散落。

**新 Challenger**：`稳定生产/challengers/tool-orchestrator-v2/`
- **AdapterBase 契约**（`adapters/_adapter_base.py`）：`validate_inputs / dry_run_plan / invoke / verify_outputs` 四段能力 + Provenance dataclass + wraps_script SHA drift 检测 + writes-policy 门禁
- **GenericScriptAdapter + registry.json**：把 18 项 Champion tool 声明式包装（不改 tool 脚本），加 2 项新 Challenger tool（automix-2track-v1 + speaker-diarize-v1）→ 共 20 项 adapter
- **planner_v2 / executor_v2**（`orchestrator_patch/`）：planner 读 episode config → `plan.json`；executor 拓扑排序执行 → 逐步 provenance + `execution_manifest.json`；fail-fast 精准重试单步
- **契约测试**：8 + 9 + 5 = 22 项通过；合成 fixture 走完完整链路；Champion SHA 保持不变
- **不改主流程**：`delivery_orchestrator.py` 4640 行不动；v2 保持并联，晋升需独立复核

**新能力上线契约**：新加一个 tool（如 automix / speaker_diarize）不需要改主流程——写一个 adapter contract JSON + tool 脚本，注册进 registry.json 即可。这才是 v2 的验收标准。

## 功能目的

把已有的音频能力变成一个真正面向使用者的入口：用户放入同一期 N 条对齐 mono WAV，Agent 自己创建 run、冻结方案、调用工具、准备少量人工审核、渲染两种明确标识的版本、加入固定音乐、做 QC，并交付一份可读报告。

统筹 Agent 是编排与审计层，不是“替人决定语义”的模型。完整的目标契约以《Agent交付流程-从音频到成片》为准；本文件说明它需要实现什么，以及现有代码做到哪里。

## 当前版本与同步门

当前 run、规则和数量只读 CURRENT_DELIVERY_FACTS：截至 2026-08-16，唯一可继续审核的是
`EP04-label-loop-v1-20260815-1805`，并复用外部 `faster-whisper small` 的 EP04 v13 成果；v20 仅是历史参考。前端、服务端或审核字段的修复不得直接改写带 `ui_sha256` 的生成页面。只有尚未开始审核的包可调用 `refresh-review` 归档旧包、校验候选边界并重建；当前 run 已存在 `review_draft.json`，因此页面、候选、决定和侧车全部冻结，必须等待下一次新审核包。

每次交付改动完成后，必须运行：

```bash
python3 main/orchestrator/check_current_delivery_sync.py --check
```

它会把当前项目进度中的结构化事实与 live `run_identity/state/review_package/analysis_reuse_manifest`
逐项核对，并检查审核页仍有语义上下文和 `feedback` 保存能力。

## 目标状态机

~~~text
RECEIVED
  → INPUT_VALIDATED
  → TIMELINE_READY
  → DENOISED
  → ANALYZED
  → CANDIDATES_FROZEN
  → CALIBRATION_REVIEW_REQUIRED
  → CALIBRATED
  → MACHINE_ASSISTED_DRAFT_RENDERED
  → FINAL_QC_REQUIRED
  → DELIVERY_DECISION_RECORDED
       ├── human_approved_delivery
       ├── policy_authorized_delivery
       ├── REWORK
       └── HOLD
~~~

人工闸门只有两类：

- CALIBRATION_REVIEW_REQUIRED：真人审核全部高风险候选和低风险的分层代表样本；
- FINAL_QC_REQUIRED：真人听整片、确认是否继续、返工或 hold。

它们不是失败，也不能被 Agent 跳过。输入、哈希、时间线、工具契约、音乐或 QC 失败时进入 BLOCKED 或 FAILED，保留最后一个完整产物而不伪造成功。

## 运行中必须管理的对象

| 对象 | Agent 必须做的事 |
| --- | --- |
| plan.json | 冻结输入 SHA、节目类型、规则/模型/工具版本、经验快照、音乐模板、随机种子、候选规则 SHA 与本期审核预算 |
| processing_manifest.json | 绑定 DeepFilterNet CLI SHA、参数、原始轨、派生轨、样本数和时间线回填；普通新 run 缺失即停止 |
| preference profile | 在 plan 中冻结 `preference_profile_id`、快照 SHA 与使用范围；它只影响候选/排序/试听参数，不能代替 autocut policy |
| all_candidates.json | 保存全部候选及安全阻断项；候选生成本身不删音频 |
| calibration_package | 按风险和分层抽样产生简短审核包，并绑定 A/B、候选与输入 SHA |
| review_packet.md / review_decisions.template.json | 给不使用前端的审核人看的简短文字/试听包及不可直接提交的空模板；真实人工决定仍只能写入 `human_decisions.json` |
| human_decisions.json | 只保存真人决定；不得把机器预测写进这个文件 |
| review_draft.json | 前端自动保存的未完成草稿；可以有 pending 和 feedback，但绝不能让 `resume` 把它当成人工决定；存在即冻结当前审核包 |
| review_bundle/event_routes.json | 未来新包的只读历史事件路由侧车；绑定 package/manifest，显示为参考，不生成当前决定、EDL 或自动剪辑权限 |
| human_approval_scope.json | 仅用于负责人明确批准一个 SHA 冻结整片试听包的窄例外；不伪造逐项标签，不进入训练或自动政策 |
| calibration_report.json | 说明本轮真人样本覆盖了什么、是否足以对低风险类别作预测，以及哪些类别仍须回到人审 |
| prediction_manifest.json | 保存全量机器预测、阈值、政策、规则/模型、证据 SHA 和 provenance |
| benchmark_evidence.json | 每个 run 的 development benchmark 旁路结果：候选负担、备注回归、无候选区抽查计划和 scorecard 的 SHA；只能是 `PASS` 或 `BENCHMARK_EVIDENCE_UNAVAILABLE`，不能创建语义删剪决定 |
| preference_application_report.json | 列出本期哪些候选/渲染参数来自偏好快照、哪些被安全门阻断、使用哪个音乐模板，以及与快照的差异 |
| 双 EDL | 同时生成 human_approved.edl.json 与 machine_assisted_draft.edl.json，绝不混名 |
| run_identity.json | 冻结 episode_id、run_id、契约版本和本次相对 run 路径；每一步先校验身份再继续 |
| DELIVERY_REPORT.md | 用人话解释这次剪了什么、谁决定的、音乐和 QC 是否通过、能否作为发布候选 |

## Tool 注册表的责任

Tool 注册表不是“有 18 个名字”就算可交付。每个被统筹层调用的 Tool 必须声明：

- 输入/输出 schema、读写范围、失败码和可恢复条件；
- 使用的音频时间基、源文件 SHA、参数与版本；
- 是否会创建全局同步剪口、仅处理源轨，还是只做包装；
- 产物路径和 manifest；
- run 身份字段、相对路径规则，以及如何拒绝旧 run/旧机器绝对路径混入；
- 本地运行、无上传真实素材的保证。

统筹层还必须校验上一步产物是否完整、`episode_id/run_id` 与当前 run 一致，再调用下一步；
不能靠路径存在就假定结果可用。目录名、run identity、EDL 文件名、输出名或 manifest
任一处引用旧 run、旧 episode 或失效绝对路径时，必须停止为
`BLOCKED: RUN_IDENTITY_MISMATCH`。

## 已验证事实

- main/tools/tools.json 登记了 18 项本地能力；名称、参数和脚本路径已做过静态校验。
- 普通 `start` 在 `TIMELINE_READY` 后无条件运行固定 SHA 的 DeepFilterNet，生成 run-local 派生轨并进入 `DENOISED`；没有 raw/afftdn 的生产开关。EP04 两段真实三轨试听已经实测等长、可解码，真人听感结论仍待记录。
- 隔离的 tool-orchestrator-v1 能冻结 N 轨输入 SHA 与 plan，真实调用两个只读前置 adapter，并在 HUMAN_REVIEW_REQUIRED 强制停止；合成三轨 fixture 的注册表、runner 与安全门测试为 26/26 通过。
- 这个 Challenger 没有用 EP04 真实节目跑完整链，也没有接通 ASR、候选、代表性抽样审核、双 EDL、音乐或渲染。因此它不能称为“只输入音频就可交付”。
- 现有 main/orchestrator/orchestrator.py 主要是历史状态机演示。它的 ARCHIVED 只表示旧状态走完，不表示有人审、机器辅助草稿、QC 或发布批准。
- `main/orchestrator/delivery_orchestrator.py` 已作为新入口实现：它会在全新 run 中校验 mono WAV/共同时间线/固定音乐，实际调用 DeepFilterNet、P0 与 filler/global-pause Challenger，冻结全量候选、抽取代表样本、生成审核包，读取真人标签后生成 calibration report、prediction manifest、双 EDL、双渲染、QC 和 DELIVERY_REPORT。默认冻结 `filler-global-pause-v16`，默认审核预算为 20；高风险超过预算会 fail closed，低风险样本不足则保持未剪。它同时生成不依赖前端的 `review_packet.md`，并在审核包中保留高风险 A/B 必听要求。当前只连接口癖、紧邻重复与全轨长停顿三个低层候选家族；未连接的高风险家族会在报告中保留覆盖缺口，不能静默 auto-cut。
- 这个入口现已把 JSON-only `editing-e2e-v1` benchmark 旁路接入 `start`、`refresh-review`、`resume` 和 `record-final`，CLI 默认 `--benchmark-mode auto`；每次会写 `benchmark_evidence.json` 与命令日志。旁路只测候选负担、历史备注回归、无候选抽查计划、scorecard 与渲染后的剪口复听排序；失败只留下 `BENCHMARK_EVIDENCE_UNAVAILABLE`，不得改变交付状态、真人决定、EDL 或音频。v20 已用显式 `benchmark` 命令实跑该路径，仍为 `CALIBRATION_REVIEW_REQUIRED`。

在 `CALIBRATION_REVIEW_REQUIRED` 阶段，`status` 是正确的进度检查入口。`verify` 是完整交付检查，若此时报告两份 EDL 缺失，表示审核尚未完成，不表示输入、复用链或审核包损坏；只有 `resume → FINAL_QC_REQUIRED → record-final` 后才应期待 `verify` 通过。

当前 N 轨审核包的 schema 是 `review-product-mvp-v2`，须使用 `稳定生产/challengers/review-product-v1/scripts/validate_mvp.py` 校验。旧 `validate_review_package.py` 仅适用于早期 `review-product-v1` 双轨包；它对 v20 误报失败不构成当前 run 的失败证据。
- 已用可删除的 30 秒三轨 PCM fixture 实测 `start → 审核包 → 人工决定 → resume → 双渲染/固定音乐/QC → record-final → verify`。该测试真实调用候选生成、审核包构建、FFmpeg 渲染和 QC；其中 DeepFilterNet 与 ASR 使用明确标注的本地 fixture adapter，因此它证明状态机和证据链，不证明真实节目上的降噪或转写质量。
- 用户整片听审 EP04 v12 后，`promote-v12` 已实际创建 `main/runs/EP04/EP04-human-approved-v12-20260813-152847/`。它以 `human_whole_episode_audition` 记录范围批准、验证新旧主文件精确 SHA，并不把旧机器动作改写成逐项 human label。随后 `recheck-qc` 用 `qc_recheck.json` 记录 SHA/版本的本地 FFmpeg 对同一母带独立测得 -16.54 LUFS / -0.86 dBTP；音频本体没有改写。该本地依赖尚未完成常规生产审计，且此数值不是 Mentor 冻结的发布规格。

## 当前缺口与下一道实现门

当前入口已经接通 1–6 的最小范围；下一道实现/验收门是：

1. 在一集新的真实 WAV 上独立跑完 `start → review_packet（或本地页面）→ resume → record-final`，而不是只复用 EP04 的已批准试听；
2. 把说错重来、语义重复、离题、串音归属和瞬态事件接入同一 `all_candidates → risk → review` 契约；
3. 加入 source-track gate 的常规渲染、主麦 automix、音乐 ducking 和 Mentor 冻结的发布规格；
4. 将 18 项 Tool 注册表与实际 adapter/恢复层统一，补全每种失败的 fail-closed 测试；
5. 为当前已发现且在 EP04 QC 留痕的本地 FFmpeg 完成来源、许可证、遥测和目标机部署审计；再对常规路径进行真实 P0、双渲染和响度重测。

## 安全边界

- 统筹层不能伪造 reviewer，不能把 machine_proposed_accept 写成 human_accept。
- 负责人对冻结整片的明确批准可以写为 `human_whole_episode_audition` scope，但它只能覆盖该具 SHA 的动作集合，不能回填为逐项 `human_accept` 或变成下一期策略。
- 没有有效 autocut_policy 时，机器预测可以进入 machine_assisted_draft 的试听渲染，但不能冒充 human_approved 或发布候选。
- 有效政策只可授权其明确列出的低风险类别；长停顿、说错重来、语义重复、离题、串音归属不明和敏感语义默认仍回到人工。
- 候选/渲染偏好快照不属于政策。即使快照含长停顿阈值、词表、crossfade、音乐或响度参数，也只可帮助生成候选、A/B 和试听草稿；不得据此自动通过高风险项或重命名输出状态。
- 审核预算是为了降低一次给人看的数量，不是削减高风险审核范围。超过预算的高风险项必须停止，不能静默丢弃、降级或批量标为保留/采用。
- 全局时间线剪口以整数 sample 表达并同步作用于全部语音轨；源轨 gate 只处理指定轨且不改变节目时长；音乐不属于语义删剪。
- 原始 WAV、Mentor 成果、Champion 和历史哈希产物只读。Challenger 不得覆盖 Champion。

## 验收标准

- 新用户只需提供同一期音频目录，不需要找脚本、拼命令、手工挑音乐或整理交付文件；
- 每次 run 都能在两个正常人工闸门停止和恢复；
- 人审版、机器辅助版和发布候选在文件名、EDL、manifest 与报告中严格区分；
- run 目录、run identity、文件名、相对引用和 manifest 身份一致；旧 run 或其他机器的
  绝对路径不会被误当成本期产物；
- 机器预测、自动政策、人工决定和渲染动作均可追溯到具体 SHA；
- 一次完整真实节目跑通后，独立复核确认没有把机器输出写成人工决定；
- 最终整片听审、音乐、响度和发布规格通过前，系统只交付草稿或 hold，不称为成片。

## 实现与证据入口

- 目标操作契约：统筹全局/Agent交付流程-从音频到成片.md
- 顶层路由：SKILL.md
- 当前实现：main/orchestrator/
- Tool 注册表：main/tools/tools.json
- 每期 run：main/runs/<EP>/
- 最小编排 Challenger：稳定生产/challengers/tool-orchestrator-v1/

> `EP04-v22-20260815-1315` 是历史工程记录。当前审核 run、规则与数量只能读取《当前项目进度》的 `CURRENT_DELIVERY_FACTS`；截至 2026-08-16 为 `EP04-label-loop-v1-20260815-1805`，不能由本文件的旧脚注覆盖。
