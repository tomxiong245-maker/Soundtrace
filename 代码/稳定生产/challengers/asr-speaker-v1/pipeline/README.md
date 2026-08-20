# asr-speaker-v1 · Pipeline 使用指南

**目标**：一条命令跑完，产出五个指标的报告 —— 中文识别错字率、漏识别、静音幻听、说话人搞混、重叠漏检。

## 一条命令

```bash
cd 稳定生产/challengers/asr-speaker-v1

# 首次运行：装依赖（一次即可，~5-10 分钟）
bash pipeline/install.sh

# 出报告（每次改代码/数据后跑）
bash pipeline/run_all.sh
```

跑完看：
- `main/runs/EP03-asr-speaker-v1/final_report.md`（人看）
- `main/runs/EP03-asr-speaker-v1/metrics.json`（机器）

## 五个指标怎么保证的

**关键**：你的音频是**双轨物理录制**——`female.wav` 和 `male.wav` 是两支独立麦克风。基于两轨能量对比 + 10 ms 帧栅格，可以构造**物理银标**：

- **静音**：两轨同时 < -50 dBFS → 无人说话（frame=0）
- **female 主讲**：female 比 male 响 ≥ 3 dB → frame=1
- **male 主讲**：male 比 female 响 ≥ 3 dB → frame=2
- **重叠**：两轨都 ≥ -50 dBFS 且响度差 < 3 dB → frame=3

这不是模型，是物理事实（麦克风能量测量）。因此下面四个指标**无需人工 gold**：

| 指标 | 银标怎么算 |
|---|---|
| 静音幻听 | hypothesis 词落在 silence frame 内的时长 |
| 漏识别 | speech frame 未被 hypothesis 覆盖的时长 |
| 说话人搞混 | Hungarian 映射后，diar 把 female frame 归给 male（或反之）的帧数 |
| 重叠漏检 | overlap frame 中 diar 只输出单说话人的帧数 |

第五个：

| 指标 | 方案 |
|---|---|
| CER 绝对值 | 有 gold 时用 jiwer 算；无 gold 时用**三引擎交叉一致率**给相对排名 |

## 引擎选型（都是 GitHub 开源）

| 用途 | 主选 | 备选 | 兜底 |
|---|---|---|---|
| 中文 ASR | **SenseVoice-Small** (FunAudioLLM) | Champion baseline (faster-whisper small) 切段 | — |
| Apple 加速 ASR | **MLX Whisper Turbo** (mlx-community) | — | — |
| Diarization | **pyannote 3.1**（如设 HF_TOKEN）| **sherpa-onnx** | **双轨能量分割**（本 pipeline 独有，永远可用） |

`pipeline/run_diar.py` 会**自动降级**。三级路径任何一级能工作就用哪级；`main/runs/.../diar/USED_ENGINE.txt` 记录实际使用的引擎。

## 目录结构（跑完之后）

```
main/runs/EP03-asr-speaker-v1/
├── silver/                              双轨银标
│   ├── S01.npz .. S12.npz               (10ms 帧 + rms + labels)
│   └── silver_summary.json
├── raw/
│   ├── sensevoice_small/                原始 SenseVoice JSON
│   └── mlx_whisper_turbo/               原始 mlx-whisper JSON
├── normalized/
│   ├── faster_whisper_small/S*/{f,m}.words.json
│   ├── sensevoice_small/S*/{f,m,speech_mix}.words.json
│   └── mlx_whisper_turbo/S*/{f,m,speech_mix}.words.json
├── diar/
│   ├── USED_ENGINE.txt                  实际使用的 diar 引擎
│   └── <engine>/S*.json                 speaker intervals
├── metrics.json                         机器可读五指标
└── final_report.md                      人看的报告 + verdict
```

## 边界（本 Challenger 严格遵守）

- 不修改 Champion（`稳定生产/scripts/` `稳定生产/rules/` `端到端学习剪辑/代码/`）。
- 不修改 P1 目录（`审核前端/`）。
- 不修改 cross-track-safety-v1 已哈希产物。
- 不修改 `benchmark/EP03-ASR-mini-gold-v1/{gold.json,label.html,segments/*}`（gold.json 由人工填写，label.html 由 P1 拥有）。
- 只写：本 challenger 目录 + `main/runs/EP03-asr-speaker-v1/` + `benchmark/.../hypotheses/` + `benchmark/.../metrics/`。

## 如果 install.sh 某一步失败

- 缺 python3.11：`brew install python@3.11`
- pyannote 装不上：不设 `HF_TOKEN` 就跳到 sherpa，还失败自动兜底到 dual-track（永远可用）。
- SenseVoice 或 MLX 装不上：**指标依然出**——报告里对应引擎显示为空即可，其他引擎照常评分。

## 三档诚实标注

- **已验证事实**：pipeline 代码全部落盘；双轨银标算法基于物理事实，可复现。
- **已决定的方向**：ASR 用 SenseVoice / MLX / baseline 三选优，diar 三级降级。
- **待验证假设**：真实 CER 绝对值仍需人工 gold；本 pipeline 的相对排名与人工 gold 结果的一致性未验证。
