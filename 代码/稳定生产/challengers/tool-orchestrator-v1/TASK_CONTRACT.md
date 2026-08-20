# Task Contract · tool-orchestrator-v1 · 夜间施工周期

> Worker：单一施工 Agent（Claude Code，本地会话）
> 起始日期：2026-08-12
> Challenger 隔离目录：`稳定生产/challengers/tool-orchestrator-v1/**`
> 新 run 目录（尚未创建）：`main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1/**`

## 目的

在不覆盖 Champion 与既有 run 的前提下，建立“Tool 注册表 → 统筹层”的最小可运行桥接，并留下完整、可复现工程证据。这是 F07 的第一次真正把 `tools.json` 接进 runner，且只走到 `HUMAN_REVIEW_REQUIRED` 就停止；不得越过真人审核。

## 允许修改

- `稳定生产/challengers/tool-orchestrator-v1/**`（本 Challenger）
- 新建的 `main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-<timestamp>/**`

其它一切目录只读。

## 严禁

- 修改 `main/tools/tools.json`、`main/orchestrator/orchestrator.py`、`稳定生产/scripts/**`、`稳定生产/rules/**`、`端到端学习剪辑/代码/**`。
- 修改或删除 `main/runs/` 中任何已有目录。
- 覆盖任何 Champion 产物或已有 run。
- 使用真实 WAV、Mentor 素材或既有转写作为输出目标。
- 自动 accept/reject 语义删剪；自动进入 APPROVED/FINALIZED/ARCHIVED。
- 引入 LLM 做剪口决定；下载新模型或安装新依赖；把音频上传外部服务。
- 修改 `.gitignore`、进行 git commit/push/reset/checkout。

## 输出与证据

- `TASK_CONTRACT.md`（本文件）
- `before_inventory.md`
- `baseline/git_status_before.txt`、`baseline/champion_sha256_before.txt`
- `checkpoints/phase-XX-*.md`
- `HANDOFF.md`（暂停或结束时）
- `优化候选.md`、`benchmark_report.md`、`exact_commands.md`、`README.md`

## 完成门（本次夜间周期）

1. Phase 1 注册表校验器：19 tool 全部通过静态校验，或对每个失败项写明诊断；对失败/异常 fixture 均有断言。
2. Phase 2 Runner：能读 tool 注册表、生成冻结计划、只跑 `reads_only=true` 前置工具并在 `HUMAN_REVIEW_REQUIRED` 停止；有 dry-run / resume / stop-at；重复运行拒绝覆盖。
3. Phase 3 N 轨契约：episode config 用 `track_id / label / sha256`；2/3/4 轨自动测试通过。
4. Phase 4 前置工具接通：至少一个真实脚本经 runner 调用得到输出（在新 run 目录），并另外维持一个 mock tool 用于契约测试。
5. Phase 5 安全门：11 类失败场景各有测试通过。
6. Phase 6：合成 fixture 全流程真实跑到 `HUMAN_REVIEW_REQUIRED`。
7. Champion SHA、`tools.json`、`orchestrator.py` 结束时保持不变。
