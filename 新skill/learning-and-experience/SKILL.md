---
name: learning-and-experience
description: 四合一学习层 SKILL——单期偏好学习驱动、每次 save 后 online refresh 闭环、多期离线案例蒸馏、**端到端剪辑偏好学习（从人工成品 + raw material 反推剪辑偏好）**共用一套 labels_lake / case_store / active_label_learning_snapshot 读写协议。命中词：标签学习 / label learning / preference snapshot / labels_lake / case_store / online refresh / regate / 案例蒸馏 / experience distiller / shadow prediction / backtest / autocut_policy / active_label_learning_snapshot / **端到端偏好学习 / gold EDL 反推 / mentor 成品学习 / 人工剪辑成品 / 人工成片 / 人剪版 / mentor 成品**。触发关键词：label_learning_driver、refresh_lake_and_regate、experience_consumer_adapter、build_case_memory、preference snapshot、extract_gold_cut_features、reverse_edl_from_master。
status: active
owner: champion
entry_tool: label_learning_driver
related_tools:
  - apply_preference_snapshot
  - refresh_lake_and_regate
  - build_labels_lake
  - experience_consumer_adapter
  - build_case_memory
preconditions:
  - "main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json 存在且 schema_version=='active-label-learning-snapshot-pointer-v1'"
  - "active_label_learning_snapshot.v1.json.active_refresh_run_relpath 指向的 refresh_manifest.json 与 preference_snapshot/snapshot_manifest.json 都存在且哈希与指针记录一致"
  - "main/orchestrator/label_learning_driver.py 存在且 driver_source_sha256() 可计算"
  - "稳定生产/challengers/experience-ingestion-v1/case_store/ACTIVE_SNAPSHOT.md 与 index.json 指向的活跃案例集存在"
  - "target run 目录下 run_identity.json / input_manifest.json / candidate_source.json 齐全"
postconditions:
  - "单期 evidence 包写入 main/runs/LABEL-LEARNING-DRIVER-v<N>-<ts>/ 内 shadow_prediction_manifest.json / backtest_report.json / evidence_manifest.json / target_integrity.before.json / target_integrity.after.json / RUN_REPORT.md"
  - "online refresh 写入 main/runs/LABEL-LEARNING-AUTO-<label>-<ts>-<seq>/preference_snapshot/ 与同目录 refresh_manifest.json (schema=='label-learning-auto-refresh-manifest-v1', activation=='PENDING_ATOMIC_POINTER_UPDATE')"
  - "refresh_lake_and_regate --run 后 main/knowledge/labels_lake.json (schema=='labels-lake-v2') 增量覆写；target run 下 autocut_gate_regated/summary.json 与 regate_diff.json 生成"
  - "案例蒸馏产物只增量写入 稳定生产/challengers/experience-ingestion-v1/case_store/cases/<EP>.jsonl / exclusions.jsonl / quarantine.jsonl / index.json / ingestion_manifest.json；不写 experience_cases.jsonl（本项目不存在该文件）"
  - "所有 shadow / backtest 产物内 policy.autocut_policy == 'NOT_APPROVED' 且 creates_human_decision / creates_edl_action / creates_autocut_permission 全部 false"
covers_decision_points:
  - single-episode-preference-learning
  - post-save-online-refresh
  - cross-episode-case-distillation
  - end-to-end-preference-learning
  - shadow-prediction-emission
  - labels-lake-regate
  - active-snapshot-pointer-swap
  - external-knowledge-consumption
covers_claude_md_rules:
  - "§10"
  - "F08 §120"
  - "F08 §121"
  - "F08 §122"
  - "F08 §123"
  - "F08 §124"
  - "F08 §125"
  - "F08 §126"
  - "F08 §127"
pre_flight_check: scripts/preflight/check_learning-and-experience.py
---

# learning-and-experience

## 1. 定位

本 skill 是 minglue 剪辑项目"学习层"的唯一入口，把原来分散的 label-learning-driver（单期偏好学习驱动 + shadow prediction + leakage-safe backtest）、online-learning-refresh（每次 /api/save 之后 rebuild labels_lake 并对活跃 run 做 regate）、editing-experience-distiller（多期离线案例蒸馏 + 只读经验查询）合并为一层，并**新增第 4 段"端到端剪辑偏好学习"**——从已有的人工剪辑成品 + raw material 反推剪辑偏好（不需要候选池 · 补齐"给我一份专业剪辑师剪的、你告诉我他的风格"这个能力）。**四段共用 labels_lake / case_store / active_label_learning_snapshot 读写协议**。它只产出机器建议、只读经验、只读回测证据，任何 EDL、human_decision、autocut 权限都由本 skill 明文禁止落地。

