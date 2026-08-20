# PROMOTION MANIFEST · 2026-08-19 · 4 Challenger → Champion

## 签字

- **project_owner**: 熊镇正 (2026-08-19 用户明确 "我通过 · 我签字")
- **mentor**: 熊镇正代 mentor 确认 (用户 2026-08-19 明确 "mentor 也同意了")
- **independent_verification**: DEFERRED (未来 EP06/EP07 rerun 后补齐 · 用户 2026-08-19 明示接受此风险 · "赶紧接入")

## 晋升的 4 个 Challenger

1. **nisqa-cutverify-v1** → Champion (Stage 4.5 · cut-verify Check 5 + Stage 6.5 · 成品 benchmark)
   - 工具: check_nisqa_mos · compute_mos_delta · route_by_mos
   - 来源: Fraunhofer NISQA v2.0 · 90k+ 样本预训练 · MIT
   - 集成点: skills/cut-verify/scripts/verify_cut_plan.py + run_end_to_end.py Stage 6.5

2. **speaker-diarization-v1** → Champion (Stage 3.4 · pyannote RTTM 消费 · 替代能量启发式)
   - 工具: run_diarization · assign_word_speakers
   - 来源: pyannote-audio 4.0.7 · pyannote/speaker-diarization-community-1 · MIT · DER 12.2%
   - 集成点: run_end_to_end.py stage_pyannote_diarize + speaker_role_filter (default TRUE)

3. **iterative-cut-refinement-v1** → Champion (Stage 6.7 · Optuna TPE + warm start 迭代)
   - 工具: iterate_until_clean · optuna_refine · render_clip_for_iteration
   - 来源: Optuna 4.9.0 TPE · KDD 2019 Akiba et al. · MIT
   - Warm start 数据: mentor gold cut_parameters.json + YouTube learning session_feedback
   - 集成点: run_end_to_end.py Stage 6.7 (default TRUE · 5 次上限 · Tr1 空轨兜底)

4. **case-memory-embedding-v1** → Champion (Stage 6.8 · Whisper encoder + FAISS 案例检索)
   - 工具: build_case_embeddings · embed_candidate · retrieve_similar_cases
   - 来源: faster-whisper CT2 backend + faiss-cpu 1.15 · MIT
   - 集成点: run_end_to_end.py Stage 6.8 (default TRUE · index 未 build 时静默 skip)

## 集成入口 (全部 default ON · 无需 opt-in flag)

主入口: 稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py

默认调用链:
Stage 1 · Denoise + ASR + Automix (Champion)
Stage 3.4 · **pyannote-audio** RTTM · **PROMOTED · default TRUE**
Stage 3.5 · MFA 边界精修 (Champion)
Stage 3.6 · autocut_gate 7 门 (Champion)
Stage 4.5 · cut-verify 前 4 项 (Champion) + **NISQA Check 5 · PROMOTED**
Stage 5 · EDL (Champion)
Stage 6 · Render + Automix + Loudnorm (Champion)
Stage 6.5 · **NISQA benchmark · PROMOTED**
Stage 6.7 · **Optuna iterative refinement · PROMOTED · default TRUE**
Stage 6.8 · **case embedding retrieval · PROMOTED · default TRUE**
Stage 7 · audit report (Champion)

## 回滚方案

若晋升后发现 regression, 回滚步骤:
1. run_end_to_end.py 加 opt-out flag: --no-pyannote-enabled · --no-auto-iterate-refine · --no-auto-case-embedding
2. 4 项工具的 v2_status 从 "adapter_registered_promoted" 改回 "adapter_registered_skeleton"
3. verify.sh 重跑确认 verify 全绿
4. Champion pipeline 恢复到 pre-2026-08-19 行为

## 待验证假设 (三档语气)

**已验证事实**:
- 4 Challenger 契约测试全绿 (nisqa 16/16 · optuna_refine 智能测试 OK · pyannote 19/19 · embedding 10/10)
- NISQA 真跑 EP04 C007 · 5 维分正常
- pyannote 4.0.7 装 · community-1 license accepted · pipeline load 待 EP05 首跑验证
- Optuna TPE + warm start · code 就绪 · EP05 首跑验证

**已决定的方向**:
- EP05 首跑 · 4 Challenger 全 pipeline 端到端验证
- Tr1 空轨 · 系统鲁棒性测试

**待验证假设**:
- EP05 端到端 4 Challenger 稳定收敛
- pyannote community-1 中文 3 人 diarization 精度
- Optuna 5 次收敛率 (期望 70%+)
- case embedding index 首次构建 (若未预 build · Stage 6.8 skip)

## 2026-08-19 下午 · 增补: Stage 6.9 二轮 Optuna + 全走 Optuna 决策

### 新增 Stage 6.9 · 人审 REJECTED 触发第二轮 Optuna
- 触发: audit_verdicts.json 存在且 verdict=REJECTED
- 逻辑: re_iterate_from_audit.py 对每 REJECTED 候选跑 10 iter Optuna (skip_warm_start · seed=43)
- 目的: 探索第一轮 warm-start 未采样区 · 直到 NISQA benchmark 通过
- 兜底: 二轮 10 iter 仍 escape → verdict=SECOND_ROUND_ESCAPED → M3 元规则人审
- 集成: run_end_to_end.py Stage 6.9 · default TRUE · 用 --no-auto-second-round-optuna 关

### 全走 Optuna 决策 (关 Stage 4.5 Check 5)
- 用户 2026-08-19 明确 "全走 Optuna · 关 Check 5"
- Stage 4.5 前 4 项 check 保留 · Check 5 (NISQA gate) 默认关
- Stage 6.7 过滤条件放宽: 所有 verdict != REJECT 的候选都进 Optuna
- Warm-start 让好候选 1 iter 内 pruned() 早停 · 平均 30s/好候选
- 时间预算: 5min 音频 ~40 min · 全片 ~10 hrs
- 反开: --stage45-check5

### 新脚本
- 稳定生产/challengers/iterative-cut-refinement-v1/scripts/re_iterate_from_audit.py (新建)
- 最终交付镜像同路径

### policy 更新
- refinement-policy-v1.json 加 second_round block (max=10 · seed=43 · skip_warm_start=true)
- 双处同步

## 2026-08-19 evening · 增补: GOLDEN PATH FROZEN

- **开发者身份 · 突破 M1** · 图片架构直接冻结为 Champion 最终版
- 冻结 8-stage LLM-first 流水 (候选 rules → autocut_gate 4 结构门 → LLM Stage 3.7 唯一判决 → EDL → Automix → Optuna → NISQA → re-render)
- **verify 结果**: EP05-first5min-final-v2-20260819-1710 · 6 全通 · 2 部分通 (Stage 3.7 未在 pipeline 触发 · Stage 6.10 rerender 未生效)
- **文档**: 统筹全局/GOLDEN_PATH_FROZEN_2026-08-19.md · 冻结后 M1 生效 · 未来任何改动即使开发者也走晋升
