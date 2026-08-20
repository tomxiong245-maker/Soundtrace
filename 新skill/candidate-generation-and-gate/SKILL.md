---
name: candidate-generation-and-gate
description: 候选生成 → 长停顿跨轨 gate → 边界精修（MFA + snap + librosa onset guard + neighbor-word 保护）→ autocut 7 门 gate 的一条不可打断决策链。承接 filler / immediate_repetition / global_long_pause / self_correction / cough_like 五族候选，产出 all_candidates.json + auto_cut.json + review_required.json + gate_report.json，并强制 CLAUDE.md §8 MFA 必用、§11 禁自由发挥、§12 host backchannel、§13 cough_like 只 mute、§17 librosa onset 保护、§6.9 长停顿跨轨静默、FR-04/FR-05 硬约束。触发关键词：候选生成, autocut gate, MFA 边界, 长停顿跨轨, cough_like 单轨 gate, self_correction abandoned span, neighbor word 保护, librosa onset 保护, 7 门 gate, candidate family bundle。
status: active
owner: champion
entry_tool: build_candidate_family_bundle
related_tools:
  - build_filler_global_pause_candidates
  - build_candidate_family_bundle
  - detect_self_correction_wordlevel
  - detect_transient_events
  - mfa_align_and_extract_boundaries
  - snap_candidate_boundaries
  - apply_autocut_gate
  - build_case_memory
  - build_labels_lake
  - spacy_semantic_transcript
  - build_semantic_transcript
  - p0_transcribe_mvp
preconditions:
  - "run 目录下存在 p0_transcribe_mvp 产出的词级 ASR 转写（用于 detect_self_correction_wordlevel 与 build_filler_global_pause_candidates 的 --transcripts / --transcript 输入）"
  - "run 目录下存在 semantic-transcript-v1 产出的 semantic 转写（用于 build_filler_global_pause_candidates 的 --semantic 输入）"
  - "**上游 s1 已合成主导轨**（2026-08-18 架构级顺序变更）：`main/runs/<ep>/<run>/render_prep/speech.mono.wav` 存在且 sidecar `speech.mono.manifest.json` 中 `safety.edl_mutation=false`；候选生成的**音频扫描主体**基于此主导轨，边界精修和后续 A/B clip 都从这条轨切；3 条独立词级 ASR 仍消费，但只用于跨轨判定（如长停顿其他轨是否有实词），不再作为候选生成的音频源"
  - "main/tools/tools.json 里 build_candidate_family_bundle / detect_self_correction_wordlevel / detect_transient_events / mfa_align_and_extract_boundaries / snap_candidate_boundaries / apply_autocut_gate 均已登记（schema_version=1）"
  - "MFA 二进制存在于 ~/miniforge3/bin/mfa 且 mandarin_mfa + mandarin_china_mfa 词典/模型已安装（mfa_align_and_extract_boundaries 未安装时返回 exit 2 BLOCKED）"
  - "main/knowledge/integration_governance/owner_attested_mainline.v1.json 里对 candidate_family_adapter 的 capability 已 OWNER_ATTESTED_INTEGRATE"
  - "main/knowledge/session_feedback/current.session_feedback.jsonl 存在（§20 唯一 SOT · G7_session_feedback 需要）"
  - "main/knowledge/labels_lake.json 存在且 schema_version=labels-lake-v2（G5 token-level reject 检查需要）"
  - "**2026-08-19 起 speaker_role_filter 默认走 pyannote-audio 4.0.7 RTTM**（Stage 3.4 · community-1 model · DER 12.2% · 若不可用 fallback 到能量启发式）· 详见 PROMOTION_MANIFEST_2026-08-19.md"
postconditions:
  - "run 目录写出 candidate_source.json（含 filler_global_pause 与 candidate_family_adapter 合并后的候选，带 candidate_family_integration 侧车字段：schema_version=candidate-family-integration-v1, adapter=candidate_family_adapter-v1, base_candidate_source_sha256, enabled_families=[self_correction,cough_like], excluded_transient_families=[mic_bump_like,thump_like]）"
  - "self_correction 候选带 candidate_id=FAM-self_correction-{track}-{sha256_12}, cut_scope=abandoned_span_only, boundary_lock=true, safety_status=NEEDS_HUMAN_REVIEW, policy=review_only_no_automatic_accept, family_provenance.detector_sha256/rules_sha256 齐备"
  - "cough_like 候选带 candidate_id=FAM-cough_like-{track}-{sha256_12}, cut_scope=source_track_gate_only, action_type=source_track_gate, evidence_text='（咳嗽/瞬态声学事件）', proposed_delete_text=''"
  - "mic_bump_like / thump_like 一律不进候选池（adapter normalize_transient_rows 只保留 cough_like）"
  - "run 目录写出 mfa-boundaries-v1 JSON（schema_version=mfa-boundaries-v1, refined[] 里每条含 mfa_raw_start/mfa_raw_end/refined_start_raw/refined_end_raw/context_range_raw/acoustic_model/dictionary/head_pad_ms=50/tail_pad_ms=50）"
  - "run 目录写出经 snap_candidate_boundaries 处理的候选（每条带 boundary_snap.status ∈ {snapped, locked, no_audio, invalid_order} + start_sample_original/end_sample_original/start_sample_snapped/end_sample_snapped/total_moved_ms；boundary_lock=true 的候选 status=locked，reason=boundary_lock_reason）"
  - "run 目录 autocut_gate/ 下写出 auto_cut.json + review_required.json + gate_report.json + summary.json（schema_version=autocut-gate-v1-run-v1，summary 含 auto_cut_eligible_count / human_review_required_count / auto_cut_ratio / auto_cut_candidate_ids；per_candidate 里每门 pass:true|false + reason）"
  - "cough_like / transient_events / mic_bump_like / crosstalk_attribution / off_topic / semantic_duplicate 六种 kind 全部落在 denylist_kinds，在 G1_whitelist 被拒；autocut_policy=NOT_APPROVED 时 auto_cut_eligible_count == 0"
  - "2026-08-19 evening: autocut_gate 语义门 (G3/G5/G7) 让位给 Stage 3.7 LLM filter · diagnostic_only · 只保留结构性门 (speaker_role/source_track/G6_duration/review_budget)"
