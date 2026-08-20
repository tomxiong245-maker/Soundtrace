---
name: audition-and-delivery
description: 从 gate 后批准候选 → **从上游 s1 已合成的主导轨切 A/B clip**（只保留一版）→ 人审后 render → QC → delivery report 的完整交付通道 skill · 收编 podcast-editing-orchestrator（大部分）+ 新增 A/B clip 生成 + delivery QC gate · 拦截"v207→v217 一天 12 版"漂移与"EP04-DELIVERY-20260817-1427 单点通过流程未沉淀"。**架构级顺序变更（2026-08-18）**：全片 automix 合成已前置到 s1 接单后立刻做，本 skill 只消费上游主导轨、应用 EDL 剪切、最终响度归一化。触发词：automix, A/B clip, 试听, 交付, delivery, render, QC, 响度, loudnorm, publish_candidate, DELIVERY_MANIFEST, 从主导轨切, 上游主导轨。
status: active
owner: champion
entry_tool: automix_render_speech
related_tools:
  - automix_render_speech
  - generate_ab_clip_learning_driven
  - render_approved_edl
  - assemble_program
  - finish_approved_project
  - analyze_transition_qc
  - analyze_cut_transitions
  - write_delivery_report
  - run_development_benchmark
  - check_current_delivery_sync
  - build_review_package
  - build_priority_review_page
  - create_aligned_ab_previews
  - approve_review_candidates
  - serve_review_ui
  - review_event_routes
  - run_versioning_guard
preconditions:
  - "上游 gate 已产出 machine_assisted_draft.edl.json（schema delivery-edl-v1, variant machine_assisted_draft, actions[].decision=machine_proposed_accept）或 human_approved.edl.json（variant human_approved, global_sync_actions[].decision=human_whole_episode_approved），路径在 main/runs/<episode-run>/ 下"
  - "release_specs.json 已冻结（reference-linear-v1, target_integrated_lufs=-22.2, target_true_peak_dbfs=-0.1 with safety_floor=-1.0, target_lra_lu=7.9, container=mp3, bit_rate_bps=192000, sample_rate_hz=48000, channels=2），路径 main/runs/RELEASE-SPEC-FROM-EP03-20260817-1204/release_specs.json"
  - "音乐模板存在（reference_music_relpath: 音频参考库/raw material/第三集/片头片尾music.mp3, sha256 3f3a7150...ed83）"
  - "N 轨对齐 mono WAV + 词级 canonical 转写（automix_v1.py --tracks / --release-spec / --music / --music-template 均可用）"
  - "current.session_feedback.jsonl 存在（main/knowledge/session_feedback/current.session_feedback.jsonl · §20 单一 SOT）"
postconditions:
  - "automix_full/speech.mono.wav 与 automix_manifest.json（schema automix-run-manifest-v1, adapter_source_sha256/source_edl_sha256/inputs[].sha256/output.sha256/parameters/stats.primary_frame_counts/stats.ambiguous_percent/safety.semantic_decision=false/safety.edl_mutation=false）写入 main/runs/<episode-run>/render_<variant>/"
  - "current_audit_clips/<candidate>_原.mp3, <candidate>_剪.mp3, <candidate>.manifest.json 写入（schema comprehensive_cut-v219, tools_used_all=true, chain_len/n_cut/kept_at_s/asr_range_s/librosa_range_s/final_range_s/prev_word_end_s/next_word_start_s）— 只保留一个版本（禁止 v20X_* 累积）"
  - "每候选 manifest.json 的 automix wav sha256 == render_<variant>/speech.mono.wav sha256（run_versioning_guard 强制比对）"
  - "transition_qc.json（schema rendered-transition-qc-v1, STATUS=OBJECTIVE_ANOMALY_RANKING_SUBJECTIVE_LISTENING_REQUIRED, transition_count/priority_relisten_count）写入 render_<variant>/"
  - "loudness_report.json（schema loudness-qc-v1, target_spec/candidates[].measured/vs_target.verdict∈{PASS_ALL_TARGETS,OFF_TARGET_TOO_LOUD,...}）写入 delivery 目录"
  - "DELIVERY_MANIFEST.json（schema delivery-manifest-v1, approval_chain[]必须含 role=mentor + role=project_owner 两条 verdict=approved, delivered_master, content_provenance, loudness_correction.method=two_pass_loudnorm_linear, independent_verification.overall_verdict, target_spec_reference, qc_report_relpath, boundaries_respected[]）+ state.json（schema delivery-state-v1, state ∈ {SOURCE_MP3_IDENTIFIED, LOUDNORM_CORRECTED, AB_LISTENING_SENT, DELIVERY_DECISION_RECORDED, MACHINE_ASSISTED_DRAFT_RENDERED, ...}, history[]） 写入 main/runs/<EPXX-DELIVERY-YYYYMMDD-HHMM>/"
  - "DELIVERY_REPORT.md 通过 write_delivery_report 产出（含 双 EDL global_sync_actions/source_track_gates 计数、剪口复听排序引用 transition_qc.json、响度实测、发布状态）"
  - "check_current_delivery_sync 验证 统筹全局/当前项目进度.md CURRENT_DELIVERY_FACTS 与 live run 一致"
  - "Stage 6.7 · Optuna TPE iterative refinement 自动跑 (default ON · 用户 2026-08-19 晋升 · PROMOTION_MANIFEST 见 统筹全局/) · 对 NEEDS_HUMAN_REVIEW 候选 5 次上限迭代 · warm start by kind"
  - "Stage 6.8 · case embedding retrieval 自动跑 (default ON · index 未 build 静默 skip)"
  - "Stage 6.9 · 若 audit_verdicts.json 里有人审 REJECTED · 自动触发 re_iterate_from_audit.py 跑二轮 Optuna 10 iter (skip_warm_start · seed=43 · 探索新区) · 用户 2026-08-19 明确要求 · **默认开** · 详见 PROMOTION_MANIFEST_2026-08-19.md"
  - "Stage 3.7 · LLM 语义 filter (candidate-semantic-veto skill · 唯一候选决定者) · 生成 llm_verdicts.json · Stage 5 EDL 只用 KEEP_CUT"
