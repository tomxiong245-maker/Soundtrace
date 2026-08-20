# crosstalk-candidate-v1 · 任务契约

## 目标

在 `cross-track-safety-v1` 的**跨轨安全门**已有基础上，把“串音本身”变成候选：
识别源轨在此段主要是 bleed（另一轨为 primary）的时段——建议真人对源轨做
**降混/gate**，而不是全轨删除。

不修改 Champion / 现有 Challenger；不做说话人识别（延续既有能量启发式
`activity.classification`，未来 diarization 替换只需换 classification 字段）。

## 输入

- 词级 `classified.json`（含 `activity.classification ∈ {primary, bleed, ambiguous}`）

## 输出

- `reason_key = crosstalk_on_source`
- `track_id`：串音发生的物理麦
- `other_dominant_track_id`：真正的说话轨（可空，若多轨主导）
- `bleed_ratio`：该窗口 `bleed / (primary + bleed)` 比例
- `ambiguous_ratio`
- `suggested_action`：`gate_source_track` 或 `duck_source_track`
- **只标记源轨**（`applies_to_tracks = [source_track]`），**不产生全轨同步删除**

## 算法

1. 用滑动窗口（默认 2.0 秒，步长 0.5 秒）扫源轨词级 classification；
2. 窗口内若：源轨 `bleed >= min_bleed_words (=3)` 且 `bleed/total >= min_bleed_ratio (=0.7)`
   且**另一条轨在该窗口 primary_count > 0** → 判为串音候选；
3. 合并相邻窗口（gap ≤ `merge_gap_seconds`）；
4. 若该段源轨 primary_count ≥ 1 也算命中，但降级为 `ambiguous`（改为 review 更保守）。

## 边界

- 不生成任何“全轨同步删除”的 EDL。
- 只写本 Challenger 目录与 `main/runs/CROSSTALK-CANDIDATE-v1-<ts>/`。
- 不修改 activity 数据，只读。
