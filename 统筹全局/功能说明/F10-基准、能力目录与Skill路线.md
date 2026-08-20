# F10 基准、能力目录与 Skill 路线

## 功能目的

让项目不靠口头记忆判断“新工具 / 新规则 / 未来模型是否更好”。本功能建立三件事：

1. **ASR 公共基准路线**：用成熟公开语料先筛听觉工具；
2. **端到端剪辑基准路线**：用本项目的 `raw material → 人工最终剪辑版` 判断产品是否真的接近人工结果；
3. **能力目录与 Skill-first 路线**：让人和 Agent 都能知道已有能力在哪里、何时可调用、何时必须停下。

流程位置：`工具/Skill 选择 → Challenger → benchmark → 人工晋升`。

## 已决定的方向

### 1. ASR 不自造大考试，优先复用公开基准

公开基准用于回答“这个引擎是否具备基础可用性、多人/重叠场景是否值得进入本地实验”，不用于代替章鱼 AI 节目的真实验收。

第一批候选语料按任务分工：

| 用途 | 候选公开资源 | 本项目用途 |
| --- | --- | --- |
| 中文基础转写 | AISHELL-1 | CER 和基本中文 ASR sanity check |
| 多人会议 / 重叠 | AliMeeting、AISHELL-4 | ASR、VAD、diarization、overlap 的压力测试 |
| 更接近网络节目与长音频 | WenetSpeech | 只作泛化参考，不把自动标注当人工真值 |

每次接入公开数据必须在 `benchmark/asr-public-suite-v1/` 的 manifest 记录：官方来源、版本/切分、许可证、下载校验、转换过程、模型版本、机器配置和结果。没有这些信息不得把分数用于工具晋升。

公共分数至少报告：

- 中文 CER；
- 运行速度 RTF、失败、内存与降级；
- 若语料提供可靠说话人标注，再报告 DER / JER；
- 若语料提供 VAD / overlap 标注，再报告 miss、false alarm、overlap recall。

**公开 benchmark 只做筛选；本地 EP03/EP04 人工 mini-gold 仍是最终领域校准。**

### 2. 剪辑质量以端到端回放为主

剪辑 benchmark 的最小单元不是某一个口癖候选，而是一整期节目：

```text
同一份 raw N 轨
→ 系统按冻结版本完整运行
→ 候选、审核包、EDL、系统成片、工时报告
↘ 与同一期人工最终剪辑版比较
```

每一期 benchmark 只保存路径和 SHA，不复制公司音频：

```text
raw_tracks                  # 原始 N 轨，只读
human_final                 # 人工最终成片，只读
reference_edl（可选）       # 若存在，是最有价值的诊断真值
human_edit_map（可选）      # 从 EDL 或人工对齐得到的删剪区间映射
system outputs              # 本次 Challenger 的候选、EDL、成片、QC
human evaluation            # 盲听、误删、返工、工时
```

端到端评估分三层：

| 层 | 要回答的问题 | 核心结果 |
| --- | --- | --- |
| 诊断层 | 系统找的候选是否覆盖人工真正剪掉的位置 | candidate / deletion interval 覆盖、误提和边界差异 |
| 成片层 | 系统版本是否接近人工成片且不损伤表达 | 盲听、吞字、突兀剪口、语义误删、串音问题 |
| 产品层 | 它是否真的值得使用 | 整片主观通过率、返工发生率（2026-08-17 起不再包含净节省时间数据） |

没有人工 EDL 时，不能把音频对齐反推的剪口当绝对真值；它只能作为定位辅助。最终发布质量仍由整片听审决定。

### 2.1 减少人工审核必须有独立的效率与漏检证据

“候选数量变少”只能说明系统少打扰人，不能证明它没有漏掉本该处理的位置。要把人工审核从整片、逐版试听收敛为少量高价值审核，每次 development run 至少同时记录：

- 候选负担：候选数 / 节目小时、审核分钟 / 节目小时、前 5/10 条实际 `accept` 占比；
- 过剪提名：被 `reject` 的候选及其原因；
- 漏检抽查：从**未提名区域**固定随机抽样，人工标明是否存在明确需要处理的点；
- 剪口听感：对已采用剪口做不显示参数的 A/B 盲听，区分“自然、剪辑痕迹、吞字/撞字、底噪跳变、内容本不该剪”；
- 产品代价：返工发生率与维护复杂度（2026-08-17 起不再包含净节省时间数据）。

严重语义误删为零容忍门。声学跳变、音量差与频谱差只用于排序最该复听的剪口，不能作为“自然”或“可自动删除”的判定。现有 EP03/EP04 的逐项决定、Mentor 备注和 v12 整片听审均是 development 证据；下一期独立节目才可冻结为不再调参的 benchmark。