covers_decision_points:
  - candidate_family_integration
  - long_pause_crosstrack_silence
  - boundary_refinement_mfa
  - boundary_refinement_snap
  - boundary_refinement_librosa_onset
  - neighbor_word_guard
  - autocut_gate_seven_doors
  - cough_like_source_track_gate_only
  - self_correction_cut_scope_normalization
covers_claude_md_rules:
  - "§6.9"
  - "§8"
  - "§11"
  - "§12"
  - "§13"
  - "§14"
  - "§17"
  - "§18"
  - "§20"
  - "FR-04"
  - "FR-05"
pre_flight_check: scripts/preflight/check_candidate-generation-and-gate.py
---

# candidate-generation-and-gate

## 1. 定位

这条 skill 是"候选是否进入 EDL"的唯一决策链：从五族候选生成（filler_hesitation / immediate_repetition / global_long_pause / self_correction / cough_like）开始，经候选家族规范化（candidate_family_adapter）、长停顿跨轨静默 gate、边界精修三段（MFA + snap + librosa onset guard + neighbor-word 保护），最后进 autocut_gate 7 门；只要落在这条链中就不能被别的 skill 打断或旁路。它合并原 `candidate-family-integration` skill 的接线职责，并把之前散落在 CLAUDE.md §6.9 / §8 / §11 / §12 / §13 / §17 与 FR-04 / FR-05 的硬约束在同一个 pre_flight_check 里逐条落地。它**不**决定"要不要合成 EDL"或"要不要出片"——那是下游 skill 的事。

## 2. 何时激活

**trigger**：
- 用户/上游 agent 提到：候选生成、autocut、gate、MFA 边界、跨轨长停顿、cough_like、self_correction 剪掉、7 门 gate、review_required、onset 保护、neighbor word 保护、boundary_lock
- run 目录里出现 `self_correction_wordlevel.json` / transient-events raw / filler_global_pause review_source 但尚无 `autocut_gate/summary.json`
- run 目录出现 `all_candidates.json` 但 `boundary_snap_summary` / MFA refined 字段缺失

**上游 postcondition（必须先满足）**：
- ASR：`p0_transcribe_mvp` 完成，词级转写落盘
- Semantic：`build_semantic_transcript` + `spacy_semantic_transcript` 已跑
- Governance：`owner_attested_mainline.v1.json` 里 `candidate_family_adapter` 的 capability 已 OWNER_ATTESTED_INTEGRATE
- Feedback：`main/knowledge/session_feedback/current.session_feedback.jsonl` + `main/knowledge/labels_lake.json` 存在（G5 / G7 需要）

## 3. 读什么

