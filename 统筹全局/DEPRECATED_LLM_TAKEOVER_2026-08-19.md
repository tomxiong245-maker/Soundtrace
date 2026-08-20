# DEPRECATED · LLM Takeover · 2026-08-19

## 用户明确 · 2026-08-19 evening
"把过去的旧方法的文件全部冻结" · LLM 完全主导后 · rules-based candidate 生成/判决**不再消费**.

## 冻结原则

- **保留代码** · 不删 (未来若 LLM 挂 · pipeline 可 fallback)
- **不再消费其输出** (pipeline Stage 3.5.5 用 LLM 直接扫 transcript)
- **每旧文件加 FROZEN 头注释** (5 行说明)
- **禁止未来使用** (除非明确 opt-out LLM 主导)

## 冻结清单 · 5 类

### 1. Rules-based Candidate Detectors (核心冻结)
- `稳定生产/challengers/filler-global-pause-v1/scripts/build_filler_global_pause_review_source.py`
- `稳定生产/challengers/self-correction-v1/scripts/detect_self_correction.py`
- `稳定生产/challengers/self-correction-v1/scripts/detect_self_correction_wordlevel.py`
- `稳定生产/challengers/transient-events-v1/scripts/detect_transient_events.py`
- `稳定生产/scripts/generate_cut_candidates.py` (基础 candidate 生成)

**冻结原因**: LLM 从 transcript 直接扫 · 语义级 · 精度高.

### 2. Rules JSON (词表 · 阈值)
- `稳定生产/challengers/filler-global-pause-v1/rules/candidate-generation.filler-global-pause-v1.json`
- `稳定生产/challengers/filler-global-pause-v1/rules/candidate-generation.filler-global-pause-v13.json`
- `稳定生产/challengers/filler-global-pause-v14/rules/candidate_rules.v18.json`
- `稳定生产/challengers/self-correction-v1/rules/self-correction-wordlevel.v1.json`
- `稳定生产/challengers/transient-events-v1/rules/transient-events.v1.json`

**冻结原因**: LLM 不看词表 · 靠语义.

### 3. autocut_gate 语义门 (今晚已让位 · 现在完全冻结)
- `稳定生产/challengers/autocut-gate-v1/scripts/apply_autocut_gate.py` (部分冻结 · **保留结构性门** speaker_role / source_track / G6_duration / review_budget · 其它 G3/G5/G7 冻结)

**冻结原因**: LLM 判 KEEP_CUT · 语义门冗余.

### 4. cut-verify 前 4 项 verdict 决定 EDL 的路径
- `skills/cut-verify/scripts/verify_cut_plan.py` (代码保留 · 但 verdict 不再影响 EDL · 只作诊断)

**冻结原因**: LLM 判决先于 cut-verify · cut-verify 只做参数验证.

### 5. content_verify + iterative-cut-refinement 里的 rules-based verdict 判断
- `稳定生产/challengers/iterative-cut-refinement-v1/scripts/iterate_until_clean.py` (rule-based iterate 路径 · 保留但 Optuna 路径主用)
- `稳定生产/challengers/iterative-cut-refinement-v1/rules/refinement-policy-v1.json` (rules 保留 · 但 Optuna 主用)

**冻结原因**: Optuna 参数级迭代 · 不需要 rule-based iterate.

## 当前架构 (LLM 主导 · 保留冻结项作 fallback)

```
Whisper 转写 → transcript.json
                  ↓
     Stage 3.5.5 · LLM 全流程主导 ⭐
     ├── llm_full_pipeline.py 扫 transcript
     ├── 一步 · 发现 + 判决 + confidence
     └── 输出 llm_verdicts.json (KEEP_CUT list)
                  ↓
     Stage 3.6 · autocut_gate (**只保留结构性门**)
     ├── speaker_role · 剪错人不行
     ├── source_track · 伪影不行
     ├── G6_duration · > 0.8s 剪不干净
     └── review_budget · 人审资源
                  ↓
     Stage 5 · EDL (只从 llm_verdicts KEEP_CUT)
                  ↓
     Stage 6 · Automix (denoised · 修完 bug 后)
                  ↓
     Stage 6.7 · Optuna (参数级 · 对 KEEP_CUT)
                  ↓
     Stage 6.5 · NISQA (客观分)
                  ↓
     Stage 6.10 · re-render (LLM ∩ Optuna 交集)
```

## 回退方案 (若 LLM 挂)

- llm_full_pipeline.py 若 3 mode 全挂 · Stage 3.5.5 silent skip
- Stage 5 EDL fallback 到老 rules path (backward compat 已在)
- **老 rules pipeline 全部代码保留** · 冻结只是"不消费" · 不是"删除"

## 冻结签字

- **project_owner**: 熊镇正 (开发者身份) · 2026-08-19 evening
- **原文**: "把过去的旧方法的文件全部冻结"
- **突破 M1**: 开发者身份直接冻结架构

## 未来重启使用条件

- 若 LLM 精度稳定验收 (EP06/EP07 端到端)
- 冻结项**可以物理删除** (不再需要 fallback)
- 需另开 GOLDEN_PATH_v2_MANIFEST 记录
