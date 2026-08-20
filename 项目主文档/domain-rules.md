# 明略剪辑项目 · 域规则（§1-§22）

> **权威源**。原本住在 `CLAUDE.md` 里的 22 条编号规则搬到此处，编号（§编号）不变，内容不变。CLAUDE.md 只保留 6 条元规则和到本文件的 pointer。
>
> **依赖锚点**：`skills/cut-verify/SKILL.md` frontmatter 的 `covers_claude_md_rules:` 字段直接引用本文件的 § 编号（字段名沿用历史 · 语义指的是本文件与 `CLAUDE.md` 里的 § anchor · SKILL.md 本身内容不动）。其它 skill 通过 § grep 查找条款也在本文件内。
>
> **元规则映射**：每条规则末尾的 (M?) 是它对应哪条元规则的具体展开（M1 分层 / M2 只读 / M3 人签字 / M4 EDL 整数 sample / M5 契约先行 / M6 报告纪律）。元规则本身住 `CLAUDE.md`。

## 分类导航

- **元规则展开** · §1 §2 §3 §4 §5 §6 §7
- **Cut-verify 域**（内容不动 · 最高优先级）· §8 §11 §14 §15 §16 §17 §19 §20 §22
- **已进代码强制**（1 行 pointer 版）· §9 §10 §12 §13 §18
- **数据模型规则**（无法完全代码化）· §20 §21

---

## §1

原始音频、Mentor 成果、Champion 和已哈希运行产物只读。（M2）

## §2

语义删剪必须有真人决定，或有负责人签署且版本化的 `autocut_policy` 明确授权的低风险自动剪；不得自批准、超时批准、默认全接受，或把机器预测伪装成真人决定。（M3）

## §3

EDL 使用整数 sample，批准区间同步作用于全部语音轨。（M4）

## §4

公司音频、转写、候选和内部资料不得上传；真实推理必须本地。（M2）

## §5

不运行 `curl | sh`、不透明 `inference.sh`，不覆盖系统 Python，不修改全局 Skill。（M2）

## §6

外部工具先记录官方 URL、版本/commit、许可证、权重 SHA、依赖、遥测、网络和数据流。（M5）

## §7

无冻结 benchmark、独立复核和回滚，不得晋升 Challenger。（M1）

## §8

**候选边界精修必须来自 MFA 音素级 alignment**（`稳定生产/challengers/mfa-alignment-v1/`）而非 ASR 词 timestamp + 手工扩阈值。中文候选走 mandarin_mfa；英文/混合词（如 GoGoFlow 里的 "go"）OOV 时走人审，不 auto-cut。理由见 v26 用户 accept 记录 [[minglue-audit-feedback-20260817]]。

## §9

**A/B clip 必须走 automix**（cut-verify 域）→ 见 `main/orchestrator/make_edl_ab_clips.py` + `automix_adapter`。裸 `ffmpeg amix` 现场三轨叠加**禁止**（历史事故：v22 单轨 / v24-v27 / v207 LG48/51/56）。`delivery_orchestrator.py` L4096/L4136 两处裸 amix 待 Session 3 迁移为 `automix_adapter` 调用；L2375 (A/B preview) 归 cut-verify 域另行处理。

## §10

**online 学习闭环** · 每次人审 save 后触发 `main/orchestrator/refresh_label_learning_snapshot.py` + `main/orchestrator/refresh_lake_and_regate.py --run <active>`。lake 增量后 gate 自适应用户偏好，无需重跑候选生成层。evolution path 1（偏好学习）。

## §11

**【禁止自由发挥】做过的东西都是工具，能用就用**。不允许 agent 自己写**候选生成 / boundary 精修 / gate 判决 / 混音 / clip 生成**脚本。找不到工具时的正确顺序：（1）查 `main/tools/tools.json`（55 项）；（2）查 `skills/*/SKILL.md`；（3）查 `稳定生产/challengers/*/scripts/`；确认都没有再自己写。写完必须先登记到 `tools.json`（含 description / params / full_path / reads_only）才能提交，否则视为破坏契约。**已进代码强制**（M5）：`verify.sh` Layer 21 + `executor_v2` + `_adapter_base.dry_run_plan` 三层。历史 bug 证据：2026-08-18 20-pack 事件（agent 为凑 20 条自行扩填 filler 词表挡不住"嗯/啊"backchannel，用户 reject "没一个通过"）。

- **候选生成必用**：`build_filler_global_pause_review_source.py` + `immediate_repetition` + `detect_self_correction_wordlevel.py`（含 sentence_position_gate + boundary_lock + english_fragment_context_guard）
- **边界精修必用**：`mfa_align_and_extract_boundaries.py`（中文 mandarin_mfa / 英文 english_mfa，`--language auto`）
- **Gate 判决必用**：`apply_autocut_gate.py`（六道门 + 三层 signal 消费 lake + case_memory + wordlevel_cross_track）
- **混音必用**：`automix_v1.py` 或封装 `main/orchestrator/automix_adapter.py`（run-local · 只做电平不改 EDL）
- **Clip 生成必用**：`main/orchestrator/make_edl_ab_clips.py`（三轨 amix + loudnorm -22.2）

