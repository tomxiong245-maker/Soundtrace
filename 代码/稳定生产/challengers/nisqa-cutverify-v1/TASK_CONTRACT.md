# TASK_CONTRACT · nisqa-cutverify-v1

## 挑战对象

- **目标 Champion**：`skills/cut-verify`
- **加什么**：给现有 4 项 check 追加 **Check 5 · 无参考 MOS 预测**（NISQA v2.0）
- **不改什么**：Champion 的 4 项 check、`verify_cut_plan` 入口、`verified_edl.json` schema 关键字段、`cut_parameters.json`、`tools.json`

## 输入 / 输出契约（骨架层面 · 未接线）

### `check_nisqa_mos.py`

- **输入**：单条 WAV 片段（`--clip-path`）· 模式（`--mode=overall|delta`）· 输出路径（`--out-json`）
- **输出 JSON**（骨架 · 暂 raise NotImplementedError）：
  ```json
  {"clip_path": "...", "mode": "overall", "mos": null, "dims": {"noi": null, "col": null, "dis": null, "loud": null}, "engine": "nisqa-2.0", "status": "SKELETON"}
  ```

### `compute_mos_delta.py`

- **输入**：`--before-clip <wav>` · `--after-clip <wav>` · `--out-json`
- **输出 JSON**：
  ```json
  {"before_mos": null, "after_mos": null, "delta": null, "status": "SKELETON"}
  ```

### `route_by_mos.py`

- **输入**：单 clip absolute MOS 或 delta JSON · `--out-json`
- **输出 JSON**：
  ```json
  {"verdict": null, "reason": null, "status": "SKELETON"}
  ```
- **判决表**（编入 docstring · 未实现）：
  | 条件 | verdict |
  |------|---------|
  | absolute MOS < 3.0 | `HUMAN_REVIEW` |
  | delta MOS < -0.5   | `REJECT` |
  | 其它               | `PASS` |

## 验收（本次交付）

- [x] 目录结构齐全
- [x] 三个脚本 `ast.parse` 无异常
- [x] `unittest` 三分支 mock 通过（`python -m unittest tests/test_nisqa_wrapper.py`）
- [x] audits/nisqa-2.0.md 4 段结构完整
- [x] 未 pip install · 未下载权重
- [x] `skills/cut-verify/` diff 为空
- [x] `tools.json` diff 为空

## 严禁边界（红线）

1. **不 `pip install nisqa`**，不下载 NISQA 权重（.tar / .pt）到本仓库。
2. **不改 `skills/cut-verify/`** 任一文件（SKILL.md / scripts / audits · 一字节不动）。
3. **不改 Champion** 任何入口、schema、参数源（`cut_parameters.json`）。
4. **不改 `tools.json`**（Registry 登记走独立 skeleton adapter 任务）。
5. **不实现** 任何真实推理逻辑 —— 所有骨架函数体 `raise NotImplementedError`。

## 后续 promote 前提

- Sandbox 环境验证 NISQA 官方 demo 可运行（独立 venv · 与主环境隔离）。
- 3 集真实数据 shadow · 与前 4 项 verdict 交叉验证一致性。
- 编写完整 unit + integration test 覆盖率 ≥ 80%。
- 走 e2e-auto-runner-v1 完整回归。
