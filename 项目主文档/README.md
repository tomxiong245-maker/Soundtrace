# 明略多轨播客剪辑助手

> **MVP / 内部工程原型** · 状态截至 2026-08-16
> 新 Agent 的唯一操作契约见 `统筹全局/Agent交付流程-从音频到成片.md`。`main/orchestrator/delivery_orchestrator.py` 已接通输入检查、DeepFilterNet、P0、v18 口癖/全轨长停顿候选、20 项审核预算、审核包、双 EDL、双渲染和 QC；它还会在每个未来新 run 冻结 `editing-policy-guards-v1`，只自动保护已知误报或升级人工审核。它仍未覆盖全部候选类别或达到无人值守发布。
> 最新听感偏好集中在 `统筹全局/当前剪辑偏好快照.md`：它帮助 Agent 生成候选和试听，不能把高风险项目自动删掉或伪装成人审。
> ASR 之后的语义分句/标点假设层已在 EP04 三轨真实运行并归档；它只给后续判断模块提供句子、分句和词上下文，不决定是否删剪。
> 当前可继续审核 run 以 `统筹全局/当前状态摘要.md` 和 CURRENT_DELIVERY_FACTS 为准：目前是 `EP04-label-loop-v1-20260815-1805`，复用外部 `faster-whisper small`，标签快照只改变审核排序/提示；审核页备注/反馈链和文档同步门已接通。

这是一个本地运行、可追溯、由真人掌握最终语义决定的中文多轨播客后期助手。机器负责输入检查、转写、候选准备、风险分层、固定音乐、同步剪切和技术检查；真人决定高风险语义/听感候选和最终整片是否合格。机器预测必须与真人决定分开留痕。

## MVP 流程

```text
同一期 N 条对齐 mono WAV
→ 输入格式、共同时间线与同步检查
→ DeepFilterNet 逐轨降噪与时间线回填
→ ASR / VAD / 轨道活动分析
→ 全量候选冻结 + 高风险全审/低风险代表性抽样
→ 真人校准标签 + 机器全量预测
→ human_approved / machine_assisted_draft 双 EDL
→ 全轨同步剪切 + source-track gate + 混音
→ 固定片头片尾音乐
→ 人工整片听审、响度与发布 QC
```

产品价值不以“自动化比例”衡量，而以净节省时间衡量：

```text
净节省时间 = 原人工剪辑时间 - 审核时间 - 返工时间 - 系统维护时间
```

## 当前真实状态

EP04 已用一整期真实三轨节目完成到“技术试听版”的闭环：

