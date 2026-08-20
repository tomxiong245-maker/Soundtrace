# candidate-family-integration · flow boundary

## ✅ 本 skill 允许做的

- 消费 `稳定生产/challengers/self-correction-v1/` 输出，产出 canonical `self_correction` review candidates
- 消费 `稳定生产/challengers/transient-events-v1/` 输出，产出 canonical `cough_like` review candidates（**仅作为 source-track gate 信号，绝不写全轨 cut**）
- 输出 `{run}/candidate_family_review.json`（新审核包侧车），SHA 绑定 run_identity + input_manifest
- 在 `main/knowledge/integration_governance/owner_attested_mainline.v1.json` 里查确认接入是 `OWNER_ATTESTED_INTEGRATE`

## ❌ 本 skill 禁止做的（违反 = 破坏契约）

- **绝不生成真人决定**（`human_decisions.json` / `human_approved.edl.json` 只读）
- **绝不生成 EDL 或 render_sync_cuts**（那是下游 `apply_autocut_gate` + `automix_adapter` 的职责）
- **绝不修改 `main/tools/tools.json`**（本 skill 只消费不注册）
- **绝不改 `稳定生产/challengers/self-correction-v1/rules/`**（规则冻结）
- **绝不为凑数放宽 filler 词表 / min_chars / max_gap**（违反 CLAUDE.md §11）
- **绝不把 `cough_like` 升级成全轨 cut**（source-track gate 是硬边界）

## 依赖的工具（tools.json 已登记）

- `detect_self_correction_wordlevel`（scripts/detect_self_correction_wordlevel.py）
- `detect_transient_events`（scripts/detect_transient_events.py）
- `apply_candidate_family_adapter`（本 skill entry_tool）

## 违反本边界的历史证据

- 2026-08-18 20-pack 事件（agent 自扩 filler 词表挡不住"嗯/啊" backchannel → 用户 reject "没一个通过"）
- 修法：CLAUDE.md §11 加"禁止自由发挥"
