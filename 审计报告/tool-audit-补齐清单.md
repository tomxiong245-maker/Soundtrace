# Tool Audit 补齐清单

**当前状态**：tools.json 共 **51 项** · audit 覆盖率 **2/51**（ffmpeg + extract_gold_cut_features）· runtime_dependencies 覆盖率 **4/51**

**任务**：为剩下 49 项 tool 补 audit md（照抄 `ffmpeg-homebrew-9.0.1.md` 四段结构）。分两批优先级：

- **P0 主线在用**（CLAUDE.md §11 明列必用 + 11 主线 champion challenger + 交接点 postcondition 引用）
- **P1 辅助工具**（未在主 pipeline 直接调用 · 但用于 fixture / QC / 分析）

模板见文末 §模板。

## ✅ 已有 audit（7 项）

| tool | audit | runtime_deps | 说明 |
|---|---|---|---|
| `p0_transcribe_mvp` | ✅ | ❌ | P0 baseline: faster-whisper 词级 ASR + energy 启发式 activity。本项目... |
| `detect_self_correction` | ✅ | ❌ | self-correction-v1 检测器：找说错后停顿重来的候选。输入词级 transcript，输出 self_c... |
| `detect_self_correction_wordlevel` | ✅ | ❌ | self_correction v2 词级 sliding-window 检测器：不用切句，对每个位置 i 取 pre=... |
| `mfa_align_and_extract_boundaries` | ✅ | ✅ | Montreal Forced Aligner 局部对齐 · 输入候选 + 3 轨 wav + ASR 分析目录，输出精... |
| `apply_autocut_gate` | ✅ | ❌ | autocut-gate-v1 7-gate 判决器：对候选按 whitelist + high_confidence ... |
| `write_delivery_report` | ✅ | ❌ | 写 DELIVERY_REPORT.md：读 run 目录 identity/qc/candidates/predict... |
| `extract_gold_cut_features` | ✅ | ✅ | 从人工 gold EDL 反向提取每条剪切的 WHERE/HOW 特征 (crossfade_ms, RMS envel... |

## ❌ P0 主线在用 · 亟需补 audit（18 项）

| tool | audit | runtime_deps | 说明 |
|---|---|---|---|
| `denoise_tracks` | ❌ | ❌ | 正式降噪：调用本地固定版本 DeepFilterNet，对每条 48 kHz mono 轨处理并恢复同一 sample ... |
| `build_filler_global_pause_candidates` | ❌ | ❌ | 口癖 + 全轨长停顿候选生成。当前 Champion 候选家族的唯一实现；受 candidate_rules.v18.j... |
| `snap_candidate_boundaries` | ❌ | ❌ | 候选边界 ±150 ms 内找能量最低点/零交叉点做精修。纯 stdlib，输出 snapped canonical b... |
| `apply_preference_snapshot` | ❌ | ❌ | 把 editing-experience-distiller 冻结的 preference snapshot 应用到候选... |
| `label_learning_driver` | ❌ | ❌ | 标签学习驱动器：对每条候选输出 MACHINE_CUT_SUGGESTED / MACHINE_PRESERVE_SUG... |
| `build_case_memory` | ❌ | ❌ | 案例记忆侧车：从冻结的逐项真人案例中为每条候选检索可解释的相似 case，输出历史 accept/reject、备注、匹... |
| `validate_integration_governance` | ❌ | ❌ | 校验负责人确认的能力接入登记，并在新 run 中冻结其 SHA；组件接入批准与语义删剪/发布授权严格分离，不生成 hum... |
| `build_candidate_family_bundle` | ❌ | ❌ | 运行负责人批准接入的说错重来与咳嗽检测器，并把输出规范化为 canonical ntrack-review-source... |
| `automix_render_speech` | ❌ | ❌ | 对已执行 EDL/source-track gate 的 N 轨 stem 做主麦 automix，生成 run-loc... |
| `experience_consumer_adapter` | ❌ | ❌ | 只读 experience-ingestion 适配器：给定 case_store，返回当前经验摘要 / 相关历史案例（... |
| `build_labels_lake` | ❌ | ❌ | 扫全项目 human_decisions.json 汇总真人打标数据到 main/knowledge/labels_la... |
| `detect_transient_events` | ❌ | ❌ | 检测 cough_like 等瞬态候选；主线适配器只保留 cough_like，并强制 human_review_req... |
| `refresh_lake_and_regate` | ❌ | ❌ | online 学习闭环 (evolution path 1): 每次人审后 rebuild labels_lake + ... |
| `run_end_to_end` | ❌ | ❌ | zero-touch pipeline: N 轨 mono WAV -> machine_assisted_draft ... |
| `auto_speaker_role` | ❌ | ❌ | v20.6 Q2 自动判说话人角色 (host/guest): 用 faster-whisper 词级输出统计 back... |
| `spacy_semantic_transcript` | ❌ | ✅ | v20.6 Q1 spaCy 中文语义分句 (zh_core_web_sm): 对 faster-whisper 词级 ... |
| `generate_ab_clip_learning_driven` | ❌ | ✅ | A/B clip 生成 · **唯一合规入口** (v215, 2026-08-18). 强制从三条进化路径学参数 (s... |
| `feedback_engine` | ❌ | ❌ | 反馈闭环唯一入口 (合并 v220) · 读(retrieve_before_decision)+写(analyze_f... |

## ❌ P1 辅助工具 · 次要补齐（26 项）

| tool | audit | runtime_deps | 说明 |
|---|---|---|---|
| `inspect_audio` | ❌ | ❌ | 读取 WAV 文件的容器/编码/采样率/声道/时长，输出 inspection.json。用于输入检查与 QC。... |
| `measure_loudness` | ❌ | ❌ | 用 FFmpeg ebur128 测量 integrated LUFS / loudness range / true ... |
| `analyze_reference_timeline` | ❌ | ❌ | 对比 Mentor 参考成品与本地降噪轨的时间线偏移，作为 sanity check。... |
| `estimate_sync` | ❌ | ❌ | 估计两条独立轨道之间的 offset 与 clock drift（ppm）。低置信度会拒绝自动校正。... |
| `correct_clock_drift` | ❌ | ❌ | 在 estimate_sync 给出可信 drift 后，对轨道做线性 clock drift 校正（重采样）。... |
| `create_clock_drift_fixture` | ❌ | ❌ | 生成合成 fixture（已知 offset+drift）用于 estimate_sync/correct_clock_... |
| `analyze_denoise_previews` | ❌ | ❌ | 从降噪前/后各截几段做 A/B 短片段试听。... |
| `transcribe_tracks` | ❌ | ❌ | 调用外部 faster-whisper（当前 baseline）做中文词级 ASR；本项目只校验并保存上游时间戳与文本。... |
| `classify_track_activity` | ❌ | ❌ | 对每 20ms 窗口比较两轨能量，标注 primary / bleed / ambiguous（串音标注）。... |
| `build_review_package` | ❌ | ❌ | 把转写+串音标注+启发式候选生成规则，做成 edit_candidates.json + review.html + r... |
| `build_priority_review_page` | ❌ | ❌ | 把候选按风险分层（高风险剪口 / 声学抽查 / 常规抽查）生成 priority-review.html。... |
| `create_aligned_ab_previews` | ❌ | ❌ | 为每个候选生成 original / proposed-cut 两段 MP3 试听（对齐时间线版）。... |
| `approve_review_candidates` | ❌ | ❌ | 把 review_manifest + 人工决定合成 approved EDL（integer samples 时间基）... |
| `analyze_cut_transitions` | ❌ | ❌ | 对 approved EDL 里每个剪口做客观 QC——crossfade、边界能量、削波检查。... |
| `render_approved_edl` | ❌ | ❌ | 按 approved EDL 对每条降噪轨做同步剪切+短 crossfade，输出剪切后的多轨 WAV。... |
| `assemble_program` | ❌ | ❌ | 把剪切后的多轨混音成单轨节目 WAV；本步可选拼授权片头片尾（本轮不加）。... |
| `finish_approved_project` | ❌ | ❌ | 从 approved EDL 一步跑完剪切→拼接→MP3 编码→编码后 QC（inspection + loudness... |
| `build_semantic_transcript` | ❌ | ❌ | 生成语义分句/标点假设层：从词级 ASR 出 sentence/clause 边界与 word_context_inde... |
| `serve_review_ui` | ❌ | ❌ | 启动本地 HTTP 审核前端服务器：/api/save 草稿保存 + /api/submit 正式决定。触发点：每次有效... |
| `analyze_transition_qc` | ❌ | ❌ | 剪口客观 QC：电平、频谱、边界波形异常排序。绑定 EDL + render manifest + WAV SHA；不判... |
| `predict_cut_artifact` | ❌ | ❌ | 剪口质量预测：dBFS 阈值 (-50/-35/-25) 输出 OK / HUMAN_REVIEW / BLOCK ve... |
| `review_event_routes` | ❌ | ❌ | 把历史逐项人审 run 的事件路由（already_reviewed_exact / semantic_reuse_bo... |
| `run_development_benchmark` | ❌ | ❌ | 端到端 benchmark：候选覆盖、无候选区抽查、剪口客观异常、备注回归、scorecard。orchestrator... |
| `load_session_feedback` | ❌ | ❌ | v20.6 Q4 备注记忆: 加载 main/knowledge/session_feedback/EP0X.sessi... |
| `feedback_first_retrieval` | ❌ | ❌ | [LEGACY SHIM] RAG · 决策前必调 · 检索 session_feedback + labels_lak... |
| `user_feedback_analyzer` | ❌ | ❌ | [LEGACY SHIM] 用户反馈决策路由器 · CLAUDE.md §21. 分析 verdict+note 提炼 ... |

---

## §模板 · 每个 audit md 必须含 4 段

```markdown
# <tool_name> <版本> · 本机运行审计

> 状态：<日期> · <一句话本项目定位 · 只读或读写 · 不上传>

## 固定信息

- 脚本路径 或 可执行文件路径
- SHA-256（本机指纹）
- Python / 二进制 版本
- 直接依赖 + 间接依赖
- 上游许可证
- 数据流：`<local files only / <说明是否触及网络/遥测>>`

## 本项目使用范围

- 输入：<路径 + 字段>
- 输出：<路径 + 字段>
- 用途：<被哪个 skill 消费 · 做什么决策>
- 归属 skill：<s1-s6>

## 已知限制

- <版本钉死 / 泛化未验 / 依赖 schema 对齐 / 只做分析不入生产 / 等>
```

总项 · 51 = done 7 + P0 18 + P1 26
