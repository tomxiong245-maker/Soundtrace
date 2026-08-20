# Agent 交付流程：从音频到成片

> 当前状态指针：本手册是按需操作手册，不是当前版本事实来源。当前 run 和硬要求读取《当前状态摘要》及 CURRENT_DELIVERY_FACTS；版本/哈希变更再读取《版本同步与交付事实门》。

> 契约版本：`delivery-contract-v1.2`（操作文档修订：2026-08-14）
> 作用：让后续 Agent 在拿到一集音频后，知道该自动做什么、何时只让人看少量高价值内容、何时必须停下，以及输出究竟是试听草稿还是可发布候选。`main/orchestrator/delivery_orchestrator.py` 已实现本契约的最小可运行入口；候选覆盖和本机依赖仍有限，具体范围见第 11 节。

## 版本同步硬门

当前仍需真人继续审核的 EP04 run 是 `EP04-label-loop-v1-20260815-1805`；它继续复用外部
`faster-whisper small` 的 `EP04-v13-20260813-2002` 词级成果。`EP04-v20-20260814-1617` 是已完成候选审核/技术渲染的历史参考，不是当前待审指针。若只修改审核前端、语义上下文展示或备注字段，不能重跑 ASR，也不能直接
改写已绑定 `ui_sha256` 的 `review_bundle/index.html`。只有未产生草稿/决定的包可使用 `refresh-review` 归档旧包、重建同一候选源，并写 `review_package_revision.json`；存在 `review_draft.json` 或 `human_decisions.json` 的包必须冻结，未来页面功能只能随下一次新审核包生效。每次变更结束都运行
`python3 main/orchestrator/check_current_delivery_sync.py --check`，确认本文件、当前进度、长期记忆和 live run
讲的是同一版本。

## 先说结论

“只输入音频”指的是：节目负责人不需要再手工找脚本、拼文件、选片头片尾、计算剪口或整理产物。Agent 按一个冻结的节目配置自动完成输入检查、转写、候选、抽样审核包、基于标签的重新判断、同步剪切、固定音乐、QC 与交付报告。

它**不**等于“机器可以把自己的预测伪装成人工决定”。必须区分两种结果：

| 结果 | 可以包含什么 | 可以叫什么 | 能否直接作为发布候选 |
| --- | --- | --- | --- |
| `human_approved` | **仅**真人逐项 `human_accept` 的剪口 | 人审批准版 | 不可以直接发布；仍须最终整片听审、规格和 QC |
| `machine_assisted_draft` | 真人采用的剪口 + 通过安全门、经本轮校准后机器预测为可剪的低风险剪口 | 机器辅助试听版 / 自动剪辑草稿 | 当前不可以；只有有效 `autocut_policy`、最终人工听审和发布规格都满足时，才可另行成为发布候选 |

所以，用户所说的“人只审核最高风险、最有代表性的候选；机器再判断其余候选；把机器判断为 accept 的也剪进去”是允许的**自动试听草稿流程**。这里的“机器 accept”在文件中必须叫 `machine_proposed_accept`，而不是伪造为真人 `accept`。无论自动剪辑政策是否启用，机器辅助版都应包含通过安全门、校准充分的低风险机器预测；若要让它进入发布候选，必须先由负责人明确签署“哪些低风险类别可以被自动剪”的政策。当前项目还没有这份已验证、已启用的政策。

`human_approved` 是剪口来源，不是“已经可发布”的承诺。自动剪辑政策绝不能把机器剪口塞进 `human_approved.edl.json`。

ASR 也不是每次都要重跑。项目已经有经过真实音频运行、词级时间戳校验和负责人抽听的
`faster-whisper small` 成果；如果本次上游音频和模型配置没有变化，Agent 必须先复用这份不可变成果，
把时间花在候选、审核和渲染上。只有哈希或模型配置确实变化时，才重跑受影响阶段。

一个窄例外是负责人已经听完一个带 SHA 的、冻结的整片试听包，并明确批准该完整动作范围。该 run
可使用 `human_whole_episode_audition` 作为 `human_approved` 的批准来源，但必须保留原机器/历史
provenance，写 `human_approval_scope.json`，并明确它不是逐项人工标签、训练标签或跨期政策。

### 一集节目到底按什么顺序处理

后续 Agent 必须按下面这个顺序执行，不能把“少量人审”理解成“少量内容可以被
机器随意删除”：

1. 先冻结**全部**候选；候选本身不改变音频。
2. 把长停顿、说错重来、语义重复、离题、咳嗽/碰麦、串音/混音不确定等高风险项
   全部送给真人；人决定 `accept / reject`。