covers_decision_points:
  - automix_before_ab_clip
  - ab_clip_single_variant_only
  - splice_method_must_be_crossfade
  - current_audit_clips_single_dir
  - release_specs_gate_before_publish_candidate
  - approval_chain_mentor_plus_project_owner
  - double_edl_render_variants
  - loudness_two_pass_linear
  - source_track_gate_never_in_render_sync_cuts
covers_claude_md_rules:
  - §9
  - §11
  - §13
  - §15
  - §16
  - §17
  - §20
  - F06
  - FR-06
  - FR-07
pre_flight_check: scripts/preflight/check_audition-and-delivery.py

---

# audition-and-delivery

## 1. 定位

从 gate 通过后的批准候选出发，**消费上游 s1 已合成的主导轨**（`render_prep/speech.mono.wav`），生成 A/B clip 单一变体、人审、EDL 剪切 render、transition/loudness QC、直到 DELIVERY_MANIFEST + DELIVERY_REPORT 的整条交付通道；把原 `podcast-editing-orchestrator`（Challenger 研发定位、preconditions 明确说"不实现最终渲染"）与新的 A/B clip 生成、delivery QC gate 合并为一个"gate 后到交付前"的单一 skill。不做候选生成、不做 boundary 决策，不改 EDL。

**2026-08-18 架构级顺序变更**：全片 automix 合成（3 条独立轨 → 一条主导轨）**已前置到 s1**（接单登记员出口的最后一步执行动作）。本 skill 出口第一步不再是"自己合成主导轨"，而是**验证上游主导轨已存在 + SHA 一致**，然后直接对主导轨应用 EDL 剪切 + 最终双遍 loudnorm。这样 A/B clip 与成片使用的都是**同一条上游主导轨的下游派生**，从架构层消除了"A/B 拼接不是从 automix 切"这类历史 bug 的可能性（原 CLAUDE.md §9 用户 4 次强调）。

## 2. 何时激活

上游触发 postcondition：
- machine_assisted_draft.edl.json 或 human_approved.edl.json 已冻结在 main/runs/<episode-run>/ 下
- release_specs.json 已按 EP03 Mentor 成品冻结（-22.2 ± 1.0 LUFS · TP -0.1 或 safety -1.0 · LRA 7.9）
- 用户/编辑说："跑 automix / 出试听 / 出 A/B / 渲染 / 交付 / 出 delivery report / QC 一遍 / 响度对不对"

不激活的情形：
- 候选还没过 gate（仍在 autocut_gate G1–G7 阶段）
- release_specs.json 尚未冻结（此时输出只能叫"试听草稿/技术样片"，见 F06 五档 delivery 状态）
- 只是本地闲聊或纯文件读写

## 3. 读什么

