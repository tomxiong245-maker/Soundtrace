# filler-global-pause-v14 · Challenger 边界

## 定位

从 `filler-global-pause-v13` 继承并按 `EP04-review-mixed-14` mentor 反馈迭代。**不修改 Champion；不修改任何既有 run**。

## v14 相对 v13 的变更（一句话概括）

| ID | 变更 | 命中 mentor evidence |
|---|---|---|
| **D1** | 撤销 cough_like / mic_bump_like 候选家族 | S025/S081 "没有咳嗽" · S082 "根本没有" |
| **D2a** | filler_hesitation 加 `strong_next_char_blocklist`（"额+度/外/头/定/角/上/面"等禁匹配） | E301/E303 "额度是一个词" |
| **D2b** | english_filler 若相邻 token 也是英文/字母片段则不生成候选 | E302 "er" 落在 "ild\|er\|对" 上下文 |
| **D2c** | english_filler 上下文 ±4s 内中文 token 占比 <50% 则不生成 | 兜底 |
| **D3** | 强制 rendering-quality gate（50→80→120ms crossfade，equal-power sin/cos 默认） | 4 处 "剪辑痕迹明显" feedback |
| **D4** | weak_tokens 拆三档：backchannel / topic_connective / filler，"对" 特别严格（后必须是 backchannel 才提名） | E304 accept vs E305 reject 的差异建模 |
| **D5** | long_pause / immediate_repetition 阈值不变 | 数据不足以调整 |

## 验证结果（EP03 · 2026-08-14）

用 v14 规则在 EP03 男女双轨 word-level ASR 上跑，与 mentor 14 条决定的 6 条 EP03 事实完全一致：

| cid | mentor 决定 | v14 规则输出 | 一致 |
|---|---|---|---|
| E301 "额" | reject（额度） | BLOCKED（`strong_next_char_blocklist:额+度`） | ✓ |
| E302 "er" | reject（完整单词） | BLOCKED（`english_filler_adjacent_english:ild\|er\|对`） | ✓ |
| E303 "额" | reject（一个词） | BLOCKED（同 E301） | ✓ |
| E304 "然后这个" | **accept** | LIVE（`filler_weak(topic+wk)`） | ✓ |
| E305 "对这个" | reject（完整词） | NOT NOMINATED（D4 "对" 保护） | ✓ |
| E306 "一个/一个" | **accept** | LIVE（`immediate_repetition`） | ✓ |

**Bonus**：v14 还在 EP03 female @ 820.44s 找到另一个"然后这个"，mentor 未曾评过 → 下一轮值得补审。

## EP04 待验证

- cough_like（19）/ mic_bump_like（11）应全部 disabled
- filler_immediate_repetition 数量应与 v4 池一致（规则未变）
- long_pause 数量应与 v4 池一致（规则未变）

需要在 EP04 三轨 canonical transcripts 上跑 v14 得到实际候选清单，跟 GPT 渲染时的 EDL 对齐。

## 文件清单

```
稳定生产/challengers/filler-global-pause-v14/
├── README.md                                    (本文件)
├── rules/
│   └── candidate_rules.v14.json                 (机读规则)
└── scripts/                                     (待补：v14 candidate generator)
```

Validator（本 challenger 之外，验证 v14 与 mentor evidence 一致性）：

```
/tmp/validate_v14_rules.py     (EP03 pass 6/6)
```

## 边界

- Champion `稳定生产/rules/candidate-generation.v1.json` **不动**
- Champion `filler-global-pause-v1/v13` 冻结产物 **不动**
- Mentor 成果 / 原始音频 / 已哈希 run 产物 **只读**
- v14 生效路径：Challenger 通过冻结 benchmark + 独立复核 + 人工晋升 → 升为 Champion

## 交接给 GPT / Codex 完整版渲染

见 `GPT_HANDOFF.md`（下一步生成）。