3. 对规则明确的低风险连续口癖、紧邻重复，真人只看分层代表样本和阈值边界样本。
4. 读取本期真人标签后，Agent 对所有尚未人工决定的**低风险**候选重新判断；高风险
   未决项只能是 `human_review_required`，不能从相似标签外推成可剪。
5. 人审采用项进入 `human_approved`；人审采用项加上安全、校准充分的低风险
   `machine_proposed_accept` 一起进入 `machine_assisted_draft`，因此所有机器判为可剪
   的合格低风险项都会被剪进试听草稿。
6. 两个版本都要加入固定片头片尾音乐、跑 QC，再由人试听整片决定继续、返工或 hold。

若高风险项很多，Agent 可以按类别和时间段分批显示来减少页面负担，但不能借此跳过
任何一项；未审完的高风险项保持未剪并阻止本期进入最终交付决定。

## 0. 先检查并复用已有 ASR 成果

这是每次新任务开始时的第一道机器检查，不能因文件名相同就跳过。

### 0.1 复用资格

Agent 先在已有 `main/runs/<episode_id>/` 中寻找同一期、已完成且未损坏的 ASR/semantic transcript。逐轨同时核对：

1. 原始 WAV 的 SHA-256；
2. 实际送入 ASR 的音频文件 SHA-256（通常是 DeepFilterNet 输出）；
3. ASR 引擎、模型、模型快照/版本、设备、量化、解码参数和提示词；
4. transcript 的 `source_audio_sha256`、词级时间戳资格校验和报告 SHA；
5. 若使用句子/分句层，再核对它引用的源 transcript/report SHA。

全部一致时：

- 直接复用原始词级 JSON、normalized transcript 和 semantic transcript；
- 不再调用 faster-whisper；
- 在新 run 写入 `analysis_reuse_manifest.json`，记录 `reused_from_run`、每轨源文件/源 SHA、模型配置和复用原因；
- 新候选规则、审核页面或试听参数变化，只重跑候选、A/B、审核包和渲染，不改写旧 ASR。

如果同一份 P0 报告下存在多个通过校验的 semantic transcript，Agent 必须显式选择一个：
`start --reuse-analysis-run <P0-run> --reuse-semantic-run <semantic-run>`。省略后者时入口
直接 `BLOCKED`，不得按时间或文件名自动挑选。复用模式同时禁止 `--model` 和非空
`--context-prompt`，因为这两个参数会改变 ASR 结果的来源。

当前已验证的复用源是 `EP04-v13-20260813-2002`：外部 `faster-whisper small`，
CPU `int8`，模型快照 `Systran/faster-whisper-small` revision
`536b0662742c02347bc0e980a01041f333bce120`，三轨 normalized 词数为
`12,467 / 11,853 / 6,732`，非法时间戳均为 `0`；其 semantic transcript 归档于
`EP04-semantic-transcript-v1-20260814-120456`。

### 0.2 什么时候不能复用

只要任一上游 SHA、模型、解码参数或来源引用不同，就不能把旧 transcript 冒充为新音频的结果。
特别是：原始 WAV 相同但新一轮降噪输出 SHA 不同，也不能直接复用旧 transcript。
此时 Agent 只能二选一并明确记录：

- 继续使用旧的“降噪轨 + ASR + semantic transcript”作为冻结上游，候选阶段明确标注
  `source_artifact_mode=frozen_prior_analysis`；或
- 在得到允许后，对新的降噪轨重新跑 faster-whisper small，并保存新的模型/输入 SHA。

不能通过改文件名、复制旧 JSON 或只改 manifest 文字来绕过这道门。

### 0.3 当前 EP04 证据

EP04 v13 的三条降噪轨 SHA 为：

```text
track_01  4fd1c414f4ebc062caee4b3383b8b2e53aa0f536a1ab98df84ff49032f025f1d
track_02  47112c7f0a8d37e3a5d25686c8a80c60e01db89be08dfca828a4f44609abf524
track_03  18e386d4aa595f564b33b44767ba99f26f09e8e70d45fd73bf65f06a775250fd
```

因此，若后续只改变 v18 候选规则、生产保护规则或审核页面，应复用 v13 的三条降噪轨和 ASR；不能把另一套降噪输出当成同一输入。

### 0.4 审核前的状态与完整交付校验

新 run 生成审核包后，`CALIBRATION_REVIEW_REQUIRED` 是正常人工闸门。此时 `status` 应显示 run 身份无误；`verify` 若报告两份 EDL 缺失，只说明真人审核与后续渲染尚未发生，不能把它写成输入、降噪、ASR 或审核包失败。只有完成 `resume`、自动 QC、整片人听审与 `record-final` 后，才以 `verify` 的 PASS 作为完整交付通过门。

