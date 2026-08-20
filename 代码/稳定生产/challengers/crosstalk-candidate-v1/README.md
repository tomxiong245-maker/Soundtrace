# crosstalk-candidate-v1

隔离 Challenger。把“串音”作为**源轨专属**候选：源轨某段主要是 bleed、
且另一条轨真正在说话（primary）——建议真人对源轨做 gate/duck，而不是全轨删除。
**不修改任何 Champion / 其他 Challenger**。

## 状态

- 8/8 自动测试通过（`tests/test_crosstalk_candidate.py`）。
- 只依赖 Python 标准库。
- 尚未在真实 EP03/EP04 activity 数据上运行；召回 / 误报需真人审核确认。

## 算法

1. 读词级 `activity.classification ∈ {primary, bleed, ambiguous}`；
2. 2 秒滑窗、0.5 秒步长扫每条轨；
3. 当源轨窗口内 `bleed >= 3` 且 `bleed/(primary+bleed+ambiguous) >= 0.7`，且另一条轨
   `primary_count >= 2` → 判为串音候选；
4. 源轨若也有 primary，降级为 `medium + duck_source_track`，否则 `high + gate_source_track`；
5. 相邻窗口按 `merge_gap_seconds = 0.5` 合并。

## 输出

```json
{
  "reason_key": "crosstalk_on_source",
  "track_id": "track_01",
  "applies_to_tracks": ["track_01"],
  "other_dominant_track_id": "track_02",
  "suggested_action": "gate_source_track",
  "confidence": "high",
  "bleed_words": 4,
  "primary_words_on_source": 0,
  "other_primary_words_total": 5,
  "policy": "review_only_no_automatic_accept"
}
```

## 运行

```bash
python3 稳定生产/challengers/crosstalk-candidate-v1/scripts/detect_crosstalk_candidates.py \
  --transcript track_01=/abs/track_01.classified.json \
  --transcript track_02=/abs/track_02.classified.json \
  --transcript track_03=/abs/track_03.classified.json \
  --rules 稳定生产/challengers/crosstalk-candidate-v1/rules/crosstalk-candidate.v1.json \
  --out main/runs/CROSSTALK-CANDIDATE-v1-<ts>/candidates.json
```

## 边界

- **绝不产生全轨同步删除**；`applies_to_tracks` 恒为 `[source_track]`。
- 未来若接入真实 diarization，只需更换 `activity.classification` 数据源，规则不变。
- 只写本 Challenger 目录与 `main/runs/CROSSTALK-CANDIDATE-v1-<ts>/`。
