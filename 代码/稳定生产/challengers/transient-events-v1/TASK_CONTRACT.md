# transient-events-v1 · 任务契约

## 目标

在**不修改任何 Champion / 现有 Challenger** 的前提下，识别咳嗽、碰麦、桌敲等**瞬态非语音事件**，作为
`NEEDS_HUMAN_REVIEW` 候选交给真人审核，供未来接入审核前端与 EDL。

它不做任何模型训练；不删除任何原音频；不生成正式 EDL；不修改
`稳定生产/rules/**`、`稳定生产/scripts/**`、`稳定生产/challengers/filler-global-pause-v1/**`。

## 输入

- 一条或多条 mono 48 kHz 16/24-bit PCM WAV（对齐后的物理麦）
- 词级转写（可选；若提供则用于“该窗口有词 → 不是纯瞬态”反证）

## 输出

- `main/runs/TRANSIENT-EVENTS-v1-<ts>/`
  - `candidates.json`：每条候选带 `reason_key ∈ {cough_like, mic_bump_like, thump_like}`、
    起止 sample、置信度、能量/频谱特征、是否被词级活动反证。
  - `RUN_REPORT.md`
- 本 Challenger 内 `fixtures/` 与 `tests/`。

## 算法（工程共识，不训练模型）

- 短时窗（10 ms hop, 25 ms window）计算 RMS 与 peak dBFS。
- **peak-to-RMS crest factor**：瞬态事件通常 crest > 12 dB。
- **spectral flux**（相邻 STFT 帧幅度差平方和）出现骤增。
- 事件持续时间约束在 `[30 ms, 400 ms]`，超短的是量化尖峰，超长的可能是语音。
- 反证：候选时段内若源轨 ASR 有词、且 activity 不是 bleed，判 `LIKELY_SPEECH_NOT_TRANSIENT`。
- 全部结果只写候选，绝不自动裁剪。

## 边界

- 仅调用标准库 + `numpy`/`scipy`（用户环境里已有）；若 `scipy` 不可用，回退纯 numpy。
- 不联网、不下载权重。
- 只写本 Challenger 目录和 `main/runs/TRANSIENT-EVENTS-v1-<ts>/`。
