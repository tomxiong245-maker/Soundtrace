# EVENING MANIFEST · 2026-08-19 · LLM-First 架构落地

## 用户签字
- **project_owner**: 熊镇正 · 2026-08-19 evening · 明确:
  - "只用 LLM 负责 candidate"
  - "cut-verify 后面用 Optuna 和 benchmark"
  - "用 subagent 跑就好了"
  - "冻结 cough_like"
  - "长停顿保持 1.5s"
- **上台分享**: 2026-08-19 明天 (需 A/B demo · 5+ 例子)

## 架构决策 · 完整清单

### 1. LLM 是唯一候选决定者 (最核心)
- **谁判**: Claude subagent (workflow LLM · workflow 里天然是 LLM)
- **怎么调**: 3 mode 优先级
  - Mode 1 · claude CLI (subprocess) · 无 API key · 无 SDK · 首选
  - Mode 2 · Anthropic API (SDK + KEY) · 备选
  - Mode 3 · candidates_with_context.json · 半自动 fallback
- **prompt**: 前 5s + 剪的词 + 后 5s + kind · 输出 JSON {verdict, reason, confidence}
- **中文特殊判决原则**: "就是"/"这个"/"那个" 语境敏感 · 句号后新句 REJECT · 跨说话人 REJECT · 长停顿话轮转换 REJECT
- **证据文件**:
  - `<PROJECT_ROOT>/skills/candidate-semantic-veto/scripts/llm_semantic_filter.py`
  - `<PROJECT_ROOT>/skills/candidate-semantic-veto/SKILL.md`
  - `<PROJECT_ROOT>/交付/最终交付文档/新skill/candidate-semantic-veto/`

### 2. Pipeline Stage 改动
| Stage | 改动 | 证据 |
|---|---|---|
| 3.4 pyannote | 保留 · 依然生成 RTTM · 供 speaker_turnover_guard 用 | `稳定生产/pyannote_stage.py` |
| 3.6 autocut_gate | **语义门全让位** · 只留结构性门 (speaker_role · source_track · G6_duration · review_budget) | `稳定生产/autocut/gates.py` |
| **3.7 (新) LLM filter** | 唯一候选决定者 · 生成 llm_verdicts.json | `skills/candidate-semantic-veto/scripts/llm_semantic_filter.py` |
| 4.5 cut-verify | 前 4 项 check 保留 · **verdict 降级为诊断** · 不影响 EDL | `稳定生产/challengers/iterative-cut-refinement-v1/scripts/content_verify_cut.py` |
| 5 EDL | **只从 llm_verdicts.json 读 KEEP_CUT** · 不再从 cut-verify verdict 决定 | `稳定生产/edl_builder.py` |
| 6.7 Optuna | 保留 · 只做**参数级**优化 (crossfade / pause / boundary_offset / head_pad / room_tone) | `稳定生产/optuna_params.py` |
| 6.5 NISQA benchmark | 保留 · 客观打分 | `稳定生产/nisqa_bench.py` |
| 6.8 case_embedding | 保留 · Whisper encoder + FAISS 27 mentor case · 作 sidecar (未来接入 gate) | `scripts/build_case_embeddings.py` |
| 6.10 Optuna re-render | 保留 · converged 参数回写 · automix 重跑 | `稳定生产/optuna_rerender.py` |

### 3. 让位的规则 (弱智规则淘汰)
- G3_no_preserve · 让位 LLM (diagnostic only) · `稳定生产/autocut/gates.py::g3_no_preserve`
- **G5_history** · 让位 LLM · MINGLUE_G5_DISABLED_WHEN_LLM=auto (default) · `稳定生产/autocut/gates.py::g5_history`
- G7_session_feedback · 让位 LLM · 但保留 hard override · `稳定生产/autocut/gates.py::g7_session_feedback`
- G7_protection · 让位 LLM (opening/closing 判定) · `稳定生产/autocut/gates.py::g7_protection`
- cut-verify Check 1-4 verdict 决定 EDL · 让位 LLM · `稳定生产/challengers/iterative-cut-refinement-v1/scripts/content_verify_cut.py`

