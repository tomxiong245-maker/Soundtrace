---
name: episode-triage-and-plan
description: 拿到一期音频后，判断 episode_type、建立 run 目录、冻结 plan.json 并声明 speaker_map；这一 skill 结束前 pipeline（denoise/ASR/candidates/render）不启动。合并原顶层 audio-clips-orchestration 的类型判别 + input-triage 的输入检查 + speaker-map-required 的说话人契约。触发关键词：新一期音频、EP0X 开工、建 run、冻结 plan、判 episode_type、speaker_map、host guest、章鱼 AI 播客、多轨 WAV、input_manifest、inspect_audio、estimate_sync、auto speaker。
status: active
owner: champion
entry_tool: inspect_audio
related_tools:
  - inspect_audio
  - measure_loudness
  - analyze_reference_timeline
  - estimate_sync
  - correct_clock_drift
  - create_clock_drift_fixture
  - auto_speaker_role
preconditions:
  - "已在磁盘上放好 N 轨 mono WAV，且每条轨 sample_rate_hz/channels/bits_per_sample 一致（可用 inspect_audio 事后校验，但拿到手时必须至少能被 ffprobe 打开）"
  - "已读 /Users/renting/Desktop/minglue/剪辑项目/统筹全局/Agent交付流程-从音频到成片.md 的 §1 与 §1.1（输入不合格时 Agent 停下不猜）"
  - "已读 /Users/renting/Desktop/minglue/剪辑项目/统筹全局/功能说明/F01-输入检查与同步.md 的验收标准段"
  - "/Users/renting/Desktop/minglue/剪辑项目/main/tools/tools.json 中 inspect_audio / measure_loudness / analyze_reference_timeline / estimate_sync / correct_clock_drift / create_clock_drift_fixture / auto_speaker_role 七个 tool 全部登记且脚本存在"
postconditions:
  - "main/runs/<episode_id>/<run_id>/run_identity.json 存在且 schema_version==\"run-identity-v1\"，其 episode_id/run_id 与 plan.json 完全一致"
  - "main/runs/<episode_id>/<run_id>/plan.json 存在且 schema_version==\"delivery-plan-v1\"，contract_version 已冻结，run_identity_sha256 与 run_identity.json 内容 SHA 一致"
  - "main/runs/<episode_id>/<run_id>/input_manifest.json 存在且 schema_version==\"delivery-input-manifest-v1\"，tracks[] 每条含 track_id/label/input_relpath/source_filename/audio_sha256/sample_rate_hz/frame_count/channels/bits_per_sample/duration_seconds，source_access 字面串以 \"relative symlinks within this run; raw sources are read only\" 结尾"
  - "main/knowledge/speaker_maps/<episode_id>.speaker_map.json 或 <episode_id>.speaker_map.auto.json 存在，schema_version==\"speaker-map-v1\"，且 map 里至少一个 track 的 role==\"host\"（否则拒绝进入 denoise 阶段）"
  - "状态机推进到 INPUT_VALIDATED；未通过则停在 RECEIVED 并把失败原因写进 run 目录的 triage_notes.md（人读）而不是自行降级"
  - "run_id 必须含时间戳（格式 YYYYMMDD-HHMM 或 YYYYMMDD-HHMMSS），且 run_dir 是新目录；禁止覆盖历史 run（对应 podcast-editing-orchestrator 已有硬约束 A2-12）"
  - "本 skill 完成后写一份 main/runs/<episode_id>/<run_id>/triage_summary.md（人读）并调用 context_checkpoint 更新 统筹全局/当前状态摘要.md（AGENTS.md 必读路由的最短入口）"
  - "**主导轨已合成**（2026-08-18 架构级顺序变更）：本 skill 出口必须已产出 `main/runs/<episode_id>/<run_id>/render_prep/speech.mono.wav` 及其 sidecar `speech.mono.manifest.json`（含 automix_v1 参数 SHA + 3 轨 SHA + 主导轨 SHA + safety.edl_mutation=false）。**下游 s2 候选生成、s2 边界精修、s3 A/B clip、s3 render 全部从这条主导轨消费，不再从 3 条独立轨现场合成**。跨轨判定（如长停顿其他轨在不在说话）仍消费 3 条独立词级 ASR，但候选主体和音频操作对象是主导轨。这一步解决了 cross_track_speaking 59/59 假阳的架构根因"
