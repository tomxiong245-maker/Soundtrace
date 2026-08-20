# podcast-editing-orchestrator · flow boundary

## ✅ 本 skill 允许做的（zero-touch 主入口）

**一条命令**：
```bash
python3 scripts/run_end_to_end.py \
  --episode-id EP0X \
  --from-raw-wav track_01.wav track_02.wav track_03.wav \
  --tracks-for-automix track_01.wav track_02.wav track_03.wav \
  --out-dir main/runs/EP0X-AUTO-<时间戳>
```

Pipeline 11 步（**顺序不可打乱** · v20.6 更新）：
1. **denoise**（DeepFilterNet 保留时长）
2. **ASR 词级**（faster-whisper small · 未来 large-v3-turbo 可选）
3. **候选生成**（**必调** `build_filler_global_pause_review_source` + `immediate_repetition` + `detect_self_correction_wordlevel`；含 sentence_position_gate + boundary_lock + english_fragment_context_guard + PRONOUN_LIKE 豁免 + probability_gate）
4. **Stage 3.1 · auto_speaker_role**（v20.6 Q2 · 若无 speaker_map.json 则自动统计生成 host/guest 判决）
5. **Stage 3.2 · spacy_semantic_transcript**（v20.6 Q1 · zh_core_web_sm 分句 + interrogative/declarative）
6. **Stage 3.3 · session_feedback lookup**（v20.6 Q4 · 加载 `main/knowledge/session_feedback/<EP0X>.session_feedback.jsonl` + `labels_lake feedback` → inject candidate.previous_user_feedback · **必读！CLAUDE.md §14**）
7. **Stage 3.4 · speaker_role_filter**（v20.5 · 从源头挡主持人 backchannel · CLAUDE.md §12）
8. **Stage 3.5 · MFA 精修边界**（**必调** `mfa_align_and_extract_boundaries`, `--language auto` · CLAUDE.md §8）
9. **experience_lookup**（**必调** `stage_experience_lookup` 查 65 条案例）
10. **autocut_gate 判决**（**必调** `apply_autocut_gate` 六道门 + G7 `previous_user_feedback` 一票否决 · 三层 signal）
11. **EDL + automix**（**必调** `automix_v1.py` or `automix_adapter.py`，双遍 loudnorm -22.2 LUFS · reference-linear-v2 music timing）

## v20.6 强制流程要点

- **每期节目上线前必做**：建 `main/knowledge/speaker_maps/<EP0X>.speaker_map.json`（若不建，Stage 3.1 会自动跑 auto_speaker_role.py 生成，但**人工优先**）
- **每次用户 chat 反馈**：agent 必须主动 append 到 `main/knowledge/session_feedback/<EP0X>.session_feedback.jsonl`（含 timestamp / reviewer / kind / candidate_pattern / verdict / note）—— 否则下次 chat 会重复问同一问题（CLAUDE.md §14）
- **G7 hard reject**：候选若命中 `previous_user_feedback[].verdict == "never_cut"` → 自动不进 auto_cut · 走人审并显示反馈

## ❌ 本 skill 禁止做的（违反 = 破坏契约）

- **绝不自己写候选生成脚本** —— 用系统 tools（CLAUDE.md §11）
- **绝不自己写 boundary 精修** —— MFA 精修是唯一入口（CLAUDE.md §8）
- **绝不自己写 automix / mix / loudnorm** —— 用 automix_v1 或 automix_adapter（CLAUDE.md §9）
- **绝不放宽白名单 kind** —— `autocut_policy` 只允许 filler_hesitation / immediate_repetition / global_long_pause / self_correction
- **绝不把机器输出标 human_approved** —— 输出永远是 `machine_assisted_draft`
- **绝不写 `human_decisions.json`**（只有真人前端能写）
- **绝不覆盖历史 run**（每次跑新一期新时间戳）

## 依赖的工具（tools.json 已登记）

主入口：`run_end_to_end`（scripts/run_end_to_end.py）
调用链：所有 tools.json 里 41 项按 pipeline 顺序调用（详见 `main/tools/tools.json`）

## 输出目录约定（不可改）

```
main/runs/{episode-id}-AUTO-{yymmdd-hhmm}/
├─ run_identity.json          ← 运行身份 SHA
├─ analysis/                  ← denoise + ASR 词级
├─ all_candidates.json        ← 系统候选池（不许手写扫描替换）
├─ mfa_boundaries.json        ← MFA 精修产物
├─ experience_context.json    ← case_memory 侧车
├─ autocut_gate/              ← gate 判决
├─ machine_assisted_draft.edl.json  ← EDL
├─ render/                    ← automix + music + mp3
├─ audit_report.md            ← 剪了/留了什么给你审
└─ regate_diff.json           ← 若跑过 refresh_lake_and_regate
```

## 违反本边界的历史证据

- 2026-08-18 20-pack 事件（agent 绕过 pipeline，自写候选扫描 → 全部 reject）


## v20.7 · A/B clip 硬规则 (CLAUDE.md §9)

**必须从 automix_v1.py 输出的 automix.wav/mp3 切**, 禁止 ffmpeg amix=inputs=3:normalize=0 现场叠加 · 用户 4 次强调.
