# 学习流选择器 · 三条 SOP 用哪一条

**用户 2026-08-18 明确要求**:
> "今后有新的可以作为答案出现的东西,进行学习(就我们的三个学习流,自己去找应该用哪个),然后沉淀进来"

**背景**:项目积累了 3 条独立学习 skill,各自适用不同 shape 的新数据 · 但没有"该用哪一条"的路由 · 造成 agent 每次都自己写脚本(违反 CLAUDE.md §11)。

---

## 三条学习流对比表

| Skill | 输入 shape | 输出目标 | 何时用 |
|---|---|---|---|
| **feedback-engine** (`analyze` 方向) | 单条真人反馈 (verdict + note) | append 到 `current.session_feedback.jsonl` 或 route 到 TOOL_APPLY / DOC_REFERENCE | 用户 chat 里 "GF5 是另一位说话人的嗯 不应该剪" · 单条纠错 |
| **label-learning-driver** | 多条 `human_decisions.json` 里成对 accept/reject | `preference_snapshot` (case-store) + shadow prediction | 一批人审做完 · 想让机器学 accept/reject 判别边界 |
| **editing-experience-distiller** | Mentor 剪辑成品 (gold EDL) · 或已归档的多期审核结果聚合 | 经验卡 + 下一轮 Challenger 假设 + `preferences_for_agent.md` | 拿到 mentor 的 mp3 或 human_approved.edl · 想知道 "mentor 怎么剪 · 系统学到了什么" |

---

## 路由决策树

```
新的 "答案" 数据到手
    │
    ├── 是单条 chat 反馈? (verdict + note · N=1)
    │      → feedback-engine analyze → append session_feedback (最后手段前先 TOOL_APPLY / DOC_REFERENCE)
    │
    ├── 是一批 accept/reject 对? (human_decisions.json · N≥5)
    │      → label-learning-driver → preference_snapshot + shadow
    │
    ├── 是 mentor 剪辑成品? (gold EDL · 或 mp3+deleted_text · N 任意)
    │      → editing-experience-distiller → 经验卡 + Challenger
    │
    └── 三者兼有? (多期归档 · chat 反馈 · mentor 成品)
           → 先跑 label-learning-driver (量化 discriminator)
           → 再跑 editing-experience-distiller (提炼经验)
           → 最后 feedback-engine analyze (routing 每条 chat)
```

---

## 每条流的入口 tool 与命令

### 1. feedback-engine (retrieve + analyze)

```bash
# 决策前 · retrieve
python main/orchestrator/feedback_engine.py retrieve \
    --candidate-json '{"filler_token":"呃","reason_key":"filler_hesitation"}' \
    --decision-type cut_boundary --episode-id EP04

# 决策后 · analyze (含 TOOL_APPLY / DOC_REFERENCE / PATCH 三级路由)
python main/orchestrator/feedback_engine.py analyze \
    --candidate-json '...' \
    --verdict "never_cut" \
    --note "一些不应该剪 · 内容词" \
    --apply
```

SKILL: [skills/feedback-engine/SKILL.md](../skills/feedback-engine/SKILL.md)

### 2. label-learning-driver

```bash
# 冻结 snapshot
python main/orchestrator/label_learning_driver.py backtest \
    --snapshot-dir main/runs/LABEL-LEARNING-v3-20260816/preference_snapshot \
    --out main/runs/<NEW-LEARNING-RUN>/backtest_report.json

# shadow predict
python main/orchestrator/label_learning_driver.py shadow \
    --snapshot ... --target-review-package ...
```

SKILL: [skills/label-learning-driver/SKILL.md](../skills/label-learning-driver/SKILL.md)

### 3. editing-experience-distiller

```bash
python 稳定生产/challengers/experience-ingestion-v1/scripts/experience_consumer_adapter.py \
    --case-store main/runs/LABEL-LEARNING-v3-20260816/preference_snapshot \
    --reason-key filler_hesitation \
    --out main/runs/<NEW-DISTILL-RUN>/experience_query.json
```

