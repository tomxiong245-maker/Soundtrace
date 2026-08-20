# pyannote-audio 3.x Speaker Diarization · 项目审计

> 审计日期：2026-08-17
> 审计对象：`pyannote-audio` 3.4.0 + `pyannote/speaker-diarization-3.1` pipeline
> 用途：本地推理，中文多人对谈播客每词说话人标注（Apple M3 / 16 GB / 离线）
> 结论：**可以进入 Challenger（`speaker-diarization-v1`）**，条件见 §放行条件

## 1. 官方来源

| 类别 | URL |
| --- | --- |
| GitHub 代码 | https://github.com/pyannote/pyannote-audio |
| PyPI | `pyannote.audio` |
| Pipeline model card | https://huggingface.co/pyannote/speaker-diarization-3.1 |
| Segmentation 依赖 | https://huggingface.co/pyannote/segmentation-3.0 |
| FAQ | https://github.com/pyannote/pyannote-audio/blob/main/FAQ.md |

## 2. 版本选择

- 主线：4.0.7（2026-06-30）——breaking changes（config.yaml 格式、`use_auth_token`→`token`、cache 路径、torchaudio→torchcodec），不采用
- **锁定：`pyannote.audio == 3.4.0`（2025-09-09）**——3.x 分支末端，只锁死依赖上限的维护版本；3.3.1 已 drop Python 3.8

## 3. 许可证

- 代码：MIT（Copyright 2020 CNRS）
- 权重（`speaker-diarization-3.1` + `segmentation-3.0`）：MIT
- **商用可，无 attribution 陷阱**（保留 copyright + license 文本即可）
- HF gated（需 accept user conditions + read token）**≠** 许可证限制；一次性下载后无 EULA 约束

## 4. 权重来源与 SHA

| Repo | 用途 | Gated |
| --- | --- | --- |
| `pyannote/speaker-diarization-3.1` | pipeline config.yaml + 聚类超参 | 是 |
| `pyannote/segmentation-3.0` | 分段模型 `pytorch_model.bin`（powerset 7-class） | 是 |
| `pyannote/wespeaker-voxceleb-resnet34-LM` | speaker embedding（纯 PyTorch，非 ONNX） | 是 |

**官方未公示权重 SHA-256** → 项目层必须建 `稳定生产/challengers/speaker-diarization-v1/models/hashes.txt` 兜底，首次下载后自算，之后每次加载前 verify。

## 5. 依赖清单（3.4.0 `requirements.txt`）

Python 3.9/3.10/3.11 · torch ≥ 2.0 · torchaudio ≥ 2.2 · lightning ≥ 2.0 · speechbrain ≥ 1.0 · pyannote.core 5.x · huggingface_hub ≥ 0.13 · soundfile ≥ 0.12

**下限太松、上限缺失**：装时锁死 `pyannote.audio==3.4.0`，否则老版 3.1.x 遇 speechbrain 1.x 会炸。

## 6. Apple Silicon (M3) 支持

**官方立场：不支持，wontfix**（issues #1091 / #1337 / #1418 / #1886）。#1337 报告 MPS 下 timestamp 呈规则伪影，绝不可走 MPS。

**CPU RTF 参考**：issue #1626 实测 AWS c5.2xlarge 8 vCPU 45min 音频，speaker-diarization-3.1 全流程 ~36.5 分钟（RTF ≈ 0.81），embedding 阶段占 35.2 分钟（3.1 版本 wespeaker 纯 PyTorch 比 3.0 ONNX 慢 2.5×）。

**M3 CPU 8 性能核推断**：RTF ≈ 0.5–1.0，30–60 s fixture 30–60 s 内跑完，可接受；EP03 30 min 节目全流程约 15–30 分钟。

## 7. 遥测 / 网络行为

