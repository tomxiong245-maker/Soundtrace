# skills-registry-v1 · Challenger

> **Slot**: P2 系列（P2a-P2d）。跟 P1 系列（tool-orchestrator-v2 → tool_lookup.py → tools.json）平行。
> **不改 Champion**：不改 `main/orchestrator/*.py`、不改任一 SKILL.md 的**行为**（只加 frontmatter 元数据）、不改 `main/tools/tools.json`。
> **产物集中在**：`稳定生产/challengers/skills-registry-v1/`。

## 问题陈述

P1 系列解决了 "orchestrator 硬编码路径 vs tools.json 声明"的漂移。
类似的漂移在 skill 层依然存在：

- 项目里有 7 份 SKILL.md，但没有程序化入口读它们。
- SKILL.md 只有 `name` / `description` 两个 frontmatter 字段，没有 `entry_tool`、没有 `status`、没有 owner。
- 无法回答问题：`podcast-editing-orchestrator` skill 的主入口 tool 是哪个？某 tool 被哪些 skill 引用？
- 无法防止：SKILL.md 声明的能力其实已经从 tools.json 里消失（孤儿 skill）；tools.json 加了新 tool 但没 skill 引用它（孤儿 tool）。

## 目标（P2a-P2d）

1. **P2a 契约**：给 SKILL.md frontmatter 定义一份可解析、可校验的 schema。
   见 [`docs/skill_frontmatter_contract.md`](docs/skill_frontmatter_contract.md)。
2. **P2b 补全**：给每份**项目自维护**的 SKILL.md 加 `entry_tool` 等字段。Mentor 只读目录用 `external_skills.json` sidecar 登记（不动 SKILL.md 本文）。
3. **P2c registry**：`scripts/skills_registry.py` 单一入口，读取 SKILL.md frontmatter + external sidecar，提供 `by_name()` / `by_entry_tool()` / `all()` / `assert_reachable()`。
4. **P2d 契约测试**：pytest 用例：Skill.entry_tool 必须存在于 tools.json；无孤儿；status=deprecated 的 skill 不被 active skill 引用。

## 严格保留边界

- 不改 `mentor的成果/**/SKILL.md`（Mentor 只读；用 sidecar 登记）
- 不改 `main/tools/tools.json`
- 不改 `main/tools/tool_lookup.py`（P1 产物）
- 不改任何 Champion 生产脚本
- P2b 对项目自维护 SKILL.md 的修改仅限 YAML frontmatter；正文一字不动

## 相关

- P1 姊妹产物：[main/tools/tools.json](../../../main/tools/tools.json)、[main/tools/tool_lookup.py](../../../main/tools/tool_lookup.py)
- Champion 施工规则：CLAUDE.md 的"目录所有权"与"不可违反的边界"
