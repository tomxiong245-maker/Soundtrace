# Task Contract · tool-orchestrator-v2

> Worker：单一施工 Agent（Claude Code）
> 起始日期：2026-08-17
> 用户批准记录：2026-08-17 会话，用户明确说 "L2-8 赶紧搭，我觉得这是很多时候反复做无用功的问题"

## 目的

在不覆盖 Champion 与既有 run 的前提下，把 `main/tools/tools.json` 升级为主流程真正调用的能力目录，并把 `delivery_orchestrator.py` 4640 行胶水层拆成 planner + executor 两层。之后加新能力（如即将做的 speaker diarization + automix）不需要改主流程胶水，只需加一个 adapter + 一行 tool 注册。

## 允许修改

- `稳定生产/challengers/tool-orchestrator-v2/**`
- `main/tools/tools.json`：仅允许追加/修正 schema 描述与 audit 引用，**不改现有 tool 的 name/params/script 路径**
- `main/orchestrator/delivery_orchestrator.py`：分离 planner/executor **且保留旧路径并联**，新老通过 dry-run 结果哈希对齐；不删任何旧函数
- 新建 `main/runs/TOOL-ORCH-V2-*-<timestamp>/**`

## 严禁

- 修改 `稳定生产/scripts/**`、`稳定生产/rules/**`、`端到端学习剪辑/代码/**`
- 修改 `main/orchestrator/orchestrator.py`（旧演示）
- 修改 v1 的 runner/registry_validator 源码（v2 通过 subclass/patch 扩展，v1 保持不动）
- 修改或删除 `main/runs/` 中任何已有目录
- 覆盖任何 Champion 产物
- 使用真实 EP03/EP04 WAV 做输出目标（合成 fixture 为主，真实素材只做只读输入验证）
- 自动 accept/reject 语义删剪；自动进入 APPROVED/FINALIZED/ARCHIVED
- 引入 LLM 做剪口决定；下载新模型或装新依赖；把音频上传外部
- 修改 `.gitignore`、进行 git commit/push/reset/checkout

## 完成门

### Phase 1 · Adapter 契约（对应任务 C2）
- 定义 `AdapterContract` 抽象基类：
  - `validate_inputs(inputs) -> None | raise`
  - `dry_run_plan(inputs) -> dict`（命令、环境、期望输出路径、超时）
  - `invoke(inputs, run_dir) -> dict`（真实执行、返回 provenance）
  - `verify_outputs(outputs) -> None | raise`（SHA、schema、时间戳合法性）
- schema：JSON Schema for adapter input/output
- 契约测试：抽象基类的 5 个正/反向 fixture 全绿

### Phase 2 · 16 个新 adapter（对应任务 C3）
- 每个 adapter 一个文件；tools.json 里除 `inspect_audio` / `summarize_inspection`（v1 已做）外 16 项各一个
- adapter 内部 subprocess 调 tool script；不改 script 本身
- 每个 adapter 单元测试：至少 dry_run 通过 + 一次 fake 输入的 invoke 断言 SHA/schema
- `reads_only=false` 的 write-tool（denoise/correct_drift/render/assemble/finish 等）新增 policy 参数：`writes_policy_id` + `writes_scope_hash` 必须显式传入，否则拒调

### Phase 3 · Runner 扩展（write-tool 通道）
- 位于 `runner_patch/`：不改 v1 源码，用继承或组合方式扩展
- 支持：调 write-tool、SHA 冻结、超时、失败精准重试
- 拒绝：未声明 policy 的写调用、路径越界、已存在的输出覆盖
- 契约测试：新旧 runner 对同一 read-only fixture 输出等价（dry-run manifest bit-for-bit 一致）

### Phase 4 · planner/executor 分离（对应任务 C4）
- `main/orchestrator/delivery_orchestrator.py` 内新增：
  - `planner_v2.py`：读 episode config + 规则 + 偏好 → `plan.json`
  - `executor_v2.py`：消费 `plan.json`，通过 v2 runner 调 adapter
- **旧函数保留，新入口并联**：`delivery_orchestrator.py start --executor v2` 走新路径；默认 `start` 仍是旧路径
- 契约测试：同一合成 fixture 走旧路径和 v2 路径，最终产物 SHA 一致（除时间戳/run_id 类不可复现字段）

### Phase 5 · 契约测试 + fixture 全绿（对应任务 C5）
- 合成三轨 fixture 完整 v2 链路：inspect → denoise(fake) → ASR(fake) → activity → 候选 → 审核 → 双 EDL → 渲染
- Champion SHA 结束时保持不变（`main/orchestrator/orchestrator.py`, `稳定生产/scripts/`, `端到端学习剪辑/代码/`）
- `main/tools/tools.json` 允许追加字段但 name/params/script 保持不变
- v1 runner 源码 SHA 保持不变

## 输出与证据

- `TASK_CONTRACT.md`（本文件）
- `baseline/git_status_before.txt` ✅
- `baseline/champion_sha256_before.txt` ✅
- `checkpoints/phase-XX-*.md`（每 phase 结束一份）
- `contracts/adapter.schema.json`、`contracts/plan.schema.json`
- `adapters/*_adapter.py`（16 个）
- `runner_patch/*.py`
- `orchestrator_patch/planner_v2.py`, `orchestrator_patch/executor_v2.py`
- `tests/`（≥30 项通过）
- 每个 phase 一份 `main/runs/TOOL-ORCH-V2-phase-N-<timestamp>/evidence_manifest.json`

## 不可违反的顺序

1. 先写失败测试，再写实现（CLAUDE.md §施工方式 #2）
2. 每个 adapter 完成后必须有对应契约测试通过才推进下一个
3. Phase 4 的旧/新路径对齐通过前，不许把新路径设为默认
4. 任一 Champion SHA 意外变化 → 立即停工，比对 diff，回退

## 后续能力接入示范

Speaker diarization + automix 两个新 tool 完成后：
- 写 `speaker_diarize_adapter.py`, `automix_adapter.py`
- 在 `main/tools/tools.json` 追加两项 tool 定义
- **不需要改 delivery_orchestrator.py 主流程**——planner 从规则表读到"要跑 diarize + automix"就自动挂进 plan
- 这才是 v2 的验收标准