covers_decision_points:
  - episode_type_routing
  - noise_floor_gate
  - drift_ppm_correction_gate
  - speaker_map_host_declaration
  - master_track_synthesis_before_candidates
covers_claude_md_rules:
  - "§1"
  - "§12"
  - "FR-01"
  - "FR-02"
  - "FR-03"
pre_flight_check: scripts/preflight/check_episode-triage-and-plan.py
---

# episode-triage-and-plan

## 1. 定位

这一 skill 是一期音频从"文件躺在硬盘上"到"pipeline 可以开跑"之间的**唯一入口**。它做四件事：**判类型（episode_type）→ 建 run 目录 → 冻结 plan.json → 声明 speaker_map**。四件事任何一件没落地，下游 denoise / ASR / candidates / render 一律不启动。

它替代并合并了三个旧顶层入口：
- `audio-clips-orchestration`（原 /Users/renting/Desktop/minglue/剪辑项目/SKILL.md 的类型判别职责）
- `input-triage`（输入 QC + sync 门禁）
- `speaker-map-required`（说话人契约 · host 归属必须先声明）

## 2. 何时激活

激活 trigger（满足任一即进入本 skill）：
- 用户消息含 "新一期"/"EP0X 开工"/"建 run"/"冻结 plan"/"判类型"/"speaker_map"/"章鱼 AI 播客" 等命中词。
- 存在 N 轨 mono WAV 但 `main/runs/<episode_id>/<run_id>/plan.json` 尚不存在。
- 存在 `plan.json` 但同 run 下 `main/knowledge/speaker_maps/<episode_id>.speaker_map*.json` 不存在。

上游 postcondition（进入本 skill 前必须满足）：
- 状态机为 `RECEIVED`（见 /Users/renting/Desktop/minglue/剪辑项目/统筹全局/Agent交付流程-从音频到成片.md 状态机段）。
- 用户已在硬盘上放好原始 WAV，且承诺路径不再变。

出口：状态机推进到 `INPUT_VALIDATED`（下一 skill 才能开始 denoise，进入 `TIMELINE_READY`）。

## 3. 读什么

3.1 **/Users/renting/Desktop/minglue/剪辑项目/统筹全局/Agent交付流程-从音频到成片.md**
- §1 "只需提供的输入" + §1.1 "输入不合格时，Agent 只能停下，不得猜"
- "第 3 节 · 输入检查阶段"表：Agent 自动完成 = SHA、格式、N 轨共同时间线、授权配置、漂移门禁；必须留下的证据 = `input_manifest.json`、`plan.json`。
- 状态机段（`RECEIVED → INPUT_VALIDATED → TIMELINE_READY → ...`）。

3.2 **/Users/renting/Desktop/minglue/剪辑项目/统筹全局/功能说明/F01-输入检查与同步.md**
- "验收标准"段（第 42-48 行）：格式异常/长度不一致/缺失元数据必须明确报告；已知 offset/drift fixture 的估计误差在门限内；低置信度样本 fail closed；下游使用的每条音轨有明确时间线版本与 SHA。
- "输入与输出"段声明的三种同步决定（`accept_zero / correct / manual_review`）实际编码在 `sync_report.json` 的 `estimation_status` + `automatic_correction_allowed` 两字段组合上，不是单独字段。

3.3 **/Users/renting/Desktop/minglue/剪辑项目/main/tools/tools.json**
- 顶层 `scripts_root="端到端学习剪辑/代码"`；前 6 个 tool 相对该 root 解析，`auto_speaker_role` 用 `full_path=main/orchestrator/auto_speaker_role.py`。
- 全局 `runtime_dependencies` 声明 ffmpeg 9.0.1 @ `/opt/homebrew/bin/ffmpeg`，SHA `11012f10d9d2eff4df94d760eec5964980880ced20bd4cdbd9f82ec399867e9d`。

