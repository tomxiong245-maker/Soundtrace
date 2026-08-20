# F03 ASR、VAD 与说话人

## 2026-08-17 新增 · speaker-diarization-v1 Challenger（真说话人识别）

**用户 2026-08-17 明确指令**："第四条主麦混音和跨轨归属你赶紧去做，能用外部的库用外部的"。

当前 `primary/bleed/ambiguous` 是段落能量启发式，不是真人物识别。新 Challenger 引入外部 pyannote-audio 3.4.0：

- 目录：`稳定生产/challengers/speaker-diarization-v1/`
- 审计：`audits/pyannote-audio-3.4.0.md`（MIT 商用可 · M3 强制 CPU · 中文 AISHELL-4 DER 12.2%）
- 锁定：`pyannote.audio==3.4.0` + `pyannote/speaker-diarization-3.1` pipeline
- 骨架 `assign_word_speakers.py`：pyannote turn + faster-whisper word timestamp 时间重叠归属（照抄 WhisperX 逻辑）→ 每词 `speaker_id` + `speaker_assignment_confidence`
- 通过 `tool-orchestrator-v2` registry 注册 `speaker-diarize-v1` adapter（当前 SKELETON）

**限制**：pyannote 权重需 HF token + accept license 一次性下载；不改 activity 启发式（保留原字段作 additive）；不走 MPS（issue #1337 wontfix）。

## 功能目的

给后续候选和审核提供可靠的文字、词级时间戳、语音区间、说话人/轨道归属及重叠信息。它解决“机器是否听准”，不决定“内容是否该剪”。

流程位置：`降噪 → ASR/VAD/说话活动 → 候选生成`。

## 输入与输出

输入：时间线安全的任意 `N` 条单声道音频；当前新增重点是 3 轨。轨道身份来自输入清单或物理文件，不靠模型猜“男/女”。

标准输出至少包含：

- 词文本、开始/结束 sample、置信信息和原始引用；
- VAD speech intervals；
- speaker intervals 或明确的 unknown/ambiguous；
- 重叠、漏识别和静音幻听信息；
- 工具、模型、配置、运行时间、内存和 SHA。

推荐把三个概念分开保存：

- `track_id`：物理轨道，流程必需且全程稳定；
- `speaker_id / display_name / role`：人物与节目角色，可由人工或可靠映射补充；
- `gender`：当前不作为必需字段，也不作为主讲/串音判断依据。

## 当前状态

> 当前基线指针：ASR 只保留外部 faster-whisper small。FunASR/MLX/SenseVoice 的未验证 Challenger 已移出项目，归档清单见 `统筹全局/ASR-当前基线与归档.md`；本文件中的旧多引擎段落仅作历史设计证据，不能作为普通入口配置。

### 架构决定（2026-08-14）

- ASR、VAD 与说话人能力一律来自可审计的**外部上游项目**；项目不训练、不维护自研声学/转写模型。
- 本项目只做四件事：把本地多轨 WAV 交给上游、原样留存上游输出、验证是否真的有可用的词级时间戳、转换到统一 schema 供审核与剪辑使用。
- 当前实际基线是外部 faster-whisper small。FunASR Paraformer 是中文 Challenger，不是已验证的生产替换；只有在本机离线实跑、模型许可证与 SHA 落盘、并完成同一 mini-gold 对照后才可晋升。

### 已验证事实

- 当前 baseline 为外部 faster-whisper small / CPU int8，已对 EP03 双轨产出词级结果。
- 现有 `primary / bleed / ambiguous` 来自 segment 级双轨能量差，并复制到词；它不是男/女声识别，也不是真实说话人模型。
- P0 Challenger 已落盘 schema、adapter、scorer、测试代码和依赖审计骨架。
- 当日最小 P0 已支持直接传入任意数量 WAV、专业录音常见的 24-bit PCM extensible WAV，以及可配置节目专名提示。
- 三轨 20 秒兼容夹具已真实转写：3/3 有词、0 非法时间戳、RTF 约 0.18–0.21。它证明工程可运行，不证明字幕准确率。
- EP04 的三条真实 Zoom H6 WAV 已通过输入门并完成 faster-whisper small 转写：三轨时长各 3,272.7 秒，原始输出分别有 12,848 / 12,526 / 7,732 个词。原始输出保留不动；仅 79 个零长度 token 在 normalized 副本中按“最多 20 ms、不越过下一词”的确定性规则修复，三轨非法时间戳均为 0。该结果证明真实三轨能进入下游，不证明内容、专名、说话人或重叠已经识别准确。
- 当前生产输入的 `EP04-v13-20260813-2002` 再次完成三轨词级转写：12,467 / 11,853 / 6,732 个词，非法时间戳均为 0。2026-08-14 负责人抽听约 `01:00–01:30` 时确认中文主体准确、英文术语仍有问题；在 `47:37.5–48:10.5` 的中英混说术语密集片段（含 `MCP`、`DeepMiner`、`OpenClaw`、`CC`、`Codex`、`agent`）确认识别良好。该抽听使转写可用作口癖/长停顿定位和人工审核底稿，仍不构成人工 gold、整集准确率、专名准确率或引擎胜负结论。
- V13 transcript 的标准底层产物仍是不可修改的词级 JSON（词、sample 边界、置信/原始引用）。2026-08-14 已新增并归档并列的 `semantic-transcript-v1` 句子/分句上下文层：它给每个原始 `word_id` 增加 `sentence_id`、`clause_id`、句/分句内位置、边界理由/置信度和句/分句 sample 范围。该层不是可靠的可读稿标点模型，且绝不改写原词、时间戳、候选或 sample 剪口。
- 中英混说存在英文子词拆分和候选边界落在词内的问题。EP03 `C022` 中 `feature` 被拆成 `fe + ature`，候选又从 `ature` 词内开始，页面因此把 `ature特别特` 一并标红；这同时是转写归一化问题和边界对齐问题，不应直接归因于 LLM 缺失。