当前 N 轨审核包按 `review-product-mvp-v2` 校验，应使用 `稳定生产/challengers/review-product-v1/scripts/validate_mvp.py`。旧 `validate_review_package.py` 只适用于早期双轨 `review-product-v1` 包；它对 v20 的误报不能作为停机或重建审核包的理由。

## 1. 只需提供的输入

默认交付配置名为 `minglue-mandarin-multitrack-podcast-v1`。当它已经冻结时，用户只需要把同一期的音频放进一个新目录；Agent 从目录名生成不冲突的 `episode_id/run_id`，也可接受用户给出的节目编号，但不能要求用户手工创建计划、EDL、JSON 或命令。

```text
<episode-input>/
├── track_01.wav
├── track_02.wav
├── track_03.wav                # 轨道数可以是 N，不写死三轨
└── （可选）speaker-map.json     # 人名/角色已知时提供；未知也不阻塞
```

固定配置由 Agent 自动读取：

- 节目类型：中文多人对谈播客；不符合时停止并说明原因；
- 固定音乐：`音频参考库/raw material/第三集/片头片尾music.mp3`；
- 音乐 SHA-256：`3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83`；
- 当前音乐时序模板：`reference-linear-v1`；
- 当前剪辑偏好快照：`editing-preference-profile-v15-draft`；它只控制候选提名、排序和试听渲染参数，不是 `autocut_policy`。如需试听 v12 的 15 秒交叉音乐结构，必须在计划中显式写 `music_template_id=EP04-v12-crossfade-audition`；未确认前不替换 `reference-linear-v1`；
- 默认候选规则：`filler-global-pause-v18`。它默认保护完整句内语义、保留所有 acknowledgement 型“嗯”，并把紧邻重复/强口癖只作为审核候选；长停顿仅生成自然压缩 A/B 候选。具体以 CURRENT_DELIVERY_FACTS 与本期 `frozen/candidate_rules.json` 为准；
- 活跃生产保护规则：`editing-policy-guards-v1`。普通 `start` 必须冻结它并写 `policy_application.json`；它只自动保留精确的 ASR/词边界误报或升级为人工审核，永远不能生成 `auto_cut_eligible`、EDL 动作或真人决定；
- 当前审核容量：默认 `20` 项。它是给人的一次性容量上限，不是高风险删减权限；高风险超过此数时必须停止并报告，不能漏审或机器代决；
- 当前候选/审核/渲染规则版本、工具版本、经验快照与授权政策版本；
- 输出根目录：`main/runs/<episode_id>/<run_id>/` 的全新版本目录，例如
  `main/runs/EP04/EP04-v12-20260813-1520/`；绝不覆盖历史 run、原始 WAV 或 Mentor 成果。

### 输入不合格时，Agent 只能停下，不得猜

- 文件不是同一期、没有授权、不是独立 mono WAV；
- 采样率、样本数或共同时间线无法验证；
- 不属于已支持的中文对谈节目；
- 固定音乐缺失或 SHA 不一致；
- 需要改变节目内容、删除整段离题内容、重排章节或新增旁白，但没有明确的节目编辑政策。

停下时只给出一个短报告：发现了什么、需要谁补什么、哪些文件没有被修改。

### 正常使用者只会看到两次人工动作

1. `CALIBRATION_REVIEW_REQUIRED`：查看 `review_packet.md`（或需要时打开本地审核页）审核高风险候选和低风险代表样本；只回复/选择 `accept / reject`，不手改边界或命令。长停顿必须听原版与压缩版 A/B。
2. `FINAL_QC_REQUIRED`：试听指定版本和整片，选择“继续交付 / 返工 / hold”。

除输入问题和这两个闸门外，Agent 不得把“找脚本、运行命令、改 EDL、复制路径或挑音乐”转嫁给使用者。若稳定入口尚未实现某一步，Agent 应在自己的交付报告中标为缺口或 `BLOCKED`，不能假装让人手工补一段就叫“只输入音频”。

## 2. Agent 的固定状态机

```text
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

任一步出现输入/哈希/安全/工具错误 → BLOCKED 或 FAILED（保留最近完整产物）
```

`CALIBRATION_REVIEW_REQUIRED` 和 `FINAL_QC_REQUIRED` 是正常人工闸门，不是 Agent 失败。`human_approved_delivery` 只对应人审版；`policy_authorized_delivery` 只能在有效自动剪辑政策覆盖机器剪口、且负责人完成最终听审时使用。除了这两个闸门，Agent 不应让用户手工拼接命令或调剪口。

## 3. 每一步具体做什么

