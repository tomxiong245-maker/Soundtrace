# self-correction-v1 · 任务契约

## 目标

识别“说错后重来”的候选：说话人说到一半自我打断、然后重新表述。
交给真人审核；不删除任何原音频；不生成正式 EDL。

不修改 Champion 与其他 Challenger。

## 输入

- 词级转写（`{track, words[]{text, start_seconds, end_seconds, ...}}`，与
  `06_activity/*.classified.json` 兼容）

## 输出

- 每条候选：`reason_key = self_correction`，`abandoned_span`（前半），
  `retry_span`（后半），`shared_prefix`（共享的开头几字），`interrupt_gap_seconds`，
  `edit_distance_ratio`，`interrupt_words`（可选，如“不对/呃/等一下”），
  以及总候选起止时间。
- 只 `NEEDS_HUMAN_REVIEW`。

## 算法（纯规则，不 LLM）

对同一说话人（同一 track）的相邻两个短句 A、B 判定：

1. `A.end` 与 `B.start` 之间的 gap ∈ `[interrupt_min_gap, interrupt_max_gap]`
   （默认 `[0.05, 2.0]` 秒），或 gap 中的最后一个词属于打断词表；
2. `A`、`B` 共享前缀字数 ≥ `min_shared_prefix_chars`（默认 2）；
3. `B` 相对 `A` 的编辑距离比 ≥ `min_edit_ratio`（默认 0.5）——即 B 是"改写"而不是"逐字重复"；
4. `A` 的长度不超过 `max_abandoned_chars`（默认 12，短句是自我打断，长的可能是完整表述）；
5. 打断词表：`不对 / 不是 / 等一下 / 我说错了 / 应该是 / 那个 / 呃`（可扩展）。

给出边界：`start = A.start_sample`，`end = A.end_sample`（**只删弃用段**，
`retry_span` 保留）。

## 边界

- 只依赖已有 `06_activity/*.classified.json` 之类的词级转写。
- 只调用标准库；不新增依赖。
- 只写本 Challenger 目录与 `main/runs/SELF-CORRECTION-v1-<ts>/`。
- 输出必须携带原始候选与规则版本 SHA，便于回滚。