**外部知识循环的新定位（2026-08-18 起）**：用户已要求"参数全用工具里的"—— 因此外部学习循环**不再产参数常量**（crossfade 长度、room tone 处理等已固化到 PARAMETER 层）。外部学习今后只学 3 类元知识：**(a) 新流派/新场景**（采访/独白/多人小组/跨语言/直播录制等当前项目未覆盖类型）·**(b) 边界情况库**（现场噪音/重叠说话/专名密集等罕见但重要的情形）·**(c) 新工具/新模型评估**（新版 whisper、新版 diarization 等技术选型建议）。本 skill 消费外部快照时按这个新分类走。

## 2. 何时激活

- 触发词：标签学习 / label learning / preference snapshot / labels_lake / case_store / online refresh / regate / 案例蒸馏 / experience distiller / shadow prediction / backtest。
- 上游 postcondition：
  - `/api/save` hook 已成功调用 `refresh_label_learning_snapshot.py`，生成新的 `LABEL-LEARNING-AUTO-<label>-<ts>-<seq>/` 目录（`activation=='PENDING_ATOMIC_POINTER_UPDATE'`）；本 skill 负责后续判定是否原子交换 `active_label_learning_snapshot.v1.json`。
  - 手动一次性 evidence 包由本 skill 的 `label_learning_driver` 入口产出 `LABEL-LEARNING-DRIVER-v<N>-<ts>/`；实测样本 `LABEL-LEARNING-DRIVER-v5-20260817/`。
  - 离线案例查询由 `experience_consumer_adapter` 只读走 `case_store/`；实测活跃案例集为 `LABEL-LEARNING-v3-20260816`（65 条独立逻辑事件，24 accept / 41 reject，37 条来源不完整被隔离，覆盖 `immediate_repetition` / `filler_hesitation` / `global_long_pause`）。

## 3. 读什么

- `main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json`
  - 字段：`schema_version`, `updated_at`, `active_refresh_run_relpath`, `active_snapshot_manifest_relpath`, `active_snapshot_manifest_sha256`, `active_snapshot_id`, `refresh_manifest_relpath`, `refresh_manifest_sha256`, `source_review_run_relpath`, `source_human_labels_relpath`, `source_human_labels_file_sha256`, `source_decision_content_sha256`, `source_trigger_kind`, `source_effect`, `label_source_trust`, `scope`, `prohibited[]`。
- `main/runs/LABEL-LEARNING-AUTO-<label>-<ts>-<seq>/preference_snapshot/`
  - 文件：`snapshot_manifest.json`, `aggregated.json`, `feedback_classifications.jsonl`, `policies.md`, `policy_cards.json`, `preferences.md`, `preferences_for_agent.md`, `rules_suggestions.json`。
- `main/runs/LABEL-LEARNING-AUTO-<label>-<ts>-<seq>/refresh_manifest.json`
  - schema `label-learning-auto-refresh-manifest-v1`；字段：`run_id`, `trigger{kind:"final_human_review_submit", review_run_relpath, human_labels_relpath, human_labels_file_sha256, frozen_human_labels_relpath, frozen_human_labels_sha256, frozen_source_files{}, decision_content_sha256, review_package_relpath, review_package_sha256, reviewer, decision_count, accept_count, reject_count, require_complete}`, `snapshot_counts{records,accept,reject,quarantine,rules,policy_cards}`, `observed_label_counts{reject,accept}`, `backtest_status`, `artifacts{source_human_labels.json/run_identity.json/preference_snapshot/snapshot_manifest.json/backtest_report.json/backtest_report.md 全部带 sha256}`, `activation`。
- `main/knowledge/labels_lake.json`
  - `schema_version=='labels-lake-v2'`；顶层 `note`, `project_root`, `excluded_reviewers[AUTOMATED_TEXT_FIRST, LEARNED_FROM_HUMAN_v4]`, `summary{total_decisions, total_accept, total_reject, distinct_runs, distinct_reviewers, hd_files_scanned}`, `by_reason_key`, `by_run`, `by_reviewer`；三级嵌套 `reason_key → _subtypes → _tokens`，每层 `accept/reject/total/accept_rate/case_ids`。实测 EP04 baseline：33 项 / 5 runs / 1 reviewer，`filler_hesitation` accept_rate 0.625，`filler_hesitation.strong_hesitation_sound` accept_rate 1.0。
- target run 身份：`main/runs/<run>/run_identity.json`, `input_manifest.json`, `candidate_source.json`（+ 可选 `all_candidates.json` overlay），以及 `main/runs/<run>/review_package/` 内 `review_package.json` / `review_manifest.json`。
- 案例集：`稳定生产/challengers/experience-ingestion-v1/case_store/`
  - 文件：`cases/EP03.jsonl`, `cases/EP04.jsonl`（`schema_version=='experience-case-v1'`；每行字段：`case_id, episode_id, candidate_id, candidate{reason_key, source_track_id, track_count, start_sample, end_sample, start_seconds, end_seconds, deleted_text, evidence_text, risk, required_listen_to}, label{decision, review_basis, reviewer, decided_at, applied_to_edl, final_start_sample, final_end_sample, edl_status}, review_quality{review_complete, package_hash_valid, candidate_hash_valid, source_audio_hash_valid, required_audio_evidence_complete}, eligibility{eligible_for_rule_analysis, eligible_for_model_training, status, reason}, provenance{source_run_dir, package_id, review_manifest_sha256, candidate_semantic_sha256, source_package_sha256, rules_sha256, tool_or_model_versions}`）
  - `exclusions.jsonl`（`schema_version=='experience-exclusion-v1'`；bulk_accept 等被剔除项）
  - `quarantine.jsonl`（37 条 legacy 不完整身份被隔离）
  - `index.json`, `ingestion_manifest.json`, `source_inventory.json`, `ACTIVE_SNAPSHOT.md`
  - 历史提名快照目录：`two-state-v1-20260812-1612/`, `two-state-v1-20260812-1627/`, `two-state-v2-20260812-1647/`（**旧提名 · 不可再作为活跃指针**）。
