# extract_gold_cut_features 2026-08-18 · 本机运行审计

> 状态：2026-08-18 · Mentor gold cut 逐条 where/how 特征提取器（PARAMETER/PREFERENCE 分家的证据源工具）。它是**只读**分析器，只产出 jsonl 特征文件，不改任何生产规则、EDL、音频或 tools.json。

## 固定信息

- 脚本路径：`main/orchestrator/extract_gold_cut_features.py`
- SHA-256（2026-08-18 版本）：`22b266de31d197db947c0a497259bb4896e0546a4733dcfcd68c4b320bcb9f29`
- 大小：13512 字节
- Python 语言版本：`>=3.10`（用到 `from __future__ import annotations` + `list[tuple[float,float]]` PEP 604 语法）
- 直接依赖：Python 标准库（`json / sys / argparse / pathlib / typing`）+ `numpy` + `librosa`
- 间接依赖（librosa 拉进来）：`scipy` / `soundfile` / `audioread` / `numba` / `pooch`
- 上游许可证：
  - `numpy` = BSD-3-Clause
  - `librosa` = ISC License
- 不调用云端 API，不启动遥测，不上传真实音频
- 数据流：`local files only; reads raw WAV + gold EDL + candidate_package + ASR; writes gold_cut_features.jsonl only`

## 本项目使用范围

- **输入**：
  - EP03 gold EDL（56 cuts）+ candidate_package + ASR + raw WAV
  - EP04 gold EDL（3 cuts）+ machine_assisted 的 crossfade_ms 来源 + ASR + raw WAV
- **输出**：`gold_cut_features.jsonl`（每行一条 · 含 WHERE 段和 HOW 段）
  - **WHERE**：candidate 位置 · ASR prev/next word gap · cross-track speaking · deleted_text
  - **HOW**：crossfade_ms · RMS envelope · librosa onset · silence gaps
- **用途**：由 s5 learning-and-experience 案例蒸馏段消费；workflow 多角度分析这份 jsonl 提炼三个顿悟：
  1. mentor 剪 71% 是 semantic_boundary（不是 filler）
  2. crossfade per-episode constant
  3. cross_track_speaking 定义 59/59 假阳
- **归属 skill**：主消费方 s5 · 生产方 s6（本 tool 登记归属）· 结果被 s2 / s3 用来更新 PARAMETER / PREFERENCE

## 已知限制

- 目前只对 EP03（56 cuts）+ EP04（3 cuts）实测；跨节目泛化未验证
- librosa 版本必须钉死（当前 `librosa 1.0.0`），不同 librosa 版本的 `onset_detect(backtrack=True)` 行为可能不同
- 依赖 candidate_package 与 gold EDL 的时间戳字段命名对齐；若上游 schema 改动，本 tool 需同步
- 输出的 jsonl 仅供**分析**，不能直接作为 rules 输入生产（须经 s5 案例蒸馏 → challenger 提名 → 独立复核 → 人工晋升）
- 本轮只验证了在 EP03 / EP04 fixture 上的可执行性；跨集应用需另跑一次