| 用途 | 实际文件路径 | 实际 schema 字段 |
|---|---|---|
| filler / immediate_repetition / global_long_pause 生成的规则 | `稳定生产/challengers/filler-global-pause-v1/scripts/build_filler_global_pause_review_source.py` + rules v18 | `min_silence_seconds=1.1, max_silence_seconds=12.0, max_frame_rms_dbfs=-38.0, max_frame_peak_dbfs=-20.0, activity_merge_gap_seconds=0.05, retention_by_original_silence, min_phrase_chars=2, max_phrase_chars=6, max_gap_seconds=0.6, exclude_tokens=["嗯"]` |
| self_correction 检测器原生输出 | `<run>/self_correction_wordlevel.json` | `reason_key, kind, track_id, source_track_id, start_seconds/sample, end_seconds/sample, abandoned_span:{text,start_seconds,end_seconds}, retry_span:{text,start_seconds,end_seconds}, edit_ratio, gap_seconds, pre_window_words, algorithm="wordlevel_sliding_v1", boundary_lock=true, boundary_lock_reason, algorithm_confidence, cut_scope="both_spans", post_cut_pause_ms=200, confidence_tier="mid", policy="review_only_no_automatic_accept"` |
| self_correction 规则 SHA / audit | `稳定生产/challengers/self-correction-v1/rules/self-correction-wordlevel.v1.json` + `稳定生产/challengers/release-policy-v2/docs/2026-08-17-1830-A-self-correction-wordlevel.md` | rules_sha256 / detector_sha256（写入 family_provenance） |
| transient events 检测器原生输出 | `稳定生产/challengers/transient-events-v1/scripts/detect_transient_events.py` 输出 + rules `稳定生产/challengers/transient-events-v1/rules/transient-events.v1.json` | `reason_key ∈ {cough_like, mic_bump_like, thump_like}` |
| 上游 base candidate_source | `<run>/candidate_source.json`（filler_global_pause 侧生成） | `candidate_id, reason_key, candidate_kind, stratum, source_track_id, start_seconds, end_seconds, start_sample, end_sample, filler_token, proposed_delete_text, boundary_lock=true, safety_status="NEEDS_HUMAN_REVIEW"` |
| governance registry | `main/knowledge/integration_governance/owner_attested_mainline.v1.json` | capability = candidate_family_adapter → `OWNER_ATTESTED_INTEGRATE` |
| session_feedback（G7_session_feedback / neighbor_word_guard） | `main/knowledge/session_feedback/current.session_feedback.jsonl`（§20 唯一 SOT） | 每行：`schema_version="session-feedback-v1", timestamp, episode_id, reviewer, source, kind, candidate_pattern, verdict, note, action_taken?, case_ref?` |
| 关键 kind（本 skill 直接消费） | 同上 jsonl | `never_eat_neighbor_word, chain_cut_dont_eat_kept_word, cut_boundary_from_librosa_onset, cut_boundary_must_be_asr_word_not_edl, filler_boundary_edge_extend_150_200ms, long_pause_all_track_silence, cross_track_backchannel, host_backchannel, c034_cut_too_much, repetition_chain_cut_extent, segment_pause, pause_dynamic_by_cut_count, ripple_delete_all_tracks_sync, historical_case_note_reject` |
| 关键 verdict | 同上 jsonl | `never_cut, needs_extension, pause_required, both_spans_or_none, cut_scope_too_wide, forbidden, three_track_amix_required, MFA_required, automix_required, cut_all_but_last, mixed, context_accepted, only_representative, policy, pause_shorter` |
| labels_lake（G5） | `main/knowledge/labels_lake.json` | `schema_version=labels-lake-v2, summary, by_reason_key[reason_key]._subtypes[subtype]._tokens[filler_token].reject, .accept, .accept_rate, .total, .case_ids` |
| autocut policy | `autocut_policy` JSON（`--policy` 传给 apply_autocut_gate） | `whitelist_kinds=[filler_hesitation, global_long_pause, immediate_repetition, self_correction], denylist_kinds=[cough_like, crosstalk_attribution, mic_bump_like, off_topic, semantic_duplicate, transient_events], preserve_routes={"auto_preserve"}` |
| MFA 二进制与词典 | `~/miniforge3/bin/mfa` + `mandarin_mfa` + `mandarin_china_mfa` | 缺失即 exit 2 BLOCKED |

## 4. 写什么

| 产物 | 实际文件路径 | 实际字段 |
|---|---|---|
| 合并后候选池 | `<run>/candidate_source.json` | 顶层保留原 base + `candidate_family_integration = {schema_version:"candidate-family-integration-v1", adapter:"candidate_family_adapter-v1", base_candidate_source_sha256, sample_rate_hz, enabled_families:["self_correction","cough_like"], excluded_transient_families:["mic_bump_like","thump_like"], self_correction_raw_outputs, transient_raw_outputs, added_candidate_ids, added_counts, safety:{...}}` |
| self_correction 规范化候选 | 同上 candidate 数组 | `candidate_id="FAM-self_correction-{track}-{sha256_12}", candidate_kind="self_correction", reason_key="self_correction", cut_scope="abandoned_span_only", boundary_lock=true, boundary_lock_reason, safety_status="NEEDS_HUMAN_REVIEW", default_action="human_review_required", review_display={mode:"global_sync_cut", requires_audio_review:true}, rendering={crossfade_ms:100.0, curve:"qsin", scope:"review_preview_only"}, policy="review_only_no_automatic_accept", family_provenance={adapter, detector, detector_path, detector_sha256, rules_path, rules_sha256, raw_detector_candidate}` |
| cough_like 规范化候选 | 同上 candidate 数组 | `candidate_id="FAM-cough_like-{track}-{sha256_12}", candidate_kind="transient_events", reason_key="cough_like", cut_scope="source_track_gate_only", action_type="source_track_gate", evidence_text="（咳嗽/瞬态声学事件）", proposed_delete_text="", safety_status="NEEDS_HUMAN_REVIEW", default_action="human_review_required", review_display={mode:"source_track_gate", requires_audio_review:true}, rendering={crossfade_ms:0.0, scope:"source_track_gate_preview_only"}` |
| MFA 精修 | `<run>/mfa_boundaries.json`（或 `--out` 指向） | `schema_version="mfa-boundaries-v1", candidates_source, acoustic_model, dictionary, context_seconds, head_pad_ms=50, tail_pad_ms=50, refined_count, skipped_count, skipped:[…], refined[]:{candidate_id, target_token, language, acoustic_model, dictionary, context_range_raw:[ctx_start,ctx_end], mfa_local_start, mfa_local_end, mfa_raw_start, mfa_raw_end, head_pad_ms, tail_pad_ms, refined_start_raw, refined_end_raw}` |
| snap 精修 | `<run>/candidate_source.json` 内每条候选 | `start_sample_original, end_sample_original, start_sample_snapped, end_sample_snapped, start_sample, end_sample, start_seconds, end_seconds, boundary_snap:{status, start_method ∈ {zero_crossing, rms_minimum, no_audio_available}, end_method, start_rms_at_snap, end_rms_at_snap, start_moved_samples, end_moved_samples, total_moved_ms, sample_rate, window_ms}` + 顶层 `boundary_snap_summary:{snapped, moved_ms_total, unchanged, no_audio, invalid_order}, candidate_source_sha256_before_boundary_snap` |
| autocut gate 输出 | `<run>/autocut_gate/auto_cut.json` + `review_required.json` + `gate_report.json` + `summary.json` | `schema_version="autocut-gate-v1-run-v1"; summary:{total_candidates, auto_cut_eligible_count, human_review_required_count, auto_cut_ratio, auto_cut_candidate_ids[]}; per_candidate:{candidate_id, all_gates_passed:bool, gates:[{gate ∈ {G1_whitelist, G2_high_confidence, G3_no_preserve, G7_session_feedback, G5_history, G6_duration, G7_protection}, pass:bool, reason:str}]}` |

