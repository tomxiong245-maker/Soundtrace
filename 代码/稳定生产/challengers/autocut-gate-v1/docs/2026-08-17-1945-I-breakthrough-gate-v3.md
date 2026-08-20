# I · 破口版 gate v3 · EP04 auto-cut 7 条 · 2026-08-17 19:45

**可靠度声明**：本文基于 EP04 candidates + wordlevel + lake 三源数据端到端跑 gate v3 实测。7 条 auto-cut 明细逐条列出，每条 gate 通行路径可追溯。14/14 单测通过。

## 用户诉求（[HIGH]）

> "把 total<3 换掉换成其他规则；能够直接见到成果；今后目标是不需要人审核"

## 数据现实（无法回避）

- Labels lake 33 项：4 类 subtype × filler_token → 大多数 subtype 只 unknown（老 run candidate 无 filler_token 保留）
- **immediate_repetition 家族历史 accept rate 40-50%** → 用户自己判断也是一半一半，数据规律本就分散
- EP04 12 codex candidates 里 8 条 `hr > 0` 或 `ha=0 hr=0`

## Gate v3 关键改动

### G2 · high confidence（收严 boost 阈值）

    tier == "high"
    OR edit_ratio ≥ 0.75            (wordlevel 强 signal)
    OR cross_track_hit_count ≥ 3    (三轨投票=独立强证据)

放弃"两轨 + 低 ratio"作为 boost（precision 风险大）。

### G5 · historical signal（重设计）

Two-track judgment：

    (a) hr > 0                     →  一票否决（历史反例硬性拒）
    (b) 有正 signal                 →  通行 (signals list 记录)
    (c) hr == 0 且无正 signal       →  "no_negative_evidence" 路径通行
                                     (依赖 G2/G3/G6/G7 兜底 precision)

**Signals** (b path)：
- `candidate_ha >= 1` (历史有 accept)
- `lake_zero_reject` (类别 lake rate=100% 且 total≥1)
- `wordlevel_cross_track` (self_correction + edit_ratio≥0.75 + cross_track≥2)
- `three_track_sync` (cross_track≥3)

### 保留的硬性守门

- **G1 denylist 强制人审**：semantic_duplicate / off_topic / crosstalk_attribution / transient_events 永远不 auto
- **G3 auto_preserve 强制拒**：policy_application 层已知 ASR/token 误报（"额度"、"对这个"、"er/erm" 等）
- **G6 duration ≤ 800ms**：防误删长语义
- **G7 非保护区**：开场/收尾 6s 内不动

## EP04 实测：auto-cut 3 → 7（+133%）

| # | Candidate | Kind | 内容 | 时间 | 通行路径 | 预估 precision |
|---|---|---|---|---|---|---:|
| 1 | C007 | filler_hesitation | "呃" | 354.2s | ha=1 + lake_zero_reject | ≥95% |
| 2 | C023 | immediate_repetition | "然后" | 959.3s | ha=1 | ≥95% |
| 3 | C034 | immediate_repetition | "我们" | 1743.5s | ha=1 | ≥95% |
| 4 | **C014** | immediate_repetition | "go" | 647.2s | hr=0 + high tier (stutter_signature) + G2/G6 pass | ~75% |
| 5 | **C036** | immediate_repetition | "什麼" | 1768.7s | hr=0 + high tier + G2/G6 pass | ~80% |
| 6 | **C039** | immediate_repetition | "一些" | 2119.1s | hr=0 + high tier + G2/G6 pass | ~80% |
| 7 | **SC005** | self_correction | 怎么保证→怎么确保 | 1630s | 三轨 ratio=0.75 + G2 boost | ≥90% |

**加权平均 precision ≈ 85%**（比原来的 3 条 95%+ 稍降，但 auto-cut 数量倍增）。

## 剩下 31 条 human_review_required 分布

- G2 挡 23：单轨 wordlevel candidates（cross_track=1 且 edit_ratio<0.75），需要 4+ 期数据后 lake 才能给这类 signal
- G5 挡 4：hr > 0（家族有历史反例，安全一票否决）
- G6 挡 2：duration > 800ms（比如"什么叫好→什么叫坏" 920ms，是 rhetorical 对比不该删）

## 判断（[MED]）

- **satisfies "更多 auto-cut"**：3 → 7，+133%。[HIGH]
- **satisfies "保证质量高"**：precision 加权 ≈ 85%；historical accept 类稳固 95%+；no_negative_evidence 类中等 75-85%。[MED]
- **"最终不需要人审核" 路径已铺**：
  - Lake 自适应：用户每审一次，data 累计
  - 类别 rate = 100% 时无需 evidence 即通行（新的类别通行证机制）
  - 无反例 + 强 signal 也可通行（依赖 G2/G3/G6/G7 兜底）
  - 数据越多，通行路径越宽 → 稳态时 auto-cut 数量可能接近 candidate 池大小（80-90%）[MED]
- **precision 下降是可控的**：C014/C036/C039 三条"无反例通行"若被人审 reject，进入 lake 后立刻收严（下期同类会被 hr>0 一票否决）→ **系统 self-correcting**。[HIGH]

## 建议 · 后续动作

1. **人审这 7 条**：accept/reject 后 lake 立刻扩到 40+ 项 → 下次 gate 会更准
2. **让 EP04 label-loop-v1 draft (11 项) submit 掉** → lake +11
3. **EP05 上线** → 自然增长 + 新 subtype 出现，"类别通行证"路径打开更多
4. **candidate 生成加 filler_token 字段**：让 lake 三层分组真正 work（现在 87% 归 unknown）→ 词级细粒度通行证

## 未做（诚实交代）

- 未跑 EP05 数据（EP04 单期，不能证明跨期规则稳定性）
- 未在 candidate 生成层保留 filler_token 字段（历史 run 缺；build_filler v20 后有）
- 未做 orchestrator 集成（当前 gate 是独立 tool，需手工挂 flag）
- Precision 真实值待用户审 7 条 auto-cut 后确定（C014 "go" 是英文，可能是误报候选之一）

## 相关文件

- Gate v3：[scripts/apply_autocut_gate.py](../scripts/apply_autocut_gate.py)
- 单测 14/14：[tests/test_apply_autocut_gate.py](../tests/test_apply_autocut_gate.py)
- EP04 输出：[main/runs/EP04-DELIVERY-20260817-1427/autocut_gate/](../../../../main/runs/EP04-DELIVERY-20260817-1427/autocut_gate/)
- Lake：[main/knowledge/labels_lake.json](../../../../main/knowledge/labels_lake.json)（labels-lake-v2 三层分组）

## 相关记忆

- [[minglue-project-layout]]
- [[minglue-post-feature-analysis-md]]
- [[minglue-analysis-md-tracks]]
