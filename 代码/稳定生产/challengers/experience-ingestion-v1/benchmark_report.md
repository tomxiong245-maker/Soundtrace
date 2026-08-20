# benchmark_report · experience-ingestion-v1

> 本 Challenger **不做**候选/EDL 生成质量 benchmark。本任务只把已有真人审核结果整理为
> 隔离的经验案例库，并做只读消费。本文件用于记录“**入库/消费**过程的自动与真实运行结果”。

## 自动测试

- 历史首轮：18 条 pytest 契约测试通过（`pytest -x -q`）。
- 覆盖：合法导入、bulk_accept 排除、package_id 缺失、review_manifest 不一致、candidate
  semantic sha 不一致、未知 candidate、重复决定、pending 决定、缺 must_listen、无 EDL 的
  accept 不被标 applied、source SHA 漂移 fail-closed、quarantine 不入统计、数据不足不给
  生产变更、consumer 不写规则、readiness=NOT_READY、adapter 禁止动作齐全、SHA 可复现、
  Champion 目录不被写入。

## 当前二态复核（2026-08-12 16:27）

- 新增两条契约：逐项 `accept/reject` 案例不因缺 `review_mode` 降级；`adjust=0` 与缺
  `review_mode` 不属于当前二态路线的 readiness 阻塞理由。
- 当前机器没有 pytest；新增标准库 runner 后，**20/20 PASS**。
- 旧 baseline 首次复跑正确 fail-closed，检出 EP04 filler/long-pause 的审核保存文件在旧冻结后
  发生 SHA 漂移。逐字段复核三条决定的语义投影后，使用独立 two-state baseline 重新运行。
- 当前有效 run：`main/runs/EXPERIENCE-INGESTION-v1-20260812-1627/`；结果为 cases=27、
  exclusions=26、quarantine=0。详见该目录 `RUN_REPORT.md`。

## 真实运行统计（2026-08-12 14:46）

- 案例总数：27
- 按 episode：`EP03=11`，`EP04=16`
- 按 reason_key：`immediate_repetition=20`，`filler_hesitation=6`，`global_long_pause=1`
- 按决定：`reject=15`，`accept=12`，`adjust=0`
- 按 review_basis：`text_only=11`，`text_with_audio=14`，`text_and_audio=2`
- 有 EDL：10（EP03 4 项 + EP04 6 项 `review-product-v1/v2`）；无 EDL：17
- 音频证据完整率：100%（27/27，含 `filler-global-pause` 3 条必听候选）
- 排除 bulk_accept：26（`main/runs/EP03/`）
- quarantine：0

## 按规则的人工接受率（不是模型 precision）

| reason_key | 总数 | accept | reject | adjust | 人工接受率 | 节目 |
|---|---:|---:|---:|---:|---:|---|
| immediate_repetition | 20 | 8 | 12 | 0 | 40.00% | EP03, EP04 |
| filler_hesitation | 6 | 3 | 3 | 0 | 50.00% | EP03, EP04 |
| global_long_pause | 1 | 1 | 0 | 0 | 100.00% | EP04 |

## 训练准备度

- `status = NOT_READY`
- `model_trained = false`
- reasons：
  1. 有效数据量 27 < 500
  2. 审核期数 2 < 10
  3. 独立审核人 1 < 2
  4. 无冻结独立 benchmark
  5. 无独立复核
  6. 无回滚演练

## SHA 复核

- 14 项来源文件 SHA 前后一致。
- 11 项禁止目录/文件 SHA 未变（含 `稳定生产/rules`、`稳定生产/scripts`、
  `稳定生产/challengers/filler-global-pause-v1`、`main/runs/EP04-filler-global-pause-v1-r2-20260812`、
  `main/runs/EP03-review-product-v1`、`main/runs/EP04-review-product-v2`、
  `main/knowledge/experience_snapshot/index.json`、`main/tools/tools.json`、
  `main/orchestrator/orchestrator.py`、`端到端学习剪辑/代码`、`审核前端`）。

## 结论

- **已验证事实**：Challenger 经验案例库、只读消费者、准备度门禁与只读 adapter 均已在真实
  数据上跑通；当前二态快照含 27 条 `eligible_rule_only` 案例。生产规则、Champion、审核前端与
  canonical experience snapshot 未被写入。
- **已决定的方向**：把入库、消费与训练准备度作为未来监督学习的稳定入口；当前
  `review_mode` 仅作兼容元数据，`adjust` 暂不做；仍需多期、多审核人、冻结 benchmark、独立复核与回滚。
- **待验证假设**：本口径下的“人工接受率”能否稳定预测未来剪辑质量，尚需多期数据佐证。
