# release-policy-v2 · Challenger

**Slot**：EP04 交付审核后（2026-08-17 16:35 用户批准修正版 mp3）→ 用户明确指令了三件事：
1. 长停顿、说错重来加入机器自主学习范围（跟口癖/即时重复同待遇）
2. 语义重复、离题、串音仍强制人工
3. 片尾曲进得晚，改成"嘉宾说最后一句"就进（新 outro_lead）

本 challenger 是把这三条指令落成**新一版 rules + policy + timing**，**不覆盖 champion**。orchestrator 未来 run 显式引用本 challenger 才生效。

## 产物清单

| 文件 | 作用 | supersedes |
|---|---|---|
| `rules/candidate_rules.v19.json` | 候选规则 fork v18 · 加 `boundary_strategy` 契约（filler 整词/immediate_repetition 扩到最后重复） | `稳定生产/challengers/filler-global-pause-v14/rules/candidate_rules.v18.json` |
| `rules/editing_policy.guards-v2.json` | 编辑政策 fork v1 · autocut whitelist = {filler_hesitation, immediate_repetition, global_long_pause, self_correction}；G-006 高风险家族缩窄到 semantic_duplicate/off_topic/crosstalk 等 | `main/orchestrator/editing_policy.guards-v1.json` |
| `timing/music_templates.v2.json` | 新 template `reference-linear-v2-guest-cued-outro`（`outro_fade_in_lead_seconds = 37.617`） | `main/orchestrator/music_templates.json::reference-linear-v1`（v1 保持只读） |
| `timing/release_specs.v2.json` | 新 spec `reference-linear-v2` 绑定新 template · TP 目标从 mentor 踩线 -0.1 直接下推到 safety_floor -1.0 | `main/orchestrator/release_specs.json::reference-linear-v1`（v1 保持只读） |
| `docs/2026-08-17-1700-analysis.md` | 完整分析：三个 EDL cut 的 ASR 诊断 + 各规则改动的证据链 + 未做的诚实交代 | — |

## 严格保留的边界

- **不覆盖 champion**：`main/orchestrator/*.json` 全部未改；`main/orchestrator/editing_policy.guards-v1.json`、`music_templates.json`、`release_specs.json` 都保持只读
- **不改 candidate rules v18**：challenger v19 fork 出来
- **不改 automix_v1.py**（前一轮已实装双遍 loudnorm，本轮无需再动）
- **不改已交付 mp3**：`main/runs/EP04-DELIVERY-20260817-1427/render/EP04_codex_loudnorm_corrected.mp3` 已 human_approved，不动
- **不改 CURRENT_DELIVERY_FACTS 里已冻结事实**（best_local_delivery / latest_delivered_master 保持不动；只加 next_run_policy 字段指向本 challenger）
- HF pyannote 环境保留，不使用（本轮完全走 codex 已跑好的 machine-assisted-draft + 用户批准）

## 晋升条件（何时把 v2 从 challenger 提到 champion）

1. 用户显式说"用 v2 到 main/"
2. 独立复核 + 回滚方案 + 契约测试全过
3. 至少 1 期新节目（EP05+）用 v2 完整跑通 orchestrator 且产出 human_approved 交付
4. `label_learning_driver` 用 v2 policy scope 跑 backtest 无跨期泄漏

## 下一步 next actions

- **EP05 开工时**：用 `--release-spec 稳定生产/challengers/release-policy-v2/timing/release_specs.v2.json --template-id reference-linear-v2 --candidate-rules 稳定生产/challengers/release-policy-v2/rules/candidate_rules.v19.json --edit-policy 稳定生产/challengers/release-policy-v2/rules/editing_policy.guards-v2.json` 明确挂新 challenger
- **label_learning_driver 侧**：读白名单，仅对 whitelist_kinds 输出 machine_cut；其它类型强制 human_review_required
- **orchestrator 侧**：读 candidate_rules.v19 的 `boundary_strategy` 段落，替换 snap_candidate_boundaries 里旧的隐式行为
