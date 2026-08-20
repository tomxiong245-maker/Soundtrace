# experience-ingestion-v1 · 任务契约

> 任务名称：experience-ingestion-v1
> 起始日期：2026-08-12
> 归属：Challenger（隔离，禁止修改 Champion 与其他生产成果）

## 一、目标

将现有的真人审核结果按 fail-closed 的方式整理进一个**隔离的 Challenger 经验案例库**，并建立一个**只读**的经验消费者。任务只负责“反馈入库与离线消费”，不修改候选生成算法、审核前端、Champion 规则、canonical experience snapshot 或任何 Champion 产物。

## 二、范围与排除

处理来源：

- `main/runs/EP03-review-product-v1/`（EP03 逐项双态审核）
- `main/runs/EP04-review-product-v2/`（EP04 逐项双态审核）
- `main/runs/EP04-filler-global-pause-v1-r2-20260812/`（EP04 口癖/长停顿审核）

明确排除：

- `main/runs/EP03/`（bulk_accept_reference_prior_authorization），只作为“排除证据”。

不重新生成候选、不重新审核、不重新生成 EDL、不重新渲染。

## 三、目录所有权

只可写入：

- `稳定生产/challengers/experience-ingestion-v1/**`
- `main/runs/EXPERIENCE-INGESTION-v1-<timestamp>/**`

不得修改：

- 原始 WAV、Mentor 成果、Champion 脚本/规则
- `main/tools/tools.json`、`main/orchestrator/orchestrator.py`
- `稳定生产/scripts/**`、`稳定生产/rules/**`
- `端到端学习剪辑/代码/**`
- `稳定生产/challengers/filler-global-pause-v1/**`
- `main/runs/EP04-filler-global-pause-v1*/`
- 审核前端现有页面
- 已完成的 EP03/EP04 审核结果
- `main/knowledge/experience_snapshot/index.json`

## 四、可交付物

1. `schemas/experience_case.schema.json` — Challenger 经验案例 schema。
2. `scripts/collect_experience_cases.py` — fail-closed 导入器。
3. `scripts/consume_experience_cases.py` — 只读经验消费者。
4. `scripts/check_training_readiness.py` — 训练准备度门禁。
5. `scripts/experience_consumer_adapter.py` — 未来统筹 Agent 的只读入口。
6. `tests/` — 先失败后通过的自动测试。
7. `case_store/` — 案例与索引产物（本目录内 fixture）。
8. `main/runs/EXPERIENCE-INGESTION-v1-<ts>/` — 真实运行输出。
9. `reports/experience_summary.{json,md}`、`reports/rule_recommendations.json`、`reports/training_readiness.json`。
10. `HANDOFF.md`、`benchmark_report.md`、`exact_commands.md`。

## 五、结果口径

允许：

- “已建立 Challenger 经验案例库”
- “已导入 EP03/EP04 逐项双态决定与 EP04 口癖/长停顿 3 项决定”
- “已生成规则建议但未修改任何生产规则”
- “训练准备度：NOT_READY”

禁止：

- “系统已经学会”
- “模型已经训练”
- “规则已经自动优化”
- “经验已经进入 Champion”
- “可以无人审核剪辑”
- “所有候选都找全了”

当前产品决定：`review_mode` 只作兼容元数据，不阻塞逐项 `accept/reject` 案例入库、案例检索或 Skill/规则分析；但它不会因此变成模型训练或生产变更授权。

## 六、SHA 冻结

baseline SHA 保存在 `baseline/source_shas.json`。若来源文件在施工过程中变化，脚本必须停止并报告，禁止继续使用旧数据。

## 七、门禁

- 先写失败测试再实现；
- 所有真实运行必须写入独立 run 目录；
- 禁止目录在运行前后 SHA 不变；
- 不安装新依赖，除非明确证明需要且先报告。