3.4 **既有 run 范例**（读 schema，不读内容）：
- `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP03-freshrun-20260810-1730/01_inspect/inspection.json` —— `inspection.json` schema 唯一实例：`schema_version=1`；`inputs[].audio.channel_stats[].rms_dbfs` 和 `sample_silence_ratio_below_minus_60_dbfs` 是**噪声底判定的实际字段**。
- `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP03-freshrun-20260810-1730/02_loudness/loudness_raw.json` —— `schema_version=2`；含 `integrated_lufs / loudness_range_lu / true_peak_dbtp`，**不含** `noise_floor_dbfs`。
- `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP03-freshrun-20260810-1730/03_sync/sync_report.json` —— 文件名是 `sync_report.json`（不是 `sync.json`）；含 `estimated_drift_ppm`、`estimation_status`、`automatic_correction_allowed`、`trusted_window_count` vs `minimum_trusted_window_count`。
- `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP04/EP04-v26-20260815-1650/plan.json` —— `delivery-plan-v1` schema 参照；注意 **该 schema 实际未写入 `episode_type` 字段**（见 §9 待验证假设）。
- `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP04/EP04-v26-20260815-1650/run_identity.json` —— `run-identity-v1` schema 参照。
- `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP04/EP04-v26-20260815-1650/input_manifest.json` —— `delivery-input-manifest-v1` schema 参照。
- `/Users/renting/Desktop/minglue/剪辑项目/main/knowledge/speaker_maps/EP04.speaker_map.json` —— `speaker-map-v1` 人工 attested 版参照；host 归属通过 `map.<track_id>.role=="host"` 声明，**不用 `host_track_index`**。

## 4. 写什么

按顺序落地以下产物；任一缺失下游拒开跑。

4.1 **`main/runs/<episode_id>/<run_id>/run_identity.json`**（首先写）
- 必填字段（照抄 EP04-v26 的 `run-identity-v1` schema）：`schema_version`、`episode_id`、`run_id`、`contract_version`、`run_dir_rel`、`created_at`、`purpose`、`preference_profile_id` + `preference_profile_relpath` + `preference_profile_sha256`、`candidate_rules_relpath` + `candidate_rules_sha256` + `candidate_rules_version`、`music_asset_relpath` + `music_asset_sha256` + `music_template_id` + `music_template_sha256`。
- 若参考 EP04-AUTO-VERIFY-20260817-2200 版本，可额外记录 `editing_policy_relpath / editing_policy_sha256 / editing_policy_id / editing_policy_version / experience_snapshot_id / experience_snapshot_relpath / experience_snapshot_sha256`。

4.2 **`main/runs/<episode_id>/<run_id>/input_manifest.json`**
- 必填字段（照抄 `delivery-input-manifest-v1` schema）：`schema_version`、`episode_id`、`run_id`、`run_identity_sha256`、`track_count`、`sample_rate_hz`、`frame_count`。
- `tracks[]` 每条含：`track_id`（形如 `track_01`）、`label`（形如 `ZOOM0009_Tr1`）、`input_relpath`（形如 `inputs/track_01_ZOOM0009_Tr1.wav`）、`source_filename`、`audio_sha256`（64 hex）、`sample_rate_hz`、`frame_count`、`channels`、`bits_per_sample`、`duration_seconds`。
- `source_access` 必须以字面串 `"relative symlinks within this run; raw sources are read only"` 结尾（对应 FR-01 原始只读硬边界）。

4.3 **`main/runs/<episode_id>/<run_id>/01_inspect/inspection.json`**（跑 `inspect_audio`）
- 照抄 EP03-freshrun 的 schema：`schema_version=1`、`created_at`、`processing_policy="local_read_only_inputs"`、`tools`、`inputs[]`。
- `inputs[].audio.channel_stats[]` 里 `rms_dbfs` 与 `sample_silence_ratio_below_minus_60_dbfs` 是**下一步噪声底判定唯一可信来源**。

4.4 **`main/runs/<episode_id>/<run_id>/02_loudness/loudness_raw.json`**（跑 `measure_loudness`）
- 照抄 EP03 schema：`schema_version=2`、`standard="ITU-R BS.1770 via FFmpeg ebur128"`、`measurements[]` 每条 `integrated_lufs / loudness_range_lu / true_peak_dbtp`。

