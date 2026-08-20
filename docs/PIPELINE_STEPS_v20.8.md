# Pipeline · 每步骤 · Skill / Tool / Output 完整梳理

> **版本**：v20.8（2026-08-18）· **代码 commit**: `de53dd1` + 后续 v211 学习驱动
> **入口**：`scripts/run_end_to_end.py` 一条命令跑完全部
> **梳理原则**：从 raw 3 轨 WAV → 双审通过 mp3 · 每步 · 对应 tool（tools.json 46 项之一）+ 对应 skill（5 个之一）+ 输出文件 + 学习消费源

## 一、上线前准备（每期节目 1 次）

| # | 步骤 | Tool / 命令 | Skill | Output |
|---|---|---|---|---|
| P1 | 装外部依赖 | `bash verify/setup.sh` | — | miniforge + MFA + spaCy + pyannote venv 就绪 |
| P2 | 建每轨角色声明 | 手写 `main/knowledge/speaker_maps/<EP0X>.speaker_map.json` | — | JSON: `{track_01: host/guest_A, ...}` |
| P3 | 若无 P2 → auto speaker | `auto_speaker_role.py`（tool #45） | podcast-editing-orchestrator | `speaker_map.auto.json` |

---

## 二、主 Pipeline · 11 Stages（一条命令自动）

```bash
python3 scripts/run_end_to_end.py --episode-id EP0X \
  --from-raw-wav track_01.wav track_02.wav track_03.wav \
  --tracks-for-automix track_01.wav track_02.wav track_03.wav \
  --out-dir main/runs/EP0X-AUTO-<timestamp>/
```

| # | Stage | Tool（tools.json 项）| Skill | Input | Output |
|---|---|---|---|---|---|
| **1** | denoise（每轨）| `denoise_tracks` · DeepFilterNet v0.5.6 | podcast-editing-orchestrator | raw WAV × N | `analysis/track_XX.denoised.wav` |
| **2** | ASR 词级 | `p0_transcribe_mvp` · faster-whisper small | podcast-editing-orchestrator | denoised WAV | `analysis/track_XX.transcript.json`（含 words[].probability）|
| **3** | 候选生成 | `build_filler_global_pause_candidates` + `immediate_repetition` + `detect_self_correction_wordlevel` · 含 sentence_position_gate + boundary_lock + english_fragment_context_guard + PRONOUN_LIKE 豁免 + probability_gate | podcast-editing-orchestrator + candidate-family-integration | 三轨 transcript.json | `all_candidates.json` + `review_source.json` |
| **3.1** | **auto_speaker_role**（若无 P2 map）| `auto_speaker_role` · 统计 backchannel_ratio + speaking_fraction | podcast-editing-orchestrator | 三轨 transcript.json | `speaker_maps/<EP>.speaker_map.auto.json` |
| **3.2** | **spaCy 语义分句** | `spacy_semantic_transcript` · zh_core_web_sm | podcast-editing-orchestrator | transcript.json | `spacy_semantic/track_XX.spacy_semantic.json`（每句 interrogative/declarative） |
| **3.3** | **session_feedback 加载 ⭐ 备注记忆核心** | `load_session_feedback`（Q4）· 消费 3 条进化路径累积规则 | editing-experience-distiller | `session_feedback/{<EP>,ALL}.jsonl` + `labels_lake.feedback[]` | 每候选加 `previous_user_feedback` 字段 |
| **3.4** | speaker_role_filter | `apply_speaker_role_filter`（内嵌 run_end_to_end）· host + 跨轨 backchannel 挡 | podcast-editing-orchestrator | speaker_map + 候选 + 三轨 ASR | `host_backchannel_filter.json` + 更新 all_candidates（filtered_reason） |
| **3.5** | **MFA 音素级精修** | `mfa_align_and_extract_boundaries` · mandarin_mfa + english_mfa | podcast-editing-orchestrator | 候选 + raw 三轨 + transcript_dir | `mfa_boundaries.json`（refined_start/end_raw 覆盖 ASR 值） |
| **3.6** | **experience_lookup**（case-based memory）| `build_case_memory` · 从 preference_snapshot 检索历史相似 case | editing-experience-distiller | 候选 + `preference_snapshot/aggregated.json` | `experience_context.json` + 每候选加 `experience_context` 字段 |
| **4** | autocut_gate 判决（**7 门**）| `apply_autocut_gate` · G1-G7 · 消费 lake + case_memory + wordlevel + **session_feedback G7** | podcast-editing-orchestrator | 全部候选 + `labels_lake.json` + `policy_v2.json` | `autocut_gate/summary.json` + `report.json` + `auto_cut_eligible_ids` |
| **5** | EDL 生成 | `stage_edl_from_gate`（内嵌）| podcast-editing-orchestrator | gate 结果 + candidates | `machine_assisted_draft.edl.json`（含 render_sync_cuts + insert_silence_samples）|
| **6** | Automix + 双遍 loudnorm | `automix_v1` · RMS 主导 + duck + `--edl` apply cuts | podcast-editing-orchestrator | 3 轨 WAV + EDL + music_templates.v2 | `render/EP0X-AUTO.machine_assisted_draft.mp3`（79 MB 左右）+ `render/speech.mono.wav` |
| **7** | Audit report | `stage_audit_report`（内嵌）| podcast-editing-orchestrator | gate + EDL + audio | `audit_report.md`（剪了/留了什么给你审） |

