# HANDOFF · experience-ingestion-v1

## 当前状态

- Challenger 建立完成，位于 `稳定生产/challengers/experience-ingestion-v1/`。
- 当前有效快照为 `case_store/two-state-v1-20260812-1627/`；已导入 27 条真人审核案例
  （EP03 11 + EP04 16，含 EP04 filler/长停顿 3 条）。
- 已排除 26 条 EP03 bulk_accept 案例。
- 已生成经验摘要、规则建议（全部 `NO_PRODUCTION_CHANGE`）、训练准备度（`NOT_READY`）与只读 adapter 输出；
  20/20 标准库契约测试已实际通过。
- 生产规则、Champion、审核前端与 canonical experience snapshot 均未修改。

## 未做的事情

- 未训练任何模型；未写模型权重。
- 未修改稳定生产/rules/**、稳定生产/scripts/**、审核前端。
- 未修改 `filler-global-pause-v1` Challenger 或其 run 目录。
- 未重跑候选生成、未重跑审核、未重新生成 EDL、未重新渲染。
- 未生成会话长摘要；不新增顶层文档。

## 下一步能做的事

1. 多期节目：从 EP05 起继续保留逐项审核，持续积累跨节目案例。
2. 冻结独立 benchmark：`benchmark/` 目录冻结独立 gold（非训练集）。
3. 引入独立复核人：多个 reviewer 后即可满足 `reviewer_count >= 2`。
4. 未来如恢复 `adjust`，它用于边界学习增强；当前二元候选排序研究不以它为前置条件。

`review_mode` 现在仅作兼容元数据，`adjust` 也不属于当前二态 MVP 的阻塞项；二者均不阻塞案例入库、
案例检索或 Skill/规则分析。模型训练仍因数据量、期数、审核人、冻结 benchmark、独立复核和回滚演练不足而
保持 `NOT_READY`。

## 快照陷阱

- `case_store/` 根目录和 `reports/` 里 14:46 的首轮文件保留为历史证据，其中仍可能出现
  `pending_review_mode` 字样；它们不是当前二态口径。
- 第一次新复跑 `main/runs/EXPERIENCE-INGESTION-v1-20260812-1612/` 检出 EP04 filler 的两个
  保存文件相对旧 baseline 发生 SHA 漂移，并正确隔离了该 source。该 run 是 fail-closed 证据，
  不是当前案例快照。
- 在逐字段复核三条决定的语义投影和未变化的 review package 后，已单独冻结
  `baseline/source_shas.two-state-v1.json`；它只服务当前二态经验快照，绝不改写旧 baseline。

## 已知边界与陷阱

- `main/runs/EP04-filler-global-pause-v1-r2-20260812/` 同时存在 `review_bundle/` 与
  `review_bundle-final/`；真人决定绑定 `-final`。collector 已优先选 `-final`。
- baseline 保存的是 SHA，不是内容备份。任何来源变化都会立刻 fail-closed，需要重新
  评估后再冻结 baseline，禁止直接绕过。
- 不要把当前的 `human_accept_rate_by_reason` 写成模型 precision。
- 不要把本目录挂到统筹 orchestrator 直接调用，除非上层同意“只读 adapter”限制。

## 交接文档链接

- 任务契约：`TASK_CONTRACT.md`
- 说明：`README.md`
- 命令：`exact_commands.md`
- 报告：`benchmark_report.md`
- 案例库：`case_store/`
- 消费报告：`reports/`
- 真实运行：`main/runs/EXPERIENCE-INGESTION-v1-20260812-144630/`