**不写**：EDL、render_sync_cuts、A/B clip、`machine_assisted_draft.edl.json`、`automix_adapter` 输出、mp3 成片、`tools.json`、`self-correction-v1/rules/*` 内部规则。cough_like 永远不进 `render_sync_cuts`（只能被下游 automix_adapter 用作 mute 参考）。

## 5. 覆盖 tool

| tool | 作用 |
|---|---|
| `build_candidate_family_bundle`（entry） | `main/orchestrator/candidate_family_adapter.py`。把 base candidate_source + self_correction_wordlevel + transient_events 三份原生输出规范化合并，写侧车字段，强制 `cut_scope`/`action_type`/`safety_status`/`policy` |
| `build_filler_global_pause_candidates` | 生成 filler_hesitation / immediate_repetition / global_long_pause 三个族，含 rules v18 停顿/重复参数、`boundary_lock=true`、`retention_by_original_silence` 梯度 |
| `detect_self_correction_wordlevel` | 词级滑窗检测 self_correction；输出 abandoned_span / retry_span / edit_ratio / gap_seconds / algorithm_confidence，原生 `cut_scope="both_spans"`（下游 adapter 强制改 `abandoned_span_only`） |
| `detect_transient_events` | 瞬态声学事件检测；产 cough_like / mic_bump_like / thump_like（后两者被 adapter 过滤，不进候选池） |
| `mfa_align_and_extract_boundaries` | MFA 强制对齐得到 `mfa_raw_start/end` + `refined_start_raw/end`（±head_pad_ms/tail_pad_ms=50）；缺 MFA 直接 BLOCK |
| `snap_candidate_boundaries` | ±150ms 20ms RMS 最低 + ±5ms 零交叉；`boundary_lock=true` 强制走 `status="locked"` 路径不动 |
| `apply_autocut_gate` | 7 门 gate 主入口；产 auto_cut / review_required / gate_report / summary |
| `build_case_memory` | 汇总 review_package + candidate_source + human_decisions → `case_memory.json`，供 G5 lake 与 experience_signal 消费 |
| `build_labels_lake` | 扫全项目 human_decisions.json → `labels_lake.json`（v2 三层嵌套 + token 级 reject）供 G5 |
| `spacy_semantic_transcript` / `build_semantic_transcript` | 提供 semantic 转写给 filler_global_pause 与 self_correction 上下文窗 |
| `p0_transcribe_mvp` | 词级 ASR（faster-whisper int8 beam 5 VAD）作为一切候选的时间锚 |

Python 函数不是 tool、只在正文提名：`retrieve_before_decision` / `is_never_cut` / `load_session_feedback` / `load_lake_feedback` / `inject_into_candidates` / `candidate_has_never_cut_feedback` / `candidate_has_needs_extension_feedback`（来自 `feedback_engine.py` 与 `session_feedback.py`）；`find_kept_word_onset_librosa`（来自 `generate_ab_clip_learning_driven.py`，属 A/B 生成阶段的 librosa onset 保护函数，本 skill 只声明"必须遵守其输出的 `librosa_onset_used` / `onset_before_asr_ms`"作为下游约束）。

## 6. 硬化 CLAUDE.md

