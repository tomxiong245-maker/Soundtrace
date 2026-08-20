# autocut-gate-v1 · Challenger

**Slot**：跟 `release-policy-v2` 平行的"判决层"。policy v2 定 whitelist，gate 定
**"哪些候选能真正走 auto-cut，precision 保证"**。

## 一句话

对每个候选跑 6 个门（whitelist / high_confidence / no_preserve / history_no_reject
/ duration ≤ 800ms / 非保护区），全过才 auto-cut，任一失败降级人审。**precision-first**：
牺牲 recall 保 precision ≥ 90%。

## 产物

- `scripts/apply_autocut_gate.py` — CLI + library 双模式判决器
- `tests/test_apply_autocut_gate.py` — 14 单元测试
- `docs/2026-08-17-1900-EP04-baseline.md` — EP04 首次实测 + codex 手签对比

## EP04 baseline

3/12 auto-cut：C007 / C023 / C034（详情见 baseline md）。
与 codex 手签 authorization 2/3 重合；gate 保守拒 C044（有历史反例），gate 独选
C023（历史无反例）。

## 用法

    python3 稳定生产/challengers/autocut-gate-v1/scripts/apply_autocut_gate.py \
      --candidates <run>/all_candidates.json \
      --policy 稳定生产/challengers/release-policy-v2/rules/editing_policy.guards-v2.json \
      --policy-application <run>/policy_application.json \
      --calibration-source <run>/calibration_source.json \
      --episode-duration-seconds <sec> \
      --out <run>/autocut_gate/

## 严格保留边界

- 不改 Champion（orchestrator / editing_policy.guards-v1 / release_specs.json）
- 不改 candidate 生成 tool（filler-global-pause-v1 系列）
- 只对现有 candidates 做判决；不生成新候选
- auto_cut 集合仍需 orchestrator 层集成 EDL → 属下一步 promotion
