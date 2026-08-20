# learning-pattern-from-case-v1 · 接手者路线图 (HANDOFF_ROADMAP)

**建档日期**: 2026-08-20
**当前状态**: 阶段 1 · 骨架 · 不接 pipeline
**长期定位**: **未来取代 experience-ingestion 的候选层 (PREFERENCE) 学习框架**
**严格边界**: **只学候选 · 一字不动参数**

---

## 〇、上下游全景 (2026-08-20 补 · 用户明确)

用户 2026-08-20 明确 skill 定位:
> "你现在开发的 skill 直接把整个 preference 包进去好不好. 上游就是从 case 和审查中学习. 下游就是给 llm 的文件或者直接指挥 llm."

即 **本 skill 是整个 PREFERENCE 层学习的总入口** · 不只是"pattern 蒸馏"的子步骤.

```
上游 (输入 · 3 源)                             下游 (输出 · 2 出口)
────────────────                              ────────────────
case_store/cases/EP*.jsonl        ┐           output/pattern_summary.md
                                  │            (LLM 读的语义模式 md)
learned_examples_EP*_MENTOR.md    ├──▶ SKILL ──▶ 
                                  │           
human_decisions*.json (人审)      ┘           LLM Stage 3.5.5 prompt
                                              直接注入 (阶段 3 激活)
```

**关键限制**:
- 上游**不包括** session_feedback.jsonl (那是 PARAMETER 单一 SOT · §20 · feedback-engine 独占)
- 下游**不包括** 任何数值输出 · cut_parameters.json 修改 · session_feedback append

---

## 一、为什么要建这个 challenger

用户 2026-08-20 明确决策 (原文引用):
> "你做好 learning-pattern-from-case-v1 取代现有学习框架的准备. 我们按照这个去开发, 帮助后续接手者准备好, 然后相信后人的智慧. 需要明确的是, 参数和候选一定需要分离. 我们现在只负责候选. 所以如果取代掉的流程设计参数, 你要保证参数部分正常运行."

**背景**:
- experience-ingestion (老学习框架) 输出以数字统计为主 (accept 率 · data_points · training_readiness · 恒 NOT_READY)
- 用户明确的新方向: **无量化 · narrative · 从 case 蒸馏语义模式** (给 LLM 看的 markdown · 不是给算法的数字)
- 与新方向哲学冲突, 但一刀切风险大 (老框架里 apply_preference_snapshot + build_preference_snapshot 是 Champion 直接消费的活链路)
- 故走 challenger 路径: 骨架先就位 · 未来接手者补齐主体 · A/B 验证后再逐步下架老框架

---

## 二、PARAMETER 与 PREFERENCE 分层的硬边界

### 我们负责: PREFERENCE 层 (候选 · 剪哪些)

| 输入 | 输出 |
|---|---|
| `case_store/cases/EP*.jsonl` · `learned_examples_EP03_MENTOR.md` | `pattern_summary.md` (narrative · 无数字) |
| case 里的 `candidate.reason_key` · `label.decision` · `deleted_text` · `evidence_text` | 语义描述, 例如: "mentor 常删啊/嗯/呃这类无信息 filler · 常保留有情绪或强调的重复" |

### 我们**不能碰**: PARAMETER 层 (参数 · 怎么剪)

**硬 checklist · 每次代码提交都必须重跑此 checklist**:

- [ ] **不 import** `feedback_engine.load_cut_parameters()`
- [ ] **不 import** `feedback_engine.retrieve_before_decision(knowledge_category="PARAMETER")`
- [ ] **不读** `main/knowledge/cut_parameters.json`
- [ ] **不读** `main/knowledge/session_feedback/current.session_feedback.jsonl` (那是 PARAMETER 单一 SOT · 归 feedback-engine 独占)
- [ ] **不写** 任何 `crossfade_ms` · `pause_ms` · `boundary_offset` · `gap_before_ms` · `gap_after_ms` · `rms_at_boundaries_db` · `insert_silence_samples` 数值
- [ ] **不修改** `cut-verify` skill 的任何产物
- [ ] **不修改** `experience-driven-review` skill 的任何产物 (那是 PARAMETER 层的 LLM 软判)
- [ ] **不修改** `analyze_cut_plans.py` · `cut_plan_diff.json`
- [ ] 输出 md **严格无数字** (case_id · reason_key · 数量除外)
- [ ] 每条 pattern 必须能追溯到具体 case_id (可回溯性 · 但不带任何数值统计)
- [ ] **不读** human_decisions*.json 的 PARAMETER 相关字段 (只读 candidate.reason_key · label.decision · label.feedback · label.review_basis · 忽略 EDL 里的 sample 数值)

**违反任一条 = challenger 阶段 3 不予激活 · 主 pipeline 拒接**.

### PARAMETER 层未来活代码位置 (供参考 · 严禁修改)

