# Phase 01 checkpoint · adapter contract + registry + planner/executor

> 日期：2026-08-17
> 状态：**SKELETON_TESTS_PASS + NOT_PROMOTED**

## 已完成

**C1 骨架**（`README.md` / `TASK_CONTRACT.md` / `baseline/`）
- `git_status_before.txt`（160 行改动记录）
- `champion_sha256_before.txt`（5 项关键文件 SHA-256）
- `baseline/ep03_materials.md`（EP03 Mentor + 双轨 + 音乐素材路径与 SHA）

**C2 契约**（`contracts/adapter.schema.v1.json` / `adapters/_adapter_base.py`）
- adapter.schema.v1.json：adapter self-declaration JSON schema
- AdapterBase 抽象基类：validate_inputs / dry_run_plan / invoke / verify_outputs + Provenance + writes-policy 门禁
- 8/8 契约测试通过：
  - dry_run 不执行、不产 output
  - invoke 成功写 provenance
  - missing input 立即 fail closed
  - wraps_script SHA drift fail closed
  - write-tool 缺 policy fail closed
  - write-tool 错 scope_hash fail closed
  - verify_outputs 抓缺文件/空文件/非零退出

**C3 registry**（`adapters/generic_script_adapter.py` / `adapters/registry.json`）
- GenericScriptAdapter：声明式 wrap，18 项 Champion tool 各一个 adapter contract
- 每条 adapter：wraps_script + inputs_schema + outputs_schema + reads_only + write_policy（write-tool）
- 9/9 契约测试通过：
  - registry schema 校验
  - 18 项 tool 全部注册
  - 无重复 adapter_id / tool_name
  - 每条 wraps_script 文件存在
  - reads_only=false 都声明 write_policy
  - 全部 adapter dry_run 成功
  - InputsValidationError 正确抛
  - dry_run 不产 output
  - scope hash order-independent

**C4 planner/executor**（`orchestrator_patch/planner_v2.py` / `executor_v2.py`）
- planner_v2：读 episode config + policy_bindings → delivery-plan-v1 JSON
- executor_v2：topological 排序 → dry_run 或真执行 + provenance 累积 → execution_manifest.json
- fail-fast：任一 step 失败立即停

**C5 契约测试**（`tests/test_planner_executor_fixture.py`）
- 5/5 通过：
  - planner 出 4 步（3 inspect + 1 sync）
  - dry_run 不产 output
  - 完整 chain 真跑合成 WAV → 全部 OK → 每步写 provenance
  - 首个 step 失败立即停
  - topological_order 检测环

## 未做（明确不做）

- 未改 `main/orchestrator/delivery_orchestrator.py` 4640 行主流程；旧路径不变
- 未晋升 v2 到 Champion；tools.json 未追加/修改 name/params/script
- 未接通 EP03/EP04 真实素材（此 phase 只用合成 fixture）
- 未做 write-tool 的整片渲染 fixture（速度考虑；Phase 02 补）

## Champion SHA baseline 对比

对比 `baseline/champion_sha256_before.txt` 中记录的 5 项文件当前 SHA：

- `main/orchestrator/delivery_orchestrator.py`：**未改动**（仍在 baseline SHA）
- `main/orchestrator/orchestrator.py`：**未改动**
- `main/tools/tools.json`：**未改动**（v2 通过 registry.json 独立注册，不污染 canonical）
- v1 runner.py / registry_validator.py：**未改动**

主流程和 v1 完全隔离。

## 下一 phase 目标

- Phase 02：真实 EP03/EP04 素材接入 v2 registry；跑 write-tool（denoise / render_approved_edl）真链路；对接 delivery_orchestrator 的并联入口（不删旧路径）。
- 并行：Track D speaker-diarization-v1 + automix_v1 通过 v2 registry 注册；作为 v2 首个"新能力上线不改主流程"的验收样本。
