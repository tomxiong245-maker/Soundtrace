# F06 EDL、渲染、混音、固定音乐与 QC

## 功能目的

把经过明确来源确认的剪口同步应用到所有语音轨，加入固定片头片尾音乐，输出可试听或可发布的版本，并让每个版本的来源和限制一眼可见。

流程位置：

~~~text
human_approved / machine_assisted_draft EDL
→ 全轨同步剪切或源轨 gate
→ 语音 crossfade
→ 主麦/混音
→ 固定片头片尾音乐
→ 响度、峰值、编码、剪口和整片听审 QC
~~~

## 两种 EDL、三类输出状态

### human_approved.edl.json

只允许 human_accept 产生的全局语义剪口。每个区间使用整数 sample，并同步作用于全部语音轨。它对应“人审批准版”，但在整片听审和发布规格通过前仍不能叫发布候选。

窄例外：负责人已经整片试听并明确批准一个 SHA 冻结的成片动作集合时，EDL 可写
`approval_mode=human_whole_episode_audition` 并关联 `human_approval_scope.json`。该 scope
必须绑定原始试听 WAV/MP3/EDL、音乐和动作集合，且保留每个旧动作的机器或历史 provenance；
它不伪造逐项 `human_accept`，不生成训练标签，也不成为跨期政策。

### machine_assisted_draft.edl.json

包含人审采用项，以及通过跨轨/声学安全门、并经本轮校准判为 machine_proposed_accept 的低风险机器剪口。每个机器区间必须写 decision_provenance=machine_prediction、规则/模型版本、校准报告 SHA、分数、阈值和 policy 状态。无论政策是否已启用，它都应渲染为机器辅助试听草稿；没有有效政策时不得冒充人审批准版或发布候选，有效政策也不会改变机器 provenance。

串音 gate、碰麦/咳嗽等只处理指定源轨且不改变节目时长的动作，必须在 EDL 和 manifest 中明确标为 source_track_gate，不能悄悄混入全轨语义剪口。

两份 EDL 都应分别渲染并使用清晰输出名：

```text
<run_id>.human_approved.master.{wav,mp3}
<run_id>.machine_assisted_draft.master.{wav,mp3}
```

文件名或目录中的 `human_approved` 只说明剪口来源是人审，不能代替整片听审、
音乐、规格和负责人决定。最终交付状态只能是：

- `human_approved_delivery`：只交付人审版，且本期最终人听审与已声明的本地 QC 范围已通过；它不等于已经执行外部发布，若 Mentor 的跨期发布规格尚未冻结则仍不得称为 `publish_candidate`；
- `policy_authorized_delivery`：机器版中的每条机器剪口另有有效政策覆盖，且最终人听审与
  发布规格已通过；
- `REWORK` / `HOLD`：任一内容、听感、音乐、QC 或授权门未通过。

### 单机器试听草稿的窄例外（2026-08-17）

负责人可对一集、一次明确授权生成**仅机器试听版**：该 run 只写 `machine_assisted_draft.edl.json`、`render_machine_assisted_draft/`、单变体 transition QC 和 `machine_draft_qc.json`，不补造空的 `human_approved` EDL 或音频来骗过双渲染检查。其状态必须为 `MACHINE_ASSISTED_DRAFT_RENDERED__NOT_HUMAN_APPROVED`，自动 QC 也必须标明“单变体试听范围”。所有剪口的 EDL action 必须带授权 SHA、历史证据、当前边界来源和 `machine` provenance。

这条例外不改变正常双 EDL/双渲染交付契约；它只服务于负责人已经明确授权的本地试听。`verify` 对这种 run 仍会因没有人审版而不适用，不能把该失败描述成音频或机器草稿 QC 失败。

## 固定片头片尾音乐

音乐是节目配置，不是每集临时猜测。当前固定素材：

- 文件：音频参考库/raw material/第三集/片头片尾music.mp3
- SHA-256：3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83
- 模板：reference-linear-v1

当前硬性时序（不是可选试听参数）：

- 片头 `0–5.000` 秒纯音乐；
- 节目语音在 `5.000` 秒精确进入，音乐从 `5.000` 秒线性淡出，到 `16.000` 秒消失；
- 片尾在节目语音结束前 `22.000` 秒开始同一首音乐并线性淡入；
- 语音结束后保留约 37.976 秒纯音乐尾段；
- 语音剪口 crossfade 与音乐淡入淡出是两组独立参数。

正常生产入口必须读取并冻结 `main/orchestrator/music_templates.json`，把完整 timing 与 SHA 写进 plan 和 `requirements_checkpoint.json`。渲染前再次核对 checkpoint、plan 与 canonical definition；`voice_start_seconds != 5.000` 时必须 fail closed。历史 v12 模板只能由狭窄的历史恢复路径使用，普通 `start` 不得选择。