| 文件 | 关键 schema 字段 |
|---|---|
| main/runs/<episode-run>/machine_assisted_draft.edl.json | schema delivery-edl-v1; variant=machine_assisted_draft; actions[].{action_id, action_type=global_sync_cut, candidate_id, start_sample, end_sample, applies_to_all_tracks, decision=machine_proposed_accept, decision_provenance=autocut_gate_v1, risk_level, post_cut_pause_ms}; provenance=autocut_gate_v3 zero-touch |
| main/runs/<episode-run>/human_approved.edl.json | schema delivery-edl-v1; variant=human_approved; run_identity_sha256; sample_rate_hz=48000; frame_count; tracks[].{track_id, input_relpath, audio_sha256}; global_sync_actions[].{action_id, action_type, start_sample, end_sample, applies_to_all_tracks=true, decision=human_whole_episode_approved, decision_provenance=human_whole_episode_audition, original_provenance}; source_track_gates[] |
| main/runs/RELEASE-SPEC-FROM-EP03-20260817-1204/release_specs.json | reference-linear-v1; target_integrated_lufs=-22.2; target_true_peak_dbfs=-0.1; safety_floor=-1.0; target_lra_lu=7.9; music_integrated_lufs=-14.8; music_voice_gap_lu=7.4; container=mp3; bit_rate_bps=192000; sample_rate_hz=48000; channels=2; reference_master_relpath+SHA; reference_music_relpath+SHA; evidence_run_relpath |
| 音频参考库/raw material/第三集/片头片尾music.mp3 | sha256 3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83 |
| main/knowledge/session_feedback/current.session_feedback.jsonl | 每行 schema_version=session-feedback-v1; timestamp; episode_id; reviewer; source; kind; candidate_pattern; verdict; note; action_taken?; case_ref? — 用 `from feedback_engine import retrieve_before_decision, is_never_cut` 在为每候选决定 splice/pause 前查一遍 |
| main/knowledge/labels_lake.json | entries[].feedback[] · 同上通过 feedback_engine._load_all_feedback 合并 |
| 统筹全局/当前项目进度.md | `<!-- CURRENT_DELIVERY_FACTS:start -->` … `end -->` marker，供 check_current_delivery_sync 校对 |
| 上游 automix 输入 | tracks (nargs+, mono WAV); music; music-template; template-id=reference-linear-v1 |
| 上游 raw ZOOM WAV（同 speaker） | 作为 room tone 源（在 A/B clip 生成里，从最近 30s 滑窗 step 200ms、min_dur 500ms 找最低 dBFS 段）|

## 4. 写什么