4.5 **`main/runs/<episode_id>/<run_id>/03_sync/sync_report.json`**（跑 `estimate_sync`，两两跨轨）
- 照抄 EP03 schema。三档决策等价：
  - `estimation_status=="fit_ok"` **且** `automatic_correction_allowed==true` → 走 `correct_clock_drift`（消费 `estimated_drift_ppm`）；
  - `estimation_status=="candidate_fit_manual_confirmation_required"` **或** `automatic_correction_allowed==false` **或** `trusted_window_count < minimum_trusted_window_count` → **fail closed**，冻结手动确认待办到 `triage_notes.md`；
  - `estimated_drift_ppm==0.0` **且** `estimated_initial_offset_ms` 绝对值 < 5 → `accept_zero`，不调 `correct_clock_drift`。
- 对应 FR-02：证据不足拒自动校正。

4.6 **`main/runs/<episode_id>/<run_id>/plan.json`**（冻结）
- 照抄 EP04-v26 的 `delivery-plan-v1` schema：`schema_version`、`episode_id`、`run_id`、`run_identity_sha256`、`contract_version`、`input_manifest_relpath`、`preference_profile`、`candidate_strategy`、`review_strategy`、`autocut_policy`、`music`、`denoise`、`candidate_coverage`、`random_seed`、`requirements_checkpoint_relpath`。
- **episode_type 归属**：EP04-v26 的实际 `plan.json` 中未见 `episode_type` 字段（见 §9），本 skill 采用的落地位置 = `plan.json` 顶层新增字段（下沉入 delivery-plan-v1 的兼容扩展位）。当前只允许一个合法值：`mandarin-dual-speaker-podcast`。其他判定结果（采访/独白/三人以上/背景噪声重）→ 停下问人，不写 plan.json。

4.7 **`main/knowledge/speaker_maps/<episode_id>.speaker_map.json`**（人工 attested）**或** **`<episode_id>.speaker_map.auto.json`**（`auto_speaker_role` 输出）
- 照抄 `speaker-map-v1` schema：`schema_version`、`episode_id`、`map.<track_id>.role`、`map.<track_id>.note`；人工版含 `attested_by / attested_at`；自动版含 `generator / generator_version / role_rules.host_backchannel_skip / detailed_stats`。
- **硬约束**：`map` 中至少一个 track 的 `role=="host"`（EP04 中为 `track_03`）。缺 host 声明 → 拒开 denoise。
- 优先级：`run_end_to_end.py` Stage 3.1 中人工版优先；缺失才用 auto；冲突保留人工并写 warn。
- **2026-08-19 晋升**：speaker_map 声明后 · Stage 3.4 会用 pyannote-audio 4.0.7 生成 RTTM（default ON · 替代旧启发式）。

4.8 **`main/runs/<episode_id>/<run_id>/triage_notes.md`**（人读）
- 类型判定 5 信号原始值（轨道数、时长、ASR 首 5 分钟词频、ASR 首 30 秒开场话术、噪声底 `rms_dbfs` + `sample_silence_ratio_below_minus_60_dbfs`）+ 每信号的判决。
- 若走 fail closed，写清"停在哪一步 · 需要人做什么 · 证据文件路径"。

## 5. 覆盖 tool

（全部来自 /Users/renting/Desktop/minglue/剪辑项目/main/tools/tools.json，脚本文件已确认存在）

- **inspect_audio**（entry_tool）—— 读容器/编码/采样率/声道/时长/channel_stats，输出 `01_inspect/inspection.json`。轨道数与时长信号从这里来。
- **measure_loudness** —— FFmpeg ebur128，输出 `02_loudness/loudness_raw.json`。整体响度参考；**不承担噪声底判定**（该数据在 `inspection.json.channel_stats`）。
- **analyze_reference_timeline** —— 与 Mentor 参考成品做时间线对齐 sanity check；仅当本期存在 mentor_ref 时启用。
- **estimate_sync** —— 两两轨间估计 offset + drift_ppm，输出 `03_sync/sync_report.json`。低置信度自动置 `automatic_correction_allowed=false`。
- **correct_clock_drift** —— 只在 `sync_report.json` 判定为 `automatic_correction_allowed==true` 时调用；`drift_ppm` 参数直接消费 `estimated_drift_ppm`。
- **create_clock_drift_fixture** —— 不在正常一期音频路径上；仅当需要给 estimate_sync/correct_clock_drift 做自检时手工触发，产物落到 fixture 目录不进 run。
- **auto_speaker_role** —— 从 `analysis/track_*.transcript.json` 的词级 ASR 统计 backchannel_ratio + total_speaking_fraction，输出 `speaker-map-v1` 到 `<episode_id>.speaker_map.auto.json`。判决式 `frac < 0.55 AND (is_min_speaking OR is_max_backchannel) → host`。

