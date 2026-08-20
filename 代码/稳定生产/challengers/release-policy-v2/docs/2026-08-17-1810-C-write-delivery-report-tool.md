# C · 抽 write_delivery_report 到独立 tool · 2026-08-17 18:10

**可靠度声明**：本文陈述基于实测 AST 解析 + 契约测试全过 + orchestrator 4 处 caller 保持 backward compat 的 import 验证；DELIVERY_REPORT.md 输出未在真实 run 上端到端验证（等下一次 EP05 交付时实测）。

## 事实（[HIGH]）

- 新增 `main/orchestrator/write_delivery_report.py`（198 行独立脚本，含 CLI）。[HIGH]
- `main/orchestrator/delivery_orchestrator.py::write_delivery_report`（81 行函数体）已删除，改为顶部 `from write_delivery_report import write_delivery_report` import，4 处 caller 无需改。[HIGH]
- Orchestrator 文件从 4647 → 4575 行（-72 行；-81 函数 + 9 import/comment）。[HIGH]
- Tools 从 31 → 32：新登记 `write_delivery_report`（full_path 指到新脚本，params=`run_dir/final_status/special_scope`）。[HIGH]
- 契约测试全过：P1 orchestrator↔tools.json 3/3 · P2 skills-registry 12/12 · filler 16/16 · sync PASS。[HIGH]
- Backward-compat 验证：`from delivery_orchestrator import write_delivery_report` 成功；`write_delivery_report.__module__ == 'write_delivery_report'`（即调用去了新脚本）。[HIGH]

## 判断（[MED]）

- **抽取风险 = 极低**：write_delivery_report 是**纯输出函数**（读 JSON → 拼 markdown → 写文件），无副作用非幂等操作。抽出后行为逐字一致。[HIGH]
- **依赖切断**：新脚本自包含 3 个 helper（`_read_json / _write_text / _require_identity`），不再依赖 orchestrator 的私有函数。同时**通过顶层 import 保留 backward-compat**，避免修改 orchestrator 的 4 处 caller（那是 20-line 改动，规避）。[HIGH]
- **可作为 P1 拆分示范**：证明"从 4640 行主流程抽 tool 到独立脚本 + tools.json 登记 + backward-compat import + 契约测试全过"这套流程可行。同类 P1 候选（`write_offline_review_packet` 88 行 / `qc_and_report` 90 行）应可以照抄。[MED]
- **未来 orchestrator 变薄的路径**：把 orchestrator 里 24 个大函数逐个照 C 的模式抽出（**每次一个**，独立 commit，契约测试防退化）。理论上 4640 行 orchestrator 可以缩到 500-1000 行"编排骨架 + subprocess 调用"。[MED]

## 建议 · 后续动作（[MED]）

1. **EP05 真跑时观察**：write_delivery_report 输出的 DELIVERY_REPORT.md 与 EP04 之前的报告结构一致；如有细微差异（换行/空格），返回修正。
2. **下一批 P1 拆分候选**：`write_offline_review_packet` (88 行) → 同类纯输出函数，风险低；`stage_command` (23 行) → 同上。
3. **CLI 使用示范**：`python3 main/orchestrator/write_delivery_report.py --run-dir main/runs/EP04-DELIVERY-20260817-1427 --final-status DELIVERY_DECISION_RECORDED` 现在可以直接生成 delivery report，不需要走 orchestrator 主流程。适合调试或事后补报告。

## 未做（诚实交代）

- 未修改 orchestrator 里 4 处 caller 站点（`start` / `resume` / `record-final` / `promote-v12`）。目前它们通过 `from write_delivery_report import write_delivery_report` **函数级** import 调用，行为逐字一致；不是 subprocess call。这算"半 tool"—— 逻辑抽出去了但 orchestrator 仍在同一进程调用。**若要严格 subprocess 化**：把 4 处 caller 改成 `subprocess.run([sys.executable, str(_script_for("write_delivery_report")), ...])`，是下一步动作。
- 未在真实 run 上运行 CLI 模式：`python3 write_delivery_report.py --run-dir ...` 未真跑；等 EP05 或下次交付验证。
- 未拆更大函数（build_candidates_and_review 376 行、make_base_run 288 行）；那些编排多个 tool + state transition，需要更谨慎设计。

## 相关文件

- 新工具：[main/orchestrator/write_delivery_report.py](../../../../main/orchestrator/write_delivery_report.py)
- Orchestrator diff：`main/orchestrator/delivery_orchestrator.py` L3751-L3833 删除、header 加 import
- Tools 登记：[main/tools/tools.json](../../../../main/tools/tools.json) 里 `write_delivery_report` 条目
- 契约测试：[main/orchestrator/tests/test_orchestrator_uses_tools_json.py](../../../../main/orchestrator/tests/test_orchestrator_uses_tools_json.py) （P1 3/3 通过，含 hardcoded script 反查覆盖）

## 相关记忆

- [[minglue-project-layout]]
- [[minglue-construction-rules-first]]
- [[minglue-post-feature-analysis-md]]
