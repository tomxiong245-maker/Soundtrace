# 2026-08-17 会话交接：主麦 automix / 发布规格冻结 / v2 orchestrator / 规则清理

> 用途：给下一位 Agent 快速建立 2026-08-17 当日的整体状态、动过什么、还差什么。
> 上下文起点：用户核心指令 —— "第一条（主麦 automix）赶紧做；第二条 EP03 就是例子；第三条暂不做；第四条主麦混音+跨轨归属去做，能用外部库用外部库；第五条净节省时间从 benchmark 去掉；第六条 3期/2 审核人从规则去掉。L2-8（Tool 注册表统一调用）赶紧搭。"

## 用户明确批准的方向

1. **主麦 automix + 跨轨归属**（L1-1 + L1-4）：赶紧做，能用外部库用外部库
2. **发布规格**：以 EP03 mentor 成片实测为目标冻结
3. **未接候选家族 5 类**：暂不做，Claude 单独施工中
4. **净节省时间 KPI**：从 benchmark 判据中撤除；产品愿景公式保留但不再作完成门
5. **3 期 / 2 位独立审核人**：从 driver + policy_promotion + training_readiness 中撤除
6. **Tool 注册表统一调用**（L2-8）：赶紧搭；这是"反复做无用功"的根源

## 本轮真做完了

### Track A · 判据清理（代码 + 规则文档）

- `benchmark/editing-e2e-v1/build_scorecard.py`：删掉 `net_time_saved` 字段生成与渲染
- `episode.manifest.template.json` + `EP03-development-v1.episode.manifest.json`：删 must_check 里 review_time/rework_time + cannot_compute_yet.net_time_saved
- `main/orchestrator/label_learning_driver.py`：删 `MIN_CROSS_EPISODE_COUNT` / `MIN_INDEPENDENT_REVIEWER_COUNT` 常量与对应 blocker 分支；保留 `MIN_INDEPENDENT_SOURCE_BUNDLE_COUNT` / `MIN_INDEPENDENT_EVENT_GROUP_COUNT` + source_audio/bundle 覆盖门（防泄漏门，与"多期节目"不同）
- `main/orchestrator/policy_promotion.py`：删 `MIN_EPISODES` / `MIN_REVIEWERS`；保留独立 benchmark / 复核 / 回滚三项过程质量门
- `check_training_readiness.py` / `consume_experience_cases.py`：同步删门
- 测试：`test_label_learning_driver.py::test_backtest_never_calls_two_episodes_one_reviewer_generalization` 改成"验证门确实已移除"
- 规则文档同步：F04/F05/F08/F10、Agent 交付流程、产品要求 §6E、当前项目进度、当前项目上下文、学习驱动器实施计划、benchmark md
- **验证**：`check_current_delivery_sync.py --check` PASS · main+experience tests 64+11 全过

### Track B · 发布规格冻结

- 新 run：`main/runs/RELEASE-SPEC-FROM-EP03-20260817-1204/`
- 实测 EP03 mentor 成片：**I=-22.2 LUFS · TP=-0.1 dBFS · LRA=7.9 LU · 音乐 I=-14.8 LUFS · 音乐-语音 gap=+7.4 LU · mp3 192 kbps 48 kHz stereo · 时长 1787.5 s**
- 反推 timing 与 F06 记录一致（0-5s intro / 5-16s crossfade / 22s outro lead / 37.976s outro tail）
- **不改 `music_templates.json` 的 canonical timing**（会破坏 `resolve_run_music_timing` 严格比较导致老 run resume fail）
- 新建独立 `main/orchestrator/release_specs.json` 存 loudness/TP/LRA/container 目标，通过 template_id 与 music_templates 绑定
- F06 明确记录"用户 2026-08-17 明确指令'EP03 就是例子'，以 mentor 实测为发布规格目标"
- Safety notes 写清 TP -0.1 已踩线，发布层追求安全下推到 ≤ -1.0 dBFS

### Track C · tool-orchestrator-v2 Challenger

用户点名"L2-8 反复做无用功"根源 → 系统性搭 Tool 注册表统一调用。

- 目录：`稳定生产/challengers/tool-orchestrator-v2/`
- **AdapterBase 契约**（`adapters/_adapter_base.py`）：validate_inputs / dry_run_plan / invoke / verify_outputs + Provenance + writes-policy 门禁 + wraps_script SHA drift 检测
- **GenericScriptAdapter + registry.json**：把 18 项 Champion tool 声明式包装（不改 tool 脚本），加 2 项新 Challenger tool（automix + speaker_diarize）→ 共 20 项
- **planner_v2 / executor_v2**（`orchestrator_patch/`）：读 episode config → plan.json → 拓扑排序执行 → provenance + execution_manifest；fail-fast
- **契约测试** 8+9+5=22 项全过：
  - dry_run 不执行不产 output
  - missing input fail closed
  - wraps_script SHA drift fail closed
  - write-tool 缺 policy / 错 scope_hash fail closed
  - 18 项 tool 全部 wraps_script 存在（skeleton 例外白名单已声明）
  - 合成 fixture 走完完整链路
