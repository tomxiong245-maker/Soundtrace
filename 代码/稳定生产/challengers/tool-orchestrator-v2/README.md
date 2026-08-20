# tool-orchestrator-v2 (Challenger)

> 状态：**SKELETON_CREATED**
> 日期：2026-08-17
> 隔离目录：`稳定生产/challengers/tool-orchestrator-v2/**`
> 允许写入的 run：`main/runs/TOOL-ORCH-V2-<phase>-<timestamp>/`

## 存在理由（用户反馈 2026-08-17）

> "L2-8 赶紧搭，我觉得这是很多时候反复做无用功的问题"

`main/orchestrator/delivery_orchestrator.py` 4640 行，每加一个新能力（口癖候选、边界精修、剪口质量、guards、label learning driver…）都要往里塞代码，形成事实上的"胶水层"。`tools.json` 有 18 项能力登记，但主流程并不通过它统一调度——每个 tool 各自 `subprocess.run` 直调脚本，参数、超时、日志、provenance、错误恢复全部散落。

v2 目标：**让所有能力必须走 registry 的 adapter 接口，delivery_orchestrator 分离为 planner + executor，从此加新能力不改主流程代码，只加一个 adapter + 一行 tool 注册。**

## v1 已完成的、v2 复用不重造

- `runner/registry_validator.py`：18 项 tool 静态校验（脚本存在、路径安全、schema 合规）
- `runner/runner.py`：读注册表 → 冻结 plan → 只读 tool 调用 → HUMAN_REVIEW_REQUIRED 停止
- `adapters/{inspect_audio,summarize_inspection}_adapter.py`：2 个已就位 adapter
- 26/26 契约测试

## v2 必须补的

1. **剩余 16 个 adapter**（v1 只做了 2 个）：把 `main/tools/tools.json` 里其它 16 项 tool 各写一个 adapter，声明 inputs schema、outputs schema、命令模板、超时、provenance 字段
2. **write-tool 调用契约**：v1 的 runner 拒跑 `reads_only=false`；v2 加"受政策管控的 write-tool 通道"——runner 允许调 write-tool，但每个写调用必须声明 policy（谁授权、什么范围）、输入 SHA 冻结、输出 SHA 校验、失败可回滚
3. **planner / executor 分离**：`delivery_orchestrator.py` 拆成
   - planner：读 episode config + 规则/偏好，输出 `plan.json`（tool 调用序列 + 参数 + 依赖 + 期望输出）
   - executor：消费 `plan.json`，通过 v2 runner 逐步调 adapter，失败可精准重试单步
4. **契约测试**：合成三轨 fixture 走完 DeepFilterNet fake → ASR fake → 候选 → 审核 → 双 EDL → 渲染，全程通过 v2 adapter；证据 manifest 记录每一步的 provenance

## 严禁（Champion 边界）

- 不改 `稳定生产/scripts/**`、`稳定生产/rules/**`、`端到端学习剪辑/代码/**`（tool 本身脚本不动）
- 不改 v1 runner（v2 通过 patch 或 subclass 扩展）
- 不改 `main/orchestrator/orchestrator.py`（旧状态机演示，保留）
- 不自动 approve / finalize；写-tool 通道启用后仍必须停在真人审核门
- 不下载模型；不装新依赖；不上传音频

## 允许修改

- 本 Challenger 目录：`稳定生产/challengers/tool-orchestrator-v2/**`
- `main/tools/tools.json`：**只允许追加/修正 schema 描述**，不改现有 tool 的 name/params/script 路径
- `main/orchestrator/delivery_orchestrator.py`：分离 planner/executor 且**旧路径继续可用**（并联而非替换），新老对齐通过契约测试后由人工晋升
- 新建的 `main/runs/TOOL-ORCH-V2-*-<timestamp>/`

## 目录

```
稳定生产/challengers/tool-orchestrator-v2/
├── README.md                          (本文件)
├── TASK_CONTRACT.md
├── baseline/
│   ├── git_status_before.txt
│   └── champion_sha256_before.txt
├── adapters/                          (16 个新 adapter)
├── runner_patch/                      (v1 runner 扩展：write-tool 通道)
├── orchestrator_patch/                (delivery_orchestrator planner/executor 分离补丁)
├── contracts/                         (adapter schema、plan.json schema)
├── audits/
├── checkpoints/                       (每 phase 结束写一份状态 md)
└── tests/
    └── fixtures/
```

## 相关任务

对应主任务列表 C1-C5：
- C1（本文档骨架）
- C2 adapter 接口 schema
- C3 包裹现有 tool（16 个新 adapter）
- C4 planner/executor 分离
- C5 契约测试 + fixture 全绿