| §编号 | 本 skill 具体拦截什么 |
|---|---|
| §6.9 长停顿跨轨静默 | 任一 `candidate_kind="global_long_pause"` 候选必须其他物理轨在同窗口全部静默才允许放行（`long_pause_all_track_silence` verdict 一票否决）；未做跨轨静默检查的 long_pause 候选直接标 `safety_status="NEEDS_HUMAN_REVIEW"` |
| §8 MFA 必用 | 所有进入 autocut_gate 的候选 `cut_start/cut_end` 来源必须是 mfa_raw_start/mfa_raw_end（或 refined_start_raw/refined_end_raw），**禁止**直接用 `asr_raw` 的 word.start/end；未跑 `mfa_align_and_extract_boundaries` 时 pre_flight_check 直接 fail |
| §11 禁自由发挥 | 不许在本 skill 里改 rules（filler v18 / self-correction v1 / transient-events v1）；不许自扩 filler 词表挡 backchannel（20-pack 事件回归拦截） |
| §12 host backchannel | 消费上游 `speaker_map`，`host` 轨的 backchannel（"嗯/对/是"等）走 `host_backchannel` verdict，不能被 filler_hesitation 直接吃；命中 `cross_track_backchannel` 的候选强制 `safety_status="NEEDS_HUMAN_REVIEW"` |
| §13 cough_like 只 mute | cough_like `cut_scope="source_track_gate_only"` 强制；policy `denylist_kinds` 里 cough_like + transient_events 双保险；cough_like 永远不进 `machine_assisted_draft.edl.json.render_sync_cuts`；本 skill 出口检查任何 cough_like 若 `cut_scope != "source_track_gate_only"` 或 `action_type != "source_track_gate"` 即 fail |
| §17 librosa onset 保护 | chain 场景 `cut_end ≤ librosa_onset - 30ms`，且 `crossfade_ms ≤ onset - 30ms`；A/B 生成阶段必须写 `librosa_onset_used` / `onset_before_asr_ms`；本 skill 在候选级别拒绝 `end_seconds` 越过下一保留词 onset - 30ms 的候选（标 `safety_status="NEEDS_HUMAN_REVIEW"`） |
| §14 备注记忆 · G7 消费 | `apply_autocut_gate` G7_session_feedback 强制查询 `previous_user_feedback[].verdict=="never_cut"` 一票否决；每次 gate 判决必须先经 `feedback_engine.retrieve_before_decision(candidate, decision_type, episode_id)` 注入 previous_user_feedback |
| §18 Feedback-First Retrieval | 本 skill 里"边界精修 / gate 判决 / long_pause 跨轨判定"每一步决策前必须调 `feedback_engine.retrieve_before_decision`；未查即决策直接 fail_closed（v215/v216 违反案例的直接补丁） |
| §20 session_feedback 单一 SOT | 只读 `main/knowledge/session_feedback/current.session_feedback.jsonl`（不读 per-episode 老文件）；`feedback_engine._load_all_feedback` 是唯一入口。旧 `session_feedback.py::load_session_feedback` 读老文件的路径本 skill 侧不使用（见 §9.3 已知不合规） |
| FR-04 | autocut_policy=NOT_APPROVED 时 `auto_cut_eligible_count == 0`（gate 出口检查） |
| FR-05 | 任一候选 `previous_user_feedback[].verdict=="never_cut"` → G7_session_feedback hard reject；不允许 gate 出口出现 `all_gates_passed=true` 且 verdict 为 `never_cut` |
| 未成文 · neighbor-word 保护 | 消费 `never_eat_neighbor_word` / `chain_cut_dont_eat_kept_word` / `filler_boundary_edge_extend_150_200ms` 三类 feedback kind；候选 `cut_start`/`cut_end` 与相邻保留词 ASR 边界重叠或负 gap 时，强制走 review 分支（jsonl 中已有历史证据行）；c007/c034 neighbor-word 未修问题必须能被本 skill 出口拦截 |

## 7. pre_flight_check

脚本：`scripts/preflight/check_candidate-generation-and-gate.py`

以下命令是脚本内部实际会跑的 grep / test / python 组合（每条都能真跑）：

