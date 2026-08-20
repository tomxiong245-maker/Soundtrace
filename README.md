# 章鱼 AI 播客 · 多轨后期辅助系统 · 最终交付

## 项目一句话

本地跑、可回放、真人签字的多轨播客后期助手。**LLM 主导候选决定** + **Optuna 参数级优化** + **NISQA 客观分**。零上云 · 零 API key · 全 Apple Silicon 本地运行。

## 目录结构

- **项目主文档/** · CLAUDE.md (6+1 元规则 · M0 开发者模式) · domain-rules.md (§1-§22) · 20 参数地图
- **统筹全局/** · PROMOTION_MANIFEST · GOLDEN_PATH_FROZEN · EVENING_MANIFEST · DEPRECATED · DEVELOPER_MODE_LOG
- **新skill/** · 14 skill (含 candidate-semantic-veto)
- **knowledge/** · cut_parameters.json · case_memory · case_embeddings/FAISS · learned_examples_EP03_MENTOR.md (56 mentor 位置) · session_feedback
- **代码/** · 稳定生产/challengers/ (12+ Challenger 含 learning-pattern-from-case-v1) · main/orchestrator/
- **参考成品/** · EP04 交付母带 mp3 · gold_edl 反推
- **benchmark/** · 契约测试 + 冻结基线
- **verify/** · verify.sh 多层强制

## 快速开始

```bash
git clone https://github.com/tomxiong245-maker/Soundtrace.git
cd Soundtrace
git lfs pull
bash verify/setup.sh

cd 代码
MINGLUE_PARAM_GATES_OFF=1 python3 稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py \
  --episode-id EP05 \
  --out-dir ../main/runs/EP05-$(date +%Y%m%d-%H%M) \
  --nisqa-python /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  --review-budget 100 \
  --from-raw-wav <track_01.wav> <track_02.wav>
```

## GOLDEN PATH · LLM-First 架构

```
Raw 3 轨 WAV → Denoise (DeepFilterNet) → Whisper ASR
        ↓
Stage 3.5.6 · ASR probability 硬约束 (word.probability<0.60 for filler/rep → REJECT · pipeline 前置)
        ↓
Stage 3.5.5 · LLM 主动扫全文 · 唯一 "该不该剪" 判决 ⭐
  ├── verdict: KEEP_CUT / REJECT_KEEP / NEEDS_REVIEW
  ├── 3 mode: claude CLI / Anthropic API / fallback
  └── few-shot: EP03 mentor 56 位置 (learned_examples_EP03_MENTOR.md)
        ↓
autocut_gate (只保留 4 结构性门): speaker_role · source_track · duration · review_budget
        ↓
Stage 5 EDL (只用 LLM KEEP_CUT) → Stage 6 Automix (denoised · 音乐淡入淡出 · 人说最后一句时淡入片尾曲)
        ↓
Stage 6.5 NISQA benchmark · Stage 6.7 Optuna · Stage 6.10 re-render (LLM ∩ Optuna)
        ↓
Stage 7 audit report → 人审 → session_feedback → 下期 pipeline 更准
```

## 关键元规则 (7 条 · 项目 canonical)

- **M0** (新) · 开发者模式 · 触发 "开发者模式" 突破 M1-M6 (物理/安全约束除外)
- **M1** · 分层 · Champion / Challenger / run-local
- **M2** · 只读 · 公司音频不出本地
- **M3** · 人签字 · 语义删剪必须真人批准
- **M4** · EDL 整数 sample
- **M5** · 契约先行 · tool 必须登记
- **M6** · 报告纪律 · 三档措辞

## 依赖 (完全本地)

| 工具 | 用途 | 许可 |
|---|---|---|
| DeepFilterNet v0.5.6 | 降噪 | MIT |
| faster-whisper (CT2 int8) | ASR 转写 · word.probability | MIT |
| pyannote-audio 4.0.7 | 说话人分离 | MIT |
| MFA 3.4.1 + mandarin_mfa | 词级边界精修 | MIT |
| NISQA v2.0 (Fraunhofer) | 音质评估 · 5 维 MOS | MIT |
| Optuna 4.9 (Preferred Networks) | 参数优化 · Bayesian TPE | MIT |
| faiss-cpu 1.15 (Meta) | 案例向量索引 (未来解锁) | MIT |
| ffmpeg 9.0.1 | 混音 · loudnorm · 音乐淡入淡出 | LGPL/GPL |
| Claude Code CLI | LLM 语义判决 (无 API key · 本地) | Anthropic |

## 唯一硬约束

**ASR probability < 0.60** (for filler/rep candidates) → REJECT
- 出处: EP04 实测 (正常词 ≥0.75 · 幻觉词 ≤0.49) + silvacarl2 社区共识 (github/whisper#679)
- 位置: pipeline Stage 3.5.6 前置过滤

## 数据流

- **不上云**: 音频 / 转写 / 内部资料默认不离开本机
- **可追溯**: 每期一个 run 目录 · 输入 SHA / 命令 / 环境 / 模型版本全记录
- **可回滚**: run 目录独立 · 删目录 = 回滚

## 冻结状态 · 2026-08-19 evening

- GOLDEN PATH 冻结 · 详见 统筹全局/GOLDEN_PATH_FROZEN_2026-08-19.md
- EP04 case_store 冻结 · 语义识别当时太差 · 不进入训练集
- 旧 rules-based 方法冻结 · DEPRECATED_LLM_TAKEOVER_2026-08-19.md
- 未来改动需触发 "开发者模式" 或走 M1 晋升流程

## 学习路径 (未来)

- **learning-pattern-from-case-v1** · 从 mentor 反推 56 位置 + 用户 case 蒸馏模式 · 骨架已建
- **Q3 解锁条件** · mentor gold ≥ 100 · 才启用 embedding retrieval

## 上台一句话

"5 门语义规则让位 · 4 门结构性门保留 · LLM 唯一决定候选 · Optuna 只调参数 · NISQA 只打分 · **规则不死 · 只做召回和物理约束** · AI Agent 时代的规则治理"
