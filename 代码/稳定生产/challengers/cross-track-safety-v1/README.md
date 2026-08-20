# cross-track-safety-v1 (Challenger)

**状态**：Challenger（隔离运行，不动 Champion）
**日期**：2026-08-11
**作者**：Renting

---

## 存在的问题（Champion 目前的行为）

Champion 的 EDL 是**全局时间轴的 cut**，删除会同时作用于两条对齐的音轨（`render_approved_edl.py` 用同一 filter graph 应用到每条 stem）。但候选生成 (`generate_cut_candidates.py`) 只看**单轨** ASR 的词流，因此：

- **long_pause** 会在源轨的两个词之间挖静音，但另一轨此刻可能正在讲话——一刀切下去就把另一位的主讲砍掉了。
- **source_without_primary / bleed>primary** 说明部分候选的"源轨"本来就是串音，删的其实是另一位讲话人的漏音，语义责任错配。

Baseline 复现（8/8 完全一致，见 `before_metrics.json`）：

| 指标 | 值 |
|---|---|
| 总候选 | 56 |
| long_pause | 34 |
| immediate_repetition | 15 |
| filler_hesitation | 7 |
| source_without_primary | 27 |
| source_bleed>primary | 28 |
| long_pause 另一轨有 primary | 27 |
| long_pause 另一轨 primary ≥3 | 21 |

半数以上候选存在"跨轨误删"风险。

---

## Challenger 的做法（明确的边界）

1. **只保留** `filler_hesitation` + `immediate_repetition`；**不生成 `long_pause`**（长停顿必须走人工，Challenger 认为纯规则不能判它）。
2. 每条候选调用 `evaluate_candidate_safety()`，返回四态之一：
   - `SAFE` — 通过所有守卫，进 `safe_candidates.json`
   - `BLOCK` — 违反跨轨守卫，进 `blocked_candidates.json`，附 reason_code
   - `NEEDS_HUMAN_REVIEW` — 有歧义（如 ambiguous 占比高），进 `blocked_candidates.json`，附 reason_code
   - `FAIL_CLOSED` — 上游数据缺失，无法判断，进 `blocked_candidates.json`
3. **审核包携带完整上下文词表**：每条 candidate 直接嵌入 `context_window`（cand.start-5s ~ cand.end+5s）的两轨词表，前端不再拼 `track_activity.json`。
4. **独立 Challenger 前端**：`审核前端/challenger-cross-track-safety-v1/`，数据缺失时显红，不静默空白。
5. **不做的事**（守住 NOT-TODO）：
   - 不改 Champion（`稳定生产/scripts/*` / `端到端学习剪辑/代码/*` 一行不动）
   - 不覆盖 `main/runs/EP03/`、`main/runs/EP03-freshrun-20260810-1730/`、`审核前端/index.html`
   - 不加 `--accept-all-for-mvp` 类的旁路
   - 不自动通过任何候选（`SAFE` 也必须过人工）
   - 不上传音频、不下载新模型、不改 ASR
   - 不声称 ASR 漏字已修好（这是另一件事）

---

## 目录

```
cross-track-safety-v1/
├── README.md                    # 本文
├── before_metrics.json          # baseline SHA + 8 指标独立复现
├── rules/
│   └── candidate-generation.safety-v1.json   # 只启 filler + repetition
├── scripts/
│   ├── generate_safe_candidates.py           # 主生成器（不修改 Champion）
│   ├── evaluate_candidate_safety.py          # 单一决策函数
│   ├── build_challenger_review_package.py    # 打审核包（含 context 词表）
│   ├── run_tests.py                          # 跑 T01-T12
│   └── run_benchmark.py                      # 跑安全 & 上下文完整性 & 幂等性
├── tests/
│   └── fixtures.json                         # T01-T12 fixture
└── after/                       # 生成物（run 时创建）
```

## 出错时的处理

按任务要求：任一 benchmark 失败 → 不声称完成、不改 Champion、保留失败工件、不扩展。