- 驱动器本体：`main/orchestrator/label_learning_driver.py`
  - 模块 docstring L1-20 声明 prohibited scope；`driver_source_sha256()` L79-86；防泄漏门 `MIN_INDEPENDENT_SOURCE_BUNDLE_COUNT=3`, `MIN_INDEPENDENT_EVENT_GROUP_COUNT=3`（2026-08-17 用户已从规则中删除 `MIN_CROSS_EPISODE_COUNT=3` 与 `MIN_INDEPENDENT_REVIEWER_COUNT=2`）；每条 prediction L611-617 常量；shadow policy L794-801；backtest summary L951/L972 与中文结论串；evidence 分支 L1108-1114。

## 4. 写什么

- 单期 evidence 包：`main/runs/LABEL-LEARNING-DRIVER-v<N>-<ts>/`
  - `shadow_prediction_manifest.json` + `.md`：schema `label-learning-prediction-v1`；`driver_id=='transparent-pattern-evidence-v1'`；顶层字段 `driver_source_sha256, snapshot{snapshot_id, snapshot_manifest_sha256, aggregated_sha256, eligible_records_used, excluded_episode_ids[], excluded_run_ids[], invalid_legacy_identity_record_count, excluded_same_episode_count, excluded_same_source_bundle_count}, learning_status=='SHADOW_PATTERN_EVIDENCE_ONLY', target_identity{run_id, episode_id, run_identity_sha256, input_manifest_sha256, source_bundle_sha256, source_audio_sha256_by_track{track_01/02/03}, target_run_dir}, leakage_audit{...}, candidate_input{...}, prediction_counts{HUMAN_REVIEW_REQUIRED/MACHINE_CUT_SUGGESTED/MACHINE_PRESERVE_SUGGESTED}, policy{machine_suggestions_only:true, all_suggestions_require_human_review:true, never_creates_human_decision:true, never_creates_edl:true, never_creates_autocut_permission:true, autocut_policy:'NOT_APPROVED'}, predictions[]`
  - 每条 prediction 字段：`schema_version, candidate_id, feature_view{reason_key, candidate_kind, match_text, proposed_text, filler_subtype, clause_position, duration_seconds, duration_bin, source_track_id, source_audio_sha256, safety_status, artifact_risk_verdict, has_lexical_context}, match_tier, matched_cases[], matched_case_count, semantic_vote_counts{cut, preserve}, independent_case_count, independent_run_count, independent_episode_count, independent_source_bundle_count, independent_event_group_count, independent_reviewer_count, evidence_scope, missing_features[], execution_warning, creates_human_decision:false, creates_edl_action:false, creates_autocut_permission:false, requires_human_review:true, machine_label, confidence, review_priority, reason`
  - `backtest_report.json` + `.md`：schema `label-learning-backtest-v1`；`method.split=='leave_one_episode_out'`；`method.forbidden` 明列 held-out episode labels / held-out source-audio labels / same case_id / machine labels as truth；`summary.autocut_policy=='NOT_APPROVED'`；`folds[]` 每 fold 记 `held_out_episode_id, training_episode_ids[], training_record_count, holdout_record_count, holdout_identity_incomplete_count, case_id_overlap[], held_out_source_audio_sha256[], held_out_source_bundle_sha256[], machine_suggestion_count, human_review_required_count, suggestion_correct_count, harmful_suggestion_count, suggestion_precision, predictions[]`
  - `evidence_manifest.json`：schema `label-learning-evidence-v1`；`driver{id, source_path, source_sha256}, snapshot{path, snapshot_manifest_sha256, aggregated_sha256, snapshot_id}, target_identity{...}, inputs{candidate_source_sha256, input_manifest_sha256, overlay_sha256}, commands{backtest{subcommand, snapshot_dir}, shadow{subcommand, snapshot_dir, target_run_dir, candidate_source, candidate_overlay}}, outputs{backtest_report_sha256, shadow_prediction_sha256, target_integrity_before_sha256, target_integrity_after_sha256}`
  - `target_integrity.before.json` + `target_integrity.after.json`：目标审核包 before/after 哈希（变化即 fail closed）
  - `RUN_REPORT.md`
