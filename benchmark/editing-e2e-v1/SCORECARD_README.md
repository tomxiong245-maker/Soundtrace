# Development Benchmark Scorecard

`build_scorecard.py` 把某个 delivery run 的**可用证据与缺口**放进一份可重复检查的 JSON + Markdown 报告。它是 development 诊断工具，不是自动剪辑器：不打开音频、不改 run、不写审核决定、不生成 EDL，也不授予发布或 Champion 晋升资格。

它同时汇总四类信息：

- 当前 run 的候选负担（候选数 / 节目小时、审核包、风险和预算）；
- 当前 run 的正式人审决定与备注状态；
- 已冻结的 Mentor 备注回归集（只作历史 development 参考）；
- 无候选区抽查计划/人审结果，以及 `resume` 后才能产生的两份 `transition_qc.json`。

## 最重要的规则

`NOT_MEASURED` 从不表示“0 个问题”或“通过”。例如：

- 5 条候选只是审核负担较小，不能证明没有漏剪；
- 已生成、但还没试听的无候选窗口只能叫**计划**，不是漏检率；
- `transition_qc` 不存在，通常表示还没有渲染，不能算剪口自然；
- 即使 `transition_qc` 已存在，它也只是把声学异常排出优先顺序，仍需要人耳听自然度与语义；
- 当前包的 accept 比例只是已审核候选的观察值，不是候选召回、整体质量或自动删剪许可。

## v20 的构建与重复检查

从项目根目录运行：

```bash
python3 benchmark/editing-e2e-v1/build_scorecard.py --build \
  --run-dir main/runs/EP04/EP04-v20-20260814-1617 \
  --no-candidate-audit benchmark/editing-e2e-v1/audits/EP04-v20-20260814-1617.no-candidate-audit-v1/no_candidate_windows.json \
  --output-dir benchmark/editing-e2e-v1/scorecards/EP04-v20-20260814-1617.current

python3 benchmark/editing-e2e-v1/build_scorecard.py --check \
  --run-dir main/runs/EP04/EP04-v20-20260814-1617 \
  --no-candidate-audit benchmark/editing-e2e-v1/audits/EP04-v20-20260814-1617.no-candidate-audit-v1/no_candidate_windows.json \
  --output-dir benchmark/editing-e2e-v1/scorecards/EP04-v20-20260814-1617.current
```

默认读取 `mentor-feedback-regression-v1/catalog.json`。只有这个回归集本身通过它的严格构建器，且 `split=development`、非 frozen、非 training gold 时，scorecard 才会接受它。

当真实人审、无候选试听或 `resume` 改变任何输入 JSON 后，`--check` 会故意失败，防止把旧 scorecard 误当最新。重新生成派生报告时必须显式加 `--replace`：

```bash
python3 benchmark/editing-e2e-v1/build_scorecard.py --build --replace \
  --run-dir <run-dir> \
  --no-candidate-audit <no_candidate_windows.json> \
  --output-dir <existing-scorecard-dir>
```

## 无候选区结果协议

现有 `no_candidate_windows.json` 初始全部是 `PENDING_HUMAN_LISTENING`。真人同步听完每个窗口后，必须在 `human_finding`（或 `human_review_status`）填写以下三个机器可读值之一：

- `NO_CLEAR_ISSUE`
- `POSSIBLE_MISSED_EDIT`
- `CLEAR_MISSED_EDIT_NEEDS_NEW_CANDIDATE`

只有所有窗口都有上述明确结果，scorecard 才会把该小样本标为 `MEASURED_DEVELOPMENT_SAMPLE_ONLY`。即使全部为 `NO_CLEAR_ISSUE`，它也不等于整期无漏剪；发现问题必须另建候选并走正常真人审核，不能直接改 EDL。

## transition QC 的边界

正常 `resume` 后，orchestrator 才会在下面两处生成客观复听排序：

```text
render_human_approved/transition_qc.json
render_machine_assisted_draft/transition_qc.json
```

scorecard 只读取其中的 JSON 身份、排序和计数；不会打开或哈希其引用的 WAV。两份报告完整且身份一致时，scorecard 会标为 `MEASURED_OBJECTIVE_PRIORITY_ONLY`，而**人耳自然度仍是 `NOT_MEASURED`**，直到另有明确的人审听感记录。

## 测试

```bash
python3 -m unittest discover -s benchmark/editing-e2e-v1/tests -p 'test_build_scorecard.py' -v
```

测试只构造 JSON 与假媒体路径，不读取任何真实 WAV/MP3。