| 阶段 | Agent 自动完成 | 交给人的最小动作 | 必须留下的证据 |
| --- | --- | --- | --- |
| 输入检查 | SHA、格式、N 轨共同时间线、授权配置、漂移门禁 | 无；除非失败 | `input_manifest.json`、`plan.json` |
| 音频分析 | 先执行第 0 节复用门；不满足时才跑降噪后的词级 ASR、VAD、轨道活动、串音/重叠提示 | 无 | `analysis_reuse_manifest.json`（如复用）或 ASR 工具版本、参数、输入/输出 SHA 与时间线映射 |
| 候选冻结 | 生成所有候选，不删任何内容；标注风险、理由、跨轨守卫结果和 `preference_profile_id` | 无 | `all_candidates.json`、候选 SHA |
| 代表性审核 | 自动挑选必须由人看的高风险项与分层代表样本，生成简短 `review_packet.md` / A-B；高风险超预算即停止 | 只做 `accept / reject`，可写每项 `feedback`；长停顿必须试听两版 | `calibration_source.json`、`review_packet.md`、`review_bundle/`、`review_draft.json`、`human_decisions.json` |
| 标签归纳与全量判断 | 冻结历史有效案例 + 本轮真人标签，调用 `label_learning_driver.py` 对**全部候选**输出机器建议、案例证据、执行风险和缺失特征；不改全局规则 | 无 | `label_learning_application.pre_review.json`、`review_bundle/label_learning_application.post_boundary.json`、`calibration_report.json`、`prediction_manifest.json`、`experience_proposal.json` |
| 自动试听渲染 | 合并真人采用与机器低风险预测采用，得到机器辅助试听版；另产出只含人审采用的 EDL；按计划使用偏好快照中的非语义渲染参数 | 无 | 两份 EDL、render manifest、音乐/混音报告、每份 render 的 `transition_qc.json` |
| 最终 QC | 检查时长、采样率、编码、响度、峰值、剪口、固定音乐，并按 `transition_qc.json` 的客观异常排序生成重点复听；客观低分不能自动通过 | 听整片并选择“可继续/返工/hold” | `qc_report.json`（含两份 transition QC 的路径/SHA）、`final_listening_decision.json` |

### 3.3 标签学习闭环与历史事件路由

`human_decisions.json` 进入经验层前必须经过事件账本。稳定身份由输入/轨道、`match_text`、候选家族和时间重叠窗口组成；候选 ID、边界精修或 semantic SHA 变化不能单独造成新标签。

| 路由 | 处理 |
| --- | --- |
| `already_reviewed_exact` | 显示既有决定/备注作参考；不复制为本轮 human decision、EDL 或自动剪辑权限 |
| `semantic_reuse_boundary_review` | 显示旧语义线索，单独复核新边界/剪辑执行 |
| `rejected_false_positive` | 形成抑制同类候选的 Challenger 证据，未晋升前不静默隐藏当前候选 |
| `rejected_execution_issue` | 保留语义候选，转为边界、crossfade、音量或渲染修复 |
| `new_event` | 按风险分层进入当前审核包 |

真人备注要归类为 `asr_error`、`semantic_keep`、`semantic_cut`、`execution_issue`、`false_positive` 或 `unknown`。归类不能改写真人决定；它只生成带条件、案例、反例、版本和状态的 Challenger 政策卡。政策卡经历史回归、独立样本和人工晋升后，最多影响 `machine_assisted_draft`，不能伪造 `human_approved`。

在开始下一期审核包前，Agent 应先用 `main/orchestrator/build_label_learning_challenger.py` 生成一个新的、不可覆盖的学习证据包。随后通过 `delivery_orchestrator.py start --event-history-run <历史逐项人审 run>` 生成绑定 package/manifest 的 `review_bundle/event_routes.json`；它只作为审核排序/去重的输入，不得直接覆盖已有 `human_decisions.json`。边界变化仍展示为复听项，执行问题仍转为修复项；已有草稿的旧包不得补写侧车。

当前有效学习证据是 `LABEL-LEARNING-v3-20260816`（65 个独立逻辑事件、20 张政策卡）；`LABEL-LEARNING-v2-20260816` 因重复计数和 manifest 失效只作历史诊断。V3 已派生 `editing-policy-guards-v1`：未来新 run 可以自动保护精确的完整词/词边界误报，或把模糊重复与高风险家族升级人审。它不是自动删剪政策；`autocut_policy` 仍为 `NOT_APPROVED`。

### 3.1 每次 run 的持续 benchmark 旁路

这个旁路的目的是减少未来人工找问题的时间，不是增加本次交付的语义审核数。`delivery_orchestrator.py` 默认会在候选冻结、审核包刷新、渲染后和最终决定后调用 `benchmark/editing-e2e-v1/run_development_benchmark.py`，生成/检查 scorecard、固定随机无候选区抽查计划和 Mentor 备注回归；在渲染后 scorecard 会收录两份 `transition_qc.json` 的优先复听排序。人类把 QA 结果回填后，Agent 用同一个入口刷新：

