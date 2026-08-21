# Soundtrace · 章鱼 AI 播客多轨后期辅助系统

## 项目一句话

**本地跑、每一步可追溯、真人拍板** 的多轨播客后期助手。
**LLM 主导候选决定**（Claude Haiku 4.5）+ **Optuna 参数级优化**（TPE 5 维）+ **NISQA 客观分**（Fraunhofer v2.0）。
零上云 · 全 Apple Silicon 本地运行。

---

## 📖 必读文档（新读者按此顺序）

1. **[项目主文档/系统架构白皮书-2026-08-21.md](项目主文档/系统架构白皮书-2026-08-21.md)** —— **系统实现的权威技术说明** · 六层 Skill / Pipeline 20 stages / 数据模型 / 参数默认值全在这里
2. **[项目主文档/CLAUDE.md](项目主文档/CLAUDE.md)** —— 六条元规则（M0-M6）· 项目宪法
3. **[项目主文档/domain-rules.md](项目主文档/domain-rules.md)** —— §1-§22 具体条款（涉及相关领域时读）
4. **[与AI的上下文/2026-08-21-2000-pre-讲稿-演讲版.md](与AI的上下文/2026-08-21-2000-pre-讲稿-演讲版.md)** —— 项目思路的自然语言叙述

---

## 目录结构

```
项目主文档/         · CLAUDE.md · 系统架构白皮书 · domain-rules · SKILL 总览
统筹全局/           · 状态摘要 · 版本 manifest · 讲稿归档
新skill/            · 6 层 skill 定义 + preflight checks
knowledge/          · cut_parameters · labels_lake · session_feedback · speaker_maps · tools.json
代码/main/          · orchestrator · feedback_engine · label_learning_driver · integration_governance
代码/稳定生产/       · challengers/ (12+ 实验组件 · 4 已晋升 champion)
新落地/             · verify layer 18/21 · tool audits
verify/             · verify.sh 21 层强制
与AI的上下文/       · 讲稿 · 演讲版 · 项目历史
docs/               · domain-rules § 展开
```

---

## 快速开始

```bash
git clone https://github.com/tomxiong245-maker/Soundtrace.git
cd Soundtrace

# 安装依赖（12 个 · 全部 conda-forge 或 pip · 无需 API key）
# 见 项目主文档/系统架构白皮书-2026-08-21.md § 3

# 单期节目跑通
cd 代码
python3 稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py \
  --episode-id EP0X \
  --from-raw-wav <track_01.wav> <track_02.wav> <track_03.wav>
```

**先建 speaker_map**：`main/knowledge/speaker_maps/EP0X.speaker_map.json`（至少一 track `role="host"`）。否则 orchestrator 拒跑。

---

## GOLDEN PATH · LLM-First 架构

```
Raw N 轨对齐 mono WAV
    ↓
Stage 1 · DeepFilterNet 降噪 (48kHz · 1440 samples 尾部回填)
    ↓
Stage 2 · faster-whisper ASR (small · int8 · word 级 · probability)
    ↓
Stage 3.5.6 · ASR probability 硬约束 (word.probability<0.60 for filler/rep → REJECT · 前置过滤)
    ↓
Stage 3.5.5 · LLM 语义 veto ⭐ · 唯一候选决定者
  ├── 模型: claude-haiku-4-5-20251001 · window 30s · overlap 5s
  ├── verdict: KEEP_CUT / REJECT_KEEP / NEEDS_REVIEW
  └── 3 mode: claude CLI / Anthropic API / fallback
    ↓
Stage 4 · autocut_gate (5 门结构性红线 · 语义活让位 LLM)
  ├── G3 no_preserve · G5 history · G6 duration ≤0.8s
  └── G7 protection (片头片尾 6s) · G7 never_cut (用户签字 override)
    ↓
Stage 5 · EDL 生成 (整数 sample · 三轨同步 · 只装 KEEP_CUT)
    ↓
Stage 6 · Automix (denoised · 双遍 loudnorm -22.2 LUFS · 音乐淡入淡出)
    ↓
Stage 6.5 NISQA benchmark → Stage 6.7 Optuna refinement → Stage 6.10 交集门 re-render
    ↓
Stage 7 · Audit report → 人审 → session_feedback SOT → 下期 pipeline 更准
```