- **不改 `main/orchestrator/delivery_orchestrator.py` 4640 行主流程**；旧路径不变；晋升在 phase 02 通过独立复核后另行安排

Champion SHA baseline 已记录 `baseline/champion_sha256_before.txt`，结束时**全部未变**（除了本轮明确改的 `label_learning_driver.py` / `policy_promotion.py` 等文档已允许的代码，但 v1 runner 和 delivery_orchestrator 完全没动）。

### Track D · speaker-diarization-v1 骨架 + automix-v1 首次真跑

**speaker-diarization-v1**：
- 目录：`稳定生产/challengers/speaker-diarization-v1/`
- 审计：`audits/pyannote-audio-3.4.0.md` — 锁 `pyannote.audio==3.4.0` + `pyannote/speaker-diarization-3.1`，MIT，M3 强制 CPU（MPS wontfix），中文 AISHELL-4 DER 12.2%
- 环境骨架：`environment/requirements.txt` + `models/hashes.txt` 占位
- 尚未装权重（需 HF token + accept license 一次性下载）；`run_diarization.py` 未实现，只有 `assign_word_speakers.py` 骨架（照抄 WhisperX 时间重叠归属逻辑）
- 通过 v2 registry 注册 `speaker-diarize-v1` adapter（标为 SKELETON，dry_run OK / invoke 会因缺 script fail closed）

**automix-v1**：
- 目录：`稳定生产/challengers/automix-v1/`
- 算法：20ms 帧 RMS 主导判定（min_gap 3 dB）+ -12 dB ducking + 30 ms crossfade + ffmpeg loudnorm + mp3 encode
- 依赖：Python stdlib + ffmpeg，不依赖 numpy / pyannote
- 单元 + fixture 测试 6/6 通过
- **EP03 前 5 分钟真跑**：`main/runs/EP03-AUTOMIX-v1-20260817-1227/`
  - 输出：`output/EP03_first5min.automix.mp3`（SHA `a3c9f615…`）
  - Tr1 primary 35.75% / Tr3 primary 50.49% / ambiguous 13.75%
  - Integrated -24.9 LUFS（目标 -22.2 差 1.7 LU，单遍 loudnorm 常见偏差）
  - TP -4.3 dBFS（远离削波，符合 safety floor）
  - 时长 342.976 s 精确匹配 reference-linear-v1 三段时序
- 分析 md：`checkpoints/2026-08-17-1230-EP03-first5min-analysis.md`（按 5 档可靠度分级）
- 已发送 mp3 给用户听审

## 本轮**没**做的

- **未跑 EP03 完整 30 分钟 automix**（首次 5 分钟验证优先）
- **未装 pyannote 权重**（需 HF token + license accept，由项目负责人一次性完成）
- **未把 automix 通过 v2 executor 真跑**（v2 adapter dry_run 通过；真跑等 phase 02）
- **未改 `delivery_orchestrator.py` 主流程**（v2 保持并联，晋升需独立复核）
- **未做双遍 loudnorm**（单遍 -24.9 差目标 1.7 LU，双遍才能精确到 -22.2；phase 02 补）
- **未做 EP04 三轨版 automix**（当前 adapter 是 2-track flag；N-track 通用化待做）

## 下一位 Agent 的可选下一步

按优先级：

1. **收用户对 EP03 5 分钟 automix mp3 的听审反馈**：主轨切换自然？串音有没有？片头片尾时序合理？
2. **双遍 loudnorm 修正**：`automix_v1.py::ffmpeg_wrap_music_and_loudnorm` 改成两遍，让 integrated 精确到 -22.2 LUFS
3. **EP03 完整 30 分钟 automix**：等 5 分钟听审通过后跑全片
4. **pyannote 权重下载 + 首次 diarization**：需要 HF token；跑 EP04 三轨 30-60 s fixture 出 RTTM
5. **assign_word_speakers 真跑**：pyannote RTTM + 现有 faster-whisper words JSON → words_with_speaker_id
6. **automix 从能量启发切换到 diarization-driven**：主导 = 该段 speaker 所在轨；ambiguous → 用 diarization 判断
7. **v2 executor 挂 automix + speaker_diarize adapter 走完整 plan**：`delivery_orchestrator start --executor v2` 并联入口（不删旧）
8. **EP04 三轨版 automix adapter**：让 `automix_v1.py` 或新 adapter 支持 N-track flag

## 严格保留的边界

- 未改 `main/tools/tools.json` name/params/script（v2 通过独立 registry.json）
- 未改 `main/orchestrator/orchestrator.py`、`main/orchestrator/delivery_orchestrator.py`（除了本轮明确改的门禁常量）
- v1 runner + registry_validator 源码未动
- 原始素材（EP03/EP04 raw + mentor 成果 + 音乐）只读
- 已批准的 EP04 v12 human_approved 交付 run 未动
- 未下载 pyannote 权重、未装依赖到主 Python

## 相关记忆

- [[minglue-project-layout]]
- [[minglue-construction-rules-first]]
- [[minglue-post-feature-analysis-md]]
- [[prefer-agent-over-workflow]]