- online refresh：`main/runs/LABEL-LEARNING-AUTO-<label>-<ts>-<seq>/`
  - `preference_snapshot/`（`snapshot_manifest.json`, `aggregated.json`, `feedback_classifications.jsonl`, `policies.md`, `policy_cards.json`, `preferences.md`, `preferences_for_agent.md`, `rules_suggestions.json`）
  - `refresh_manifest.json`（schema `label-learning-auto-refresh-manifest-v1`；`activation=='PENDING_ATOMIC_POINTER_UPDATE'`）
  - `run_identity.json`, `source_human_labels.json`, `backtest_report.json` + `.md`
  - 实测样例：`LABEL-LEARNING-AUTO-HUMAN-BACKFILL-20260817-001/`
- regate 侧产物（由 `refresh_lake_and_regate.py --run <active>` 写到 target run 下 · 不在 LABEL-LEARNING-AUTO 目录）：
  - `main/knowledge/labels_lake.json`（增量重建，覆写；`schema_version=='labels-lake-v2'`）
  - `main/runs/<run>/autocut_gate_regated/summary.json`
  - `main/runs/<run>/regate_diff.json`：字段 `run, lake_changed, new_gate_out, new_summary, prev_summary, auto_cut_added[], auto_cut_removed[], auto_cut_stable[]`
- 案例蒸馏（不覆盖任何生产规则）：只追加或原子替换 `稳定生产/challengers/experience-ingestion-v1/case_store/cases/<EP>.jsonl`, `exclusions.jsonl`, `quarantine.jsonl`, `index.json`, `ingestion_manifest.json`, `source_inventory.json`, `ACTIVE_SNAPSHOT.md`。**本项目不存在 `experience_cases.jsonl` 文件**，"经验条目"就是 `cases/<EP>.jsonl` 的逐行 case。
- **端到端剪辑偏好学习产物**（**新 · 见 §end_to_end_preference_learning_spec.md**）：**触发条件——用户声明"这是人工剪辑后的成品 / 人工成片 / 人剪版 / mentor 成品"即自动触发端到端偏好学习流水线**（自动反推 gold EDL + 提取 gold_cut_features + 汇总偏好），**不需要用户额外声明 'gold'，也不需要用户手动提供 `gold_edl.json`**（机器把该声明视同 gold 标准，直接进流水线）。写到独立 run 目录 `main/runs/E2E-LEARN-<episode>-<ts>/`，含 `gold_edl.json`（反推的剪切清单）· `gold_cut_features.jsonl`（每条剪切的 WHERE/HOW 特征）· `preference_analysis.md`（偏好统计 + 规则假设）· `challenger_task.md`（提名下一版 rules）· `alignment_report.json`（raw vs 成品对齐质量报告）。**只作 Challenger 提名**，不改任何生产 rules / EDL / audio。
- 活跃指针原子交换：只允许通过临时文件 + os.replace 更新 `main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json`；每次交换必须同时刷新 `active_refresh_run_relpath`, `active_snapshot_manifest_relpath`, `active_snapshot_manifest_sha256`, `refresh_manifest_relpath`, `refresh_manifest_sha256`, `source_*` 与 `updated_at`。**禁止**手改 JSON，禁止跨 skill 变更此指针。

## 5. 覆盖 tool

- **entry_tool `label_learning_driver`**（`main/orchestrator/label_learning_driver.py`；参数 `[snapshot_dir, candidates, target_run_identity, output_dir]`）：单期偏好学习驱动，产出 shadow_prediction / backtest / evidence 三件套；三档机器输出 `MACHINE_CUT_SUGGESTED / MACHINE_PRESERVE_SUGGESTED / HUMAN_REVIEW_REQUIRED`；不写决定 / EDL / 音频。
- `apply_preference_snapshot`（`稳定生产/challengers/experience-ingestion-v1/scripts/apply_preference_snapshot.py`；参数 `[snapshot_dir, candidates, output_dir]`）：把冻结的 preference snapshot 应用到候选，只影响排序 / 展示，**不改决定或 EDL**。
- `refresh_lake_and_regate`（`main/orchestrator/refresh_lake_and_regate.py`；参数 `[run, policy, episode_duration_seconds]`）：online 学习闭环 evolution path 1；每次人审后 rebuild labels_lake，加 `--run <dir>` 则同时 regate 指定 run 并 diff old vs new auto_cut。
- `build_labels_lake`（`main/orchestrator/build_labels_lake.py`；参数 `[project_root, exclude_reviewers, out]`；`reads_only:true`）：扫全项目 `human_decisions.json` 汇总到 `main/knowledge/labels_lake.json`；autocut-gate-v2 消费本文件做类别通行证。
- `experience_consumer_adapter`（`稳定生产/challengers/experience-ingestion-v1/scripts/experience_consumer_adapter.py`；参数 `[case_store, episode_id, reason_key, max_examples, out]`；`reads_only:true`）：**editing-experience-distiller 部分的主入口**，只读查 case_store 出经验摘要 / 历史案例 / 规则建议 / 训练准备度 / 明确禁止动作；不改生产规则、不批 EDL、不训练模型。
- `build_case_memory`（`main/orchestrator/case_memory.py`；参数 `[snapshot_dir, candidate_source, candidate_overlay, target_run_identity, review_package, output_json]`）：案例记忆侧车，为每条候选检索可解释相似 case，输出历史 accept/reject / 备注 / 匹配理由 / 审核优先级；不改候选 / 审核包 / 决定 / EDL / 自动剪辑权限。