细节看 **[系统架构白皮书](项目主文档/系统架构白皮书-2026-08-21.md) § 2**（每 stage 的脚本 / 参数 / 产物 / 失败行为）。

---

## 六层 Skill · 按"边界不能跨"分

| L | Skill | 职责 | 硬边界 |
|---|---|---|---|
| L0 | `episode-triage-and-plan` · 门卫 | 冻结 plan.json | 不冻结 · 一步不启动 |
| L1 | `feedback-engine` · 记忆 SOT | session_feedback 单一入口 | 反馈进出只此一门 |
| L2 | PREFERENCE 层 × 3 | LLM 主决策 · gate 只兜结构 | 不碰参数 |
| L3 | PARAMETER 层 · Optuna | 5 维参数 TPE 搜索 | 不碰候选语义 |
| L4 | `learning-and-experience` | 跨期沉淀 · 只写数据源 | 不改生产代码 |
| L5 | `governance-and-tool-registry` | tools.json + adapter 契约 | 未登记 → verify.sh FAIL |

---

## 六条元规则

| # | 名 | 内容 |
|---|---|---|
| **M0** | 最高规则 · 开发者模式 | 触发"开发者模式"绕过 M1-M5 · 但 **音频不出本地** + **原始素材只读** 永远不能碰 |
| M1 | 分层 | Champion / Challenger / run-local 严格隔离 |
| M2 | 只读 | 原始素材 · 公司音频不出机器 |
| M3 | 人签字 | 语义删剪必须真人明确批准 |
| M4 | 整数采样 | EDL 批准区间三轨同步 · 一 sample 都不能偏 |
| M5 | 契约先行 | 工具必须登记 tools.json + adapter · 未登记即报错 |
| M6 | 报告纪律 | 三档措辞 · 已验证事实 / 已决定方向 / 待验证假设 |

---

## 依赖

12 个第三方 · 全部本地可离线运行。详见 **[系统架构白皮书 § 3](项目主文档/系统架构白皮书-2026-08-21.md#3)** 的版本 / URL / 许可证 / 权重 SHA / CLI 参数完整表。

**关键钉版**：`DeepFilterNet v0.5.6` · `pyannote-audio 4.0.7` · `Optuna 4.9.0` · `ffmpeg 9.0.1` · `claude-haiku-4-5-20251001`。

---

## 唯一硬约束

**ASR probability < 0.60**（for filler/repetition candidates）→ REJECT
- 出处：EP04 实测（正常词 ≥0.75 · 幻觉词 ≤0.49）+ silvacarl2 社区共识（github/whisper#679）
- 位置：Pipeline Stage 3.5.6 前置过滤（`MIN_PROB=0.60`）

---

## 数据流

- **不上云**：音频 / 转写 / 内部资料默认不离开本机（M2 硬红线）
- **可追溯**：每期一个 run 目录 · 输入 SHA / 命令 / 环境 / 模型版本 / 每个工具的输出日志全记录
- **可回滚**：run 目录独立 · 删目录 = 回滚

---

## 冻结状态 · 2026-08-21

- **GOLDEN PATH 冻结** · 详见 `统筹全局/GOLDEN_PATH_FROZEN_2026-08-19.md`
- **8 门 gate → 5 门 gate** · 语义判决全交 LLM
- **参数与候选分离** 元原则（08-20）· Optuna 独占参数 · LLM 独占候选
- **EP05 首跑通过** · LLM 剪 65 条 · 出 25:37 mp3 · 已交付
- 未来改动需触发"开发者模式"或走 M1 晋升流程

---

## 上台一句话

> "语义规则让位 · 5 门结构性门保留 · **LLM 唯一决定候选** · **Optuna 只调参数** · NISQA 只打分 · 规则不死 · 只做召回和物理约束。这是 AI Agent 时代的规则治理。"

---

## 相关

- License：MIT
- Author：熊镇正 · 香港中文大学（深圳）
- 更多问题：GitHub Issues