```bash
python3 main/orchestrator/delivery_orchestrator.py benchmark \
  --run-dir main/runs/<EP>/<RUN> --phase manual
```

它只读写 JSON/Markdown，不接触真实媒体，不能创建 human decision、EDL、自动删除、Champion 晋升或发布批准。若这条旁路失败，run 内必须写 `BENCHMARK_EVIDENCE_UNAVAILABLE`，但不能把已经成功的审核/渲染阶段改成失败；只有隔离 fixture 或故障定位可以显式 `--benchmark-mode off`。

- 当前语义审核包与 development QA 分开。比如 v20 的 8 个无候选窗口不属于当前 5 条 `accept/reject`，也不能直接形成 EDL。
- `transition_qc` 是“先听哪里”的排序，绝不是“这条剪口自然”的判定。
- 无候选区、剪口盲听、整片听审缺失时，scorecard 必须写 `NOT_MEASURED`；不得以候选少、未发现问题或历史标签替代。2026-08-17 起，"返工/维护时间"已按项目负责人明确指令从 scorecard 判据中撤除。
- 每轮运行前后都要保持 `mentor-feedback-regression-v1/build_catalog.py --check`、`build_scorecard.py --check` 和相关契约测试可复跑。证据变化时 wrapper 会用 `--build --replace` 重建派生 scorecard，不能手改历史报告。

只有"候选覆盖、无候选区漏检、剪口听感"三项一起改善，才可以向负责人提出"下一期可再缩小审核包"的 Challenger 假设；这仍不等于自动剪辑政策。

## 4. “人只看高风险和代表性样本”如何执行

### 4.1 必须全量交给人的候选

下面任何一项都不允许靠机器预测直接进入 `human_approved`：

- 数字、专名、否定、结论、承诺、敏感表达、话题转折与明显离题；
- 说话人切换、重叠、串音归属不确定、其他轨有不同文字；
- 长停顿压缩、咳嗽/碰麦、爆音、音乐干扰等听感事件；
- 说错后重说、远距离语义重复、整句语义重复；
- 剪口边界不确定，或拟删内容超过低风险政策的长度上限；
- 任一跨轨安全守卫给出 `BLOCKED` / `ambiguous`。

这些候选在 `prediction_manifest.json` 中可以记录当前路由为
`human_review_required`，但不得因同类低风险标签得到 `machine_proposed_accept`。
如果人尚未决定，它们既不进入人审版，也不进入机器辅助试听版的全局语义剪口。

### 4.2 低风险候选的代表性抽样

低风险候选（例如规则明确的连续口癖、紧邻重复）不需要一开始全部给人看。Agent 用固定随机种子做分层抽样：

1. 按 `reason_family / reason_key / 置信度档 / 时长档 / 轨道活动` 分层；
2. 每层至少取 3 个；样本很多时取该层的 10% 且最多 10 个；
3. 额外纳入每层最接近阈值的边界样本；
4. 样本不足时标为 `INSUFFICIENT_DATA`，不能假装已经学会；
5. 人只对这些样本做 `accept / reject`，并留下 reviewer、时间、审核材料 SHA 与是否试听。

这样“代表性”是可复现的，不是 Agent 随意挑几个看起来容易的片段。

### 4.3 标签归纳后，Agent 如何处理全部候选

Agent 只能生成下面四种机器结论，不能把其中任何一种写成裸的 `accept`：

```text
machine_proposed_accept     # 机器认为可剪，但仍只是预测
machine_proposed_reject     # 机器认为保留
auto_cut_eligible           # 另满足已启用自动剪辑政策；可在最终 QC 后走政策授权交付路径
human_review_required       # 必须回到人审
```

每一条机器结论必须带：

- `policy_id`、`policy_sha256`、规则/模型版本；
- 本轮校准样本和 `calibration_report` 的 SHA；
- 预测分数、阈值、候选类别、边界和跨轨安全结果；
- 产生该结论的时间和确定性随机种子。

`machine_proposed_accept` 已足以进入 `machine_assisted_draft`；它只适用于未人工决定、
通过安全门且属于低风险类别的候选。`auto_cut_eligible` 不是试听草稿的前提，而是
“这条机器剪口是否可能在最终听审后走政策授权交付”的额外判定。当前只有少量逐项
案例，能够做的是“规则/排序假设”和自动试听草稿，不足以证明一个模型已经会判断整集内容。

### 4.4 人工标签如何变成“固定记忆”

“固定记忆”分为本期确定记录和跨期可复用经验，不能混成一件事：

