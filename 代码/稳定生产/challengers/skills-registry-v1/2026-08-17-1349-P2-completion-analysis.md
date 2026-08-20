# P2 skills-registry-v1 完成 · 分析

**可靠度声明**：本文陈述基于本次实际写码与实测的 unittest 输出，负测试与回滚都实跑过。SHA 与文件路径来自 shell 命令输出。

## 事实（[HIGH]）

- 新建 challenger 目录 `稳定生产/challengers/skills-registry-v1/`，含 4 份产物：
  - `README.md` · challenger 定位与边界声明
  - `docs/skill_frontmatter_contract.md` · P2a 契约（YAML schema + sidecar 规则）
  - `scripts/skills_registry.py` · P2c 单一入口（`all_skills` / `by_name` / `by_entry_tool` / `assert_reachable` / `clear_cache`）
  - `external_skills.json` · Mentor 只读 SKILL.md sidecar 登记
  - `tests/test_skills_registry.py` · P2d 契约测试（12 test）[HIGH]
- 给 5 份**项目自维护** SKILL.md 加了 P2a 契约字段（`status/owner/entry_tool/related_tools/preconditions/postconditions/[supersedes/superseded_by]`），正文一字未动：[HIGH]
  - `./SKILL.md` (audio-clips-orchestration, active, entry_tool=null)
  - `./skills/label-learning-driver/SKILL.md` (active, entry_tool=`label_learning_driver`)
  - `./skills/podcast-editing-orchestrator/SKILL.md` (active, entry_tool=`build_review_package`, 13 related_tools)
  - `./skills/editing-experience-distiller/SKILL.md` (active, entry_tool=null)
  - `./端到端学习剪辑/skill/dual-track-podcast-editing-mvp/SKILL.md` (deprecated, superseded_by=audio-clips-orchestration)
- **Mentor 目录 2 份 SKILL.md 未改**（严守只读边界），通过 `external_skills.json` 登记；契约测试 `test_mentor_skills_registered_via_sidecar_not_frontmatter` 显式锁死这一约束。[HIGH]
- 12/12 契约测试通过（含 registry_covers_every_skill_md_on_disk：磁盘上所有 SKILL.md 都必须在 registry 里能反查得到）。[HIGH]
- P1 契约测试 3/3 无退化（`test_orchestrator_uses_tools_json.py`）。[HIGH]
- **负测试证据（活契约）**：把 `label-learning-driver/SKILL.md` 里 `entry_tool` 改成不存在的 tool，跑测试 → 2 处 fail-closed（`test_every_entry_tool_exists_in_tools_json` + `test_assert_reachable_is_healthy`）；回滚后 12/12 恢复通过。[HIGH]

## 判断（[MED]）

- SKILL.md 契约以 YAML frontmatter 为 schema、`assert_reachable()` 为守门员、`pytest`/`unittest` 为门口的三段式，跟 P1 (tools.json + tool_lookup.py + `test_orchestrator_uses_tools_json.py`) 是对称结构。这两组一起把"reference 层"（谁调谁）钉死。[HIGH]
- `entry_tool` 字段暴露了一个之前隐藏的孤儿：`editing-experience-distiller` 依赖 `稳定生产/challengers/experience-ingestion-v1/scripts/experience_consumer_adapter.py`，但该 adapter 未登记 tools.json。P2 阶段没修（属于 tool 层补齐，走 P1 路径），但已在 SKILL.md 的 `notes` 字段与 README 里留痕。[MED]
- Mentor 只读走 sidecar 是双赢：既满足契约完整性（registry 能反查到），又不违反"Mentor 成果只读"边界。这种 sidecar 模式后续可以复用（例如第三方 Skill、外部 model card）。[MED]

## 建议（[MED]）

1. **补 `experience_consumer_adapter` 到 tools.json**：把 `editing-experience-distiller` 的 entry_tool 从 null 改成对应 tool name；然后 registry `assert_reachable` 就有一个可 resolve 的入口。
2. **给 orchestrator 加 skill 反查**：以 `by_entry_tool(tool_name)` 让 orchestrator 知道"运行到这个 tool 的时候，我处于哪个 skill 的哪一步"。当前 orchestrator 只知道 tool，不知道 skill 语境。
3. **CLAUDE.md 的"目录所有权"章节加一句**："项目内 SKILL.md 的元数据修改仅限 P2a 契约字段；正文变更走 challenger promotion 流程。"—— 免得下次 Agent 直接改正文。
4. **CI 里挂两条契约**：`python3 -m unittest main/orchestrator/tests/test_orchestrator_uses_tools_json.py` + `python3 -m unittest 稳定生产/challengers/skills-registry-v1/tests/test_skills_registry.py`，作为"reference 层"守门线。

## 未做（诚实交代）

- **未**改 orchestrator 主流程接入 skills_registry。P2 只做 registry，没做 consumer。
- **未**把 registry 从 challenger 目录晋升到 Champion（`main/`）。晋升要走独立复核，P2 完成后另行安排。
- **未**给 `experience_consumer_adapter` 补 tools.json 登记（跨 P1/P2 修补，本次 scope 外）。
- **未**动 `main/tools/tools.json`（严守边界）。
- **未**改 Mentor 目录任何文件。
- **未**给现有 20+ Challenger 目录里的 non-SKILL.md 元文档（如 registry.json、AdapterBase.py）纳管——P2 只管 SKILL.md 层，别的层各自守自己的契约。

## 相关记忆

- [[minglue-construction-rules-first]]
- [[minglue-project-layout]]
- [[minglue-post-feature-analysis-md]]
- [[minglue-analysis-md-tracks]]
