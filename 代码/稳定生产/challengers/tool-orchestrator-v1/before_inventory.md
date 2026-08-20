# before_inventory · tool-orchestrator-v1 · Phase 0

> 更新时间：2026-08-12（Phase 0）
> 事实标记：`[已验证事实]` 指该行来自当前工作树的直接读取与哈希计算。

## 1. 工作树基线 [已验证事实]

- 当前分支：`codex/publish-mvp`（`baseline/git_status_before.txt` 首行）。
- `git status --short --branch` 共 159 行；工作树自开工前就存在大量既存修改与删除（主要涉及 `mentor的成果/`、`main/runs/EP03-*`、`审核前端/`、`端到端学习剪辑/`）。**本 Challenger 不动这些遗留改动，也不 commit 它们。**
- 未跟踪或删除项与本任务无关，视为“环境噪声”。任何 Phase 都不得试图“修复”它们。
- 完整状态见 `baseline/git_status_before.txt`。

## 2. Champion / Tool 层哈希基线 [已验证事实]

`baseline/champion_sha256_before.txt` 记录 27 个文件的 SHA-256，覆盖：

- `稳定生产/scripts/annotate_shortlist.py`
- `稳定生产/scripts/generate_cut_candidates.py`
- `稳定生产/rules/candidate-generation.v1.json`
- `稳定生产/rules/shortlist.v1.json`
- `main/tools/tools.json`
- `main/orchestrator/orchestrator.py`
- `端到端学习剪辑/代码/*.py`（21 个脚本；不含 `__pycache__` 与隐藏文件）

结束 Phase 7 时会重新计算并比对。

## 3. Tool 注册表盘点 [已验证事实]

- 文件：`main/tools/tools.json`
- `schema_version = 1`
- `scripts_root = 端到端学习剪辑/代码`
- `tool_count = 19`
- 19 项脚本在 `scripts_root` 下**全部存在**；无重名 tool；无重复 script 路径。

按顺序：

| tool | reads_only | script | 状态 |
| --- | --- | --- | --- |
| inspect_audio | true | inspect_audio.py | 存在 |
| measure_loudness | true | measure_loudness.py | 存在 |
| analyze_reference_timeline | true | analyze_reference_timeline.py | 存在 |
| estimate_sync | true | estimate_sync.py | 存在 |
| correct_clock_drift | false | correct_clock_drift.py | 存在 |
| create_clock_drift_fixture | false | create_clock_drift_fixture.py | 存在 |
| denoise_tracks | false | denoise_tracks.py | 存在 |
| analyze_denoise_previews | false | analyze_denoise_previews.py | 存在 |
| shift_transcript_timeline | false | shift_transcript_timeline.py | 存在 |
| transcribe_tracks | false | transcribe_tracks.py | 存在 |
| classify_track_activity | false | classify_track_activity.py | 存在 |
| build_review_package | false | build_review_package.py | 存在 |
| build_priority_review_page | false | build_priority_review_page.py | 存在 |
| create_aligned_ab_previews | false | create_aligned_ab_previews.py | 存在 |
| approve_review_candidates | false | approve_review_candidates.py | 存在 |
| analyze_cut_transitions | true | analyze_cut_transitions.py | 存在 |
| render_approved_edl | false | render_approved_edl.py | 存在 |
| assemble_program | false | assemble_program.py | 存在 |
| finish_approved_project | false | finish_approved_project.py | 存在 |

## 4. 现有 orchestrator 接口 [已验证事实]

- 位置：`main/orchestrator/orchestrator.py`
- 是状态机演示，只演示 CREATED → PLANNED → PREPARED → WAITING_FOR_HUMAN_REVIEW → APPROVED → FINALIZED → ARCHIVED，并不调用注册表工具。
- 默认 `--run-dir` 指向 `main/runs/EP03`。
- 本 Challenger 不修改它，只作为“将来要替换/补充的目标”参考。

## 5. 相邻 Challenger 接口摘要 [已验证事实]

- **P0 asr-speaker-v1**（`稳定生产/challengers/asr-speaker-v1/README.md`）：只写 `challengers/asr-speaker-v1/`、`main/runs/EP03-asr-speaker-v1/`、`benchmark/EP03-ASR-mini-gold-v1/`。产出 normalized transcripts 供下游使用。
- **P1 review-product-v1**（`稳定生产/challengers/review-product-v1/README.md`）：新 episode 通过 `scripts/server_episode.py --config <cfg>` 构建/复用审核包，`render_episode.py` 渲染 EDL。
- **N-track ntrack-episode-bridge-v1**（`稳定生产/challengers/ntrack-episode-bridge-v1/README.md`）：把 P0 输出与 P1 MVP 桥起来，输入契约用 `track_id`。

## 6. 已存在的输出目录（禁止覆盖） [已验证事实]

`main/runs/` 现有目录（保持只读）：

- `EP03`
- `EP03-asr-speaker-v1`
- `EP03-cross-track-safety-v1`
- `EP03-freshrun-20260810-1730`
- `EP03-review-product-v1`
- `EP04-input-check-20260811`
- `EP04-ntrack-bridge-v1`
- `EP04-ntrack-bridge-v2`
- `EP04-p0-20260811`
- `EP04-p0-normalized-20260811`
- `EP04-review-product-v1`
- `EP04-review-product-v2`

本 Challenger 的输出目录使用 `main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-<timestamp>/`，与所有既有目录严格隔离。

## 7. 与 F07 记忆文档的一致性检查

- 记忆声明“19 项工具、脚本路径、参数、`reads_only` 已登记，未由 orchestrator 真实编排”。
- 本 inventory 独立复算得到相同事实：19 项、脚本齐全、`orchestrator.py` 只做状态机演示。**基线与记忆一致，可以继续 Phase 1。**
