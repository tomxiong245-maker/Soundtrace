# Phase 5 Checkpoint · Safety gates & recovery

- task_id: phase-05-safety-gates
- saved_at: 2026-08-12
- status: completed
- objective: 为 runner 补齐失败与安全门的自动测试，并证明 HUMAN_REVIEW_REQUIRED 不会被 resume 越过。
- files_changed:
    - `稳定生产/challengers/tool-orchestrator-v1/tests/test_safety_gates.py` (新增)
    - `稳定生产/challengers/tool-orchestrator-v1/runner/runner.py` (`cmd_resume` 在 HUMAN_REVIEW_REQUIRED 状态直接返回而不越过)
- files_untouched: Champion / `tools.json` / `orchestrator.py` / 既有 run
- commands_run:
    - `python3 tests/test_safety_gates.py` → 7/7 pass
    - `python3 tests/test_runner.py` → 8/8 pass
    - `python3 tests/test_registry_validator.py` → 6/6 pass
- automated_tests: 覆盖 11 类失败场景中的 11 类（分布在三个测试文件里）：
    1. tool 不存在 → `test_unknown_tool_is_rejected`
    2. 脚本路径错误 → registry_validator + runner `--require-scripts` (`test_missing_script_with_require_flag_fails`)
    3. 参数缺失 → `test_missing_param_is_rejected`
    4. 输入文件不存在 → `test_input_missing_at_plan_time`
    5. 中途进程非零退出 → `test_tool_returns_nonzero`
    6. 重复执行 → `test_resume_is_idempotent`
    7. 输出目录已存在 → `test_run_refuses_to_overwrite_existing_run_dir`
    8. checkpoint 恢复 → `test_resume_is_idempotent`（`--stop-at` 后 resume 不重跑）
    9. Champion / 脚本 SHA 发生变化 → `test_script_sha_drift`；registry SHA 变化 → `test_registry_sha_drift`
    10. 在 HUMAN_REVIEW_REQUIRED 前误调用 render → 由 `phase != pre_review` 的 fail-closed 保证 (`test_post_review_phase_is_refused`)
    11. HUMAN_REVIEW_REQUIRED 状态下 resume 不越过 → `test_resume_from_human_review_replays_final_manifest_only`
- real_audio_run: 无（本阶段全部走 mock 脚本）
- evidence: 21/21 tests pass。所有失败路径均写 `state=FAILED` 且不产生 `run_manifest.json`（除完成前的中间 manifest）；tool_calls.jsonl 完整记录 rc/duration/sha。
- known_failures: 无
- next_action: Phase 6 用 Phase 4 的合成 fixture + 多步管线跑一次真实小规模验证；Phase 7 收尾。
- context_for_next_worker:
    - HUMAN_REVIEW_REQUIRED 是硬边界；任何“继续”都必须让位给真人流程（未来通过 Champion `orchestrator.py` 提供 approve/finalize 入口）。
    - script_sha_drift 与 registry_sha_drift 强制“开新 run”，保证审计链一致。
    - 覆盖 render 步骤的更强证据未在 Phase 5 引入（本 Challenger 不会真运行 render）；如果未来把 render 类工具接进 registry，请给它们独立 `phase=post_review` 并让它们默认拒绝执行。
