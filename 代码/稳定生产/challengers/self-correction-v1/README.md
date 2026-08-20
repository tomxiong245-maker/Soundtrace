# self-correction-v1

隔离 Challenger。识别“说错重来”的自我更正候选：说话人先说到一半、被打断，
然后重新表述。只出 review-only 候选。**不修改任何 Champion / 其他 Challenger**。

## 状态

- 8/8 自动测试通过（`tests/test_self_correction.py`）。
- 只依赖 Python 标准库（`difflib`）。
- 尚未在真实 EP03/EP04 音频上运行。

## 算法

1. 按 `sentence_split_gap_seconds`（0.6 s）把词级转写切成短句 A、B、C、…
2. 相邻两句 A、B 判定为自我更正候选：
   - gap ∈ `[0.05, 2.5]` 秒，或 A 尾/B 首出现打断词（不对/不是/等一下/…）；
   - 共享前缀字数 ≥ 2；
   - `difflib.SequenceMatcher(A, B).ratio() ≥ 0.4`；
   - A 长度 ≤ `max_abandoned_chars = 12`；
   - A 起始不落在 `protected_starts`（大家好/欢迎/…）内。
3. 完全逐字重复不出候选（那属于口癖 Challenger）。
4. 候选边界 = A 段（弃用段）；B 段（重说段）不动。

## 运行

```bash
python3 稳定生产/challengers/self-correction-v1/scripts/detect_self_correction.py \
  --transcript track_01=/abs/track_01.classified.json \
  --transcript track_02=/abs/track_02.classified.json \
  --rules 稳定生产/challengers/self-correction-v1/rules/self-correction.v1.json \
  --out main/runs/SELF-CORRECTION-v1-<ts>/candidates.json
```

## 边界

- `policy = review_only_no_automatic_accept`；不产生 EDL。
- 只写本 Challenger 目录与 `main/runs/SELF-CORRECTION-v1-<ts>/`。