当前只冻结素材、SHA 和时序形状；音乐/语音增益、ducking 仍是渲染参数层的实验值，Agent 不得自行把实验参数升级为规范。

**2026-08-17 新增 · automix-v1 Challenger**：用户 2026-08-17 明确指令"第一条（主麦 automix）赶紧做"。新 Challenger `稳定生产/challengers/automix-v1/` 用 20 ms 帧 RMS 主导判定 + -12 dB ducking + 30 ms crossfade + ffmpeg loudnorm + mp3 encode，纯 Python stdlib + ffmpeg（不依赖 numpy / pyannote）。EP03 前 5 分钟真跑（`main/runs/EP03-AUTOMIX-v1-20260817-1227/`）：Tr1 primary 35.75% / Tr3 primary 50.49% / ambiguous 13.75%；输出 mp3 时长 342.976 s 精确匹配 reference-linear-v1 三段时序；Integrated -24.9 LUFS（目标 -22.2，单遍 loudnorm 偏差 1.7 LU 待双遍修正）；TP -4.3 dBFS 安全。通过 `tool-orchestrator-v2` registry 挂 `automix-2track-v1` adapter；不改 `assemble_program.py`。当前主导轨判断仍是能量启发式；等 pyannote speaker diarization 装好后可切换为 diarization-driven 主导判断。

**2026-08-17 · 发布规格已按 EP03 Mentor 成片冻结**：用户 2026-08-17 明确指令"EP03 就是例子"，把 `音频参考库/成品/EP03.mp3`（SHA `8dd95ac3…353f4`）实测值作为 `reference-linear-v1` 的发布规格目标：

- 整片 Integrated Loudness：`I = -22.2 LUFS`（容差 ±1.0 LU）
- True Peak：`TP = -0.1 dBFS`（EP03 mentor 已触及削波线；发布层若追求安全应下推到 ≤ -1.0 dBFS，作为 `release_target_true_peak_dbfs_safety_floor` 记录）
- Loudness Range：`LRA = 7.9 LU`
- 音乐段独立响度：`I(music) = -14.8 LUFS`；音乐相对语音高约 `7.4 LU`
- 容器/编码：`mp3 / 192 kbps / 48 kHz / stereo`

完整数值在 `main/orchestrator/music_templates.json` 的 `reference-linear-v1.release_*` 字段；证据在 `main/runs/RELEASE-SPEC-FROM-EP03-20260817-1204/release_spec_evidence.json`。**注意事项**：此值来自单期 EP03 mentor 实测，用户口径为 example，不要求跨期泛化；未来引入新 mentor 参考必须建立独立 audit，不静默替换。`music_gain_db -12.0` 是渲染时对音乐轨的衰减参数，与 `release_music_voice_gap_lu 7.4` 是两回事（前者渲染参数、后者最终响度差观察值）；渲染实现落地时必须验证两者对齐。

EP04 v12 还试过另一种 `EP04-v12-crossfade-audition` 结构：约 15 秒片头、3 秒 equal-power
交叉进入语音；片尾 3 秒交叉进入、约 15 秒尾乐且最后 3 秒淡出。它是最新的试听偏好，
不是对 `reference-linear-v1` 的静默替换。最终 QC 必须明确记录选用的 `music_template_id`；
在此之前可做对比试听，但不能把 v12 结构、音乐增益或响度目标称为发布规范。

v12 的两遍 `loudnorm` 工作目标为 `I=-16 LUFS / TP=-1 dBTP / LRA=11`。在正确身份的
EP04 交付 run 中，2026-08-13 已用 `qc_recheck.json` 记录 SHA/版本的本地 FFmpeg 对冻结母带
重测为 `-16.54 LUFS / -0.86 dBTP`；因此 true peak 比工作目标高 0.14 dB。这是一次真实的客观
QC 观察；发布规格仍需 Mentor 冻结，不能因通过自动身份校验或整片听审就标记“发布可用”。

如果素材缺失、SHA 不一致、解码失败或长度不满足模板，必须 BLOCKED，不得自动换音乐。

每次渲染的 `music_manifest.json` 至少记录素材 SHA、模板版本、片头/片尾音频在输出
时间线中的开始与结束 sample、语音开始/结束 sample、所有 fade 参数、音乐与语音的
实际增益/ducking 参数和渲染工具版本。尚未冻结的参数可以记录为 `experiment`，但输出
状态必须维持试听草稿或 HOLD。

## 已验证事实

