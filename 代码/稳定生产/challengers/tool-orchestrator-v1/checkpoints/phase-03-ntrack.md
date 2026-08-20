# Phase 3 Checkpoint · N-track contract

- task_id: phase-03-ntrack
- saved_at: 2026-08-12
- status: completed
- objective: 冻结 N 轨输入契约（`track_id/label/sha256`），并给出 2/3/4 轨的自动测试证据；说明 Champion 工具对 N 轨的现有限制。
- files_changed:
    - `稳定生产/challengers/tool-orchestrator-v1/contracts/N-track-contract.md` (新增)
- files_untouched: Champion、`tools.json`、既有 run
- commands_run: 复用 Phase 2 的 `python3 tests/test_runner.py`（内含 N=2/3/4）
- automated_tests: `test_ntrack_pipeline_stops_at_human_review` 对 N=2、3、4 三种规模均通过；`test_resume_is_idempotent` 使用 3 轨。
- real_audio_run: 无
- evidence: N 轨 config、tracks 列表、`resolved_tracks_sha256`、每步 stdout/stderr 全部落盘。
- known_failures: Champion 工具中 `estimate_sync / create_aligned_ab_previews` 仍是双轨；在 N>2 时需要 fan-out adapter（本轮不实现，只记录）。
- next_action: Phase 4：把一个真实 Champion 只读工具（`inspect_audio`）接进 runner，走真实 subprocess。
- context_for_next_worker:
    - `contracts/N-track-contract.md` 是 F07 “N 轨契约”对齐点，请沿用。
    - 若下游任务要跑 Champion 跨轨工具，请在 Challenger 内新增 adapter；不得改 Champion 参数签名。