### 2.2 持续 benchmark 运行规则：不把 QA 伪装成更多人审

每一个 development run 都走同一条证据循环，而不是等到某次版本听起来不好才临时找原因：

```text
候选冻结
→ 只读生成 candidate-burden scorecard、历史备注回归、固定随机无候选区抽查计划
→ 真人完成既定语义审核（与 QA 抽查分开）
→ resume 后生成 transition_qc 客观优先复听排序
→ 真人回填无候选区 / 剪口盲听 / 整片结果 / 返工和工时
→ 刷新 scorecard，提出下一轮 Challenger 假设
```

这里的“自动”只指 Agent 可以自动生成计划、校验 SHA、统计负担和排序复听优先级。它**不能**自动填写 `human_finding`、自然度、语义误删或工时；没有真人结果时必须明确显示 `NOT_MEASURED`。当前 v20 的 8 个无候选窗口是 development QA 抽样，不属于 Mentor 当前 5 条 `accept/reject` 审核，也不产生 EDL。

`delivery_orchestrator.py` 现在会默认调用 `run_development_benchmark.py`：在候选冻结、审核包刷新、渲染后和最终决定后刷新这套旁路；`delivery_orchestrator.py benchmark --run-dir <run> --phase manual` 用于真人 QA 回填后的显式刷新。这个 wrapper 只调用 JSON/Markdown 工具，不触碰媒体，不生成 EDL 或真人决定；失败写 `BENCHMARK_EVIDENCE_UNAVAILABLE`，但不撤销一个已经完成的审核/渲染阶段。只有隔离 fixture 或故障排查才允许在 CLI 用 `--benchmark-mode off`。

未来 Agent 每轮仍要保持：`mentor-feedback-regression-v1/build_catalog.py --check`、`build_scorecard.py --check` 和相应契约测试可复跑；证据变化后只用 `build_scorecard.py --build --replace` 刷新派生报告。严禁直接编辑 scorecard 或把开发集调参结果称为 Champion/发布成绩。

### 3. 当前审核固定为二态，`adjust` 不阻塞

当前 MVP 只使用：

```text
accept = 按当前边界采用剪切
reject = 保留原音频
```

`adjust` 以后作为“删剪方向正确，但边界需重设”的增强功能；它需要重新生成试听、重新确认，当前不做也不阻塞案例入库、经验总结、规则统计或未来的**二元候选排序**研究。

### 4. Skill-first，而不是 model-first

当前主要产物是：

```text
真实案例 → 可追溯证据 → Skill / 规则摘要 → Challenger 评测 → 人工批准
```

Skill 是“什么时候用什么工具、怎么做、什么不能做、失败时怎么办”的可复用工作说明；不是模型权重，也不是自动删剪授权。

未来监督学习只解决较窄的问题：例如把已有候选按“更可能被人工采用”排序。它不能替代 Skill、benchmark 或真人审核。

## Benchmark 目录约定

```text
benchmark/
├── README.md                         # 总入口
├── EP03-ASR-mini-gold-v1/           # 本地人工领域校准，已有
├── asr-public-suite-v1/             # 公开 ASR 基准的 manifest 与结果（不存大语料）
└── editing-e2e-v1/                  # raw → 人工成片的端到端剪辑基准（不复制音频）
```

- `development` 集：可以用于日常调试；
- `frozen` 集：冻结后不得按其结果继续调参；
- 任何新 Challenger 先在 development 复现，再在 frozen 集报告一次性结果；
- 原始 WAV、人工成片和已冻结历史 run 只读。

## 当前状态

### 已验证事实

