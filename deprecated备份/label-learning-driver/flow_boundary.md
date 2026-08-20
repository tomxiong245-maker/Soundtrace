# label-learning-driver · flow boundary

## ✅ 本 skill 允许做的

- 每次人审 `/api/save` 触发时，rebuild `main/knowledge/labels_lake.json`（增量）
- 生成 `LABEL-LEARNING-AUTO-*/preference_snapshot/` 冻结 SHA
- 生成防泄漏回测（leakage-safe backtest）
- 生成 read-only shadow prediction（给 gate 消费的信号，不写 EDL）
- 一键 rebuild + regate：`python3 main/orchestrator/refresh_lake_and_regate.py --run <active>`

## ❌ 本 skill 禁止做的

- **绝不写 `human_decisions.json`**（只有真人前端能写）
- **绝不改 `human_approved.edl.json`**
- **绝不改活跃 `autocut_policy`**（policy 晋升需独立复核）
- **绝不覆盖历史 preference_snapshot**（每次新 timestamp）
- **绝不改活跃指针除非新快照 + 回测都成功**（原子切换）
- **绝不把 shadow prediction 当决定**

## 依赖的工具（tools.json 已登记）

- `refresh_label_learning_snapshot`（main/orchestrator/refresh_label_learning_snapshot.py）
- `build_labels_lake`（main/orchestrator/build_labels_lake.py）
- `refresh_lake_and_regate`（main/orchestrator/refresh_lake_and_regate.py, v20.4 加）
- `label_learning_driver`（main/orchestrator/label_learning_driver.py）

## 触发时机（自动 + 手动）

- **自动**：`/api/save` hook（前端每次审核 save 时）
- **手动**：`python3 main/orchestrator/refresh_lake_and_regate.py --run <active>`

## 违反本边界的证据

- （无历史违反 —— 该 skill 边界最严格）