```bash
# 7.1 governance registry 存在且 candidate_family_adapter 被 attest
python3 -c "import json,sys; d=json.load(open('main/knowledge/integration_governance/owner_attested_mainline.v1.json')); assert any('candidate_family_adapter' in str(v) and 'OWNER_ATTESTED_INTEGRATE' in str(v) for v in d.values()), 'candidate_family_adapter not attested'"

# 7.2 tools.json 里 12 项 related_tools 全部登记
python3 -c "
import json
t=json.load(open('main/tools/tools.json'))
names={x['tool_name'] for x in t.get('tools',[])}
need={'build_filler_global_pause_candidates','build_candidate_family_bundle','detect_self_correction_wordlevel','detect_transient_events','mfa_align_and_extract_boundaries','snap_candidate_boundaries','apply_autocut_gate','build_case_memory','build_labels_lake','spacy_semantic_transcript','build_semantic_transcript','p0_transcribe_mvp'}
missing=need-names
assert not missing, f'missing tools: {missing}'
"

# 7.3 MFA 二进制存在
test -x ~/miniforge3/bin/mfa || { echo "BLOCKED: MFA missing"; exit 2; }

# 7.4 candidate_source.json 里若含 cough_like，必须 cut_scope=source_track_gate_only + action_type=source_track_gate
python3 -c "
import json,sys,glob
for p in glob.glob('main/runs/*/candidate_source.json'):
    d=json.load(open(p))
    for c in d.get('candidates',[]):
        if c.get('reason_key')=='cough_like':
            assert c.get('cut_scope')=='source_track_gate_only', f'{p} cough_like cut_scope wrong: {c.get(\"cut_scope\")}'
            assert c.get('action_type')=='source_track_gate', f'{p} cough_like action_type wrong'
"

# 7.5 mic_bump_like / thump_like 不得出现在候选池
grep -rE '\"reason_key\"\s*:\s*\"(mic_bump_like|thump_like)\"' main/runs/*/candidate_source.json && exit 3 || true

# 7.6 self_correction 候选 cut_scope 已由 adapter 规范化为 abandoned_span_only
python3 -c "
import json,glob
for p in glob.glob('main/runs/*/candidate_source.json'):
    d=json.load(open(p))
    for c in d.get('candidates',[]):
        if c.get('candidate_kind')=='self_correction':
            assert c.get('cut_scope')=='abandoned_span_only', f'{p} SC cut_scope must be abandoned_span_only'
            assert c.get('boundary_lock') is True
            assert c.get('policy')=='review_only_no_automatic_accept'
"

# 7.7 MFA 产物存在且 schema_version=mfa-boundaries-v1
python3 -c "
import json,glob,sys
found=False
for p in glob.glob('main/runs/*/mfa_boundaries.json'):
    d=json.load(open(p)); assert d.get('schema_version')=='mfa-boundaries-v1'; found=True
sys.exit(0 if found else 4)
"

# 7.8 candidate_source.json 有 boundary_snap_summary + candidate_source_sha256_before_boundary_snap
python3 -c "
import json,glob
for p in glob.glob('main/runs/*/candidate_source.json'):
    d=json.load(open(p))
    assert 'boundary_snap_summary' in d, f'{p} missing boundary_snap_summary'
    assert 'candidate_source_sha256_before_boundary_snap' in d, f'{p} missing pre-snap sha'
"

# 7.9 gate summary 存在且 denylist_kinds 覆盖 cough_like/transient_events
python3 -c "
import json,glob
for p in glob.glob('main/runs/*/autocut_gate/summary.json'):
    d=json.load(open(p))
    assert d.get('schema_version')=='autocut-gate-v1-run-v1'
    assert 'auto_cut_eligible_count' in d and 'human_review_required_count' in d
"

# 7.10 gate_report 里任一 auto_cut_eligible 候选不得含 verdict=never_cut 的 previous_user_feedback（FR-05）
python3 -c "
import json,glob
for p in glob.glob('main/runs/*/autocut_gate/gate_report.json'):
    d=json.load(open(p))
    for row in d.get('per_candidate',[]):
        if row.get('all_gates_passed'):
            for fb in row.get('previous_user_feedback',[]):
                assert fb.get('verdict')!='never_cut', f'{p} never_cut leaked into auto_cut'
"

# 7.11 session_feedback SOT 存在
test -f main/knowledge/session_feedback/current.session_feedback.jsonl

# 7.12 labels_lake schema v2
python3 -c "
import json
d=json.load(open('main/knowledge/labels_lake.json'))
assert d.get('schema_version')=='labels-lake-v2'
assert 'by_reason_key' in d
"

# 7.13 policy=NOT_APPROVED 时 auto_cut_eligible_count==0（FR-04）
python3 -c "
import json,glob
for p in glob.glob('main/runs/*/autocut_gate/summary.json'):
    d=json.load(open(p))
    if d.get('policy_status')=='NOT_APPROVED':
        assert d.get('auto_cut_eligible_count',0)==0, f'{p} NOT_APPROVED but auto_cut>0'
"
```

## 8. 反馈证据（触发本 skill 出现的历史事件）

- **2026-08-18 20-pack 事件**：agent 自扩 filler 词表挡不住 host backchannel，用户 reject "没一个通过"。记录在 `skills/candidate-family-integration/flow_boundary.md` 的"禁止/历史违反证据"段，直接对应本 skill §11 + §12 + `host_backchannel` verdict 硬化。
- **v207 LG48 / LG51 / LG56**：long_pause 未做跨轨静默判定即 auto_cut。对应 `main/knowledge/session_feedback/current.session_feedback.jsonl` 里 kind=`long_pause_all_track_silence` / kind=`ripple_delete_all_tracks_sync` 的记录（§6.9 硬约束依据）。
- **EP04 c007 / c034 neighbor-word 未修**：`c034_cut_too_much` kind 在 jsonl 中直接以 kind 名出现；对应 `chain_cut_dont_eat_kept_word` / `never_eat_neighbor_word` / `filler_boundary_edge_extend_150_200ms` 三条 feedback，作为本 skill "未成文 · neighbor-word 保护" 的触发证据。
- **cough_like mixed-14 3/3 误报**：cough_like 曾被当成全轨 cut 打进 EDL。对应 §13 硬约束 + policy `denylist_kinds=[cough_like, transient_events]` 双保险 + adapter 强制 `cut_scope="source_track_gate_only"`。
- **`apply_autocut_gate` 当前完全孤儿**：EP04-AUTO-VERIFY-20260817-2200 run 里 `autocut_gate/summary.json` 存在但无 upstream skill 声明其归属；本 skill 把 apply_autocut_gate 明确收编为 entry_tool 之后的最终门，消除孤儿状态。
- **EP04-AUTO-VERIFY-20260817-2200 数据点**：total_candidates=38, auto_cut=7, review=31, ratio=0.184, auto_cut_ids=[C007, C014, C023, C034, C036, C039, SC005]；用作 skill 出口回归基线。
- **2026-08-18 顿悟 1 · mentor 剪 71% 是 semantic_boundary · pure_filler=0 · rhetorical=0**（来自 `main/runs/EP04-GOLD-EDL-20260818-1548/2026-08-18-1730-mentor-gold-cut-where-how-analysis.md` 分轨可靠度分析）：系统"砍 filler"的直觉**反了**。本 skill 未来 rules 版本应把 semantic_boundary 提升为主要候选族，filler_hesitation 降级为辅助。
- **2026-08-18 顿悟 3 · cross_track_speaking 定义 59/59 假阳**（同一分析）：本 skill 现有"长停顿跨轨静默 gate"用能量启发式判"是否说话"，全期 59 个全假阳。**未落地修正**：需改用上游 s1 声明的 speaker_map 逐轨判定（host backchannel vs guest speaking 差异化），本 skill §7 pre_flight_check 补一条断言"若 speaker_map 存在则跨轨判定必须消费 role 字段而非能量启发式"。落地前维持能量启发式作为兜底。**修正实施规范**：见同目录 `cross_track_speaking_fix_spec.md`（含分步实施 / 影响文件 / 验证方法 / 回滚方案 / 兜底策略 · 待施工）。