## 6. 硬化 CLAUDE.md

- **§1 原始只读** —— 拦截：`input_manifest.tracks[].input_relpath` 指向 run 外的绝对路径而不是 run 内 relative symlink；或 `source_access` 字面串不以 `"relative symlinks within this run; raw sources are read only"` 结尾。这条边界在下游全部 skill 里都要遵守，但源头 gatekeeper 是本 skill。
- **§12 speaker_map 必备** —— 拦截：`main/knowledge/speaker_maps/<episode_id>.speaker_map*.json` 不存在，或存在但没有任何 `role=="host"`，或 host 声明与人工版冲突未解决 → 拒进 denoise。
- **FR-01 原始只读** —— 拦截：`input_manifest.json.source_access` 不以 `"relative symlinks within this run; raw sources are read only"` 结尾；或 `input_relpath` 指向 run 外的绝对路径而非 run 内 symlink。
- **FR-02 证据不足拒自动校正** —— 拦截：`sync_report.json.automatic_correction_allowed==false` 或 `trusted_window_count < minimum_trusted_window_count` 时仍调用了 `correct_clock_drift`。此时必须 fail closed 到 `triage_notes.md` 手动确认待办。
- **FR-03 未知说话人保留 unknown** —— 拦截：speaker_map 里出现 `role` 字段为空、`null` 或形如 `"?"` 的记录。未知就写字面 `"unknown"` + `note` 说明原因，绝不猜。

## 7. pre_flight_check

脚本路径：`scripts/preflight/check_episode-triage-and-plan.py`（尚未落地，见 §9 待验证假设）。以下每条 grep/test/python 命令**均可直接在 bash 中运行验证**：

```bash
# 7.1 三个基石文件齐全（替换 EP0X 与 <run_id>）
test -f "/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>/run_identity.json" \
 && test -f "/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>/input_manifest.json" \
 && test -f "/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>/plan.json"

# 7.2 schema_version 全对
python3 -c "import json,sys;p='/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>';\
 assert json.load(open(f'{p}/run_identity.json'))['schema_version']=='run-identity-v1';\
 assert json.load(open(f'{p}/input_manifest.json'))['schema_version']=='delivery-input-manifest-v1';\
 assert json.load(open(f'{p}/plan.json'))['schema_version']=='delivery-plan-v1';print('ok')"

# 7.3 run_identity_sha256 三处一致
python3 -c "import json,hashlib;p='/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>';\
 ri=open(f'{p}/run_identity.json','rb').read();sha=hashlib.sha256(ri).hexdigest();\
 pl=json.load(open(f'{p}/plan.json'));im=json.load(open(f'{p}/input_manifest.json'));\
 assert pl['run_identity_sha256']==im['run_identity_sha256']==sha,'run_identity_sha256 mismatch';print('ok')"

# 7.4 FR-01 源只读契约字面串
grep -F "relative symlinks within this run; raw sources are read only" \
 "/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>/input_manifest.json"

# 7.5 §12 speaker_map 至少一个 host
python3 -c "import json,glob;\
 fs=glob.glob('/Users/renting/Desktop/minglue/剪辑项目/main/knowledge/speaker_maps/EP0X.speaker_map*.json');\
 assert fs,'no speaker_map';m=json.load(open(fs[0]));\
 assert m['schema_version']=='speaker-map-v1';\
 assert any(v.get('role')=='host' for v in m['map'].values()),'no host declared';print('ok')"

# 7.6 FR-02 sync 门禁自洽
python3 -c "import json;s=json.load(open('/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>/03_sync/sync_report.json'));\
 assert (s['automatic_correction_allowed'] is True) == (s['trusted_window_count']>=s['minimum_trusted_window_count']),\
 'auto_correct flag inconsistent with trusted windows';print('ok')"

# 7.7 episode_type 白名单
python3 -c "import json;p=json.load(open('/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP0X/<run_id>/plan.json'));\
 et=p.get('episode_type');\
 assert et=='mandarin-dual-speaker-podcast',f'unsupported or missing episode_type: {et}';print('ok')"

# 7.8 tools.json 七个 tool 都在
python3 -c "import json;t=json.load(open('/Users/renting/Desktop/minglue/剪辑项目/main/tools/tools.json'));\
 names={x['name'] for x in t['tools']};need={'inspect_audio','measure_loudness','analyze_reference_timeline',\
 'estimate_sync','correct_clock_drift','create_clock_drift_fixture','auto_speaker_role'};\
 assert need<=names,need-names;print('ok')"
```