1. **本期确定记录**：每个真人 `accept / reject` 立即写入本期 `human_decisions.json`；它是本期校准和人审版 EDL 的唯一真人依据。每项可选 `feedback`（最多 500 字）必须原样保留。
2. **本期机器归纳**：Agent 读取这次标签后，对所有尚未人工决定的低风险候选写出 `prediction_manifest.json`。这只影响本期的机器辅助试听版。
3. **跨期经验提案**：最终 QC 后，Agent 将可追溯的候选特征、真人结论、返工/QC 结果和 case_id 写成 `experience_proposal.json` 与经验卡；真实音频仍留在本地 run。它可以进入下一次 Challenger 的案例检索或规则分析。

Agent 不得把一集标签直接改写 Champion、`SKILL.md`、生产规则、阈值或 `autocut_policy`。经验提案只有经过冻结 benchmark、独立复核和人工晋升，才会成为新的活动经验快照；这样“记住人工偏好”不会悄悄变成“机器替人批准”。

当本期有新的“听感偏好”时，Agent 可以提出对 `当前剪辑偏好快照.md` 的 Challenger 修订，但它同样需要明确的证据、冻结 benchmark、独立复核和人工晋升。偏好快照不是在线学习开关，更不能让已知高风险类型跳过人审。

## 5. 两份 EDL，防止概念混乱

每一集在有机器预测时，Agent 必须同时写两份文件：

```text
human_approved.edl.json
  = 仅包含逐项 human_accept 的剪口；窄例外是同一 run 绑定的 human_whole_episode_audition scope

machine_assisted_draft.edl.json
 = human_accept
  + 通过跨轨/声学安全门、且本轮校准后为 machine_proposed_accept 的低风险机器剪口
```

两份 EDL 都使用整数 sample；全局语义剪口同步作用于全部语音轨；源轨 SHA、候选 SHA、审核/政策 SHA 必须可追溯。

machine_assisted_draft.edl.json 必须在每个机器剪口上注明 decision_provenance=machine_prediction，以及规则/模型版本、校准报告 SHA、分数、阈值与 policy 状态。它不是批准 EDL，不能改名、不能混入 human_approved，也不能由 Agent 伪造 reviewer。`auto_cut_eligible` 若存在，也只能作为机器剪口的附加政策字段，不能改变其机器来源。

整片试听批准 scope 的 EDL 必须单独写 `approval_mode=human_whole_episode_audition`，并把 scope 的 SHA、
试听母带 SHA 与源 EDL SHA 写入。它只覆盖该期已经试听的冻结音频；任何新候选、新边界、新音乐或
重新渲染都会使该 scope 不再适用。

## 6. 自动剪辑政策（autocut policy）

如果负责人希望以后真的做到“丢进音频就出完整成片”，需要先一次性签署一个可回滚的自动剪辑政策。它不是一句“以后都自动剪”，而是一份机器可验证的授权文件。

最小字段：

```json
{
  "policy_id": "minglue-low-risk-autocut-v1",
  "status": "draft | active | revoked",
  "authorized_by": "负责人或 Mentor 的明确姓名",
  "authorized_at": "ISO-8601 时间",
  "episode_scope": "可用的节目类型/范围",
  "allowed_reason_keys": ["只允许明确低风险的类别"],
  "forbidden_reason_keys": ["长停顿、语义重复、说错重来、串音、瞬态事件等"],
  "max_cut_seconds_each": 0.0,
  "max_total_auto_cut_seconds": 0.0,
  "required_cross_track_result": "SAFE",
  "required_score_threshold": 0.0,
  "required_calibration_evidence": "冻结 benchmark / 抽样通过条件",
  "final_qc_required": true,
  "expires_at": "ISO-8601 时间或 episode 数上限"
}
```

当前状态：autocut_policy = NOT_APPROVED。因此，后续 Agent 完成本轮校准后，仍应把通过安全门的低风险 machine_proposed_accept 剪入 machine_assisted_draft，以满足“所有机器判定可剪的候选都进入试听”的需求；但它们不能进入 human_approved，也不能称为发布候选。有效政策只决定这些机器剪口能否在最终人工听审后被授权进入发布候选。

已启用的 `editing-policy-guards-v1` 不等于本节所说的 autocut policy。它已经进入未来 `start` 入口，但只具有两种安全动作：`auto_preserve`（阻止已证实的完整词/词边界误报进入审核和剪口）与 `human_review_required`（把高风险或没有犹豫特征的重复升级人工）。每期冻结的 `policy_application.json` 必须可回溯到规则文件 SHA；它不能创建任何语义删除。

当政策被正式启用后，负责人已经明确批准的是“在这个范围、这个版本、这个阈值下的低风险自动剪辑”，而不是允许 Agent 任意扩大删除范围。政策以外的候选始终回到人工审核。

## 7. 固定片头片尾音乐模板