| 文件 | 关键 schema 字段 |
|---|---|
| main/runs/<episode-run>/render_<variant>/speech.mono.wav | ffmpeg two-pass loudnorm 归一 → mp3 192 kbps stereo；wav 作为 A/B clip 的 --automix-wav 唯一合法来源 |
| main/runs/<episode-run>/render_<variant>/automix_manifest.json | schema automix-run-manifest-v1; run_id; run_identity_sha256; variant; edl_path; source_track_gate_count; adapter_source_sha256; source_edl_sha256; inputs[].sha256; output.sha256; parameters.{min_gap_db=3.0, secondary_atten_db=-12.0, crossfade_ms=30, frame_ms=20, loudnorm_passes=2}; stats.{primary_frame_counts, ambiguous_percent}; safety.{semantic_decision=false, edl_mutation=false} |
| main/runs/<episode-run>/current_audit_clips/<candidate>_原.mp3 | 原片对照 |
| main/runs/<episode-run>/current_audit_clips/<candidate>_剪.mp3 | pydub.AudioSegment.append(room_full, crossfade=safe_crossfade) + `back` （back 不 crossfade） |
| main/runs/<episode-run>/current_audit_clips/<candidate>.manifest.json | schema comprehensive_cut-v219; candidate_id; episode_id; learned_params; n_learned_rules; cut_range_s; cut_duration_ms; pause_ms; n_cut_in_chain; has_segment_separator; librosa_onset_used; kept_word_asr_start_s; room_tone_dBFS; chain_len; n_cut; kept_at_s; asr_range_s; librosa_range_s; final_range_s; prev_word_end_s; next_word_start_s; orig_path; cut_path; self_check.{duration_expect_ms, duration_actual_ms, duration_ok}; tools_used_all=true（含 feedback_first_retrieval (CLAUDE.md §18), librosa.onset (backtrack=True), librosa.feature.rms, noisereduce, pydub.crossfade + append, soundfile, scipy.signal）；跳过状态：skipped=never_cut_feedback / skipped=no_asr_match |
| main/runs/<episode-run>/render_<variant>/transition_qc.json | schema rendered-transition-qc-v1; STATUS=OBJECTIVE_ANOMALY_RANKING_SUBJECTIVE_LISTENING_REQUIRED; DEFAULT_CONTEXT_MS=150.0; DEFAULT_PRIORITY_COUNT=5; transition_count; priority_relisten_count |
| main/runs/<EPXX-DELIVERY-YYYYMMDD-HHMM>/loudness_report.json | schema loudness-qc-v1; target_spec; candidates[].{label, relpath, sha256, size_bytes, measured.{integrated_lufs, true_peak_dbfs, lra_lu, threshold_lufs}, vs_target.{integrated_delta_lu, true_peak_over_safety_floor_db 或 true_peak_headroom_db, lra_in_tolerance, verdict∈{PASS_ALL_TARGETS, OFF_TARGET_TOO_LOUD, ...}}, container.{codec, sample_rate, channels, bit_rate, duration_seconds}}; correction_method.{type=two_pass_loudnorm_linear, pass1_stderr_measurements, pass2_ffmpeg_filter, pass2_encoder, content_preserved, processing_time_seconds} |
| main/runs/<EPXX-DELIVERY-YYYYMMDD-HHMM>/DELIVERY_MANIFEST.json | schema delivery-manifest-v1; run_id; episode_id; state; created_at; approval_chain[].{role∈{mentor, project_owner}, reviewer, scope, verdict=approved, note} — **两条 role 必须齐全**；delivered_master.{relpath, sha256, size_bytes, duration_seconds, container, sample_rate_hz, channels, bit_rate_bps}; content_provenance.{source_run_relpath, source_run_state, source_master_relpath, source_master_sha256, content_edited_by, immutable_wrt_this_delivery}; loudness_correction.{method=two_pass_loudnorm_linear, pass1_measurements_from_source.{input_i_lufs, input_tp_dbfs, input_lra_lu, input_thresh_lufs, target_offset_lu}, pass2_ffmpeg_filter, pass2_encoder, processing_time_seconds}; independent_verification.{measured_at, integrated_lufs, true_peak_dbfs, lra_lu, threshold_lufs, vs_target, overall_verdict}; target_spec_reference.{spec_source_relpath, spec_id, spec_frozen_at, spec_frozen_by}; qc_report_relpath; downstream_pipeline_impact; boundaries_respected[] |
| main/runs/<EPXX-DELIVERY-YYYYMMDD-HHMM>/state.json | schema delivery-state-v1; state 序列 SOURCE_MP3_IDENTIFIED → LOUDNORM_CORRECTED → AB_LISTENING_SENT → DELIVERY_DECISION_RECORDED（另可见 MACHINE_ASSISTED_DRAFT_RENDERED / F06 五档：human_approved_delivery / policy_authorized_delivery / REWORK / HOLD / publish_candidate / FINAL_QC_REQUIRED）；history[].{from, to, at, note} |
| main/runs/<EPXX-DELIVERY-YYYYMMDD-HHMM>/DELIVERY_REPORT.md | 由 write_delivery_report 产出；段落含 episode_id 标题 / Run + 状态 + 输入 (track_count, sample_rate, frame_count) / 自动身份 + QC / 历史动作范围 (sync_cuts + source_track_gates) / 剪口来源 (human_accept vs machine_proposed_accept 计数) / 双 EDL (global_sync_actions + source_track_gates 计数) / 剪口复听排序（引用 transition_qc.json 的 transition_count + priority_relisten_count）/ special_scope 冻结整片试听批准范围说明 / 响度实测 (integrated_lufs / true_peak_dbtp / delta_from_working_target) / 音乐 / 发布状态；命令行参数 `--final-status <F06 状态名>` `--special-scope` |

## 5. 覆盖 tool