## 8. 反馈证据

本 skill 覆盖的历史事件（引用 run 目录与文件；未在此清单外发明 jsonl 行号或 verdict 枚举）：

- **EP04 auto vs 人工 speaker_map 对齐** —— `/Users/renting/Desktop/minglue/剪辑项目/main/knowledge/speaker_maps/EP04.speaker_map.json`（人工，`track_03.role="host"`）与 `auto_speaker_role.py` 的 v20.6 Q2 判决（tools.json 描述："EP04 track_03 判 host 与人工 map 一致"）一致 → 佐证 §4.7 的双源优先级策略可行。
- **EP03 sync fail closed** —— `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP03-freshrun-20260810-1730/03_sync/sync_report.json`：`trusted_window_count=2 < minimum_trusted_window_count=3`、`estimation_status="candidate_fit_manual_confirmation_required"`、`automatic_correction_allowed=false` → 佐证 FR-02 门禁字段组合的实际效果。
- **EP03 输入检查基线** —— `/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP03-freshrun-20260810-1730/01_inspect/inspection.json`：female 轨 `channel_stats[0].rms_dbfs=-25.974` + `sample_silence_ratio_below_minus_60_dbfs=0.0902` → 佐证 §4.3 噪声底信号的实际来源。
- **EP04-AUTO-VERIFY-20260817-2200** —— run 目录存在 `input_manifest.json / run_identity.json`，但 **`plan.json / inspection.json / loudness.json / sync.json` 均不存在** → 佐证并非所有 run 都完整跑完输入检查阶段，本 skill 的 pre_flight_check §7.1 就是防这种半成品状态。

（本仓库未在本次事实清单中提供 `session_feedback*.jsonl` 的 kind/verdict 枚举，因此本节不引用任何具体行号 —— 见 §9 待验证假设。）

## 9. 三档诚实标注

### 已验证事实（事实清单直接抓取）
- `/Users/renting/Desktop/minglue/剪辑项目/main/tools/tools.json` 中 `inspect_audio / measure_loudness / analyze_reference_timeline / estimate_sync / correct_clock_drift / create_clock_drift_fixture / auto_speaker_role` 七个 tool 均已登记，脚本文件均实际存在。
- `run-identity-v1 / delivery-input-manifest-v1 / delivery-plan-v1 / speaker-map-v1` 四个 schema 均可在 EP04-v26-20260815-1650 与 EP04 speaker_map 里直接查到。
- `sync_report.json` 文件名与含 `estimated_drift_ppm / estimation_status / automatic_correction_allowed / trusted_window_count / minimum_trusted_window_count` 字段的 schema 已在 EP03-freshrun-20260810-1730 落地。
- `inspection.json.inputs[].audio.channel_stats[]` 含 `rms_dbfs` 与 `sample_silence_ratio_below_minus_60_dbfs`；`loudness_raw.json` 只含 `integrated_lufs / loudness_range_lu / true_peak_dbtp`，**不含**独立 `noise_floor_dbfs` 字段。
- `EP04.speaker_map.json` 用 `map.<track_id>.role=="host"` 声明 host（EP04 是 `track_03`），**不使用 `host_track_index` 字段名**。
- `auto_speaker_role.py` 的判决常量：`HOST_TOTAL_SPEAKING_FRACTION_MAX=0.55`、`HOST_BACKCHANNEL_RATIO_ABS_MIN=0.02`、`HOST_BACKCHANNEL_TOKENS={嗯,啊,对,对对,是,是的,好,好的,唉}`。

