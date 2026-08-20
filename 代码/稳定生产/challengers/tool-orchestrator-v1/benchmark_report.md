# benchmark_report · tool-orchestrator-v1

> 数据日期：2026-08-12
> 事实标记：所有数字都来自本 Challenger 目录下的可复现命令，未估算。

## 1. 静态测试

| test file | count | 结果 |
| --- | --- | --- |
| `tests/test_registry_validator.py` | 7 | 7/7 pass |
| `tests/test_runner.py` | 12 | 12/12 pass |
| `tests/test_safety_gates.py` | 7 | 7/7 pass |
| **合计** | **26** | **26/26 pass** |

覆盖场景（对齐 TASK_CONTRACT §Phase 5 的 11 类失败）：

1. tool 不存在 (`test_unknown_tool_is_rejected`)
2. 脚本路径错误 (`test_missing_script_with_require_flag_fails`)
3. 参数缺失 (`test_missing_param_is_rejected`)
4. 输入文件不存在 (`test_input_missing_at_plan_time`)
5. 中途进程退出 (`test_tool_returns_nonzero`)
6. 重复执行 (`test_resume_is_idempotent`)
7. 输出目录已存在 (`test_run_refuses_to_overwrite_existing_run_dir`)
8. checkpoint 恢复 (`test_resume_is_idempotent`)
9. Champion / 脚本 SHA 变化 (`test_script_sha_drift`) 及 registry SHA 变化 (`test_registry_sha_drift`)
10. 在 HUMAN_REVIEW_REQUIRED 前误调用 render / post_review (`test_post_review_phase_is_refused`)
11. resume 不越过 HUMAN_REVIEW_REQUIRED (`test_resume_from_human_review_replays_final_manifest_only`)
12. `reads_only=false` 工具即使标为 `pre_review` 也在冻结计划时被拒绝
    (`test_non_read_only_tool_is_rejected_at_plan_time`)
13. `scripts_root` 不能通过 `..` 逃逸项目根
    (`test_scripts_root_parent_escape_is_rejected`)
14. 缺失 `human_review_after` 会被拒绝
    (`test_missing_human_review_gate_is_rejected`)
15. 已处于 `HUMAN_REVIEW_REQUIRED` 时直接再次 `run` 不会推进状态
    (`test_direct_run_from_human_review_cannot_advance`)
16. 输出参数不能指向 run 目录以外
    (`test_output_outside_run_dir_is_rejected_at_plan_time`)

补充：`--dry-run` 测试确认不会推进正式 `state.json`、不会写正式
`tool_calls.jsonl`，随后同一 run 仍能进行一次真实执行。

## 2. 合成音频上的真实本地小规模运行

### 2.1 3 轨合成 fixture（Phase 4）

- 输入 3 条合成 mono 48 kHz WAV（220 / 330 / 440 Hz、2 s），不是公司真实录音。
- 通过 runner + Challenger `inspect_audio_adapter` 调 Champion
  `端到端学习剪辑/代码/inspect_audio.py`。
- 输出 `inspection.json`（schema_version=1；三条轨的 sha256/duration/channels/
  bits_per_sample 全部齐全）。
- state 最终：`HUMAN_REVIEW_REQUIRED`。

### 2.2 2 步端到端（Phase 6）

- 同一 3 轨 fixture。
- 步骤 1：Champion inspect_audio（真实）。
- 步骤 2：Challenger summarize_inspection_adapter（reads_only）读 step 1 输出，
  产出 `summary.json`：`track_count=3, sample_rates=[48000],
  channel_counts=[1], durations_seconds=[2.0, 2.0, 2.0]`。
- state 最终：`HUMAN_REVIEW_REQUIRED`；`completed_step_ids = [01_inspect_all,
  02_summary]`。
- `tool_calls.jsonl` 两行，均 rc=0；`run_manifest.json` 记录两条 output_json
  的 SHA256。

### 2.3 失败证据（保留）

- `main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-20260812-010424` 是
  adapter 首次 bug 导致的 rc=2 失败：`state=FAILED`、stderr 明确写明
  "Champion inspect_audio not found"、没有 `run_manifest.json`。这条 run 作为
  fail-closed 行为的真实证据保留，不清理。

## 3. Champion 完整性

- `baseline/champion_sha256_before.txt` 覆盖 27 项文件；Phase 7 结束时复算
  0/27 变化。**Champion 未被修改。**

## 4. 明确没有做

- 没跑真实 EP04 三轨 WAV（避免动 `main/runs/EP04-*` 与原始 WAV）。
- 没跑 `measure_loudness`（本机无 ffmpeg），仅在 Champion `inspect_audio` 上
  证明真实 subprocess。
- 没跑 `estimate_sync / correct_clock_drift / denoise / transcribe /
  build_review_package / render_approved_edl / assemble_program /
  finish_approved_project`。它们是 Champion 或非 pre_review 阶段，本轮明确
  fail closed。

## 5. 状态结论

- **[已验证事实]** F07 在 Challenger 范围内首次有可复现的“注册表校验 +
  计划冻结 + 真调只读 adapter + 多步链路 + 人工闸门”证据。它并不表示
  `main/orchestrator/orchestrator.py` 已经接通 19 项生产工具。
- **[待验证假设]** 真实 EP04 三轨或更多真实素材在此 runner 下的稳定性。
- **[未做]** 未晋升 Champion；未替换 `main/orchestrator/orchestrator.py`；未接入
  P1 审核前端；未产出任何真实 approved EDL / 成片。