- **automix_render_speech**（entry）— 调用 automix_v1.py：N 轨 20ms RMS → 主导轨 gain envelope → mono → amix → 拼片头片尾 → ffmpeg 两遍 loudnorm → mp3 192 kbps。传 `--edl` 时先 `parse_render_sync_cuts` 逐轨 apply（sample-precise + symmetric crossfade），仅电平，不改 EDL。
- **generate_ab_clip_learning_driven** — v215+ 唯一合规入口。必须传 `--automix-wav`（来自 render_<variant>/speech.mono.wav）+ `--raw-track-wav`（同 speaker）。用 librosa.onset (backtrack=True) 精确辅音起音 + pydub.AudioSegment.reverse 避 room tone 循环 + pydub.AudioSegment.append(crossfade) sample-level 拼接。
- **render_approved_edl** — 人审批准的 EDL → 单轨/成片渲染。
- **assemble_program** — 片头片尾 + 主体拼接（reference-linear-v1 timing）。
- **finish_approved_project** — 交付前收尾（写入 DELIVERY_MANIFEST/state.json、锁定 delivered_master）。
- **analyze_transition_qc** / **analyze_cut_transitions** — 后渲染剪口客观异常排序，产出 transition_qc.json；只诊断，不改音频/EDL。
- **write_delivery_report** — 读 run_identity.json + input_manifest.json + qc_report.json + all_candidates.json + prediction_manifest.json + 双 EDL → DELIVERY_REPORT.md。
- **run_development_benchmark** — 基准跑一遍确认库/参数无回归。
- **check_current_delivery_sync** — 校对 统筹全局/当前项目进度.md 的 CURRENT_DELIVERY_FACTS marker 与 live run + review UI markers (`data-feedback`, `/api/save`, `semantic_context`, `id="run"`, `id="scope"`, `review_scope`, `不代表风险`) 一致。
- **build_review_package** / **build_priority_review_page** / **create_aligned_ab_previews** / **approve_review_candidates** / **serve_review_ui** / **review_event_routes** — 试听 UI 打包、优先复听排序、对齐 A/B 预览、批准回写、事件路由。
- **run_versioning_guard** — 校验每候选 manifest.json 的 automix wav sha256 == render_<variant>/speech.mono.wav sha256，并强制 current_audit_clips/ 单一目录（拦 v20X_* 累积）。

正文函数级依赖（非 tool，故不入 related_tools）：`from feedback_engine import retrieve_before_decision, is_never_cut` 在 A/B clip 生成、每候选决策前调用；写方向 `analyze_feedback + apply_decision` 由 user-feedback-loop skill 承担、本 skill 只读。

## 6. 硬化 CLAUDE.md

- **§9**：A/B clip 前必须先跑 automix 全片；`--automix-wav` 必须指向 render_<variant>/speech.mono.wav；候选 manifest sha 与 speech.mono.wav sha 不一致 → 直接 fail。
- **§11**：Automix 严禁触及 EDL 决策；automix_manifest.json 的 safety.edl_mutation=false 必须成立。
- **§13**：`source_track_gate_only` 类候选永远不进 machine_assisted_draft.edl.json 的 render_sync_cuts；只在 automix_adapter 里 mute 单轨该段。
- **§15**：pydub / librosa / noisereduce / soundfile / scipy 已装即必用；current_audit_clips/*.manifest.json 的 `tools_used_all` 必须 true。
- **§16**：拼接必须 `pydub.AudioSegment.append(...crossfade=...)` 或 ffmpeg `acrossfade`；**禁用** concat + anullsrc + afade 硬拼；safe_crossfade = max(20, min(120, len(room_full)-10, len(front)-10))。
- **§17**：cut boundary 必须以 `librosa.onset.onset_detect(delta=0.02, backtrack=True)` 精确辅音起音为准，不用 ASR word start（后者晚 100–150ms 会吃保留词）。
- **§20**：feedback 只读 `main/knowledge/session_feedback/current.session_feedback.jsonl`（单一 SOT）+ `labels_lake.json.entries[].feedback[]`；禁止读 per-episode 老文件；追加只在 current.* 里加行（走 user-feedback-loop）。
- **F06**：release_specs 通过前，输出**只能**叫"试听草稿/技术样片/HOLD"；`publish_candidate` 需 release_specs 通过 + approval_chain 含 mentor + project_owner + FINAL_QC_REQUIRED 已过。
- **FR-06 / FR-07**：双 EDL 来源/目录/字段可自动区分（variant, decision, decision_provenance, provenance, run_identity_sha256/tracks[]/frame_count 与 actions vs global_sync_actions 键名）；失败续跑 / 缓存损坏 / 参数变化 / 音乐校验失败均 fail closed。

## 7. pre_flight_check

样例命令（可直接跑，全部零参数）：

```bash
# 1) release_specs 冻结存在
test -f "/Users/renting/Desktop/minglue/剪辑项目/main/runs/RELEASE-SPEC-FROM-EP03-20260817-1204/release_specs.json"

# 2) 音乐 sha 未漂
python -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/Users/renting/Desktop/minglue/剪辑项目/音频参考库/raw material/第三集/片头片尾music.mp3').read_bytes()).hexdigest())" \
  | grep -q '^3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83$'

# 3) 单一 SOT feedback 文件存在（§20）
test -f "/Users/renting/Desktop/minglue/剪辑项目/main/knowledge/session_feedback/current.session_feedback.jsonl"

# 4) A/B clip 只保留一版：run 下无 v20X_* 累积目录
! ls -d /Users/renting/Desktop/minglue/剪辑项目/main/runs/*/v20[0-9]_* 2>/dev/null | grep -q .