### 已决定的方向（本 skill 主动选定，未来可能改）
- 合并原顶层三个 skill 到 `episode-triage-and-plan` 单入口；`audio-clips-orchestration / input-triage / speaker-map-required` 视为被本 skill 取代。
- `episode_type` 白名单在本阶段只允许 `mandarin-dual-speaker-podcast`；采访/独白/三人以上/背景噪声重的录音一律走"停下问人"路径，不加分支判决树。
- `episode_type` 字段落地位置定为 `plan.json` 顶层扩展字段（`delivery-plan-v1` schema 兼容扩展位）。
- speaker_map 双源冲突时人工优先并写 warn；`auto_speaker_role` 输出只作 fallback。
- pre_flight_check 的 7 组命令作为出口门禁，任何一组失败 → 状态机停在 `RECEIVED` 不推进到 `INPUT_VALIDATED`。

### 待验证假设（未直接从事实清单坐实的，须后续 grep/test 确认）
- **`plan.json` 增设 `episode_type` 字段** —— EP04-v26 的实际 `plan.json` 中未见此字段（事实清单原话：无 `episode_type` 字段）。本 skill 声明将其加入 `delivery-plan-v1` 兼容扩展位；是否会与 schema 校验器冲突需在实施时校验。若冲突，退路方案是把 `episode_type` 落到 `run_identity.json.purpose` 或独立 `main/runs/<episode_id>/<run_id>/episode_type.json` 小文件。
- **`scripts/preflight/check_episode-triage-and-plan.py` 落地** —— 该脚本尚未在事实清单中出现；本 skill frontmatter 声明其路径但未确认文件存在。首次实施 skill 时需要一并创建。
- **CLAUDE.md § 编号对照** —— 事实清单未提供 CLAUDE.md 全文对照；上文引用的 §1（原始只读）、§12（speaker_map 必备）与 FR-01/02/03 编号来自 Plan 防丢失审计（CLAUDE.md 实际到 §20），实施时需 grep CLAUDE.md 确认编号未漂移。
- **feedback jsonl 的实际枚举** —— 本次事实清单未包含 `session_feedback*.jsonl` 的 `kind / verdict` 枚举样本（那份契约在 skills/feedback-engine 内），因此 §8 未引用具体行号；后续消费者调 `feedback_engine.retrieve_before_decision(candidate, decision_type="episode_type_routing", episode_id)` 时会自动命中。
- **`estimate_sync` 输出文件名** —— EP03 用 `sync_report.json`；F01 功能说明未强制该文件名。本 skill 沿用 `sync_report.json`，若未来 tool 输出改名需同步修 §4.5 与 §7.6。
- **`analyze_reference_timeline` 在无 mentor_ref 一期的行为** —— tools.json 描述该 tool 是"sanity check"，未声明必跑；本 skill §5 按"仅当存在 mentor_ref 时启用"处理，需在实施时确认 tool 在缺 mentor_ref 时是否会硬失败。

### 开放 backlog（继承自 统筹全局/待优化清单.md · 合并时不能丢）
- **OPT-012 输入类型新路由** —— 单轨 / 三人 / 采访 / 讲座路由未做；本 skill 目前所有非 `mandarin-dual-speaker-podcast` 都走"停下问人"路径，未来可分支时来这里加决策树。
- **OPT-014 真实设备 clock drift 复验** —— `estimate_sync` 与 `correct_clock_drift` 仅有 fixture 通过；不同设备真实录音的 drift 泛化尚未复验。多设备一期到手时须补一份对比报告。
- **D-gap-10 Preflight §12 遗留坑** —— Python 3.13 + audioop-lts + faster-whisper venv + ffmpeg 绝对路径 + huggingface 权重缓存 + 磁盘 15G 阈值 + 音乐 SHA + v20 上游依赖 + sync check 目前只有 `preflight.sh` 人肉版，未接入本 skill 的 pre_flight_check；下一版应合并进 §7 的自动脚本。
- **AUDIO-CLEANUP-20260817 副作用（D-gap-9）** —— `main/runs/EP04-*/` 下多个 v20 mp3 已被清理但 `CURRENT_DELIVERY_FACTS.json` 的 mp3 relpath 未同步改。本 skill 的 `context_checkpoint` 步骤若发现 sync check FAIL 必须停下问人，不得自行修补 relpath。