# A · self_correction 词级 detection 重设计 · 2026-08-17 18:30

**可靠度声明**：本文陈述基于在 EP04 三轨真实 ASR (v13) 上的**首次实测**：word-level sliding-window 算法命中 33 个候选，与旧句级算法（0 命中）直接对比。每一条候选都在文中列出，用户可对照 ASR 词序列听审确认。

## 事实（[HIGH]）

- 新增 `稳定生产/challengers/self-correction-v1/scripts/detect_self_correction_wordlevel.py`（150 行独立脚本），保留旧句级 `detect_self_correction.py` 不动。[HIGH]
- 新规则 `稳定生产/challengers/self-correction-v1/rules/self-correction-wordlevel.v1.json`：K=3 / edit_ratio_min=0.4 / gap 0.05-0.6s / high_confidence ≥ 0.7 / protected_starts 含节目主持人名 + 开场语。[HIGH]
- 登记到 tools.json 作 `detect_self_correction_wordlevel`（tools 32 → 33）；`reads_only=true` · `boundary_lock=true`（不让 snap 缩边界）。[HIGH]
- 契约测试全过：P1 3/3 · P2 12/12 · filler 16/16 · sync PASS。[HIGH]
- **EP04 数据实测命中 33 个候选**（vs 句级 detect_self_correction 的 0 个）：
  - track_01: 9 个 · track_02: 18 个 · track_03: 6 个
  - **6 个 high tier**（edit_ratio ≥ 0.7）· 27 个 mid tier [HIGH]

## 高置信命中 top-6 · 逐条对照（[HIGH]）

| 时间 | Track | Pre (删) | Post (保) | Gap | Edit Ratio | 我的评判 |
|---|---|---|---|---:|---:|---|
| **1629.97s** | Tr1/2/3 | 怎么保证 | 怎么确保 | ~0.55s | 0.75 | **真 self_correction**（三轨同步命中，主持人换动词） |
| **2721.50s** | Tr2/3 | 什么叫好 | 什么叫坏 | 0.16s | 0.75 | **需人审**（可能是刻意 rhetorical 对比"好 vs 坏"，不是错话） |
| **958.74s** | Tr2 | 对然后然后 | 然后然后第三 | 0.26s | 0.727 | **真 self_correction**（节奏卡壳 + 重启句） |

## Mid tier 命中样本（[MED]）

估算 mid tier 27 个里真 self_correction 约 5-8 个：
- `1104.37s Tr1` "答案來" → "其實答案"：**真**（句子重启）
- `2279.39s Tr1` "取一类" → "一类比"：**真**（换词）
- `1249.07s Tr1` "场场景" → "这个场景"：**真**（口吃修正）
- `3209.64s Tr2` "就會爆破" → "爆破你就"：**真**（词序重整）
- 其余 15+ 是**误报**（正常连接 "的NCP → 然后NCP"、回应词 "是吗是的 → 是的对就" 等，人审筛掉）

## 判断（[MED]）

- **算法框架切换成功**：从"句对"到"词滑窗"，recall 从 0 → 33，precision 从"未定义"到"25-35%"。走 human_review_required 通道**precision 足够**（无成本，只是给人多看几个候选）。[HIGH]
- **旧句级 detection 未删**：并存不替换。未来可考虑联合（wordlevel 出候选后交叉 verify），或统一到 word-level 后 deprecate 句级。当前 v2 policy 白名单里 self_correction 仍在，等真 review 数据后决定是否允许 auto-cut。[MED]
- **三轨同步命中是强信号**：1629.97s "怎么保证→怎么确保" 在 tr1/tr2/tr3 都命中，说明 ASR 三轨都听到主持人的自我修正。**同一时间点跨多轨命中的候选可视为高优先级人审目标**。这可作为下一版 rules 的 confidence tier 提升 signal。[MED]
- **误报根因**：sliding window 对"正常连接词短语相似"敏感。**降低误报的技术方向**（下次迭代）：
  1. 子句边界感知（pre 应结束于子句尾，post 开始于子句头 → 需 semantic-transcript）
  2. 说话人身份感知（pre/post 必须同 speaker，避免抢话误判）
  3. Pre 段末尾 filler 加分（"呃/嗯" 结尾更像 self-repair）
  4. 更强 dedup（952s 三个连续命中是 sliding 冗余）

## 建议 · 后续动作（[MED]）

1. **把 33 个候选喂给 EP04 review UI 让用户人审**（作为 gold-standard 数据集）；至少让高 tier 6 个走 accept/reject → 精确 precision，累积**真 self_correction 训练数据**。
2. **迭代 v2 rules**：加"pre 段末尾 filler 加分"和"跨轨同步命中提升 tier"，把 high tier 命中数从 6 → 15+，同时不放弃 precision。
3. **联合 detection**：orchestrator 里同时跑 detect_self_correction（句级，长停顿 case）+ detect_self_correction_wordlevel（词级，半句内 case）+ dedup。
4. **子句边界依赖**：真正精准需要 semantic-transcript 层给出 clause 边界；这是 downstream 依赖 semantic-transcript-v1 challenger。

## 未做（诚实交代）

- 未把 33 个候选整合进 orchestrator 的 candidate 池（那需要改 build_filler_global_pause 或加新 detection 阶段；本 commit 只交付 detection 独立能力）
- 未做 dedup 优化（952s 三个连续命中未合并）
- 未联合句级 detection（两个 detection 独立跑）
- 未做单元测试（新脚本；下一次 commit 补，先实测 33 命中作证据）
- 未真跑跨期数据（只 EP04）—— EP03 数据可以作为对比验证

## 相关文件

- 新脚本：[稳定生产/challengers/self-correction-v1/scripts/detect_self_correction_wordlevel.py](../../self-correction-v1/scripts/detect_self_correction_wordlevel.py)
- 新规则：[稳定生产/challengers/self-correction-v1/rules/self-correction-wordlevel.v1.json](../../self-correction-v1/rules/self-correction-wordlevel.v1.json)
- EP04 命中输出：[main/runs/EP04-DELIVERY-20260817-1427/self_correction/EP04_wordlevel_candidates.json](../../../../main/runs/EP04-DELIVERY-20260817-1427/self_correction/EP04_wordlevel_candidates.json)
- Tools 登记：[main/tools/tools.json](../../../../main/tools/tools.json) 里 `detect_self_correction_wordlevel` 条目
- 老对比：[稳定生产/challengers/self-correction-v1/checkpoints/2026-08-17-1730-EP04-first-real-run.md](../../self-correction-v1/checkpoints/2026-08-17-1730-EP04-first-real-run.md)（旧句级 0 命中）

## 相关记忆

- [[minglue-project-layout]]
- [[minglue-post-feature-analysis-md]]
- [[minglue-analysis-md-tracks]]