**约 30-60 分钟自动跑完** · 输出 machine_assisted_draft 状态 mp3。

---

## 三、审核循环（每次用户听审）

| # | 步骤 | Tool | Skill | Output |
|---|---|---|---|---|
| A1 | **生成 A/B clip**（v211 学习驱动 · 不是我写 SOP）| pydub AudioSegment.crossfade + `find_room_tone_from_raw`（内嵌）| editing-experience-distiller | `<run>/audit_clips/*.mp3`（每候选 原.mp3 + 剪.mp3） |
| A2 | 用户听审前端 | `mvp.html`（src/审核前端/challenger-review-product-v1/） | — | 用户逐条 accept/reject + feedback 备注 |
| A3 | 保存 human_decisions | `/api/save` hook | label-learning-driver | `human_decisions.json` + auto trigger snapshot rebuild |
| A4 | **反馈 append 到 session_feedback ⭐ 必做** | 手工 append 到 `session_feedback/<EP>.jsonl` | editing-experience-distiller | `session_feedback/EP0X.session_feedback.jsonl` |
| A5 | rebuild lake + regate active run | `refresh_lake_and_regate.py --run <run>` | label-learning-driver | `labels_lake.json` 增量 + `regate_diff.json`（新旧 auto_cut 差） |

**A1 的关键参数从"三条进化路径"学的**（不再手写 SOP）：

- `crossfade_ms` = 从 `session_feedback` 里"剪辑痕迹很重"备注 → 120ms
- `edge_extend_ms` = 从 YouTube § 2 → 40ms（保留辅音起音）
- `room_tone_source` = `raw_tracks`（YouTube § 5 强制）
- `pause_ms` = 从 `pause_calibration` 规则学（350/200/60 上下文自适应）

---

## 四、三条进化路径（长期学习机制 · 已就绪）

| 路径 | 位置 | 数据源 | 当前数据 | 消费方式 |
|---|---|---|---|---|
| **1 · 偏好学习** | `main/knowledge/labels_lake.json` + `main/orchestrator/build_labels_lake.py` | 全部 `human_decisions.json` accept/reject | **33 决定** | Stage 4 gate G5 `lake_by_reason` |
| **2 · Case-based memory** | `main/runs/LABEL-LEARNING-*/preference_snapshot/aggregated.json` + `main/orchestrator/case_memory.py` | 冻结历史真人 case（含 feedback） | **65 记录** | Stage 3.6 `experience_lookup` → G5 `experience_signal` |
| **3 · 从视频学习（Ev3）** | `main/knowledge/session_feedback/{<EP>,ALL}.jsonl` | YouTube § 1-5 + Preflight + 每条 human_decisions feedback | **EP04 28 + ALL 14 = 42 条规则** | Stage 3.3 `stage_feedback_lookup` → G7 `never_cut` 一票否决 |

**总消费历史知识量**：**140 项**（33 + 65 + 42）

---

## 五、Verify · 6 层依赖 + 17 层验收

**装机** · `bash verify/setup.sh`：
1. ffmpeg / ffprobe
2. miniforge conda
3. MFA 3.4+（conda-forge）
4. 中文 tokenizer（spacy-pkuseg + dragonmapper + hanziconv）
4.5. **spaCy + zh_core_web_sm**（v20.6 加）
5. MFA models（本地或下载）
6. pyannote venv（备用）

**验收** · `bash verify/verify.sh`（v5 · 17 层）：
1-11 · 原有（docs / JSON / mp3 / mfa / labels / evolution / skill）
12 · `tools.json` 46 项 full_path 全在 src/
13 · 5 skills flow_boundary.md 齐全
14 · 契约测试 79/79 全过
15 · MFA smoke
16 · 自足资产（音乐 + EP03/EP04 raw + MFA models + benchmark）
17 · `session_feedback` jsonl 存在（v20.6 Q4）

---

## 六、Skills / Tools 全清单

### 5 个 Skills（`src/skills/`）