> Python 里的辅助函数（如 `driver_source_sha256`, `retrieve_before_decision`, `is_never_cut` 等）**不是 tool**，不进 related_tools；如需引用，只在本节以内联函数名说明。

## 6. 硬化 CLAUDE.md

- §10：**每次 /api/save 之后必须 online refresh**——由 `/api/save` hook 调用 `refresh_label_learning_snapshot.py`，生成新的 `LABEL-LEARNING-AUTO-<label>-<ts>-<seq>/` 目录并写 `refresh_manifest.json (activation=='PENDING_ATOMIC_POINTER_UPDATE')`；未产出该目录或 activation 状态缺失的 save 都必须回滚。本 skill 负责后续 pointer 交换。拦截：任何绕过 hook 直接改 `active_label_learning_snapshot.v1.json` 的动作。
- F08 §120：禁止**在线自动学习**——labels_lake 与 case_store 都是显式产出，不允许在 save 之外的时机被隐式重建。拦截：非 hook / 非 skill 触发的 `build_labels_lake` / `refresh_lake_and_regate` 后台调用。
- F08 §121：`autocut_policy=='NOT_APPROVED'` 是硬约束——所有 shadow / backtest / evidence / regate 产物必须写入并保留该字段字面值；任何试图设为 APPROVED 的路径都必须失败关闭。
- F08 §122：`shadow prediction` 只读——`predictions[].creates_human_decision / creates_edl_action / creates_autocut_permission` 必须为 `false`，`requires_human_review` 必须为 `true`；写入前哈希 target audit 包 before/after，任何差异即 fail closed。
- F08 §123：backtest 必须 `leave_one_episode_out`；`method.forbidden` 必须包含 held-out episode labels / held-out source-audio labels / same case_id / machine labels as truth；`INSUFFICIENT_DATA_FOR_CROSS_EPISODE_GENERALIZATION` 是合法诚实结果，不得改写成 accuracy。
- F08 §124：labels_lake 增量原子写——`refresh_lake_and_regate` 必须通过临时文件 + os.replace 覆写 `main/knowledge/labels_lake.json`；schema 必须锁定 `labels-lake-v2`；excluded_reviewers 必须包含 `AUTOMATED_TEXT_FIRST` 与 `LEARNED_FROM_HUMAN_v4`（防机器标签污染真人湖）。
- F08 §125：`active_label_learning_snapshot.v1.json` 指针原子更新——只允许 tmp+rename，更新时必须刷新 `updated_at, active_refresh_run_relpath, active_snapshot_manifest_relpath, active_snapshot_manifest_sha256, refresh_manifest_relpath, refresh_manifest_sha256, source_*`；prohibited 数组必须包含 `automatic semantic cut / human decision / EDL action / audio render / Champion promotion`。
- F08 §126：**案例蒸馏产物不覆盖生产 rules**——`experience_consumer_adapter` 只读；写入只允许追加或原子替换 `case_store/cases/<EP>.jsonl / exclusions.jsonl / quarantine.jsonl / index.json / ingestion_manifest.json`；禁止改 `preference_snapshot/`（那是 label-learning-driver 的产物），禁止改活跃指针；本轮 audit 不得进入 case memory（防泄漏，只用上一期结束案例）。
- F08 §127：driver 身份哈希强制——`driver_source_sha256()` 必须写进 shadow / backtest / evidence 三件套；一旦 driver 源码改动，历史报告只能作为 evidence-only，不得被继承。

## 7. pre_flight_check

参考实现：`scripts/preflight/check_learning-and-experience.py`。以下命令必须都能真跑（cwd = 项目根 `/Users/renting/Desktop/minglue/剪辑项目/`）：