## §12

**speaker_role_gate**（2026-08-19 fail-closed）· 主持人 backchannel 不进候选池。每期 EP0X **必须**先建 `main/knowledge/speaker_maps/<episode>.speaker_map.json`（人工声明 host/guest_A/guest_B/... · 不用 pyannote 学）。`run_end_to_end.stage_speaker_role_filter` 在 gate 前 filter 主持人短应答（±3s 其他轨有语音时）。**缺 map → SystemExit**，只有 `--allow-missing-speaker-map` fixture 例外。

## §13

**source_track_gate** · cough_like 只 mute 单轨、绝不升级为全轨 cut。候选带 `cut_scope="source_track_gate_only"` + `action_type="source_track_gate"`；gate 层禁止把 source_track_gate 转全轨。见 `candidate_family_adapter.py` cut_scope 声明。

## §14

**【备注记忆】用户反馈必须落地成 session_feedback**（2026-08-18 v20.6 · Q4 · 修"同一问题反复问"核心 bug）：agent **必须**读 `main/knowledge/session_feedback/current.session_feedback.jsonl` + `labels_lake.entries[].feedback[]` 才能生成候选。每次用户 chat 反馈**必须**由 agent 主动 append（含 kind / candidate_pattern / verdict / note / action_taken）。下游 `apply_autocut_gate` 里 **G7 · reject-on-never-cut-feedback** 消费：若候选有 `previous_user_feedback[].verdict == "never_cut"` → hard reject（不进 auto_cut，走人审并显示反馈）。运行时 `run_end_to_end.py` Stage 3.3 `stage_feedback_lookup` 自动加载并 inject 到 `candidate.previous_user_feedback` 字段。schema 见 `main/orchestrator/session_feedback.py` docstring。**违反 = 系统再次问过去问过的问题**。

## §15

**【装了的工具必须用】能用开源包就用**（2026-08-18 v20.8 · 用户明确 "他妈的用啊 · 写入流程 · 有了为什么不用"）：项目装了的开源包 —— `pydub / librosa / noisereduce / ffmpeg 内置 filters (acrossfade/volumedetect/silencedetect) / MFA / DeepFilterNet / spaCy zh_core_web_sm / faster-whisper / spacy-pkuseg / dragonmapper` —— **必须优先用**，禁止用低级 concat / anullsrc / afade 20ms 硬拼这类原始方法重造轮子。**违反案例**：v208 A/B clip 用 `concat + anullsrc + afade 20ms` 硬拼，剪辑痕迹明显；应该用 pydub AudioSegment.crossfade + room tone splice 或 ffmpeg acrossfade。**verify.sh 第 18 层**自动扫描 installed vs used：装了没 import 的包会 warn。**每次新加工具**必须先查现有包再实现。

## §16

**【剪口拼接方法】必须用高级方法**（2026-08-18 v20.8 · 用户"LP01/C023 剪辑痕迹明显"）：A/B clip 或成品剪口拼接**禁用**`concat + anullsrc` 硬拼。**必须使用**其一或组合：

- **pydub `AudioSegment.crossfade` / `.overlay`**（sample-level fade curve · 最精细）
- **ffmpeg `acrossfade` filter**（内置，50-80ms triangle/quadratic curve）
- **room tone splice**（YouTube § 5 · `volumedetect` 找最安静 200ms → aloop 到 pause 长度替代 anullsrc）

组合方式：**pydub crossfade + room tone splice** > ffmpeg acrossfade + room tone > 单独 acrossfade > 裸 concat。afade 参数 ≥ 50ms（v20.7 前 20ms 太短）。

## §17

**【librosa onset 保护】保留词不能被 crossfade 吞掉**（2026-08-18 v20.7 · 用户"然后被吞了"）：chain 场景保留最后 1 个词时，crossfade 长度**必须** ≤ `librosa.onset.onset_detect(y, backtrack=True)` 返回的辅音起音时间 - 30ms 安全 margin。ASR 词 start 通常比真实辅音起音**晚 100-150ms**，直接用 ASR 数字定 cut_end 会吃辅音。历史 bug：v213/v214 用 ASR 数字 · 保留词起音被 crossfade 淡出。

## §18