| Skill | 作用 | Entry tool |
|---|---|---|
| **podcast-editing-orchestrator** | Zero-touch 主入口 | `run_end_to_end` |
| **candidate-family-integration** | self_correction + cough_like canonical | `apply_candidate_family_adapter` |
| **integration-governance** | OWNER_ATTESTED_INTEGRATE registry | `validate_integration_governance` |
| **label-learning-driver** | 偏好学习 + online 闭环 | `refresh_lake_and_regate` |
| **editing-experience-distiller** | 案例记忆 + 备注消费 | `build_case_memory` + `load_session_feedback` |

### 46 个 Tools（`config/tools.json`）· 按 pipeline stage 分组

**输入检查**：`ensure_ffmpeg`
**降噪**：`denoise_tracks`
**ASR**：`p0_transcribe_mvp` + `build_semantic_transcript`
**候选生成**：`build_filler_global_pause_candidates` · `immediate_repetition` · `detect_self_correction_wordlevel` · `apply_candidate_family_adapter`
**Stage 3.1-3.6**：`auto_speaker_role` · `spacy_semantic_transcript` · `load_session_feedback` · `mfa_align_and_extract_boundaries` · `build_case_memory` · `validate_integration_governance`
**边界精修**：`snap_candidate_boundaries` · `predict_cut_artifact`
**Gate**：`apply_autocut_gate` · `policy_promotion`
**Mix + Render**：`automix_v1` · `automix_adapter` · `render_approved_edl` · `assemble_program` · `render_ntrack_edl`
**QC**：`transition_qc` · `check_current_delivery_sync`
**Learning**：`build_labels_lake` · `refresh_lake_and_regate` · `refresh_label_learning_snapshot` · `label_learning_driver` · `experience_consumer_adapter` · `consume_experience_cases`
**Governance**：`policy_promotion` · `production_edit_policy` · `context_checkpoint`
**（其余 15 个 · 详见 tools.json）**

---

## 七、EP04 真实 Output（已跑证据）

**位置**：`main/runs/EP04-AUTO-VERIFY-20260817-2200/`

```
├── run_identity.json                    ← 运行身份 SHA
├── input_manifest.json                  ← 输入 3 轨 SHA + drift
├── analysis/                            ← Stage 1 + 2
│   ├── track_01.denoised.wav
│   ├── track_01.transcript.json（12467 词）
│   ├── track_02.transcript.json（11853 词）
│   └── track_03.transcript.json（6732 词）
├── all_candidates.json                  ← Stage 3 · 12 严格候选
├── calibration_source.json              ← 11 review 池
├── spacy_semantic/                      ← Stage 3.2
│   └── track_01.spacy_semantic.json（12467 词 → 8 句 · 1 interrogative）
├── host_backchannel_filter.json         ← Stage 3.4
├── mfa_boundaries.json                  ← Stage 3.5（v26）
├── experience_context.json              ← Stage 3.6（case memory）
├── autocut_gate/                        ← Stage 4
│   ├── summary.json（**7 auto_cut** / 5 review_required）
│   └── report.json（G1-G7 每候选轨迹）
├── machine_assisted_draft.edl.json      ← Stage 5（7 render_sync_cuts）
├── render/                              ← Stage 6
│   └── EP04-V21.mp3（79.5 MB · 3312.5s · -22.5 LUFS）
├── automix_full/                        ← v207 语义并轨（无 EDL）
│   ├── EP04_automix_no_edl.mp3（79.6 MB）
│   └── tmp/speech.mono.wav（314 MB · A/B 生成源）
├── mfa_v29_representative/              ← v26 MFA 精修 clip
├── v208_automix_ab/                     ← v208 A/B（错版）
├── v209_smooth/                         ← v209 acrossfade（半错）
├── v210_pydub/                          ← v210 pydub crossfade
├── v211_by_learning/                    ← **v211 三条路径学习驱动 · gold standard**
│   ├── manifest.json（params_learned JSON）
│   └── clips/C023_然后_{原,剪}.mp3
└── audit_report.md                      ← Stage 7
```

**双审通过成品**：`main/runs/EP04-DELIVERY-20260817-1427/render/EP04_codex_loudnorm_corrected.mp3`（**mentor 内容 ✓ + Sophie 响度 ✓**）· LUFS -22.46 · TP -6.91 · LRA 6.30

---

## 八、下一步（EP05 上线）

1. `bash verify/verify.sh` → 17 层过
2. 建 `main/knowledge/speaker_maps/EP05.speaker_map.json`（1 分钟）
3. `python3 scripts/run_end_to_end.py --episode-id EP05 --from-raw-wav ... --out-dir main/runs/EP05-AUTO-...`
4. 30-60 min 后 → mp3 + audit_report
5. 你听审 → append `session_feedback/EP05.jsonl` + 前端保存 human_decisions
6. `refresh_lake_and_regate.py --run <EP05 run>` → lake 增量 + regate
7. mentor 整片试听 → 双审通过 → 升级 human_approved