```
# 1) 活跃指针 schema 与关键字段可解析
python3 -c "import json,sys,pathlib;p=pathlib.Path('main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json');d=json.loads(p.read_text());assert d['schema_version']=='active-label-learning-snapshot-pointer-v1',d['schema_version'];assert 'active_refresh_run_relpath' in d and 'active_snapshot_manifest_sha256' in d;print('active_pointer_ok', d['active_refresh_run_relpath'])"

# 2) 活跃 refresh 目录存在且 activation 字段字面值正确
python3 -c "import json,pathlib;ptr=json.loads(pathlib.Path('main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json').read_text());rm=pathlib.Path(ptr['refresh_manifest_relpath']);d=json.loads(rm.read_text());assert d['activation']=='PENDING_ATOMIC_POINTER_UPDATE',d.get('activation');print('refresh_manifest_ok')"

# 3) labels_lake schema 与排除名单
python3 -c "import json,pathlib;d=json.loads(pathlib.Path('main/knowledge/labels_lake.json').read_text());assert d['schema_version']=='labels-lake-v2';assert set(['AUTOMATED_TEXT_FIRST','LEARNED_FROM_HUMAN_v4']).issubset(set(d['excluded_reviewers']));print('lake_ok', d['summary'])"

# 4) driver 源存在 + prohibited scope 明文出现在源码
grep -nE 'autocut_policy.*NOT_APPROVED' main/orchestrator/label_learning_driver.py | head
grep -nE 'never_creates_(human_decision|edl|autocut_permission)' main/orchestrator/label_learning_driver.py | head
grep -nE 'leave_one_episode_out' main/orchestrator/label_learning_driver.py | head

# 5) case_store 活跃索引存在且不使用旧提名目录作为活跃指针
test -f 稳定生产/challengers/experience-ingestion-v1/case_store/ACTIVE_SNAPSHOT.md
test -f 稳定生产/challengers/experience-ingestion-v1/case_store/index.json
grep -nE 'two-state-v1-20260812-1612|two-state-v1-20260812-1627|two-state-v2-20260812-1647' 稳定生产/challengers/experience-ingestion-v1/case_store/ACTIVE_SNAPSHOT.md && echo 'STALE_POINTER' || echo 'ok'

# 6) 单期 evidence 包最新样本可解析
python3 -c "import json,pathlib;d=json.loads(pathlib.Path('main/runs/LABEL-LEARNING-DRIVER-v5-20260817/shadow_prediction_manifest.json').read_text());assert d['schema_version']=='label-learning-prediction-v1';assert d['policy']['autocut_policy']=='NOT_APPROVED';print('shadow_ok')"

# 7) tools.json 里 6 个 tool 名字全部存在（不允许拼错成 Python 函数名）
python3 -c "import json,pathlib;t=json.loads(pathlib.Path('main/tools/tools.json').read_text());names={x['tool_name'] for x in t['tools']};need={'label_learning_driver','apply_preference_snapshot','refresh_lake_and_regate','build_labels_lake','experience_consumer_adapter','build_case_memory'};miss=need-names;assert not miss,miss;print('tools_ok')"

# 8) autocut_policy 落地文件缺席警告（本项目至今没有 autocut_policy.json；只允许作字段值出现）
find . -maxdepth 5 -name 'autocut_policy*' -print
```

## 8. 反馈证据