# 5) current_audit_clips manifest.tools_used_all == true
for f in /Users/renting/Desktop/minglue/剪辑项目/main/runs/*/current_audit_clips/*.manifest.json; do
  python -c "import json,sys;m=json.load(open('$f'));assert m.get('tools_used_all') is True, '$f'"
done

# 6) A/B clip automix wav sha == render_<variant>/speech.mono.wav sha
python /Users/renting/Desktop/minglue/剪辑项目/scripts/preflight/check_audition-and-delivery.py --check ab_wav_sha_match

# 7) machine_assisted_draft.edl.json 无 render_sync_cuts 引用 source_track_gate_only 候选（§13）
grep -L 'source_track_gate_only' /Users/renting/Desktop/minglue/剪辑项目/main/runs/*/machine_assisted_draft.edl.json

# 8) DELIVERY_MANIFEST.approval_chain 必须含 mentor + project_owner
python -c "import json,glob;[__import__('sys').exit(1) for m in map(json.load,map(open,glob.glob('/Users/renting/Desktop/minglue/剪辑项目/main/runs/*-DELIVERY-*/DELIVERY_MANIFEST.json'))) if {r['role'] for r in m.get('approval_chain',[])} < {'mentor','project_owner'}]"

# 9) 拼接方法白名单（§16）：manifest.tools_used 必含 pydub.crossfade + append 或 ffmpeg acrossfade
grep -RE 'pydub\.crossfade \+ append|ffmpeg acrossfade' /Users/renting/Desktop/minglue/剪辑项目/main/runs/*/current_audit_clips/*.manifest.json

# 10) 双 EDL 变体字段冲突自检
python /Users/renting/Desktop/minglue/剪辑项目/scripts/preflight/check_audition-and-delivery.py --check edl_variant_fields
```

pre_flight_check 脚本本体位于 `scripts/preflight/check_audition-and-delivery.py`（**待创建**，见 §9 待验证假设）。

## 8. 反馈证据

触发本 skill 的历史事件：

- **v207 → v217 一天 12 版漂移**（main/runs/EP04-AUTO-VERIFY-20260817-2200/）：v208 只落 candidate list、无 splice_method；v210 首次上 pydub crossfade + room tone（但 room_tone_ms 高达 957920）；v213 引入 dynamic pause 但 self_check_duration_ok=false；v214 才补 librosa.onset 保护保留词；v217 才做 retrieve_before_decision 前置查询。→ 本 skill 用 run_versioning_guard + current_audit_clips 单目录约束把"一天 12 版"锁到只保留一个。
- **LP01 / C023 剪辑痕迹**：LP01/LP02 是 pronoun_chain_3plus 场景；C023 chain_len=4 n_cut=3 kept_at_s=960.08，早期版本 pause 350ms 固定，v213 起才动态。→ 触发 kind `chain_cut_dont_eat_kept_word` / `cut_boundary_from_librosa_onset` / `pause_dynamic_by_cut_count`（current.session_feedback.jsonl 中已收录相应规则；C036 chain_len=3 n_cut=2 kept_at_s=1768.95 同类）。
- **C007 反馈应用**：kind `boundary` + verdict `needs_extension` → 扩 180ms 整词剪 cut_ms=1040（v217_feedback_driven 首次实测生效）。
- **EP04-DELIVERY-20260817-1427**：唯一双审（mentor + project_owner，approval_chain[].verdict=approved）通过成品，state 序列 SOURCE_MP3_IDENTIFIED → LOUDNORM_CORRECTED → AB_LISTENING_SENT → DELIVERY_DECISION_RECORDED，loudnorm 双遍从 -16.38 LUFS 拉到 -22.46 LUFS（vs_target delta -0.26 LU · TP headroom 5.91 dB · verdict PASS_ALL_TARGETS），但**未跑 write_delivery_report**（目录下无 DELIVERY_REPORT.md）→ 本 skill 把 write_delivery_report 列入 postcondition，防止"单点通过、流程未沉淀"。
- **feedback jsonl 中直接命名的元规则**：`always_call_feedback_first_retrieval_before_decide`、`splice_must_be_pydub_or_ffmpeg_crossfade`、`tools_installed_must_use`、`single_version_current`、`mentor_final_gate`、`preview_and_final_must_match_mix`、`mp3_peak_verify_after_encode`、`crossfade_length_range`、`crossfade_preserves_consonant_onset`、`room_tone_from_same_recording`、`pause_dynamic_by_cut_count`、`ripple_delete_all_tracks_sync` — 本 skill 的硬化条款与 preflight 逐条对应。
- **2026-08-18 顿悟 2 · crossfade per-episode constant · 不因音频局部特征调制**（来自 `main/runs/EP04-GOLD-EDL-20260818-1548/2026-08-18-1730-mentor-gold-cut-where-how-analysis.md`）：本 skill 的 crossfade / gap_before / gap_after / boundary_offset / RMS thresholds / asymmetric_head_pad 全部走 **PARAMETER 单一冻结点**（CLAUDE.md §21）——plan.json 冻结时读一次，绝**不**在候选级别动态改。之前 A/B clip 生成里"safe_crossfade = max(20, min(120, len(room_full)-10, len(front)-10))"的动态计算是**为了避免越界的安全钳位**，不是"根据音频局部特征调制"——两者混淆过一次，本轮明标。
- **2026-08-18 EP04-COMPREHENSIVE-20260818-1730/current_audit_clips/** 8 段 mentor gold cut A/B mp3（sync 到 `交付-2026-8-17` 2.7GB）：符合本 skill "单一目录 · 覆盖上一版" 契约，是学习流选择器（`docs/learning-flow-selector.md`）"参数学习流"的证据源。

## 9. 三档诚实标注

**已验证事实（读代码/读产物直接抓到）**
- automix_v1.py CLI 参数集、pipeline 8 步、两遍 loudnorm 双遍 linear、mp3 192 kbps 参数。
- automix_adapter.py 输出 automix_manifest.json 的 schema 字段名与 safety.semantic_decision=false / safety.edl_mutation=false。
- generate_ab_clip_learning_driven.py v215 的 `--automix-wav` / `--raw-track-wav` 必需性、pydub.AudioSegment.append(crossfade) 拼接方法、librosa.onset(backtrack=True) 精确起音、pydub.reverse 反向拼 room tone、safe_crossfade 公式、dynamic_pause_ms 公式。
- current_audit_clips/ 下 comprehensive_cut-v219 manifest 全部字段（含 tools_used_all=true、chain_len/n_cut/kept_at_s、skipped 状态）。
- release_specs.json 全部数值阈值与两个参考文件 SHA。
- DELIVERY_MANIFEST.json (delivery-manifest-v1) 与 state.json (delivery-state-v1) 的字段集与 EP04 实测状态序列。
- 双 EDL 字段差异：human_approved 有 run_identity_sha256/tracks[]/frame_count!=0、用 global_sync_actions；machine_assisted_draft 用 actions[] + candidate_id/risk_level/post_cut_pause_ms、frame_count=0、provenance="autocut_gate_v3 zero-touch"。
- transition_qc.py 的 SCHEMA_VERSION / STATUS / DEFAULT_CONTEXT_MS=150.0 / DEFAULT_PRIORITY_COUNT=5 / VARIANTS。
- loudness_report.json (loudness-qc-v1) 全部字段与 EP04 codex_original vs loudnorm_corrected 实测数值。
- current.session_feedback.jsonl 是单一 SOT（§20）；feedback_engine._load_all_feedback 已优先读 current.\*；`from feedback_engine import retrieve_before_decision, is_never_cut` 是稳定接口。

**已决定的方向（本轮设计、需在项目文档中落地）**
- A/B clip 只保留一版，current_audit_clips/ 为唯一目录，禁止 v20X_* 累积。
- A/B clip 输入 wav 的 sha256 必须与 automix render_<variant>/speech.mono.wav sha256 一致，由 run_versioning_guard 校验。
- 拼接方法白名单只有 pydub.AudioSegment.append(crossfade) 与 ffmpeg acrossfade；禁 concat+anullsrc+afade。
- DELIVERY_MANIFEST.approval_chain 必须同时含 role=mentor 与 role=project_owner 两条 verdict=approved。
- 交付目录仅在 release_specs 通过 + FINAL_QC_REQUIRED 过 + approval_chain 完整后，state 才允许标 `publish_candidate`；单机器版路径按 F06 走 policy_authorized_delivery。
- 合并原 podcast-editing-orchestrator（大部分）+ 新 ab-clip-generation + 新 delivery-qc-gate 为本 skill 单一入口。

**待验证假设（事实清单未证实 · skill 落地前需确认）**
- `run_versioning_guard`、`analyze_transition_qc`、`analyze_cut_transitions`、`write_delivery_report`、`run_development_benchmark`、`check_current_delivery_sync`、`build_review_package`、`build_priority_review_page`、`create_aligned_ab_previews`、`approve_review_candidates`、`serve_review_ui`、`review_event_routes`、`assemble_program`、`finish_approved_project`、`render_approved_edl`、`automix_render_speech`、`generate_ab_clip_learning_driven` 这 17 个名字是否已在 tools.json 登记为 tool（事实清单只列了对应 Python 脚本存在；tools.json 未直接展开可核对）。若未登记，需先补登记或改为脚本调用契约。
- `scripts/preflight/check_audition-and-delivery.py` 尚未创建，第 6/10 条 preflight 依赖之。
- EP04-DELIVERY-20260817-1427 目录下未见 DELIVERY_REPORT.md，说明该 delivery 走了简化路径；本 skill 要把 write_delivery_report 强制进 postcondition，需向 champion 确认是否回补该期 report。
- `write_delivery_report.py` 中 `DELIVERY_REPORT.md` 段落集合来自 docstring 推断；实际输出格式待首次真跑一次成品后再冻结。
- retrieve_before_decision 排序末键实际为 `timestamp` DESC（与 SKILL 文案"verdict priority DESC + match score DESC + timestamp DESC"不一致）— 本 skill 引用时按"实现事实：timestamp DESC 首要 + 稳定排序保留 (priority, score) 次序"处理，如上游修 SKILL 文案要同步。
- `source_track_gate_only` 候选不进 render_sync_cuts 的约束来自 CLAUDE.md §13，事实清单未直接展示该字段值样例；preflight 第 7 条以 grep 关键字兜底，可能漏检字段以其它命名出现的情况。

### 9.4 开放 backlog（继承自 Plan 防丢失审计 · 合并时不能丢）

- **D-OPT-017 剪辑痕迹 rendering gate** —— 80ms→120ms crossfade + room tone splice 已加，但 **mentor 复听验证未完成**；4/14 feedback "剪辑痕迹明显" 未闭环。本 skill 出口的 transition_qc.json 只做客观异常排序 · 主观自然度仍需 mentor 通过。
- **D-OPT-022 审核前端保存路径可见性** —— 前端只在 HTTP 响应返回 draft/decisions 路径，页面不显示，reviewer 不知存哪。等主线 v22 审核完成后处理。**未修**。
- **E4-5 / E4-6 · SCORECARD 五档 NOT_MEASURED** —— 候选召回 / 无候选区漏检 / 剪口自然度 / 严重语义误删 / 净节省时间（净节省已从判据撤除）四项 scorecard 状态仍是 `NOT_MEASURED`；本 skill 的 `run_development_benchmark` 只产结构，主观质量门未跑。
- **D-gap-9 AUDIO-CLEANUP-20260817 副作用** —— `main/runs/EP04-*/` 下多个 v20 mp3 已被清理，但 `统筹全局/当前项目进度.md` CURRENT_DELIVERY_FACTS 的 mp3 relpath 未同步改（作历史证据）；`check_current_delivery_sync` 若挂需用户拍板，不得自行修补 relpath。
- **F-13 intro-outro-music-v1 challenger 无 README** —— 状态不明；music_templates reference-linear-v1 相关；`music_asset_sha256` 校验若因该 challenger 变更漂移需另开任务。
- **F-8 denoise-audit-v1** —— 诊断 only，回答"为什么 EP04 v12 听感差于 EP03"；不做生产，但结论应回流到本 skill 的 loudness 判定。
- **EP04-DELIVERY-20260817-1427 未跑 `write_delivery_report`** —— 目录下无 `DELIVERY_REPORT.md`；说明该 delivery 走了简化路径。本 skill 把 write_delivery_report 硬化进 postcondition，防止下期 EP05 又漏；同时需要向 champion 确认是否回补该期 report。
- **D-OPT-013 发布规格 partial** —— RELEASE-SPEC-FROM-EP03-20260817-1204 已冻结 EP03 实测（-22.2/-0.1/7.9），但仅一期证据；多期泛化未验证。
- **D-OPT-009 一键创建本期/打开审核/恢复渲染/归档** —— 未做，仍是散命令。