- `EP03-ASR-mini-gold-v1` 已有人工标注入口、schema 和 scorer 骨架，但 gold 仍未填写，不能宣称任何引擎赢家。
- EP03/EP04 已有真实逐项审核案例与技术试听版证据，足以作为端到端 benchmark 的目录和 manifest 起点；尚不足以形成冻结评测集或训练集。
- **EP03 确实曾跑通端到端工程链路**：`END_TO_END_CODEX.md` 记录了 raw 双轨 → 降噪/ASR/候选 → 全部 26 条 `bulk_accept_all_explicit_user_authorization_for_mvp` → 同步剪切/混音/QC 的 MVP 验证。它证明编排、同步 EDL、渲染和技术 QC 曾在真实音频上跑通；bulk accept 不是逐条编辑真值，不能被称为“从 Mentor 成片学习完成”。
- EP03 的 `EP03-development-v1` manifest 已把 raw 与 Mentor 参考成片索引到同一 development 集，但 `reference_edl_path=null`、`human_edit_map_path=null`，并明确禁止把 MP3 自动对齐反推的删剪区间当成语义 gold。因此“raw N 轨 + Mentor 成片 → 人工确认 human_edit_map → 规则提炼/benchmark/人工晋升”仍未完成。
- `mfa-alignment-v1` 是候选边界精修的 Challenger 基础：已在 EP04 局部中文候选有 MFA 对齐与用户试听证据；它不等于 Mentor 成片规则挖掘。现有 README 明确英文/中英混合 token 仍可能 OOV 并回落人审，且 canonical `delivery_orchestrator.py` 尚未把 MFA 作为已验证的普通新 run 阶段，因此不能称为“中英双语端到端对齐已完成”。
- `benchmark/editing-e2e-v1/sample_no_candidate_windows.py` 已提供一个只读 JSON 的 development 抽查入口：它用固定种子、输入 sample timeline、`all_candidates.json` 的候选边界及可配置 handle，生成 8–20 个互不重叠的 20–30 秒无候选人工试听窗口，并绑定 run/input/candidate SHA。空间不足会 fail closed，不读取、复制或解码媒体。EP04 v20 已生成一份待试听抽查计划；它尚未有真人听审结果，不能据此声称没有漏剪或减少审核量。
- `mentor-feedback-regression-v1` 已从两轮固定的最终人审文件建立 32 条 development 回归目录（8 accept / 24 reject / 23 条有备注）；32/32 的 candidate semantic SHA 与相应审核包一致。它明确报告旧 preview 哈希差异，不能静默当作通过，也不写入 Champion 或生产规则。
- `transition_qc.py` 已在正常 `resume` 的两份渲染后按电平、频谱与边界波形异常排出重点复听剪口；它不判定语义、自然度或 accept/reject。
- `build_scorecard.py` 已为 EP04 v20 生成并严格校验一份 development scorecard；它正确报告 `INCOMPLETE_HUMAN_REVIEW_REQUIRED` 和 `quality_pass=false`，不会把缺失人审、试听或工时误作零问题/通过。
- `run_development_benchmark.py` 已实际用 v20 的既有抽查包运行：它先读取冻结 audit 的参数，复用其旧 seed，而不是改名后重抽或覆盖人的结果；v20 的 `benchmark_evidence.json` 当前为 `PASS`，但 scorecard 仍是 `INCOMPLETE_HUMAN_REVIEW_REQUIRED`。`delivery_orchestrator.py` 已将同一 wrapper 接到常规生命周期，且 22 项 benchmark/orchestrator 契约测试通过。
- 外部资料已整理为 `benchmark/editing-e2e-v1/EXTERNAL_BENCHMARKS.md`：公开多轨会议语料只用于 ASR/VAD/重叠筛选；剪口自然度仍以局部盲听为准。未下载公开数据，也未向外部发送真实音频。
- 项目已有顶层 `SKILL.md`、双轨历史 Skill、新增项目内经验沉淀 Skill、Tool 注册表和多个隔离 Challenger；详见《能力目录》。

### 尚未完成

- 没有下载、复现或记录任何公开语料的正式结果；
- 没有冻结第一版端到端剪辑 benchmark；
- 没有建立从人工最终成片到 human_edit_map 的可靠对齐；
- 没有从 Mentor 成片反推出经人工确认、可回滚并已在独立 benchmark 验证的候选规则；
- 没有训练监督学习模型，也没有将经验案例写入 Champion。

## 验收门

### ASR 工具入选

1. 公共 benchmark 与本地 mini-gold 都有可复现 manifest；
2. 同一机器、同一切分、同一指标下比较；
3. 结果同时包含质量、速度、失败情况和许可证；
4. 工具能够输出项目的词级/sample-based 数据契约；
5. 人工确认后才可替换生产 Tool。

### 剪辑策略或模型晋升

1. 新版本在 development 集完成调试；
2. frozen 端到端集上的结果与旧 Champion 并列；
3. 无高风险语义误删，整片盲听和 QC 可接受；
4. 有独立复核、人工晋升和回滚入口。

## 实现入口

- 能力总目录：`统筹全局/能力目录.md`
- 学习路线：`统筹全局/学习路线-监督学习与Skill.md`
- ASR 本地 mini-gold：`benchmark/EP03-ASR-mini-gold-v1/`
- 公共 ASR 基准骨架：`benchmark/asr-public-suite-v1/`
- 已调研、未下载的公开语料候选：`benchmark/asr-public-suite-v1/candidate_registry.v1.json`
- 端到端剪辑基准骨架：`benchmark/editing-e2e-v1/`
- 无候选区域随机抽查说明：`benchmark/editing-e2e-v1/NO_CANDIDATE_AUDIT_README.md`
- 外部 benchmark 的采用边界：`benchmark/editing-e2e-v1/EXTERNAL_BENCHMARKS.md`
- Tool 注册表：`main/tools/tools.json`
- 顶层路由 Skill：`SKILL.md`
- 经验沉淀 Skill：`skills/editing-experience-distiller/`
