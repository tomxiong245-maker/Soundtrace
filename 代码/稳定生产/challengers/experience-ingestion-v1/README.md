# experience-ingestion-v1 · README

> 这是一个 **Challenger**，作用范围严格局限于本目录和一次真实运行 run。它不修改任何 Champion。

## 目的

把已有的真人审核结果整理进“**隔离的 Challenger 经验案例库**”，并提供一个**只读的经验消费者**，能读案例、给统计、给规则建议、给训练准备度判断，但**不修改任何生产规则、不训练模型、不批准 EDL**。

## 目录结构

```
experience-ingestion-v1/
├─ TASK_CONTRACT.md            # 任务契约与边界
├─ README.md                   # 本文件
├─ HANDOFF.md                  # 移交下一位执行者时需要知道的事
├─ baseline/
│  └─ source_shas.json         # 来源文件 SHA 冻结
├─ schemas/
│  └─ experience_case.schema.json
├─ scripts/
│  ├─ collect_experience_cases.py     # fail-closed 导入器
│  ├─ consume_experience_cases.py     # 只读消费者
│  ├─ check_training_readiness.py     # 训练准备度门禁
│  └─ experience_consumer_adapter.py  # 只读适配器
├─ tests/
│  └─ test_*.py
├─ case_store/                 # 本 Challenger 内部案例产物
│  ├─ cases/                   # 逐期 jsonl
│  ├─ exclusions.jsonl
│  ├─ quarantine.jsonl
│  ├─ index.json
│  └─ ingestion_manifest.json  # 每次真实运行覆盖为最新
├─ reports/
│  ├─ experience_summary.json/.md
│  ├─ rule_recommendations.json
│  └─ training_readiness.json
├─ exact_commands.md
└─ benchmark_report.md
```

真实运行的输出还会写入 `main/runs/EXPERIENCE-INGESTION-v1-<timestamp>/`。

## 当前有效快照

不要把 `case_store/` 根目录下 14:46 的首轮产物当作当前口径；它保留为历史证据。当前
二态 MVP 的有效快照由 `case_store/ACTIVE_SNAPSHOT.md` 指向：

- 快照：`case_store/two-state-v1-20260812-1627/`
- 真实运行报告：`main/runs/EXPERIENCE-INGESTION-v1-20260812-1627/RUN_REPORT.md`
- 结果：27 条逐项 `accept/reject` 案例、26 条 bulk accept 排除、0 条 quarantine；全部为
  `eligible_rule_only`，即**可用于案例检索、Skill/规则分析，但不可用于训练或改生产规则**。

首轮按旧 baseline 复跑曾正确 fail-closed：EP04 口癖/长停顿的审核保存文件在旧冻结之后发生了
SHA 变化。二态快照使用独立 revalidation baseline，并保留该失败 run；详见 `ACTIVE_SNAPSHOT.md`。

## 快速运行

以仓库根为工作目录。不要覆盖已有快照；每次新跑都新建 `case_store/<snapshot-id>/` 与
`main/runs/EXPERIENCE-INGESTION-v1-<timestamp>/`：

```bash
python3 稳定生产/challengers/experience-ingestion-v1/tests/run_contract_tests.py

python3 稳定生产/challengers/experience-ingestion-v1/scripts/collect_experience_cases.py \
    --repo-root . \
    --out-dir 稳定生产/challengers/experience-ingestion-v1/case_store/<snapshot-id> \
    --run-dir main/runs/EXPERIENCE-INGESTION-v1-<ts> \
    --baseline 稳定生产/challengers/experience-ingestion-v1/baseline/source_shas.two-state-v1.json \
    --reject-source-drift

python3 稳定生产/challengers/experience-ingestion-v1/scripts/consume_experience_cases.py \
    --case-store 稳定生产/challengers/experience-ingestion-v1/case_store/<snapshot-id> \
    --out-dir main/runs/EXPERIENCE-INGESTION-v1-<ts>/reports

python3 稳定生产/challengers/experience-ingestion-v1/scripts/check_training_readiness.py \
    --case-store 稳定生产/challengers/experience-ingestion-v1/case_store/<snapshot-id> \
    --reports-dir main/runs/EXPERIENCE-INGESTION-v1-<ts>/reports \
    --run-dir main/runs/EXPERIENCE-INGESTION-v1-<ts>
```

`tests/run_contract_tests.py` 只使用 Python 标准库，适用于没有 pytest 的目标机器；本轮实际结果为
20/20 PASS。

## 边界

- 只读来源。任何来源文件在运行前后 SHA 变化都会立刻 fail-closed。
- 只写 Challenger 目录和本次 run 目录。
- 不训练模型；不改稳定生产/rules/**。
- reject 也是标签；逐项 `accept/reject` 案例可进入案例检索、Skill/规则分析；bulk_accept 案例只进入 exclusions.jsonl。
- `review_mode` 只作兼容元数据，不阻塞当前案例入库或规则分析；本 Challenger 仍不训练模型、不改生产规则。

## 与另一位工程师协作

- `filler-global-pause-v1` Challenger 与其 run 目录 **禁止写入**。
- 若在运行期间发现该 run 目录 SHA 变化，本任务不使用旧快照冒充新结果；相应案例会进入 quarantine 并在报告中说明。
