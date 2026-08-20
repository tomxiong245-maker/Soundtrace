# SKILL.md frontmatter 契约 v1

> **定位**：P2a 产物。这是 `skills_registry.py` 与契约测试读取的唯一 schema 来源。
> 修改此契约需要同步更新 `scripts/skills_registry.py` 与 `tests/test_skills_registry.py`。

## 类比

| 层 | Manifest | Reader | 契约测试 |
|---|---|---|---|
| Tool | `main/tools/tools.json` | `main/tools/tool_lookup.py` | `test_orchestrator_uses_tools_json.py` |
| **Skill** | **每份 SKILL.md 的 YAML frontmatter** + `external_skills.json` | **`skills_registry.py`** | **`test_skills_registry.py`** |

## Frontmatter YAML Schema

```yaml
---
# 必填 · 已有 -----------------------------------------
name: <kebab-case>                # 项目内全局唯一 slug
description: <one paragraph>      # 触发时机 + 触发词；给 agent 判断是否激活用

# 必填 · P2a 新增 --------------------------------------
status: active | deprecated | experimental | external
                                  # active     = 当前生产在用
                                  # deprecated = 保留档案；不应被 active skill 依赖
                                  # experimental = challenger 阶段，允许被 active 引用但需标注
                                  # external   = 非本项目维护（如 Mentor 交付、外部包）
owner: <string>                   # champion | challenger:<name> | mentor | external
                                  # champion         = 由 main/ 下 Champion 拥有
                                  # challenger:<n>   = 由 稳定生产/challengers/<n>/ 拥有
                                  # mentor           = mentor的成果/ 下只读
                                  # external         = 第三方；只读
entry_tool: <tool_name> | null    # tools.json 里对应的主入口 tool.name
                                  # null 表示：路由型/纯文本型/外部 skill，没有本项目的主 tool

# 可选 · 推荐 -----------------------------------------
related_tools: [<tool_name>, ...] # skill 内部会调的其它 tool（子步骤）
preconditions: [<free text>, ...] # 运行前提；纯文本，仅供人读
postconditions: [<free text>, ...]# 完成后产物；纯文本
supersedes: [<skill_name>, ...]   # 明确取代哪些老 skill
superseded_by: <skill_name>       # 若本 skill 已被替换，指向替换者（配合 status: deprecated）
---
```

## 字段约束

1. **`name`** MUST match `^[a-z][a-z0-9-]*$` 并在项目内全局唯一（跨 SKILL.md + external_skills.json）。
2. **`status`** MUST 在 `{active, deprecated, experimental, external}`。
3. **`owner`** 前缀 MUST 在 `{champion, challenger:, mentor, external}`。
4. **`entry_tool`** MUST 或者是 `null`，或者是 tools.json 里存在的 `tools[].name`。**契约测试会 fail-closed 校验。**
5. **`related_tools`** 里每项 MUST 存在于 tools.json。
6. **`supersedes` / `superseded_by`** 引用的 skill name MUST 存在。
7. `status: deprecated` 的 skill MUST 有 `superseded_by` 或在正文顶部说明"仅存档"。
8. `status: deprecated` 的 skill MUST NOT 被 `status: active` 的 skill 通过 `related_tools` **形成同名依赖**（tools 无所谓，是不是同 tool 会由 tool 层校验）。

## `external_skills.json` sidecar

用于登记**不能修改文件**的 skill（Mentor 交付、第三方）。schema 完全同上，只是外层封在 JSON 里：

```json
{
  "schema_version": 1,
  "skills": [
    {
      "name": "podcast-audio-to-content",
      "path": "mentor的成果/podcast-audio-to-content/SKILL.md",
      "status": "external",
      "owner": "mentor",
      "entry_tool": null,
      "description": "只读快照；实际 description 见 SKILL.md 本文"
    }
  ]
}
```

- 用于 Mentor 目录（`mentor的成果/`）与任何第三方 SKILL.md。
- registry 在**合并**时以 sidecar 里的 `status/owner/entry_tool` 为准，`description` 若 sidecar 未写则回落到 SKILL.md 本文的 frontmatter。
- **同一 skill name 不得同时出现在两处**（避免歧义）。契约测试会 fail-closed。

## 空 frontmatter / 无 frontmatter 处理

- **无 frontmatter 或缺失必填字段** → `skills_registry.py` fail-closed 抛 `SkillRegistryError`。
- 契约测试 `test_all_skill_md_pass_contract` 会在 CI 阶段捕获。

## 变更流程

1. 修改 SKILL.md frontmatter 前，先在本文件对应字段旁记录 rationale。
2. 破坏性变更（改字段名、加必填字段）必须走 challenger promotion 流程，不能直接改 Champion 使用者。
3. 加新可选字段可直接在本文件的 Schema 里加一行。
