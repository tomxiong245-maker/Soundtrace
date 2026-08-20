# semantic-transcript-v1（句界与标点假设层）

## 目的

把**已冻结的词级 ASR**变成 Agent 可消费的句子/分句上下文，解决的不是“让人看稿更舒服”，而是让后续的候选判断模块能回答：一个 `嗯`、重复词或停顿候选，是否位于一段尚未结束的完整表达中。

输入中的原始 `word_id`、文字与时间戳永远不改；本 Challenger 只新增一个并列的结构层：

```text
词级 ASR（不可改）
  word_id + 起止时间 + 原文字
        ↓
语义分句/标点假设层（本目录）
  sentence_id + word_id 范围 + 分句标记 + 边界理由/置信度
        ↓
以后由独立的候选/删剪判断模块消费
```

## 当前实现与诚实边界

- 当前实现为 `timing_text_heuristic_v1`：使用词间停顿、已有源标点和少量终止词线索形成句界/逗号边界假设。
- 它**不是**标点模型、语言理解模型、说话人模型或删剪决策器；所有启发式边界均明确带有 `reason` 和 `confidence`。
- 它不生成候选、不判定 `accept/reject`、不生成 EDL、不改音频、不改原始 transcript。
- 以后如接入已审计的外部标点模型，只能新增另一个 `boundary_method`；输出仍必须引用同一批原始 `word_id`，不得重写时间线。

## 输出契约

每条轨生成一个 `*.semantic.json`，其中：

- `sentences[]`：每句引用 `word_id_start / word_id_end / word_ids[]`，并给出 `text_punctuated`、时间范围和边界证据；每句还包含更细的 `clauses[]`；
- `word_context_index[word_id]`：直接把任意候选词映射回 `sentence_id`、`clause_id`、句/分句内位置和完整范围；
- `integrity`：验证每个输入词恰好出现一次、顺序不变、时间由输入词的首尾边界导出；
- `out_of_scope.deletion_decision = NOT_INCLUDED`：防止下游把分句误当删剪授权。

## 运行

```bash
python3 '稳定生产/challengers/semantic-transcript-v1/scripts/build_semantic_transcript.py' \
  --input-report 'main/runs/EP04/EP04-v13-20260813-2002/analysis/p0_mvp_report.json' \
  --episode-id EP04 \
  --source-run-id EP04-v13-20260813-2002 \
  --run-id EP04-semantic-transcript-v1-YYYYMMDD-HHMMSS \
  --out 'main/runs/EP04/EP04-semantic-transcript-v1-YYYYMMDD-HHMMSS'
```

运行前后均不会改写 `EP04-v13-20260813-2002`。新 run 只保存 JSON、manifest、报告和来源 SHA；不复制真实音频。

## 自测

```bash
python3 '稳定生产/challengers/semantic-transcript-v1/scripts/run_tests.py'
```

测试覆盖：词 ID/时间线保留、长停顿句界、源标点、确定性、完整 input-report 运行，以及对重复 ID / 非法时间戳的 fail-closed 行为。

## EP04 真实运行归档（2026-08-14）

真实三轨运行已归档于：

`main/runs/EP04/EP04-semantic-transcript-v1-20260814-120456/`

该目录的 `ARCHIVED.md` 是归档标记。运行结果为：

| 轨道 | 词数 | 句子 | 分句 | 原词覆盖/顺序 |
| --- | ---: | ---: | ---: | --- |
| `track_01` | 12,467 | 177 | 769 | true |
| `track_02` | 11,853 | 207 | 803 | true |
| `track_03` | 6,732 | 170 | 408 | true |

这只是给后续模块消费的上下文层。`deletion_decision`、候选生成、EDL 和音频修改均为 `NOT_INCLUDED`。