固定音乐不是每集让 Agent 猜的参数，而是交付配置的一部分。

### `reference-linear-v1`

- 素材：同一首已授权音乐，片头和片尾均使用；
- 片头：`0–5.000 s` 纯音乐；节目语音在 `5.000 s` 精确进入；音乐从 `5.000 s` 线性淡出，到 `16.000 s` 消失；
- 片尾：节目语音结束前 `22 s` 开始同一首音乐；音乐在这 `22 s` 线性淡入；语音结束后保留约 `37.976 s` 纯音乐；
- 语音剪口的短 crossfade 与音乐淡入淡出是两套独立参数，不能混用；
- 当前固定的是素材和时序形状。主麦 automix、音乐/语音增益、ducking、LUFS、true peak、码率仍需要 Mentor 冻结；Agent 不得自行编造发布规格。

普通 `start` 入口只允许这个模板。开始、恢复和渲染前必须校验 plan、`requirements_checkpoint.json` 与 `main/orchestrator/music_templates.json` 一致；任何一处不是 `5.000 s` 人声进入即停止。v12 模板只作历史比较，不能因聊天摘要或旧文档被重新选为当前模板。

### `EP04-v12-crossfade-audition`（仅供对比试听）

- 片头约 15 秒音乐，以约 3 秒 equal-power 交叉进入语音；
- 片尾以约 3 秒 equal-power 交叉进入音乐，保留约 15 秒尾乐，最后约 3 秒淡出；
- 它记录最新试听偏好，不替换 `reference-linear-v1`。最终 QC 要把选择的 `music_template_id` 写入 `music_manifest.json` 与 `final_listening_decision.json`。

v12 试过两遍 `loudnorm`（工作目标 `-16 LUFS / -1 dBTP / LRA 11`），但目标和一次实测不等于已冻结的发布规格。Agent 可以把它作为 QC 报告的候选目标，不能据此自行宣布“发布可用”。

如果音乐文件 SHA 变化、长度不符合配置、音乐解码失败，Agent 必须 `BLOCKED`，不能换一首相近音乐。

## 8. Run 身份、文件名与 manifest 一致性

每次运行必须先冻结一份 `run_identity.json`。最小字段为：

```json
{
  "episode_id": "EP04",
  "run_id": "EP04-v12-20260813-1520",
  "contract_version": "delivery-contract-v1.2",
  "run_dir_rel": "main/runs/EP04/EP04-v12-20260813-1520",
  "preference_profile_id": "editing-preference-profile-v12-draft",
  "preference_profile_sha256": "<frozen SHA256>"
}
```

所有本次生成的 `plan`、候选、审核包、决定、预测、两份 EDL、render manifest、
音乐 manifest、QC 和交付报告都必须写入相同的 `episode_id` 与 `run_id`。本次 run
内部引用一律保存相对于本次 run 的路径；原始输入可另外记录原始来源路径，但必须同时
记录 SHA，且不得把上一台机器或上一轮 run 的绝对输出路径当作本次产物。

推荐输出命名为：

```text
<run_id>.human_approved.master.wav
<run_id>.human_approved.master.mp3
<run_id>.machine_assisted_draft.master.wav
<run_id>.machine_assisted_draft.master.mp3
```

在渲染前和交付前，Agent 都要检查目录名、`run_identity.json`、manifest 的
`episode_id/run_id`、EDL 文件名、输出文件名及其父路径是否一致。任一处仍写成旧 run、
旧 episode 或失效绝对路径时，状态必须为 `BLOCKED: RUN_IDENTITY_MISMATCH`；可以保留
音频供工程排查，但不能把它当作本期交付证据，更不能只靠改报告文字修复。

当前入口通过 `delivery_orchestrator.py verify --run-dir <run>` 复核输入/音乐 SHA、双 EDL、
render manifest、最终决定与反馈包的引用链；验证失败不得继续称为交付 PASS。

## 9. 每一期必须交付的文件

```text
main/runs/<EP>/<version>/
├── run_identity.json
├── plan.json
├── input_manifest.json
├── processing_manifest.json
├── analysis_manifest.json
├── analysis_reuse_manifest.json   # 复用既有 ASR/semantic transcript 时必须有
├── all_candidates.json
├── frozen/candidate_rules.json
├── calibration_source.json
├── review_packet.md
├── review_decisions.template.json     # 仅模板，不能当真人决定
├── review_bundle/
│   └── event_routes.json              # 仅未来新包的历史参考侧车；可缺省
├── human_decisions.json
├── calibration_report.json
├── prediction_manifest.json
├── preference_application_report.json
├── experience_proposal.json
├── human_approved.edl.json
├── machine_assisted_draft.edl.json
├── render_human_approved/             # 如已渲染
│   └── transition_qc.json              # 仅重点复听排序，不是自然度或批准判断
├── render_machine_assisted_draft/
│   └── transition_qc.json
├── music_manifest.json
├── qc_report.json
├── final_listening_decision.json
├── feedback_bundle.json
└── DELIVERY_REPORT.md
```

