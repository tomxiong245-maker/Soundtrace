# Runner & Episode Config schema · tool-orchestrator-v1

## 目的

在不修改 Champion / `main/tools/tools.json` / `main/orchestrator.py` 的前提下，把
一份 episode config 转成冻结执行计划，逐步骤真调 Tool 注册表登记的脚本，直到
`HUMAN_REVIEW_REQUIRED` 就停止。Runner 只作为 F07 的 Challenger 存在，禁止取代
Champion `orchestrator.py`。

## Episode config 字段 (JSON)

```json
{
  "episode_id": "TOOL-ORCH-FIXTURE-<slug>",
  "tracks": [
    {
      "track_id": "track_01",
      "label": "physical_mic_a",
      "input_path": "<absolute or project-relative>",
      "sample_rate": 48000,
      "channel_count": 1,
      "duration_seconds": 20.0,
      "sha256": "<expected hash>"
    }
  ],
  "steps": [
    {
      "step_id": "01_inspect",
      "tool": "mock_inspect",
      "phase": "pre_review",
      "params": {
        "input_json": "@track:track_01.input_path",
        "output_json": "run:01_inspect/track_01.inspection.json"
      }
    }
  ],
  "human_review_after": "<step_id>"
}
```

字段说明：

- `episode_id` 必填；用于日志与 audit。
- `tracks[*]` 使用产品层 N 轨契约字段：`track_id / label / input_path / sample_rate /
  channel_count / duration_seconds / sha256`。**禁止 female/male 字段。**
- 每条 track 的 `sha256` 是期望值；Runner 在计划冻结时会重新计算并比对，不一致
  就直接失败（fail closed）。
- `steps[*].tool` 必须命中 tool 注册表，且在本 Challenger 中必须是
  `reads_only=true`。
- `steps[*].params` 必须完整覆盖 tool 注册表所声明的参数名；每个值：
    - 字面量字符串：直接透传；
    - `@track:<track_id>.<field>`：解析成对应 track 字段；
    - `run:<subpath>`：解析为 `<run_dir>/<subpath>`；所有 `output*` 参数必须使用
      此形式，且不可通过 `..` 或符号链接逃逸；
    - `abs:<absolute_path>`：仅可用于 project 内部的输入路径，不能用于输出；
- `human_review_after`：必填。Runner 在成功执行完该 `step_id` 后必须停在
  `HUMAN_REVIEW_REQUIRED` 状态；不得自动进入后续步骤。

## 执行计划 (`plan.json`) 冻结字段

Runner 在首次运行时把 episode config 转成以下 plan：

```json
{
  "episode_id": "...",
  "created_at": "<UTC ISO>",
  "runner_version": "tool-orchestrator-v1/0.2",
  "registry_path": "main/tools/tools.json",
  "registry_sha256": "...",
  "tracks": [...],
  "resolved_tracks_sha256": {"track_01": "<measured>"},
  "steps": [
    {
      "step_id": "01_inspect",
      "tool": "mock_inspect",
      "reads_only": true,
      "script": "<registry-relative>",
      "script_path": "<full path>",
      "script_sha256": "<hash>",
      "resolved_params": {"input_json": "<abs path>", "output_json": "<abs path>"}
    }
  ],
  "human_review_after": "01_inspect"
}
```

## Runner 状态与产物

- `<run_dir>/state.json`：当前状态 (`CREATED / PLAN_FROZEN / RUNNING /
  HUMAN_REVIEW_REQUIRED / FAILED / COMPLETED_PRE_REVIEW`)、上次执行的 step_id、
  已完成 step_id 列表。
- `<run_dir>/plan.json`：冻结计划，只在首次运行 (`create`) 时写；resume 时校验
  一致性，不再改写。
- `<run_dir>/tool_calls.jsonl`：每次真实 tool 调用一行，含 `step_id / tool / cmd /
  returncode / duration_ms / started_at / stdout_sha256 / stderr_sha256`。
- `<run_dir>/dry_run_tool_calls.jsonl`：dry-run 计划检查日志；不会推进正式 state。
- `<run_dir>/run_manifest.json`：结束时（stop 或失败）写入，含每个 step 的
  output 文件路径与 SHA-256。
- 每步的 stdout/stderr 存在 `<run_dir>/logs/<step_id>.stdout.txt` 与
  `<run_dir>/logs/<step_id>.stderr.txt`。

## 命令

```bash
runner.py create --config <episode.json> --run-dir <new_dir>
runner.py run    --run-dir <run_dir> [--dry-run] [--stop-at <step_id>]
runner.py status --run-dir <run_dir>
runner.py resume --run-dir <run_dir> [--stop-at <step_id>]
```

- `create`：run_dir 已存在则拒绝（不覆盖）。
- `run`：从 `PLAN_FROZEN` 或中间 checkpoint 往前推；命中 `human_review_after`
  或 `--stop-at` 就停在 `HUMAN_REVIEW_REQUIRED` 或 `STOPPED_AT_CHECKPOINT`。
- `resume`：只恢复尚未完成的 pre-review 步骤；到达 `HUMAN_REVIEW_REQUIRED` 后，
  `run` 与 `resume` 都不会自动前进。
- 无 `approve` / `finalize` / `archive`。这些语义留给 Champion `orchestrator.py`
  与真人流程；本 Challenger 显式不做。

## 安全门（Phase 5 会补充测试）

- `run_dir` 已存在且非空：拒绝，避免覆盖既有 run。
- tool 不在注册表：拒绝并写 `FAILED`。
- `resolved_params` 中输入文件缺失或 SHA 变化：`FAILED`。
- 任意 `reads_only=false` 工具、任何 `phase != "pre_review"`、缺失人工闸门或
  run 外输出路径：拒绝。本 Challenger 不允许出现 post-review 的实际执行。
- `registry_sha256` 变化：`resume` 时拒绝继续；必须开新 run。