### 已验证的模型与成果复用规则（2026-08-14）

EP04 之前实际使用的就是外部 `faster-whisper small`：

- 模型快照：`Systran/faster-whisper-small`，revision
  `536b0662742c02347bc0e980a01041f333bce120`；
- 运行时：`faster-whisper 1.2.1`、`ctranslate2 4.8.1`；
- 推理配置：CPU、`int8`、`language=zh`、`beam_size=5`、词级时间戳、VAD 开启、
  `condition_on_previous_text=false`；
- 真实成果：`main/runs/EP04/EP04-v13-20260813-2002/analysis/`，三轨 normalized
  词数 `12,467 / 11,853 / 6,732`，非法时间戳均为 `0`；
- 语义上下文成果：`main/runs/EP04/EP04-semantic-transcript-v1-20260814-120456/`。

后续任务默认先复用这类已完成成果。复用前必须逐轨核对原始 WAV SHA、实际 ASR 输入音频 SHA、
引擎/模型/解码配置、`source_audio_sha256` 和报告 SHA；全部一致才跳过 ASR。候选规则、语义分句、
审核页或试听参数变化，不需要重跑 ASR。

当前入口实现还会校验每轨 DeepFilterNet、transcript 与 semantic output 的 SHA 链；同一 P0
报告若对应多个 semantic run，必须通过 `--reuse-semantic-run` 明确指定，否则 fail closed。
`--reuse-analysis-run` 不能与新的 `--model` 或 `--context-prompt` 同时使用。EP04 v20 的
`analysis_reuse_manifest.json` 是当前可继续审核的真实运行证据；v17–v19 只保留为被替代/失败的工程证据。

这不是“同一段原始 WAV 就永远可复用”。EP04 v16 虽然原始三轨 SHA 与 v13 相同，
但因使用了不同 FFmpeg 生成 DeepFilterNet 输出，三条降噪轨 SHA 已变化；v16 的 faster-whisper
运行又被中断，没有形成完整 P0 报告。因此 v13 transcript 不能直接冒充 v16 降噪轨的 ASR。
若要继续用 v13 成果，必须把 v13 的降噪轨和 ASR 明确冻结为本次候选阶段的上游，并在新 run
记录 `reused_from_run` 及每轨 SHA；否则就对新降噪轨重新运行同一 faster-whisper small。

### 当前 P0 状态

正式比较：`WAITING_FOR_HUMAN_GOLD`。当日 baseline：`NTRACK_ENGINEERING_PASS`。

历史计划比较（已暂停，不进入普通入口）：

- faster-whisper baseline；
- FunASR Paraformer + FSMN-VAD + CAM++；
- MLX Whisper Turbo。

当前执行只使用 faster-whisper small；其余路线必须先从项目外归档恢复并重新通过人工 gold 和许可证/设备审计。

faster-whisper small baseline 已在本机实跑；FunASR/MLX 同场比较和 12 段人工 gold 仍未完成，因此没有正式 CER、speaker confusion、overlap recall 或赢家。

## 两套 benchmark 不要混淆

P0 gold 考“有没有听准”：CER、漏句、静音幻听、speech miss、false alarm、speaker confusion、overlap recall 和边界误差。

剪辑 gold 考“有没有剪对”，属于 F05/F08。P0 的人工标签当前只用来选听觉工具，不训练剪辑决策模型。

## 安全边界

- 没有人工 gold 不得宣布哪套更准。
- CAM++ 聚类 id 不能直接叫 female/male；需要 profile 或人工映射。
- 句级时间戳不能伪装成词级。
- 不用 LLM 清理文本后再评分；原始与 normalized 输出同时保存。
- 若引入 LLM，只允许生成可读的 canonical transcript、术语纠错或候选语义提示；不得改写原始 ASR、伪造时间戳或直接决定 sample 剪口。英文相邻子词合并和候选边界吸附到完整 token 应先用确定性规则处理。
- 热词只可作为独立实验臂，不能泄漏节目正确答案。
- 多轨情况下，能量领先只可提示哪个物理麦更可能是主轨，不得叫作“说话人真值”。
- 不根据文件名、音高或音色自动把轨道命名为男/女；如需显示人物姓名或主持人/嘉宾，必须记录映射来源。
- 不把项目 adapter、文本归一化或候选脚本称作“自研 ASR”；它们不得生成、补全或均匀编造词级时间戳。
- `semantic-transcript-v1` 只是假设层，不能把启发式句号/逗号当作真实标点或删剪授权；“该不该删”属于独立候选/删剪判断模块。