`DELIVERY_REPORT.md` 必须用人话回答：

1. 这次输入是什么，是否通过共同时间线检查；
2. 一共发现多少候选，多少被安全阻断；
3. 人审了哪些高风险/代表性样本，结论是什么；
4. 机器对其余候选做了什么判断，依据哪个政策；
5. 人审版和机器辅助版各剪了多少段、多少秒；
6. 片头片尾是否按固定模板加入；
7. QC 是否通过，仍有哪些听感/发布风险；
8. 输出是“试听草稿”“人审批准版”还是“发布候选”，不能模糊表述。
9. run 身份、文件名和 manifest 引用是否全部一致；若否，明确写 `BLOCKED`，不交付。

## 10. Agent 的停止条件

任何一个条件成立时，Agent 只能写 `BLOCKED` / `HOLD`，不能自己找补：

- 输入、安全、哈希、时间线或工具契约不一致；
- run 目录、`run_identity.json`、文件名或任一 manifest 的 episode/run 身份不一致；
- 高风险候选还没有人审；
- 高风险候选数量超过本期 `review_budget`；低风险层若因剩余预算无法形成有效代表样本，则该层保持未剪并标为 `human_review_required`，不得外推；
- 校准样本不足、验证失败或预测分数不稳定；
- 不存在有效 `autocut_policy`，却有人要求把机器预测称为发布批准；
- 音乐、QC、整片听审或发布规格未通过；
- 需要做“离题”“整句语义重复”“说错后重说”等内容级删剪，但没有适用的审核或授权政策。

## 11. 当前实现差距

已实现的最小入口：新建 run、输入/音乐 SHA 与共同 timeline 校验、DeepFilterNet、P0、V13
filler/global-pause 候选、高风险全审/低风险代表样本、20 项审核预算 fail-closed、无前端
`review_packet.md`、校准报告、prediction manifest、双 EDL、双渲染、固定音乐、QC、run identity
validator、渲染后剪口客观排序、DELIVERY_REPORT 和两个可恢复人工闸门。现有临时 PCM fixture 契约测试中，完整 fixture
已跑通 `start → 审核包 → 决定 → resume → 双渲染/QC → record-final → verify`；该完整 fixture 对
DeepFilterNet 与 ASR 使用本地假 adapter，故只证明编排与证据链，不替代真实模型/真人听审。EP04 还以
“整片试听批准 scope”实际跑出一个身份一致的交付 run。

仍未完成或尚待独立真实验证：

- 常规 `start → review_packet（或本地审核页）→ resume → record-final` 尚未在一集新的真实 WAV 上完整独立复核；它需要显式传入或在 PATH 找到受审计的 FFmpeg，预检失败会在创建 run 前 BLOCKED。当前 EP04 QC 已使用一个 SHA/版本留痕的本地二进制重测，但它尚未成为常规部署依赖；
- 本节的 ASR 复用门已经接入 `delivery_orchestrator.py`，并在 `EP04-v20-20260814-1617` 真实验证：候选-only 运行生成了 `analysis_reuse_manifest.json`，没有重复调用 DeepFilterNet 或 faster-whisper；同一份 P0 报告有多个 semantic run 时已按契约 fail closed。当前待审指针以《当前项目进度》的 `CURRENT_DELIVERY_FACTS` 为准，即 `EP04-label-loop-v1-20260815-1805`；v17–v19 只保留为被替代/失败的工程证据；
- 自动候选范围目前只有连续口癖/紧邻重复与全轨共同长停顿；串音、咳嗽/碰麦、说错重来、语义重复和离题仍未接入；
- source-track gate 的常规渲染、主麦 automix、音乐 ducking、发布响度/编码规格与 Mentor 发布批准；
- 已签署且 benchmark 通过的低风险 `autocut_policy`；
- 18 项 Tool 注册表与实际调用 adapter 的统一及完整失败恢复矩阵。

EP04-v12 的历史实验 run 仍混有高风险自动处理和旧 run identity，不能直接改成未来生产脚本；新的
EP04 人工批准 run 只证明用户批准了其精确冻结试听结果，不证明这些规则已经可以跨期自动使用。

> `EP04-v22-20260815-1315` 是历史工程记录。当前审核 run、规则与数量只能读取《当前项目进度》的 `CURRENT_DELIVERY_FACTS`；截至 2026-08-16 为 `EP04-label-loop-v1-20260815-1805`，不能由本文件的旧脚注覆盖。