| 能力 | 已验证事实 | 尚未完成 |
| --- | --- | --- |
| 三轨输入 | 3 条 Zoom H6 单声道 WAV，48 kHz / 24-bit，共同时间线 3,272.7 秒 | 两台独立设备的真实 clock drift |
| ASR | 外部 faster-whisper small / CPU int8 跑完 EP04 V13 三轨（12,467 / 11,853 / 6,732 个词，非法时间戳均为 0）；负责人对中文主体与一段中英混说术语片段完成抽听，当前可作候选定位和人工审核底稿 | 人工 CER、漏句率、说话人和重叠 benchmark；英文术语与专名仍需重点留意 |
| 语义分句/标点假设层 | `semantic-transcript-v1` 已对同一三轨真实转写运行并归档：177/207/170 句、769/803/408 分句；全部原始 `word_id`、顺序和时间映射通过完整性校验，逐词 `word_context_index` 可回溯句/分句上下文 | 当前是 `timing_text_heuristic_v1` 启发式，不是可靠标点模型；尚未做人工标点质量评估，也不生成候选、删剪决定或 EDL |
| 候选 | 通用 N 轨桥接产出 13 项真人审核候选，跨轨冲突候选会阻断 | 当前主要覆盖紧邻重复，不是完整内容精剪 |
| 真人审核 | 13/13 完成；6 项删除、7 项保留；没有自动批准 | `adjust` 边界修改和重生 A/B |
| 同步渲染 | 真人 EDL 已同步作用于三条轨，生成 3 条 edited stem 和 192 kbps MP3；另有 EP04 v4 机器辅助含音乐版本 | 双 EDL/双渲染正式契约、完整主观听审和发布确认 |
| 技术 QC | 输出时长、采样率、哈希和编码已记录；试听混音无整数采样削波 | 当前试听混音约 -29.6 LUFS，明显偏低；主麦自动混音尚未完成 |
| 标签学习 / 自动规则 | `LABEL-LEARNING-v3-20260816` 已从 65 条独立历史事件生成多标签反馈归类和 20 张政策卡；`editing-policy-guards-v1` 已接生产入口 | 没有训练模型、loss 或在线学习；保护规则只能保留/升级人审，不能自动删语义内容 |
| Tool 编排 | `delivery_orchestrator.py` 已实际编排新 run、输入校验、DeepFilterNet、P0、`filler-global-pause-v18` 候选、活跃保护规则、20 项审核预算、审核包、校准、双 EDL、渲染/QC；30 秒三轨 fixture 已跑到最终 `verify` | fixture 的降噪/ASR 是明确标注的 adapter，真实新节目仍待独立全程复核；18 项注册表尚未全部收敛到同一 adapter；说错重来、语义重复、离题、串音与瞬态候选未接入稳定入口 |
| EP04 v4 | 2026-08-13 生成 122 条机器推断、86 个同步剪切、92 个源轨 gate 和含音乐 WAV/MP3 | `LEARNED_FROM_HUMAN_v4` 不是真人 reviewer；尚无有效 `autocut_policy`，只能叫 `machine_assisted_draft` |
| EP04 v12 | 使用原始三轨试跑了口癖、长停顿、瞬态、15 秒交叉音乐和两遍 loudnorm；负责人已试听整片并在当前任务明确批准其冻结动作集合；正确身份交付 run 已独立实测母带为 -16.54 LUFS / -0.86 dBTP | 原始 v12 run 的 manifest 仍写 EP04-v4 且使用旧绝对路径，仍不能直接当交付证据；已另建正确身份的 `EP04-human-approved-v12-20260813-152847` 交付 run，保留“整片批准范围”而非伪造 588 条逐项标签；工作目标和本地 FFmpeg 依赖尚未成为 Mentor 冻结发布规格或受审计的常规部署 |

因此，本仓库现在可以准确称为：

> **已经跑通真实三轨“转写—候选—真人审核—同步剪切—试听混音”的 MVP 原型，并为已整片听审的 EP04 v12 建立了身份一致的人工批准交付 run；尚不是覆盖所有剪辑问题的发布级自动剪辑产品。**

### ASR 当前可用范围（2026-08-14）

`main/runs/EP04/EP04-v13-20260813-2002/analysis/` 已保留三条真实轨的词级转写。负责人抽听约 `01:00–01:30` 时确认中文主体准确、英文术语仍有问题；在 `47:37.5–48:10.5` 的中英混说术语密集片段（含 `MCP`、`DeepMiner`、`OpenClaw`、`CC`、`Codex`、`agent`）确认识别良好。因此它已足以用于口癖和长停顿定位、以及人工审核的文字底稿。

这两段抽听不是人工 gold：不能据此宣称整集准确率、英文/专名准确率、说话人能力或“faster-whisper 最准”。当前词级 JSON 仍是不可修改的底层事实；另外已归档一个并列的句子/分句上下文层。该层用停顿、源标点和少量文本线索生成标点假设，所有非源标点边界标为低置信度，不能当作可靠标点或删剪授权。它通过 `word_context_index[word_id]` 给后续候选模块提供 `sentence_id`、`clause_id`、句内位置和句/分句 sample 范围；原始词、时间戳和剪口不会被改写。

## 当前能做什么