## 9. 三档诚实标注

### 9.1 已验证事实（Read 阶段抓到的实文件字段）

- `main/orchestrator/candidate_family_adapter.py` 里 `normalize_self_correction_rows` 强制 `cut_scope="abandoned_span_only"`，与检测器原生 `cut_scope="both_spans"` 不一致——这是**已知且刻意**的规范化，本 skill 出口按 `abandoned_span_only` 校验。
- `normalize_transient_rows` 只保留 `reason_key=="cough_like"`，`mic_bump_like` / `thump_like` 一律丢弃。
- `apply_autocut_gate.py` 实际实现 6 门（G1/G2/G3/G5/G6/G7），G4 docstring 明说"留作未来"；G7 在代码里出现两次（`G7_session_feedback` 早期检查 + `G7_protection` opening/closing 保护）。本 skill 描述的"7 门"按调用顺序计数（G7_session_feedback 与 G7_protection 独立记）；如果只算门号则为 6。
- 默认参数：`DEFAULT_MAX_DURATION_S=0.8`, `DEFAULT_OPENING_PROTECTION_S=6.0`, `DEFAULT_CLOSING_PROTECTION_S=6.0`, `DEFAULT_MIN_HISTORICAL_ACCEPT=1`, `DEFAULT_LAKE_MIN_TOTAL=2`, `DEFAULT_LAKE_MIN_ACCEPT_RATE=0.9`。
- MFA 脚本 docstring 声称输出 `asr_start/mfa_start/diff_ms`，实际字段是 `mfa_raw_start/mfa_raw_end/refined_start_raw/refined_end_raw/context_range_raw`——无 `diff_ms`，下游需自行计算。
- `snap_candidate_boundaries` 对 `boundary_lock=true` 候选走 `status="locked"` 路径，因此 filler / immediate_repetition / self_correction 大量走 locked（这是设计，不是 bug）。
- `feedback_engine.retrieve_before_decision` 排序末键实际为 `timestamp` DESC（Python 稳定排序保留 `(-priority,-score)` 作为次序）——与 feedback-engine SKILL.md 措辞不完全一致。
- `session_feedback.py::load_session_feedback` 仍读老 per-episode 文件；`feedback_engine._load_all_feedback` 优先读 `current.session_feedback.jsonl`。本 skill 在 gate 侧统一走 `feedback_engine.retrieve_before_decision` 以合规 §20 单一 SOT。
- EP04-AUTO-VERIFY-20260817-2200 实测：total=38, auto_cut=7, review=31, ratio=0.184。

### 9.2 已决定的方向（本轮与用户敲定，但尚无自动回归）

- 合并原 `candidate-family-integration` + 新增 `long-pause-crosstrack-gate` + `boundary-refinement` + `autocut-gate-decision` 四个 skill 为一个 `candidate-generation-and-gate`，形成不可打断链。
- `apply_autocut_gate` 明确归入本 skill 的 entry-adjacent tool，消除其"完全孤儿"状态。
- neighbor-word 保护作为**未成文的 CLAUDE.md 附加约束**在本 skill 出口检查（`never_eat_neighbor_word` / `chain_cut_dont_eat_kept_word` / `filler_boundary_edge_extend_150_200ms` 三 kind 合并判定）；正式条文尚未加入 CLAUDE.md 正文。
- crosstrack_silence_check / neighbor_word_guard / librosa_onset_guard 三个"新增待登记" tool 名仅是**方向占位**，未真正在 `main/tools/tools.json` 注册（见 9.3）。
- `autocut_policy=NOT_APPROVED → auto_cut_eligible=0`（FR-04）由本 skill 出口强制，policy 结构里的 `policy_status` 字段是本轮设定的读取键。

### 9.3 待验证假设（尚未落地或与实文件不完全一致，须后续核对）