## 验收标准

- 12 段 gold 由真人填写并记录 reviewer/time；
- 三条可运行路线在同一 M3、同一片段和统一 schema 下比较；
- 失败、OOM、降级和不具备词级资格的路线如实记录；
- 既报告单组件结果，也允许形成“ASR 用 A、VAD/说话人用 B”的组合建议；
- Challenger verdict 不自动晋升 Champion。

## 实现与证据入口

- Challenger：`稳定生产/challengers/asr-speaker-v1/`
- 运行报告：`main/runs/EP03-asr-speaker-v1/benchmark_report.md`
- 人工标注：`benchmark/EP03-ASR-mini-gold-v1/label.html`
- 多轨拖入入口：`审核前端/P0-多轨转写/拖入音轨开始.command`
- 当日三轨报告：`main/runs/EP03-asr-speaker-v1/mvp-three-track-direct-e2e/p0_mvp_report.json`
- EP04 输入检查：`main/runs/EP04-input-check-20260811/input_check_report.md`
- EP04 normalized P0 报告：`main/runs/EP04-p0-normalized-20260811/01_transcripts/p0_mvp_report.json`
- 语义分句/标点 Challenger：`稳定生产/challengers/semantic-transcript-v1/`
- EP04 真实归档运行：`main/runs/EP04/EP04-semantic-transcript-v1-20260814-120456/`

## 语义分句 / 标点假设层（2026-08-14）

### 目的

这层不是为了让人看稿更舒服，而是给后续候选判断模块提供稳定上下文：某个口癖、重复词或停顿发生在完整句内，还是发生在一个已经结束的句子/分句边界附近。

### 已验证真实结果

使用 EP04 V13 的 P0 词级转写真实运行并归档：

| 轨道 | 原始词数 | 句子数 | 分句数 | ID/顺序覆盖 |
| --- | ---: | ---: | ---: | --- |
| `track_01` | 12,467 | 177 | 769 | true |
| `track_02` | 11,853 | 207 | 803 | true |
| `track_03` | 6,732 | 170 | 408 | true |

自动契约测试为 `8/8 pass`。`word_context_index[word_id]` 可直接取得：

- 所在 `sentence_id`、`clause_id`；
- 句内/分句内位置和完整 `word_id` 范围；
- 句/分句的起止 sample；
- 该边界的标点假设、理由和置信度。

### 方法与边界

当前方法为 `timing_text_heuristic_v1`，只结合词间停顿、源 ASR 标点和少量终止词/连接词线索。源 ASR 已有标点可标为高置信度；新增的停顿/文本启发式边界标为低置信度。

这层保留并校验每一个原始 `word_id`、文字、顺序和时间线，另行生成 `text_punctuated` 供上下文显示。它不生成候选、不判断 `accept/reject`、不生成 EDL、不改音频。后续删剪模块必须把它当上下文证据，与原始词级转写、声学停顿和人工审核规则一起使用；不得把句号/逗号假设直接当成“应该删除”。

## 三层文本与多语 Challenger（2026-08-16）

ASR 输出必须分成三层，避免“为了让人好读”反过来污染剪辑判断：

| 层 | 用途 | 是否可改写 |
| --- | --- | --- |
| `raw_text` | 原始上游证据、词级时间戳、原始 `word_id` | 不可改写 |
| `match_text` | 事件身份、历史案例匹配、繁简归一、英文子词确定性合并 | 只能生成派生副本，必须保留映射 |
| `display_text` | 前端/审核包阅读，显示统一繁简和可读标点提示 | 可格式化，不能回写 raw 或边界 |

繁简归一只发生在 `match_text` 和 `display_text`；原始 ASR 中的字、词顺序、sample 边界和 SHA 永远保留。英文专名或子词合并也必须记录 `source_word_ids`，不得让展示文本伪造新的时间戳。

当前正式结果继续复用外部 `faster-whisper small` / EP04 v13。`language="zh"` 与 `language=None` 的 A/B 只允许在隔离 Challenger 运行，不能覆盖 v13 transcript、不能把自动识别结果直接写进当前生产入口，也不添加纯英文 `.en` 模型作为默认。只有完成同一片段的小型人工 gold、准确率/漏识别对照和 SHA/许可证记录，才可提出晋升。

三层文本现只有一个实现：`main/orchestrator/transcript_text_layers.py`。`稳定生产/challengers/asr-multilang-v1/scripts/text_layers.py` 仅是兼容入口，A/B runner 也直接调用 canonical 模块；这样繁简归一、英文碎片展示与词 ID 完整性不会出现两套漂移规则。2026-08-16 的验证为 canonical `3/3`、多语 Challenger `7/7`、A/B runner `--help` 可加载；只读检查过 EP04 v13 的 JSON sidecar，没有解码或重新转写真实音频。