SKILL: [skills/editing-experience-distiller/SKILL.md](../skills/editing-experience-distiller/SKILL.md)
产物示例: [skills/editing-experience-distiller/output/preferences-20260815-1330/preferences_for_agent.md](../skills/editing-experience-distiller/output/preferences-20260815-1330/preferences_for_agent.md)

---

## 学到的知识分两块存

### PARAMETER (决定怎么剪) · 工具直接消费

- 存储: `main/knowledge/cut_parameters.json`
- 消费者: `generate_comprehensive_cut.py` · `generate_ab_clip_learning_driven.py` · `apply_autocut_gate.py`
- 例: crossfade_ms 默认值 · gap_before target range · RMS soft/hard 阈值
- 更新: 主要由 editing-experience-distiller (从 gold 剪辑学) + label-learning-driver (从大量 accept/reject 校准)

### PREFERENCE (决定剪哪些) · 决策前 retrieve

- 存储: `main/knowledge/session_feedback/current.session_feedback.jsonl` (v2 schema · `knowledge_category` 字段)
- 消费者: `feedback_engine.retrieve_before_decision()` · `apply_autocut_gate` G7
- 例: never_cut 修辞重复 · needs_extension chain 保留最后 · semantic_boundary_primary_target
- 更新: 三条 flow 都可以 append PREFERENCE 类 rule

---

## 补丁滥用防线 (feedback-engine §18)

每次 analyze 严格顺序:
1. **TOOL_APPLY** (confidence 0.9) · 查 `tools.json` 50 项 tool 能否解决
2. **DOC_REFERENCE** (confidence 0.7) · 查 F01-F10 / YouTube 学习总结 / Preflight
3. **SESSION_FEEDBACK_PATCH** (confidence 0.5 · 最后手段) · append

违反 = 补丁滥用 · session_feedback 会失控膨胀。

---

## 三学习流合作示例 (今日 EP03+EP04 学习实际路径)

1. **editing-experience-distiller** 思路:mentor gold EDL (EP03 56 + EP04 3) 反推剪辑决定 · 提炼经验卡
   - 提取 gold cut 特征 (`extract_gold_cut_features.py` · 新建 tool)
   - 5 lens workflow (WHERE-boundary / WHERE-cross / HOW-crossfade / HOW-RMS / SEMANTIC)
   - 合并为 9 条 rule proposal

2. **label-learning-driver** 思路:EP03-review-product-v1 · 熊镇正 11 条真人 accept/reject 对
   - 已提炼:cross_track_speaking / 修辞重复 / 独讲重复 · 3 条 discriminator
   - 已 append 到 session_feedback (PREFERENCE)

3. **feedback-engine analyze**:每条新 rule 走 TOOL_APPLY 检查
   - `crossfade_by_category` → TOOL_APPLY (generate_comprehensive_cut.py 读 cut_parameters.json)
   - `emphasis_repetition_never_cut` → SESSION_FEEDBACK_PATCH (无对应 tool)
   - `boundary_offset_zone` → TOOL_APPLY (cut_parameters.json 更新)

最终产物:
- 9 条 workflow rule → 6 PREFERENCE + 3 PARAMETER
- PARAMETER → cut_parameters.json (新建 · 供工具消费)
- PREFERENCE → session_feedback.jsonl (append)
- 系统缺口 10 条 · Challenger 假设 10 条

---

## 违反 = 破坏契约

- **绕过三条流自己写学习脚本** → CLAUDE.md §11 违反 (装了的工具必须用)
- **PARAMETER 与 PREFERENCE 混一起** → 工具无法直接消费 · 需要人工挑
- **feedback-engine analyze 跳过 STEP 2/3 直接 patch** → 补丁滥用 (违反 §18)
- **不 sync 到交付包** → CLAUDE.md §20 违反 (单一 SOT)