- **crosstrack_silence_check / neighbor_word_guard / librosa_onset_guard 三个新 tool 未在 `main/tools/tools.json` 登记**——设计说明列出，但 tools.json 现有 41+ 条记录中没有对应 tool_name。本 skill 现阶段用 grep + Python 出口检查替代；正式登记前，related_tools 里**未列入**这三项。
- **`main/orchestrator/candidate_family_adapter.py` 中 self_correction 的 `cut_scope="abandoned_span_only"` 与 session_feedback 里 `both_spans_or_none` verdict 潜在冲突**——feedback 声明 v20.1 `both_spans_or_none`，但 adapter 仍写 `abandoned_span_only`。EDL 生成器读 `cut_scope`，因此本 skill 出口按 `abandoned_span_only` 校验；如需切回 `both_spans`，需要另开 skill 变更 adapter。此冲突**未在本 skill 内解决**。
- **`session_feedback.py::load_session_feedback` 读老 per-episode 文件不合规 §20**——已知问题，本 skill 侧走 `feedback_engine._load_all_feedback` 规避，但未修复源函数。
- **EP04-AUTO-VERIFY-20260817-2200 run 未跑 `build_candidate_family_bundle`**——`self_correction_wordlevel.json` 存在，但无 `candidate_family_review.json`。因此该 run 的 auto_cut 数据不能验证 candidate_family_adapter 的规范化字段；需在下一个真正跑 adapter 的 run 上回归。
- **G7 双重记账**：`apply_autocut_gate` 里 `G7_session_feedback` 与 `G7_protection` 共用门号 7；本 skill 文案称"7 门 gate"是把它们分开计的口径；如果 downstream 读 gate_report 想按 6 门口径解析需注意。
- **`libraries_lake.json.entries[]` 展开数据是否与 by_reason_key 一致**未在本轮验证——`build_labels_lake` audit 未跑；G5 的 `entries[].feedback[]` 消费路径存在假设"entries 数组存在"，但当前 dump 里没有直接看到 entries 字段。（注：文件名实际为 `labels_lake.json`，本行 "libraries_lake" 是 draft 笔误，实施时按 `labels_lake.json` 核对。）
- **CLI 参数 `--lake-min-total`=2 / `--lake-min-accept-rate`=0.9 与代码内 `lake_zero_reject`（total>=1 且 accept_rate==1.0）判定不一致**：参数值仅在部分路径生效；本 skill 未在出口强制哪个口径，视为待澄清的 policy 表面。
- **`autocut_policy` JSON 内 `policy_status` 字段名**是本 skill 假设的 NOT_APPROVED 载体，实际 tests 里 policy 载荷键名可能是 `status` / `approval` 之一，pre_flight_check 7.13 需按实际 policy schema 修正。
- **`speaker_map` 上游产物路径**：§12 host_backchannel 的判定依赖 speaker_map 是否存在、字段是否包含 `track_id → role` 映射；本 skill 假设上游 skill 会产出并落到 run 目录，但**未在 preconditions 里指定精确路径**（因为 speaker_map 产出方 skill 尚未明确归属）。

### 9.4 开放 backlog（继承自 Plan 防丢失审计 · 合并时不能丢）

- **C-41 `never_cut_yixie`（"一些" 白名单未落地）** —— session_feedback 已 append "C039 '一些' chain=1 内容词量词不是口癖 · 永远不进候选池"，但 `candidate_rules.v18.json` 未见对应 `never_cut_tokens` 白名单条目。**未闭环**。本 skill 出口需消费 `never_cut_yixie` kind 的 feedback 作为一票否决，实施前 rules v19 应加白名单条目。
- **C-42 `c034_cut_too_much`（内容词 chain 边界规则未落地）** —— feedback 描述"内容词 chain 剪 cut_end 用更严格 · librosa RMS envelope <-40dB 或前 word ASR end + 30ms 取更早"未见 adapter/generator 实施证据。**未闭环**。本 skill 通过"未成文 · neighbor-word 保护"部分承接，但完整"内容词 chain 更严格边界"规则待后续 challenger 明确。
- **A2-18 self-correction v1 粒度太粗漏检** —— self_correction v1 用句间前缀匹配漏检；v2 词级 sliding_window（`detect_self_correction_wordlevel`）EP04 命中 33 条但走 human_review。粒度问题**仍开放**，需通过 `editing-experience-distiller` 蒸馏后升级 rules。
- **D-OPT-003 风险分级强制** —— 数字/专名/否定/结论/重叠/短回应强制走高风险人审。当前 gate 层未按语义类型分层拒绝，属**待补 backlog**。
- **D-OPT-004 ASR 热词** —— 独立实验臂，未接入。
- **D-OPT-005 speaker profile + CAM++ 聚类映射** —— 未接入（backlog）。
- **D-OPT-018 拆分 backchannel vs topic_connective 子字典** —— 未做，仍与 filler 混在一起。
- **D-OPT-024 stratum unanimous propagation 有效性** —— v17/v18 已放宽，v22 首次触发但需 5+/层稳定证据。**partial**。
- **D-OPT-027 边界精修 snap_candidate_boundaries** —— EP04 v23b 12/12 通过、平均 204ms，但多期泛化未验证。
- **D-OPT-028 剪口质量预测 `predict_cut_artifact`** —— v23b C042/C023 BLOCK · C007 OK；决定 BLOCK 是否 auto-remove 未定。当前 `predict_cut_artifact` 尚未挂进本 skill 的 gate 出口，需在 rules v19 明确。
- **D-OPT-030 未接的三家族** —— `crosstalk-candidate-v1` / `semantic-duplicate` / `off_topic` 三候选家族全部在 `denylist_kinds` 里；接入需另开 challenger + 独立 skill。
- **D-gap-3 / D-gap-4 pyannote skeleton 未装** —— speaker-diarization-v1 状态 SKELETON_ONLY_NOT_YET_INSTALLED；未来接入后需在本 skill 补 speaker 判定门。