- 用户 2026-08-17 明确指令："从规则中去掉 MIN_CROSS_EPISODE_COUNT=3 和 MIN_INDEPENDENT_REVIEWER_COUNT=2"；驱动器 L51-56 保留 `MIN_INDEPENDENT_SOURCE_BUNDLE_COUNT=3` 与 `MIN_INDEPENDENT_EVENT_GROUP_COUNT=3` 作**防泄漏门**。→ 本 skill 只把这两个门当结构完整性检查，不把它们当"够不够跨节目泛化"的判定。
- 2026-08-17 online refresh 首次真实闭环：`main/runs/LABEL-LEARNING-AUTO-HUMAN-BACKFILL-20260817-001/` 由 `/api/save` hook 调 `refresh_label_learning_snapshot.py` 产出，`refresh_manifest.json.activation=='PENDING_ATOMIC_POINTER_UPDATE'`，触发 `active_label_learning_snapshot.v1.json` 更新到 `updated_at=='2026-08-17T04:53:21+00:00'`。→ 证据支持 §10 与 §125。
- 2026-08-17 单期 evidence 样本：`main/runs/LABEL-LEARNING-DRIVER-v5-20260817/` 包含 shadow / backtest / evidence 三件套 + target_integrity before/after 双哈希 + RUN_REPORT.md。→ 证据支持 §122 §123 §127。
- 2026-08-16 案例集冻结：`LABEL-LEARNING-v3-20260816` 65 条独立逻辑事件 / 24 accept / 41 reject / 37 legacy 隔离；covers `immediate_repetition`, `filler_hesitation`, `global_long_pause`。→ 证据支持 §126 "本轮 audit 不进 case memory，只用上一期结束案例"。
- labels_lake baseline：EP04 33 项 / 5 runs / 1 reviewer，`immediate_repetition` accept_rate 0.40-0.50（用户自己一半一半），`filler_hesitation.strong_hesitation_sound` 100% → 说明单人审阅不足以自动化，验证 §120 F08 拒绝在线自动学习。
- editing-experience-distiller 前 owner 记为 `challenger:experience-ingestion-v1`；本 skill 收编后 owner 修正为 `champion`；`experience_consumer_adapter` 依然作为**只读**入口继续挂在 `稳定生产/challengers/experience-ingestion-v1/scripts/` 下（脚本物理位置未移，只是治理归属改变）。
- **2026-08-18 外部知识循环** —— 本 skill 的案例蒸馏段还负责消费**外部知识**（旧流程图右上角"外部知识循环 · 异步运行"这一整块）：`从视频学习经验/references/`（YT-02 剪口 / YT-03 MP3 峰值 / YT-04 全轨 ripple / YT-05 room tone 等 10 份）· `端到端学习剪辑/skill/*/references/` · `main/knowledge/external_snapshot/index.json`（v1-2026-08-10 frozen · 由人工离线跑外部学习 Agent 产出 · 下期喂给认知层 s1）。本 skill 出口只**读**外部快照，不改；快照更新走人工离线路径，不在本 skill 内部触发。
- **2026-08-18 gold-EDL 特征提取（PARAMETER/PREFERENCE 分家的证据源）** —— `main/runs/EP04-GOLD-EDL-20260818-1548/` 下的分轨可靠度分析 md + `synthesis.json` 是本 skill 案例蒸馏段的新产物类型：从人工 gold EDL 反向提取 PARAMETER + PREFERENCE 特征，得到"71% semantic_boundary / crossfade per-episode constant / cross_track_speaking 59/59 假阳"三大顿悟。相关 tool `extract_gold_cut_features.py` 待由 s6 登记。
- **2026-08-18 学习流选择器**（`docs/learning-flow-selector.md`）—— 本 skill 作为学习层的入口 · 需在案例蒸馏段引用该决策树：**参数学习流**（gold-EDL 特征提取 → PARAMETER 更新）/ **偏好学习流**（session_feedback 累积 → PREFERENCE 更新）/ **案例蒸馏**（多期完成后离线批处理）/ **端到端偏好学习**（人工成品 + raw material → PREFERENCE 建议）四选一；**补丁滥用防线**：任何反馈**不许**直接 append session_feedback（那是最后一步），必须先经 feedback-engine 四步链（Parse → 优先用工具 → 借鉴知识沉淀 → 最后才 append）。
- **⚠️ cut-verify 4 项 check 参数不走本学习流**（2026-08-19 用户明确）：cut-verify 的 20 个数值参数（check1 prob_threshold / check2 silence params / check3 rhythm gap / check4 P1-P7 路由 / filler_asr_word_expansion）的**唯一权威口径**是 `skills/cut-verify/2026-08-19-0040-cut-verify-landing-and-EP04-delivery.md`（EP04 一晚攻坚 · mentor 审核过 · A/B v04 用户 accept）。这些参数**不通过参数学习流更新**、**不通过 gold-EDL 特征提取自动汇总产生**、**不由本 skill 干预**。变更走 cut-verify skill 自己的新一份落地报告为凭据（含 run 目录 + SHA + A/B 用户 accept）。
- **2026-08-18 端到端剪辑偏好学习（第 4 段 · 未落地代码 · 规范已写）**：用户明确需求"从已有的人工剪辑 + raw material 学剪辑偏好"。当前状态：`extract_gold_cut_features.py` 已存在（能提取"已知 gold EDL 的特征"），但**缺前置工具 `reverse_edl_from_master`**（从 raw + 成品反推 gold EDL）。完整实施规范见同目录 `end_to_end_preference_learning_spec.md`（含输入契约 / 5 步流水线 / 精度要求 / 兜底策略 / 未闭环项）。**是本 skill 的第 4 段主要缺口**，待下一次施工。
- **2026-08-18 外部学习循环重新定位**：用户明确"参数全用工具里的"——外部学习不再产参数常量。本 skill 消费外部知识快照时按新 3 类走（新流派 / 边界情况 / 新工具评估）· 详见 §1 定位段。之前 YT-02~YT-05 5 个视频产出的"crossfade 长度""room tone 取法""MP3 后编码峰值验证"等**已固化到 PARAMETER 层（cut_parameters.json）**，不再需要外部学习产出。

## 9. 三档诚实标注

### 已验证事实
- `main/knowledge/experience_snapshot/active_label_learning_snapshot.v1.json` 14 键全部实测；`schema_version=='active-label-learning-snapshot-pointer-v1'`；`prohibited` 数组含 `automatic semantic cut / human decision / EDL action / audio render / Champion promotion`。
- `main/knowledge/labels_lake.json` schema `labels-lake-v2`；三级嵌套 `by_reason_key → _subtypes → _tokens`；EP04 baseline 数值实测。
- `main/orchestrator/label_learning_driver.py` 内 prohibited scope 声明位置：模块 docstring L1-20、driver hash L79-86、每条 prediction 常量 L611-617、shadow policy L794-801、backtest summary L951/L972、evidence 分支 L1108-1114。
- `LABEL-LEARNING-DRIVER-v5-20260817/` 与 `LABEL-LEARNING-AUTO-HUMAN-BACKFILL-20260817-001/` 两个真实目录均存在，产物齐全。
- `稳定生产/challengers/experience-ingestion-v1/case_store/` 目录布局实测：`cases/EP03.jsonl`, `cases/EP04.jsonl`, `exclusions.jsonl`, `quarantine.jsonl`, `index.json`, `ingestion_manifest.json`, `source_inventory.json`, `ACTIVE_SNAPSHOT.md` + 3 个旧提名目录。
- tools.json 6 项工具名称与 full_path 实测无误。