**反馈闭环 · 决策链** · 所有 A/B 生成 / 候选生成 / gate 判决 / pause / 拼接 / 剪辑边界决策**前**必须 `feedback_engine.retrieve_before_decision(...)`；**后**用户新反馈必须 `feedback_engine.analyze_feedback(...)`。决策链顺序：TOOL_APPLY (0.9) → DOC_REFERENCE (0.7) → SESSION_FEEDBACK_PATCH (0.5 · 最后手段)。单一入口：`skills/feedback-engine/`。**违反 = 补丁滥用 · session_feedback 会失控膨胀。**

## §19

**【相邻词保护】剪辑不能吃到 prev/next word**（2026-08-18 v20.10 · 用户"c007 前一个词剪掉了 · c034 剪掉了两个词"）：剪辑边界必须**不覆盖相邻词**。规则：

- `cut_start = max(filler.start - edge_extend, prev_word.end + 20ms)`
- `cut_end = min(filler.end + edge_extend, next_word.start - 20ms)`
- chain 场景: `cut_end = min(librosa.onset(kept) - 30ms, kept_word.start - 20ms)`

Edge extend 再大也不能吃相邻词。已实现: `main/orchestrator/generate_ab_clip_learning_driven.py::safe_bounds`.

## §20

**【Session_feedback 单一 SOT】只在一份文档更新添加**（2026-08-18 v20.10 · 用户"今后只留一个版本 · 每次更新"）：session_feedback 只维护 `main/knowledge/session_feedback/current.session_feedback.jsonl` **单一文件**。旧的 `EP04.session_feedback.jsonl.archived` + `ALL.session_feedback.jsonl.archived` 只作历史证据（不再读取）。**新反馈**只 append 到 `current.session_feedback.jsonl`，`retrieve_before_decision` 只读它。**A/B clip 输出目录**同规则：只保留 `main/runs/<run>/current_audit_clips/`（每次覆盖），不再 v215/v216/v217 平行目录。

## §21

**知识分两块** · PARAMETER（`main/knowledge/cut_parameters.json`，决定"怎么剪"，工具直接消费）vs PREFERENCE（`current.session_feedback.jsonl`，决定"剪哪些"，`retrieve_before_decision` 消费）。新数据到手时按学习流选择器路由（`docs/learning-flow-selector.md`）：单 chat 反馈 → feedback-engine；批 accept/reject（N≥5）→ label-learning-driver；Mentor 成品 → editing-experience-distiller。禁 PARAMETER/PREFERENCE 混存。

## §22

**【剪口干净度 4 项 check + filler ASR-word 扩展】**（2026-08-19 · 用户 C007 A/B v04 accept · 一晚攻坚定案）：**filler_hesitation / immediate_repetition 类候选**（token=呃/嗯/然后/我们/因为...）· gold cut 位置常**保守只砍中段** · 头/尾残留 + 长 crossfade 制造 ghost = 用户听感"不干净"。**规则**：

- **entry**：`skills/cut-verify/scripts/verify_cut_plan.py`（一次跑 4 项 check + 融合 overall_verdict）
- **Check 1 · 幻觉**：faster-whisper `word.probability` < 0.6 → REJECT_LOW_PROB_HALLUCINATION（EP04 C007 呃 prob=0.488 · C014 go prob=0.460 实证）
- **Check 2 · 静音位置**：`pydub.silence.detect_silence(-40dB, 100ms, ±1.5s context)` 判 cut 是否落静音段内 → BUTT_SPLICE 可行 vs 需 crossfade
- **Check 3 · 节奏**：`cut_parameters.json.gap_before/gap_after.target_range` 阈值 · post_cut_gap < 120ms → RHYTHM_TOO_TIGHT · > 450ms → RHYTHM_TOO_LOOSE
- **Check 4 · 路由**：P1-P7 优先级 · P1 幻觉/P2 吃邻词 → REMOVE_FROM_EDL · P3 抢话/P6 内容区 → NEEDS_HUMAN_REVIEW · P4 静音段 → BUTT_SPLICE(0ms xf + 10ms room tone) · P5 边界 → CROSSFADE_50MS
- **ASR-word 扩展 · `expand_to_asr_word_boundary.py`**：filler 候选若有 ASR word 匹配且 ASR word 覆盖 gold cut · 扩展到 word 完整边界 + 50ms xfade（**EP04 C007 385ms→680ms · C044 426ms→720ms · 用户 v04 明确 accept**）
- **绝不**：自己写"剪口干净度评分"（违反 §11 禁自由发挥）· 不改 EDL · 不改 candidate · 只输出 `verified_edl.json` 侧车
- **audit**：`skills/cut-verify/audits/cut-verify-v1.md`
- **实证**：EP04 7 候选跑通 · 4/7 与 mentor gold 一致 · 冲突 3 处（C007 gold accept vs tool reject-hallucination · C036 · C039 缺内容词保护）已入 backlog
- **session_feedback 硬规则**：`filler_cut_use_full_asr_word_range_plus_50ms_xfade` (rule line 66) · 2026-08-19 用户确认
