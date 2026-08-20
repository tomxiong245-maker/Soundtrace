# transient-events-v1

隔离 Challenger。识别咳嗽、碰麦、桌敲等瞬态非语音事件，作为 `NEEDS_HUMAN_REVIEW`
候选交给真人审核。**不修改任何 Champion / 其他 Challenger**。

## 状态

- 6/6 自动测试通过（`tests/test_transient_events.py`）。
- 只依赖标准库 + `numpy`（用户环境已有）。
- 尚未在真实 EP03/EP04 音频上运行；实际召回/误报需等真人审核。

## 算法

1. 25 ms Hann 窗、10 ms hop，帧级 RMS / peak dBFS / spectral flux / 低频能量占比。
2. `crest_db = peak_db - rms_db` 达阈值或 spectral flux 骤增触发候选。
3. 按 mic_bump / cough / thump 三种阈值分类（duration、peak、crest、low_ratio）。
4. 若窗口内源轨 primary 词占比 ≥ 0.5（`asr_conflict.primary_words_veto`），
   打上 `LIKELY_SPEECH_NOT_TRANSIENT` 并丢弃。
5. 相邻同类候选合并（`merge_gap_seconds = 0.05`）。

## 运行

```bash
python3 稳定生产/challengers/transient-events-v1/scripts/detect_transient_events.py \
  --wav track_01=/abs/track_01.wav \
  --wav track_02=/abs/track_02.wav \
  --transcript track_01=/abs/track_01.classified.json \
  --rules 稳定生产/challengers/transient-events-v1/rules/transient-events.v1.json \
  --out main/runs/TRANSIENT-EVENTS-v1-<ts>/candidates.json
```

## 边界

- `policy = review_only_no_automatic_accept`；不产生 EDL。
- 只写本 Challenger 目录与 `main/runs/TRANSIENT-EVENTS-v1-<ts>/`。
- 不下载权重、不联网。
