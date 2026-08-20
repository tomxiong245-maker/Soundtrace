# Task Contract · Phase 00 · tool-orchestrator-v1

> Worker: `claude-code-audio-clips-nightly-tool-orchestrator-v1`  
> Date: 2026-08-11  
> Classification: green / read-only project inspection plus writes confined to this Challenger

## 本阶段目标

建立可复核的基线：盘点 `main/tools/tools.json` 的 19 项登记能力、其脚本与参数声明；记录现有 `main/orchestrator/orchestrator.py`、P0、P1 与 N-track bridge 的接口关系；冻结 Champion 目录哈希与当前 Git 状态。

## 本阶段不做什么

- 不改 `main/tools/tools.json`、`main/orchestrator/`、Champion、P0/P1/N-track Challenger 或既有 `main/runs/`。
- 不运行真实音频处理、ASR、候选生成、审核、EDL、渲染、混音或发布步骤。
- 不下载模型、安装依赖、访问网络、上传音频/转写，或调用外部服务。
- 不创建 approved EDL、不生成任何自动语义删剪决定。

## 输入

- `main/tools/tools.json`
- `main/orchestrator/orchestrator.py`
- `稳定生产/challengers/asr-speaker-v1/README.md`
- `稳定生产/challengers/review-product-v1/README.md`
- `稳定生产/challengers/ntrack-episode-bridge-v1/README.md`
- Git 索引、工作树状态及 `稳定生产/scripts/`、`稳定生产/rules/` 文件哈希

## 输出与可修改目录

仅允许新建或修改：

```text
稳定生产/challengers/tool-orchestrator-v1/**
```

本阶段必须产出：

- `before_inventory.md`
- `baseline/git_status_before.txt`
- `baseline/champion_sha256_before.txt`
- `checkpoints/phase-00.md`
- `优化候选.md`（没有发现时也明确写“本阶段无新增优化候选”）

## 禁止触碰的文件与目录

- `音频参考库/**`、所有 `*.wav` / `*.mp3` 原始或正式音频
- `mentor的成果/**`
- `稳定生产/scripts/**`、`稳定生产/rules/**`
- `main/tools/tools.json`、`main/orchestrator/**`
- `main/runs/**` 中的任何既有目录
- 所有既有 Challenger 的已哈希产物、正式 EDL、正式成片与人工决定

## 自动测试与核对

1. 解析 JSON 后验证注册表工具数为 19，工具名无重复，每项包含 `name/script/params/reads_only`。
2. 对每条注册项记录：注册表 `scripts_root` 下是否存在脚本、对应的当前工作区镜像脚本是否存在、声明参数清单。
3. 生成并自校验 Champion SHA-256 清单：清单中每个当前文件在写入后仍可重新计算为相同值。
4. 记录 `git status --short --branch` 原始输出；不得以干净工作树为前提。

## 真实运行条件

本阶段没有真实音频运行。任何“运行”仅限 Python 标准库解析、文件存在性与 SHA-256 检查。

## 完成门

- 19 个 Tool 均在 `before_inventory.md` 中有明确状态；缺失或不兼容要被标为事实，不能猜测修复。
- Git 与 Champion 哈希基线落盘，且本阶段结束复算一致。
- 所有新文件只在本 Challenger 内。
- 未运行音频处理，也未生成 EDL、决定或正式产物。

## 失败与回滚

若基线文件无法读取、注册表不是预期 JSON 或发现写入范围越界：停止在本阶段，记录失败原因。回滚方式是只删除本 Challenger 新增的文件；不得回滚、清理或修改项目既有文件。

## 本阶段结束后的文档更新

只更新本 Challenger 的 `README.md`（若新建）、`checkpoints/phase-00.md`、`优化候选.md`。不更新《当前项目进度》或 F07，除非本阶段已经完成可复现的实现和验证；Phase 00 本身不构成产品能力已接通的证据。