- EDL 使用整数 sample；经确认的全局区间已在合成双轨、EP03 和 EP04 三轨真实运行中同步剪切。
- 审核材料、源轨和 EDL 哈希不一致会被拒绝；参数变化或损坏缓存不得静默复用。
- EP04 v4 已生成含授权音乐的 WAV/MP3；其约 15 秒片头、约 15 秒片尾是实验版本，未按当前 reference-linear-v1 模板冻结。
- EP04 v4 的 86 个同步剪切、92 个源轨 gate 和 122 条机器推断均只能标为 machine_assisted_draft；不能称为 human_reviewed、human_approved 或 publish_candidate。
- EP04 v12 在真实原始三轨上试跑了 50 个长停顿、410 个词表口癖、30 个瞬态事件、92 个 source-track gate、交叉音乐和两遍 loudnorm；它自动处理高风险项，且历史 manifest 仍写 `EP04-v4` 并引用旧绝对路径。因此历史 run 仍只是工程/偏好实验。用户整片听审后，新 `main/runs/EP04/EP04-human-approved-v12-20260813-152847/` 以新的 run identity、双输出名和 whole-episode approval scope 封装了精确相同的 WAV/MP3；这不追溯生成逐项人审标签。

## QC 必须分层

### 自动 QC

- 所有输入/输出 SHA、时长、采样率、位深、声道和 sample timeline；
- EDL 与 review/prediction manifest 一致；
- `run_identity.json`、目录名、EDL 文件名、输出文件名及各 manifest 的
  `episode_id/run_id` 完全一致；本次产物引用必须是相对本次 run 的路径；
- 剪口没有越界、重叠或整数 sample 错误；
- 常规 `resume` 的两份渲染必须各写一份 `render_<variant>/transition_qc.json`：它绑定当前 EDL、render manifest、实际用于分析的 WAV 和各自 SHA，按电平、频谱和边界波形异常安排重点复听；缺失、哈希不一致或时间线映射失败会阻断正常自动 QC / 完整 `verify`。它**不**判断语义、自然度或是否通过剪辑，也不授权自动 `accept`；
- 片头片尾素材 SHA、淡入淡出和节目语音进入点；
- WAV/MP3 解码、true peak、响度测量和编码参数。

### 人工 QC

- 高风险剪口逐项复听；
- 优先复听每份 `transition_qc.json` 排在前面的剪口；低分只表示“本轮客观指标不突出”，绝不代表自动通过；
- 片头片尾音乐是否盖住人声、进入/退出是否自然；
- 串音 gate、咳嗽/碰麦处理是否造成声像或空间突变；
- 整片听感、无候选区域抽查和节目结尾是否完整。

未完成人工 QC 时，输出名称只能是试听草稿、技术样片或 HOLD；不能写发布候选。

## 当前状态

### 已验证事实

- `EP04-machine-assisted-draft-20260817-002` 已按负责人授权的低前置审核规则实际完成单机器试听渲染：3 个低风险同步剪口（`C007 / C034 / C044`）、3 个机器保留、6 个留待后续迭代；它没有生成或冒充 `human_approved` EDL。
- 该 run 的三条 edited stem、speech mix、双声道 WAV、192 kbps MP3、EDL、机器预测、transition QC、`machine_draft_qc.json` 与报告均已落盘；自动 QC 为 `PASS`，WAV/MP3 均为 48 kHz stereo，时长 `3313.593 s`。
- 该 run 使用 `reference-linear-v1`：前 5 秒纯音乐，人声精确在 5 秒进入，5–16 秒淡出；片尾在语音结束前 22 秒淡入，语音结束后保留约 37.976 秒尾乐。它仍只是一份本地试听草稿，下一道门是负责人整片听审。
- EP04 旧去重版本有 6 个真人批准区间，已渲染三条 edited stem、speech mix WAV 和 192 kbps MP3；它是技术试听版。
- EP04 v4 另有 86 个同步剪切、92 个源轨 gate、约 30.136 秒共同删除，并生成含音乐 WAV/MP3；其决定来源是机器阈值推断，不是逐项真人审核。
- EP04 旧去重/v4 输出仍缺主麦 automix、音乐 ducking和 Mentor 冻结的发布规格；EP04 v12 已由用户整片听审并以 scope 记录为本地 `human_approved_delivery`，但这不替代跨期发布规范或其它候选区域抽查。
- 已发现若干历史 EP04 试验 run 存在“新目录、旧 `EP04-v4` manifest 身份、旧绝对路径”
  的 provenance 不一致；它们只能用于工程排查，不是可交付证据。此后身份检查必须
  fail closed。
- EP04 v12 的历史 run 没有被改写；新交付 run 已检查输入/输出 SHA、WAV/MP3 可读性、采样率与
  run identity，并在 `qc_recheck.json` 中记录了对冻结母带的本地 FFmpeg 重测、二进制 SHA 与版本。
  实测值为 -16.54 LUFS / -0.86 dBTP；音频和审批决定均未改写。该二进制尚未完成常规生产所需的
  来源/许可证/跨机部署审计，且 v12 工作目标尚未冻结为发布规格。