- 检查 N 条 mono WAV 的格式、采样率、样本数、SHA-256 和共同时间线；
- 普通新 run 必经 DeepFilterNet 逐轨降噪；原始 WAV 保持只读，派生轨通过 manifest 和 SHA 绑定后才供分析、试听与渲染使用；
- 使用本地 ASR 生成可定位的逐词转写；
- 确定性修复零长度时间戳，并保留原始 ASR；
- 合并部分英文相邻子词，例如 `fe + ature → feature`；
- 生成与词级 ASR 并列的句子/分句上下文层，供候选判断定位完整句和分句；
- 生成紧邻重复候选，并在其他轨出现冲突文字时阻断删除；
- 生成人工审核页面和 A/B 试听；
- 保存逐项 `accept / reject` 决定；
- 将同一整数 sample EDL 同步应用于所有输入轨；
- 输出 edited stems、speech mix、WAV / MP3 和基础 QC 记录。
- 固定使用已授权音乐素材和 `reference-linear-v1` 片头片尾时序（当前只冻结素材、SHA 和形状，不代表发布响度已冻结）。
- 已有稳定入口输出 `human_approved` 与 `machine_assisted_draft` 两种来源清晰的版本；后者只能包含安全、校准充分的低风险机器预测剪口，不能冒充真人批准版。当前生产范围仅覆盖口癖/共同长停顿，其他类别仍是覆盖缺口。
- 对未来新 run 自动冻结 `editing-policy-guards-v1`：精确的完整词/词边界误报会被保护，长停顿和高风险类别会升级人审；该规则不创建 `auto_cut_eligible`、EDL 或真人决定。
- 入口会校验 run 目录、`run_identity.json`、EDL 和交付 manifest 的 episode/run 身份；不一致时停止交付。v12 的旧身份/绝对路径问题已在新 EP04 交付 run 中以来源证据记录，未被传播。
- 在隔离 Challenger 中冻结 N 轨执行计划、记录工具调用和输出哈希，并在 `HUMAN_REVIEW_REQUIRED` 停止；这不是完整生产编排。

## 当前不能自动解决什么

- 说错后重新表达、远距离语义重复和离题段落的自动批准；
- 所有口癖、异常停顿、咳嗽、呼吸、碰麦和环境噪声的可靠全量识别；
- 稳定的人物识别、男女判断、多人重叠和串音归属；
- 专名与中英文混说的全部 ASR 错误；
- 可靠的整集标点恢复；当前语义层只是带理由和置信度的边界假设；
- 主麦自动选择、专业 automix、正式响度和音乐 ducking；
- 无人审核语义删除和自动发布。

这些问题不会“自行消失”。当前 MVP 主动只自动化证据较强、可安全回退的步骤。

## 为什么不依赖剪映

当前音频 MVP 使用本地 Python 与 FFmpeg 完成同步剪切、crossfade、混音和编码，不要求安装或注册剪映。剪映可以在以后作为人工时间线精修、字幕或视频包装工具接入，但不是当前音频闭环的必需依赖。

## 安全原则

- 原始音频只读，真实素材默认只在本地处理；
- 所有改变语义的剪切必须由真人明确批准，或由已签署、可回滚且范围明确的 `autocut_policy` 授权；机器预测不得伪装成真人决定；
- EDL 使用整数 sample，并同步应用于全部语音轨；
- 输入、规则、审核材料和输出以 SHA-256 绑定；
- Challenger 不得直接覆盖稳定版本；
- 仓库不提交真实音频、完整转写、审核决定、成片或本机模型环境。

## 项目结构

```text
剪辑项目/
├── README.md                       # 本页：MVP 状态与路线图
├── SKILL.md                        # 顶层输入识别与路由
├── main/
│   ├── orchestrator/               # 统筹状态机原型
│   ├── tools/                      # Tool 注册表
│   └── knowledge/                  # 冻结知识快照指针
├── 稳定生产/
│   ├── scripts/                    # 已有稳定执行脚本
│   ├── rules/                      # 候选规则
│   └── challengers/                # P0、P1、N 轨与安全实验（含 semantic-transcript-v1）
├── 审核前端/                       # 通用审核 UI 和本地入口
├── benchmark/                      # schema、scorer 与脱敏/合成评测工具
├── 端到端学习剪辑/代码/             # 历史底层音频实现
└── 统筹全局/功能说明/               # 各项功能契约与验收标准
```