### 已决定的方向
- 三合一 skill 由 champion 拥有；`entry_tool=label_learning_driver`，`related_tools` 覆盖 apply_preference_snapshot / refresh_lake_and_regate / build_labels_lake / experience_consumer_adapter / build_case_memory 共 5 项。
- editing-experience-distiller 部分归属 champion；`experience_consumer_adapter` 仍是"案例分析助手，不做预测"，预测统一走 `label_learning_driver`。
- `active_label_learning_snapshot.v1.json` 指针交换必须原子（tmp+rename），且只允许本 skill 触发；labels_lake 覆写同规则。
- 案例蒸馏不覆盖 preference_snapshot / 生产规则；本轮 audit 数据不进 case memory。
- CLAUDE.md 硬化点：§10（save 后 online refresh）、F08 §120-127（禁在线自动学习 / `autocut_policy=='NOT_APPROVED'` / shadow 只读 / leakage-safe backtest / lake 原子写 / pointer 原子更新 / 案例蒸馏不覆盖生产 / driver 身份哈希强制）。

### 待验证假设
- `autocut_policy.json` 落地文件**在项目里不存在**（`find -maxdepth 5 -name "autocut_policy*"` 无结果）；本 skill 当前只依赖 `autocut_policy` 作为字符串字段值出现在 driver 输出的 `policy.autocut_policy` 与 snapshot 记录中。是否需要新增 `main/knowledge/autocut_policy.json` 作为单一事实源尚未决定，pre_flight_check 里以 `find` 打印告警占位。
- `scripts/preflight/check_learning-and-experience.py` 目录当前**未见落地**；本 SKILL.md 已声明 `pre_flight_check` 路径，需要另起一次施工把上文 8 条命令封装为脚本；在脚本落地前，agent 应逐条手跑上述命令代替。
- `LABEL-LEARNING-AUTO-<label>-<ts>-<seq>/` 只观察到一次真实样本（`HUMAN-BACKFILL-20260817-001`），`<label>` 枚举值范围（除 `HUMAN-BACKFILL` 外是否还有其它）尚未验证。
- `refresh_lake_and_regate.py --run <dir>` 产出的 `regate_diff.json` 字段清单来自实测目录快照，但 `lake_changed==false` 分支下的字段完整性未双跑验证。
- 原 editing-experience-distiller SKILL.md 声明的"经验条目"概念在本项目里对应 `case_store/cases/<EP>.jsonl` 逐行 case；假设"未来若引入 `experience_cases.jsonl` 汇总文件，必须先扩 F08 §126"——目前该文件明确不存在。
- CLAUDE.md 章节编号 `§10` 与 `F08 §120-127` 引用的是设计说明约定，需一次性回读现行 CLAUDE.md 确认对齐（若编号已改动，本 frontmatter `covers_claude_md_rules` 与 §6 需同步修订）。

### 开放 backlog（继承自 Plan 防丢失审计 · 合并时不能丢）

- **C-26 `distiller_after_review`（SOP 违规判据 · 无自动拦截）** —— 用户 SOP：会话开始前必读 `preferences_for_agent.md`；用户新审核后必须跑 `distill_preferences.py`。当前**无自动拦截**，只在 `Preflight-checklist.md §14` + `Agent-SOP §9` 声明。**未闭环**。本 skill 应在下一版加自动 sanity check（检测会话首次执行前是否 Read 过 preferences_for_agent.md）。
- **D-gap-1 backtest INSUFFICIENT_DATA_FOR_CROSS_EPISODE_GENERALIZATION** —— 当前 2 期 / 1 审核人 / 6-65 事件级音频身份 / 0-65 source bundle 身份都不足；backtest 明标该状态是"合法诚实结果"，不是失败。补齐 source bundle / event identity 是必须动作。
- **D-OPT-025 跨 episode 经验反馈到候选打分** —— partial（case_memory 已接 65 records · 但相似度打分未接入 gate G5）。
- **D-OPT-007 相似案例检索和候选排序** —— partial（case_memory 已接 · 排序未闭环）。
- **D-OPT-010 监督学习 · GBDT 排序器 · 冻结 benchmark** —— open · low priority · 待多期数据足够后再考虑。
- **preferences 快照三份历史目录** —— `skills/editing-experience-distiller/output/preferences-20260815-1330/` / `-label-loop-v1/` / `-label-loop-v2/` 含 11 条 P-XX 规则 + `apply_learned_filter.py` + `distill_preferences.py`；Preflight §14 强制读取。合并后 SOP 必须保留触发点，脚本物理位置不移。
- **legacy 隔离案例 quarantine.jsonl 37 条** —— 历史身份不完整被隔离；不进案例蒸馏。待身份补齐后再决定是否解禁。
- **`refresh_lake_and_regate.py --run <dir>` `regate_diff.json` 双跑验证** —— `lake_changed==false` 分支字段完整性未双跑验证。