### 4. Candidate 精度 bug 修
- filler weak_tokens **移除 "就是" 和 "就是說"** (中文内容词歧义) · `稳定生产/autocut/candidate_generators.py::FILLER_WEAK_TOKENS`
- self_correction 加 **sentence_boundary_veto** (句号后新句 skip) · `稳定生产/autocut/candidate_generators.py::self_correction`
- global_long_pause 阈值**保持 1.5s** (用户明确) · `稳定生产/autocut/candidate_generators.py::GLOBAL_LONG_PAUSE_S`
- cough_like **disable** (33/36 误报 · 中文爆破辅音误判) · `稳定生产/autocut/candidate_generators.py::COUGH_LIKE_ENABLED`
- ASR veto **unknown classification 也算 speech** · ratio 0.5 → 0.3 · `稳定生产/autocut/veto.py::asr_veto`

### 5. Case Embedding 首次落地
- Build index · 27 mentor gold case (EP03 11 + EP04 16)
- Whisper base encoder · 512 dim · FAISS IndexFlat
- EP05 检索 24 windows · top-1 similarity 0.87-0.92 · 全 accept 命中
- 状态: sidecar (未接 G8 gate · 未来集成)
- **证据文件**:
  - `<PROJECT_ROOT>/scripts/build_case_embeddings.py`
  - `<PROJECT_ROOT>/稳定生产/case_bank/mentor_cases_v1.faiss`
  - `<PROJECT_ROOT>/稳定生产/case_bank/mentor_cases_v1.meta.json`

### 6. 新脚本 · 落地
| 脚本 | 位置 |
|---|---|
| llm_semantic_filter.py | `<PROJECT_ROOT>/skills/candidate-semantic-veto/scripts/llm_semantic_filter.py` |
| content_verify_cut.py | `<PROJECT_ROOT>/稳定生产/challengers/iterative-cut-refinement-v1/scripts/content_verify_cut.py` |
| re_iterate_from_audit.py | `<PROJECT_ROOT>/稳定生产/challengers/iterative-cut-refinement-v1/scripts/re_iterate_from_audit.py` (今晚早前) |
| build_case_embeddings.py | `<PROJECT_ROOT>/scripts/build_case_embeddings.py` (已存在 · 今晚首次跑 build) |

### 7. 新 skill · 落地
| Skill | 位置 |
|---|---|
| candidate-semantic-veto | `<PROJECT_ROOT>/skills/candidate-semantic-veto/` + `<PROJECT_ROOT>/交付/最终交付文档/新skill/candidate-semantic-veto/` |

### 8. LLM Demo 数字 (EP05 前 5min)
- 候选生成 · rules 出 36 (33 cough 误报 + 3 语义)
- LLM 判决 · 3 中 1 KEEP + 2 REJECT · 全 high confidence
- 与用户直觉 100% 一致
- 扩展候选池 · rules-free 扫 28 位置 · LLM 判 14 KEEP + 7 REJECT + 7 REVIEW
- **rules pipeline 不可达的精度**: 7 REJECT 全是语义连词/过渡/话轮转换
- **证据文件**:
  - `<PROJECT_ROOT>/audio/podcast_final/EP05/llm_verdicts.json`
  - `<PROJECT_ROOT>/audio/podcast_final/EP05/candidates_with_context.json`
  - `<PROJECT_ROOT>/audio/podcast_final/EP05/llm_demo_ab.md`

## 上台可讲的一句话
"以前 rules 出候选 · 规则堆到 20+ 层 · 越堆越弱智。今天 · LLM 唯一决定 · 语义级判断 · 结构性门保留物理约束 · 每一层各司其职。规则不死 · 只做召回和物理约束 · 语义判决全归 LLM。"

## 待验证假设 (下期验)
- LLM Mode 1 claude CLI · pipeline 里从用户 shell 直调 · env 无污染
- G5 让位后 · 无 hr>0 触发 · 让位效果需下期数据验
- case_embedding 未接 G8 · 未来评估是否用它增强 LLM 判决
- pyannote DYLD env · 需在 run_end_to_end.py 里 subprocess env 注入 (未落地)