真实 `main/runs/`、音频、转写、审核包、Mentor 成果和内部会话不属于公开发行内容。

## 本地测试

核心测试只使用合成 fixture 或脱敏结构：

```bash
python3 '稳定生产/challengers/ntrack-episode-bridge-v1/scripts/run_tests.py'
python3 '稳定生产/challengers/review-product-v1/scripts/run_tests.py'
python3 '稳定生产/challengers/cross-track-safety-v1/scripts/run_tests.py'
```

P0 ASR 路线依赖单独环境与本地模型，不作为轻量 CI 的默认步骤：

```bash
python3 '稳定生产/challengers/asr-speaker-v1/scripts/run_tests.py'
```

Tool 总控 Challenger 的契约与安全测试：

```bash
python3 '稳定生产/challengers/tool-orchestrator-v1/tests/test_registry_validator.py'
python3 '稳定生产/challengers/tool-orchestrator-v1/tests/test_runner.py'
python3 '稳定生产/challengers/tool-orchestrator-v1/tests/test_safety_gates.py'
```

以上三组测试当前为 26/26 通过；真实本地 subprocess 只在合成三轨 fixture 上验证过，不能据此宣称完整生产链路已经接通。

目前还没有统一安装命令或一键公开 demo；部分历史脚本仍需移除本机绝对路径后才能做到 clone 即运行。

## Roadmap

### 下一版：完成发布候选闭环

1. 对 EP04 技术试听版完成整片主观听审和 6 个剪口复核；
2. 增加主麦选择、串音抑制和 N 轨 automix；
3. 冻结目标 LUFS、true peak、MP3 规格及授权片头片尾；
4. 完成响度标准化、音乐转场和发布 QC；
5. 记录审核、返工和维护时间，证明净节省时间为正。

### 剪辑质量

1. 扩展说错重来、远距离重复、口癖、异常停顿和声学事件候选；
2. 恢复 `adjust`，修改边界后重新生成 A/B 并使旧批准失效；
3. 建立无候选区域抽查，测量漏删而不只测误删；
4. 允许 LLM 做术语纠错和语义提示，但不允许直接决定 sample 剪口；
5. 如未来要重新比较 FunASR/MLX，先从项目外归档恢复并重新完成人工 gold、许可证和设备审计；当前普通流程只使用 faster-whisper small。

### 工程化

1. 移除硬编码的 `/Users/...` 路径，统一使用项目根目录、配置和环境变量；
2. 提供锁定依赖、统一安装命令和一键合成 demo；
3. 建立 CI，自动运行 schema、规则、N 轨、审核和渲染契约测试；
4. 让 orchestrator 真正调用 Tool 注册表并支持失败恢复；
5. 增加两台独立设备 offset / clock drift 的真实测试。

### 可控学习

1. 积累多期具备合格 `review_mode` 的真人审核数据；
2. 先改规则、阈值和相似案例检索；
3. 再训练候选风险/优先级排序器；
4. 使用冻结 benchmark、Champion / Challenger、独立复核、人工晋升和回滚；
5. 最后再评估是否用 Loop 作为控制平面。

## 文档入口

- [`统筹全局/全局统筹记忆.md`](统筹全局/全局统筹记忆.md)：产品与架构长期记忆；
- [`统筹全局/当前项目进度.md`](统筹全局/当前项目进度.md)：当前事实与下一道门；
- [`统筹全局/产品要求与验收标准.md`](统筹全局/产品要求与验收标准.md)：MVP 完成定义；
- [`统筹全局/功能说明/README.md`](统筹全局/功能说明/README.md)：各功能模块说明。

## License

当前仓库尚未提供开源许可证。除非另有书面授权，不应默认拥有复制、分发或商用权限。