- `main/knowledge/cut_parameters.json` (数值权威)
- `main/knowledge/session_feedback/current.session_feedback.jsonl` (§20 单一 SOT · feedback-engine 独占)
- `main/orchestrator/feedback_engine.py` (PARAMETER 读写单入口 · CLAUDE.md §18)
- `skills/cut-verify/SKILL.md` (20 数值参数 · 唯一权威口径)
- `skills/experience-driven-review/scripts/analyze_cut_plans.py` (6 PARAMETER 指标 LLM 软判)

---

## 三、4 阶段路线图

### 阶段 1 · 骨架 (**当前 · 已完成**)

- SKILL.md · README.md · TASK_CONTRACT.md 就位
- `scripts/extract_pattern_from_cases.py` 骨架 (~87 行 · main() 占位)
- `tests/test_smoke.py` PASS
- **不接 pipeline · 不 import 任何 champion 模块**
- output/ 目录空

**验收标准**: 
- `ls` 目录结构与本文件一致
- `python3 tests/test_smoke.py` PASS
- `grep -r 'feedback_engine\|cut_parameters\|session_feedback' scripts/` 返回 0 行

### 阶段 2 · 主体实现 (**接手者负责**)

**目标**: 让 `extract_pattern_from_cases.py` 真正跑出 pattern_summary.md.

**输入**:
- `case_store/cases/EP03.jsonl` (11 case · 活)
- `learned_examples_EP03_MENTOR.md` (56 mentor 反推位置)
- **不读** EP04.jsonl.FROZEN_2026-08-19 (用户 2026-08-19 明确冻结 · 语义识别当时太差 · 怕污染训练集)

**处理**:
- 按 `reason_key` (filler_hesitation / immediate_repetition / global_long_pause) 分桶
- 按 `decision` (accept · 保留 vs reject · 剪掉) 分桶
- 每桶挑代表性 3-5 case 的 `deleted_text` + `evidence_text`, LLM 蒸馏语义模式 (**narrative · 无数字**)
- 输出格式:
  ```
  ## reason_key: filler_hesitation
  ### decision: reject (mentor 剪掉)
  - 模式: 短促无信息 filler · 例 "呃" "嗯" "啊" 单独出现在句间
  - 例子: [case-id-x] "然后呃我们..." · [case-id-y] "所以嗯就是..."
  ### decision: accept (mentor 保留)
  - 模式: 情绪 filler 或语气强调
  - 例子: [case-id-z] "呃 这个真的很难说" (情绪停顿, 保留)
  ```

**验收标准**:
- 输出 md 通过 `grep -E '[0-9]+\.[0-9]+|[0-9]+%|rate|score|precision|recall' output/pattern_summary.md` **零命中** (无数字统计)
- 每条 pattern 至少引用 1 个 case_id
- `cd scripts && python3 extract_pattern_from_cases.py --dry-run` 完整跑通
- 手工审 pattern_summary.md · mentor / 用户认为语义描述"是那么回事"

### 阶段 3 · LLM Stage 3.5.5 接入 A/B (**接手者负责 · 需用户批准**)

**目标**: 在 candidate-semantic-veto 的 LLM prompt 里追加 `pattern_summary.md` 内容 · 与旧 case_memory.json 相似 case 并列.

**必要动作**:
1. 修改 `稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py` Stage 3.5.5
2. 增加 CLI flag `--use-pattern-summary` (default OFF · challenger 阶段不激活)
3. 加一个 A/B 对照跑: same run · 一次 `--use-pattern-summary=false` · 一次 `--use-pattern-summary=true`
4. 对比 verdict 差异 · 记录 LLM 是否引用了 pattern_summary 的模式描述

**验收标准**:
- A/B 差异有据可查 (对比 md)
- LLM 的 reasoning 里能引用 pattern 里的具体 case_id
- 无 pipeline 崩溃 · 无 PARAMETER 越界

### 阶段 4 · 稳定后逐步下架 experience-ingestion PREFERENCE 层 (**远期 · 相信后人智慧**)

**触发条件**: 阶段 3 稳定运行 3 个 episode 以上 · mentor 认可 pattern 质量 · 或 mentor gold ≥ 100 case 门槛达成.

**动作范围** (**只删 PREFERENCE 层 · 保留 apply/build snapshot 那条 Champion 活链路**):
- 归档: `experience_consumer_adapter.py`
- 归档: `consume_experience_cases.py` (数字统计)
- 归档: `check_training_readiness.py` (恒 NOT_READY 门禁)
- 归档: `collect_experience_cases.py` (数据已冻)
- 删产物: `reports/experience_summary.json/.md` · `rule_recommendations.json` · `training_readiness.json`
- 删链: `create_policy_promotion_evidence.py` (唯一还引 adapter 的地方 · 反正 promotion 没跑过)
- 更新 skill: `learning-and-experience/SKILL.md` 移除 4 个已归档脚本引用 · 加 `extract_pattern_from_cases`

**保留不动**:
- `apply_preference_snapshot.py` (label_learning_driver 直接 import)
- `build_preference_snapshot.py` (`/api/save` hook + label_learning_driver)
- `build_policy_cards.py` · `classify_feedback.py` (前者的子模块)