- **3.x 无内置遥测**（4.0 才引入可选 telemetry）
- HF phone-home：仅首次 `Pipeline.from_pretrained` 拉 config + 权重；之后 `HF_HUB_OFFLINE=1` 完全禁网
- 官方 tutorial 明确支持完全离线：改 config.yaml 里 segmentation 路径为本地绝对路径 → `Pipeline.from_pretrained("/abs/path/config.yaml")` 不传 token → 断网复现无问题

## 8. 输入 / 输出

**输入**：内部 16 kHz mono；其它采样率/声道自动 resample+downmix。可传文件路径或 `{"waveform": tensor[1,N] float32, "sample_rate": int}` 内存 dict。项目场景应传 denoised 单轨（不要传混音 mono，重叠区会污染 embedding）。

**输出**：`pyannote.core.Annotation`，**speech-turn 粒度**：`(start, end, "SPEAKER_00")` 序列，可 `write_rttm()`。**无原生 per-turn 置信度**；要拿概率需下探 `pipeline.segmentation` 的 SlidingWindowFeature（powerset 7-class 后验）。

**Word-level 说话人不是原生能力**。项目落地方案：
1. faster-whisper 出 word timestamp（已有）
2. pyannote 出 speaker turn
3. 时间重叠归属（照抄 WhisperX `assign_word_speakers`：每个 word 找覆盖它时间的 turn，多归属取最大重叠）

## 9. 中文表现

speaker-diarization-3.1 官方 benchmark：
- **AISHELL-4**（中文会议）DER **12.2%** ← in-domain 强项
- AliMeeting DER 24.4% ← 近场麦阵列，偏高但可用
- 播客场景（干净单口、少重叠、2–5 人）预期 5–15%，好于 AliMeeting

segmentation-3.0 训练集**已含 AISHELL 和 AliMeeting** → 中文近场对谈是强项。

## 10. 已知陷阱

- **说话人重叠**：powerset 7-class 每帧最多 2 speaker 并发；三人以上同时说话会漏
- **短片段**：滑窗 10s，&lt;500 ms 反馈（"嗯""对"）易被聚类阶段吞掉
- **MPS 绝对不能用**（见 §6）
- **4.0 迁移**：误装 4.0.7 会架构全变；必须锁 3.4.0
- **权重 gated**：首次下载需 HF token + accept conditions；一次性成本

## 11. 回滚方案

| 方案 | 许可证 | 商用 | 本地 | 中文 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **pyannote 3.4.0 (本方案)** | MIT | ✓ | ✓ | in-domain 12.2% | **首选** |
| pyannote 4.x community-1 | MIT | ✓ | ✓ | 未单独 benchmark | 备胎 |
| NVIDIA NeMo Sortformer | **CC-BY-NC-4.0** | ✗ | ✓ | 未声明 | **排除**（许可证不兼容） |
| Silero VAD + Resemblyzer 自拼 | MIT + Apache | ✓ | ✓ | 无 benchmark | 工程量大；overlap 全丢 |
| WhisperX 全家桶 | 组件 MIT/BSD | ✓ | ✓ | Whisper 中文强 | **推荐当上层封装**，内建 word→speaker 归属 |

## 放行条件（进入 Challenger 前须完成）

1. 锁死 `pyannote.audio==3.4.0` + `pyannote/speaker-diarization-3.1@<revision-hash>`（写死 HF commit sha）
2. 首次下载后自算三份权重 SHA-256 → `models/hashes.txt`；每次加载前 verify
3. 强制 CPU：`pipeline.to(torch.device("cpu"))`，**禁 MPS**
4. 输入用 denoised 单轨；pipeline 内自动 downmix + resample
5. Word-level 标注：pyannote turn + whisper word timestamp + 时间重叠归属；不期望 pipeline 单产 word
6. 断网复现：`export HF_HUB_OFFLINE=1` + 改写本地 config.yaml → 不传 token 加载
7. 隔离 venv：`稳定生产/challengers/speaker-diarization-v1/environment/venv/`，不污染项目主 Python