- 正常 `resume` 现已在双渲染与 `music_manifest.json` 完成后、进入 `FINAL_QC_REQUIRED` 前自动生成两份 `transition_qc.json`；合成三轨夹具已验证双报告、QC 索引、完整 `verify` 与“删报告即 fail closed”。它尚未在 EP04 v20 真实音频上运行，因为 v20 仍处于人工审核前，没有 EDL 或渲染产物。

### 尚未完成

- 常规新节目已实现双 EDL/双渲染目录和 provenance 契约；仍需独立真实全程复核与更多候选类别接入；
- reference-linear-v1 的独立渲染测试与整片听审；
- 主麦 automix、音乐/语音增益、ducking、LUFS、true peak、MP3 规格；
- 无候选区域抽查和最终发布批准门。

## 安全边界

- 不批准、过期、哈希不一致或政策外的 EDL 不得进入发布渲染。
- 目录名、`run_identity.json`、EDL、输出文件名与 manifest 任一身份不一致时，必须
  `BLOCKED: RUN_IDENTITY_MISMATCH`；不得仅改摘要或复制旧 manifest 后继续交付。
- 全局语义剪口必须同步作用于全部语音轨；源轨 gate 不得冒充全局删剪。
- 原始音频和历史成片不覆盖；每次渲染写入新版本目录。
- 客观没有硬跳变不等于主观自然；两者都要记录。

## 验收标准

- 两份 EDL 的来源和输出目录明确可区分；
- run 身份、相对路径、文件名、EDL 和所有 manifest 的 episode/run 一致性可自动验证；
- 音乐模板、素材 SHA 和每个淡入淡出参数可重现；
- 自动 QC 通过后仍完成高风险剪口与整片人工听审；
- Mentor 冻结发布规格后，WAV/MP3 和 loudness/peak 报告符合规格；
- 失败续跑、缓存损坏、参数变化和音乐校验失败均 fail closed；
- 只有完成上述闸门才允许把输出标为 publish_candidate。

## 实现与证据入口

- 渲染脚本：稳定生产/scripts/、端到端学习剪辑/代码/
- N 轨渲染 Challenger：稳定生产/challengers/review-product-v1/
- 音乐 Challenger：稳定生产/challengers/intro-outro-music-v1/
- 运行产物：main/runs/<EP>/

## 边界精修在渲染层的执行（2026-08-15 加入）

从 2026-08-15 起，普通 `start` 入口在候选冻结后、`build_calibration_package` 前自动调用 `main/orchestrator/snap_candidate_boundaries.py`：

- **输入**：候选的原始 `start_sample / end_sample`（来自 ASR 词级时间戳，误差 20–50 ms）
- **算法**：在 ±150 ms 内以 20 ms 窗口扫 RMS 找能量最低点，再在 ±5 ms 内找零交叉点
- **输出**：`calibration_source.json` 和 `all_candidates.json` 的候选边界更新为精修后的 sample 值；原边界保留在 `start_sample_original / end_sample_original`；每条候选加 `boundary_snap` 字段说明方法和移动距离

**对渲染的影响**：
- Preview（`build_mvp_package.py::render_ntrack_preview`）读的候选边界已经是精修后的 → mentor 在前端听到的 A/B 剪口自然
- Human_approved EDL / machine_assisted_draft EDL 的整数 sample 也来自精修后的边界 → 最终成片剪口自然
- **crossfade 100 ms 保持不变**（rendering_gate.speech_cut_crossfade_ms）；边界精修独立于 crossfade，两者叠加使剪口既在静音区又有平滑过渡

**验证数据**：EP04 v23b 12/12 候选全部精修，平均边界移动 204 ms（个别达 300 ms）。C007「呃」精修后边界 RMS -58.9 dBFS（静音区），C042「也是」精修后边界 RMS -30.0 dBFS（仍在语音上，`predict_cut_artifact` 因此标 BLOCK）。

详见 F04 §"边界精修与剪口质量预测"。

### 主麦自动混音（main-mic automix）

这是渲染前后的**电平控制**，不是剪辑判断：系统根据说话活动/轨道能量等证据，保留主讲话轨的稳定音量，并适度压低收到同一声音的其他物理麦（串音）。它不改变 sample 时间线、不删除句子、不生成 EDL；与 source-track gate、语义全轨剪切分开记录。若压得过快会出现 pumping，压掉“嗯/对”等回应会损失自然感，所以当前只能作为待验收的保守混音 Challenger，必须有人工 A/B 和回滚参数。
