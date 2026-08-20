# tool-orchestrator-v1 (Challenger)

> 状态：**STATIC_TESTS_PASS + SYNTHETIC_AUDIO_SUBPROCESS_RUN_PASS + NOT_PROMOTED**
> 日期：2026-08-12
> 隔离目录：`稳定生产/challengers/tool-orchestrator-v1/**`
> 允许写入的 run：`main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-<timestamp>/`

## 一句话现状（诚实分层）

- **[已验证事实]** 新 runner 能静态校验 `main/tools/tools.json`（19 项），并执行
  Challenger 内部 adapter 注册表中的安全只读工具；在 3 轨合成 WAV fixture 上真实调用了
  Champion `inspect_audio.py` 与 Challenger `summarize_inspection_adapter.py`，
  最终停在 `HUMAN_REVIEW_REQUIRED`，未越过真人审核，未自动 approve / finalize。
- **[已验证事实]** runner 在冻结计划时拒绝 `reads_only=false` 工具；注册表拒绝绝对、
  `..` 或符号链接逃逸的 scripts root；`--dry-run` 不推进正式 state，也不写正式调用日志。
- **[已验证事实]** Champion 27 项被跟踪文件在开工前后 SHA-256 完全一致；
  `main/tools/tools.json` 和 `main/orchestrator/orchestrator.py` 未改。
- **[已决定的方向]** 长期把 Champion `orchestrator.py` 替换为按注册表编排的
  runner；本 Challenger 只完成读注册表 + 只读工具接入 + 安全门；写工具、审核、
  渲染仍由 P1 review-product-v1 与 Champion 分别负责。
- **[待验证假设]** 真实 EP04 三轨 WAV 若接进这里能否稳定跑完 pre_review：本轮未做
  （不违反“不覆盖既有 run”与“不改 Champion”的约束）。

## 目录

```
稳定生产/challengers/tool-orchestrator-v1/
├── README.md
├── TASK_CONTRACT.md
├── HANDOFF.md
├── before_inventory.md
├── benchmark_report.md
├── 优化候选.md
├── exact_commands.md
├── baseline/
│   ├── git_status_before.txt
│   └── champion_sha256_before.txt
├── checkpoints/
│   ├── phase-00-blocked.md              (前一次未启动的历史遗留;保留)
│   ├── phase-00-baseline.md
│   ├── phase-01-registry-validator.md
│   ├── phase-02-runner.md
│   ├── phase-03-ntrack.md
│   ├── phase-04-inspect-real-tool.md
│   ├── phase-05-safety-gates.md
│   └── phase-06-real-e2e.md
│   ├── phase-07-docs-final.md
│   └── phase-08-independent-audit-hardening.md
├── contracts/
│   ├── Task Contract - Phase 00.md      (前一次遗留)
│   ├── N-track-contract.md
│   └── episode.example.json
├── runner/
│   ├── SCHEMA.md
│   ├── registry_validator.py
│   └── runner.py
├── adapters/
│   ├── inspect_audio_adapter.py
│   └── summarize_inspection_adapter.py
├── registries/
│   └── adapters.tools.json              (Challenger 本地注册表)
├── scripts/
├── tests/
│   ├── test_registry_validator.py       7/7 pass
│   ├── test_runner.py                   12/12 pass
│   ├── test_safety_gates.py             7/7 pass
│   └── fixtures/                        (mock 脚本与 registry fixtures)
```

对应 run 目录：

```
main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-20260812-010424/   # 失败证据
main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-20260812-010506/   # inspect_audio 成功
main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-20260812-010727/   # 2 步端到端
```

## 使用

见 `exact_commands.md`。核心命令：

```
python3 runner/registry_validator.py main/tools/tools.json \
  --project-root . --require-scripts

python3 runner/runner.py create --config <episode.json> \
  --run-dir <new_run> \
  --registry registries/adapters.tools.json --project-root .

python3 runner/runner.py run --run-dir <new_run>
```

## 明确不做（NOT-TODO）

- 不改 `main/tools/tools.json`、`main/orchestrator.py`、`稳定生产/scripts/`、
  `稳定生产/rules/`、`端到端学习剪辑/代码/`。
- 不 approve / finalize / archive；不自动生成语义 EDL；不渲染成片；不做
  automix、响度决策、发布 QC。
- 不引入 LLM 做剪辑决定；不下载模型；不安装新依赖；不上传任何音频。
- 不执行 `reads_only=false` 工具，即使它被错误放入 `pre_review` 阶段。