**验收标准**:
- online refresh 闭环 (`/api/save` → snapshot 更新 → active_label_learning_snapshot.v1.json pointer 交换) 仍工作
- `test_learning_loop.py` · `test_preference_snapshot_loop.py` 仍 PASS (删掉的 test_experience_ingestion.py 部分测试如 test_13/test_15/test_16 同期删)
- PARAMETER 链路完全未动 (grep audit)

---

## 四、目录与文件清单

```
learning-pattern-from-case-v1/
├─ SKILL.md                    · challenger status · 触发词 · frontmatter
├─ README.md                   · 概要 + 何时用
├─ TASK_CONTRACT.md            · 输入输出契约
├─ HANDOFF_ROADMAP.md          · 本文件 (接手者路线图 · PARAMETER 硬边界)
├─ scripts/
│  └─ extract_pattern_from_cases.py   · 骨架 (阶段 1) → 主体 (阶段 2)
├─ output/                     · pattern_summary.md 落地位置 (阶段 2 后有内容)
└─ tests/
   └─ test_smoke.py            · 阶段 1 冒烟 · PASS
```

活代码镜像: 稳定生产/challengers/learning-pattern-from-case-v1/ (由 workflow w6xz15ukp 补建 · 2026-08-20)

---

## 五、被取代方 (experience-ingestion) 分层归属证据

experience-ingestion 8 个脚本 100% 是 PREFERENCE 层, 零涉及 PARAMETER:

| 脚本 | 字段类型 | 层 |
|---|---|---|
| collect_experience_cases.py | label.decision · candidate.reason_key | PREFERENCE |
| consume_experience_cases.py | human_accept_rate_by_reason · by_decision | PREFERENCE |
| build_preference_snapshot.py | signal: historical_reject/accept | PREFERENCE |
| build_policy_cards.py | feedback_class: semantic_keep/cut/false_positive | PREFERENCE |
| classify_feedback.py | asr_error/execution_issue/semantic_keep/cut/false_positive | PREFERENCE |
| apply_preference_snapshot.py | review_priority (无数值) | PREFERENCE |
| check_training_readiness.py | case count 门槛 | PREFERENCE |
| experience_consumer_adapter.py | 数字统计 API | PREFERENCE |

零字段涉及: crossfade_ms · pause_ms · gap_before_ms · gap_after_ms · boundary_offset · RMS · insert_silence_samples.

**结论**: 未来即使 experience-ingestion 全下架 · PARAMETER 链路 (cut_parameters.json + session_feedback + feedback_engine + cut-verify + experience-driven-review) 一点不受影响.

---

## 六、给接手者的话

1. **先读**: 本文件 · 上级 SKILL.md · learning-and-experience/SKILL.md · CLAUDE.md 六条元规则
2. **先看**: `case_store/cases/EP03.jsonl` 前 5 行 · `learned_examples_EP03_MENTOR.md` 前 10 位置
3. **先测**: `python3 tests/test_smoke.py` 应 PASS
4. **再改**: `scripts/extract_pattern_from_cases.py` 从 skeleton 走向 阶段 2 主体
5. **每次提交前**: 重跑 §二 PARAMETER 硬 checklist 8 条
6. **发现问题**: 更新本文件 · 不要绕过 challenger 状态直接接主 pipeline
7. **相信自己的判断**: 用户 2026-08-20 明说"相信后人的智慧". 若发现某条本文件的假设不对 · 记录你的判断 + 证据 · 前进.

---

## 七、三档诚实标注

### 已验证事实
- experience-ingestion 8 个脚本字段类型统计 (2026-08-20 workflow 深挖 · SKILL.md + code grep 双重证据)
- learning-pattern-from-case-v1 骨架已就位 (SKILL.md · README.md · TASK_CONTRACT.md · scripts skeleton · tests PASS)
- PARAMETER 层活代码位置 5 处 (grep 全项目 · 与 experience-ingestion 零 import)
- EP04 case_store 已冻 (`EP04.jsonl.FROZEN_2026-08-19`)
- Round 1 已完成: `extract_gold_cut_features` 全套登记删除 · 脚本归档

### 已决定的方向
- learning-pattern-from-case-v1 走 4 阶段路线图
- 只学 PREFERENCE 层 · 不动 PARAMETER 层
- 未来下架 experience-ingestion 只碰 PREFERENCE 部分 · 保留活链路
- 阶段 2 前不接 pipeline · 阶段 3 前 default OFF · 阶段 4 前不下架任何东西

### 待验证假设
- 阶段 2 pattern 蒸馏是否能 mentor 认可 (需真跑 EP03)
- 阶段 3 A/B verdict 差异是否显著 (需 EP05+ 数据)
- 阶段 4 门槛 `mentor gold ≥ 100 case` 是否合适 (可能 50 就够 · 也可能 200 才稳)
- 若 mentor 一直不给新 gold · 阶段 3/4 无法激活 → 接手者需要评估: 是否需要另外的触发条件

