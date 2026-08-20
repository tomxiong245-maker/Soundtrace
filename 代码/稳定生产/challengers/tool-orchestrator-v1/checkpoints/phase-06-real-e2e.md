# Phase 6 Checkpoint · Real small-scale end-to-end

- task_id: phase-06-real-e2e
- saved_at: 2026-08-12
- status: completed
- objective: 用合成 3 轨 fixture 走一条 2 步管线（Champion inspect_audio → Challenger summarize_inspection_adapter），真实 subprocess，停在 HUMAN_REVIEW_REQUIRED。
- files_changed:
    - `稳定生产/challengers/tool-orchestrator-v1/adapters/summarize_inspection_adapter.py` (新增)
    - `稳定生产/challengers/tool-orchestrator-v1/registries/adapters.tools.json` (新增 tool 条目)
    - `main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-20260812-010727/**` (新增，见证据)
- files_untouched: `main/tools/tools.json`、`main/orchestrator.py`、Champion 27 项文件（结束时复算 SHA 与 Phase 0 基线一致）
- commands_run:
    - `runner.py create --config <cfg> --run-dir <RUN3>/run --registry adapters.tools.json --project-root .`
    - `runner.py run --run-dir <RUN3>/run`
- automated_tests: 复用 21/21 tests (registry_validator 6 + runner 8 + safety_gates 7)。真实端到端本身不作为单元测试断言，只留 run artifacts 作为证据。
- real_audio_run:
    - 输入：3 条合成 tone WAV（220 / 330 / 440 Hz、48 kHz mono、2 s、含 SHA256）。
    - 输出：
        - `01_inspect/inspection.json`（Champion inspect_audio 产出；三条轨的 sha256 与 audio 元信息）
        - `02_summary/summary.json`（Challenger summarize adapter 产出：track_count=3、sample_rates=[48000]、channel_counts=[1]、durations=[2,2,2]、input_json 的 SHA256）
    - `state.json` 最终 = `HUMAN_REVIEW_REQUIRED`；`completed_step_ids = [01_inspect_all, 02_summary]`。
    - `tool_calls.jsonl` 两行，均 rc=0；`run_manifest.json` 记录两条 output_json 的 SHA。
- evidence:
    - 见 `main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-20260812-010727/run/*`。
    - Champion SHA-256 复算：27/27 与 Phase 0 基线完全一致（0 changed）。
    - Runner 未自动进入 APPROVED / FINALIZED / ARCHIVED。resume 从 HUMAN_REVIEW_REQUIRED 状态直接返回，不越过（`test_resume_from_human_review_replays_final_manifest_only`）。
- known_failures: 无
- next_action: Phase 7 收尾文档与最终报告。
- context_for_next_worker:
    - 真实 EP04 3 轨 WAV 已存在于 `main/runs/EP04-*`，但为遵守“只读原始 WAV + 不覆盖既有 run”的边界，本 Challenger 未直接读取；后续把真实 EP04 输入接进来时，请：
      1. 计算并冻结每条 WAV 的 SHA256；
      2. 用新的 `main/runs/TOOL-ORCH-*` 目录作为 run_dir；
      3. 确保 runner 只走 pre_review 步骤，绝不触碰 EDL、审核决定或渲染。
    - Champion `orchestrator.py` 未修改；未来若要把 runner 替换到 Champion 位置，请另立任务并冻结 SHA。
