# exact_commands · experience-ingestion-v1

原始真实运行时间：2026-08-12 14:46（Asia/Shanghai）
原始 run 目录：`main/runs/EXPERIENCE-INGESTION-v1-20260812-144630/`

当前有效二态快照：`case_store/two-state-v1-20260812-1627/`；真实运行目录：
`main/runs/EXPERIENCE-INGESTION-v1-20260812-1627/`。该快照保留在独立目录，不覆盖原始 run。

## 环境

- Python：`/usr/bin/python3`（`Python 3.10.12`）
- 新安装依赖：`pytest 9.1.1` 及其传递依赖（`--break-system-packages`），仅用于运行本任务测试；未修改任何 Champion。

## 当前自动测试（不依赖 pytest）

```
python3 稳定生产/challengers/experience-ingestion-v1/tests/run_contract_tests.py
```

结果：`20/20 PASS`。历史 pytest 18/18 结果仍是首轮施工证据；当前机器没有 pytest，因此新增的标准库
runner 是本轮实际验证入口。

## 当前二态快照真实运行

```
# 1) 导入（fail-closed）
python3 稳定生产/challengers/experience-ingestion-v1/scripts/collect_experience_cases.py \
  --repo-root . \
  --out-dir 稳定生产/challengers/experience-ingestion-v1/case_store/two-state-v1-20260812-1627 \
  --run-dir main/runs/EXPERIENCE-INGESTION-v1-20260812-1627 \
  --baseline 稳定生产/challengers/experience-ingestion-v1/baseline/source_shas.two-state-v1.json \
  --reject-source-drift

# 2) 只读消费者
python3 稳定生产/challengers/experience-ingestion-v1/scripts/consume_experience_cases.py \
  --case-store 稳定生产/challengers/experience-ingestion-v1/case_store/two-state-v1-20260812-1627 \
  --out-dir main/runs/EXPERIENCE-INGESTION-v1-20260812-1627/reports

# 3) 训练准备度门禁
python3 稳定生产/challengers/experience-ingestion-v1/scripts/check_training_readiness.py \
  --case-store 稳定生产/challengers/experience-ingestion-v1/case_store/two-state-v1-20260812-1627 \
  --reports-dir main/runs/EXPERIENCE-INGESTION-v1-20260812-1627/reports \
  --run-dir main/runs/EXPERIENCE-INGESTION-v1-20260812-1627

# 4)（可选）只读 adapter
python3 稳定生产/challengers/experience-ingestion-v1/scripts/experience_consumer_adapter.py \
  --case-store 稳定生产/challengers/experience-ingestion-v1/case_store/two-state-v1-20260812-1627 \
  --reason-key immediate_repetition \
  --out main/runs/EXPERIENCE-INGESTION-v1-20260812-1627/adapter_immediate_repetition.json
```

## 关键结果

- 导入：cases=27（EP03 11 + EP04 16），exclusions=26（全部来自 `main/runs/EP03/` bulk_accept），quarantine=0。
- 消费者：本次 run 的 `reports/experience_summary.{json,md}` 与 `rule_recommendations.json` 已生成；所有建议 `action=NO_PRODUCTION_CHANGE`。
- 训练准备度：`status=NOT_READY`；原因只有数据量、审核期数、单审核人、独立 benchmark/复核/回滚缺失。`adjust=0` 与 `review_mode` 不再作为当前二态路线的门禁。
- Adapter：`capabilities.can_change_production_rules=false`，`can_train_model=false`，`can_read_cases=true`；`prohibited_actions` 明列 4 项。

## SHA 复核

- baseline 14 项来源 SHA 前后一致（`baseline/source_shas.json`）。
- 禁止目录/文件 11 项前后 SHA 未变（`baseline/forbidden_dirs_before.json` vs `forbidden_dirs_after.json`）。
