# main/knowledge/ ── 内外学习入口

这不是知识库本身，是**入口 + 版本冻结点**。真正的知识内容在别处（`从视频学习经验/`、`端到端学习剪辑/skill/.../references/`、以及未来的 `experience_snapshot/cases/`）。

## 两条学习线的当前状态

| 学习线 | 知识实际住哪里 | 本入口的作用 | 状态 |
|---|---|---|---|
| **外部知识循环** | `从视频学习经验/` + 主 skill references | 冻结当前使用的版本号，禁止本期 Agent 静默修改 | frozen v1-2026-08-10 |
| **内部经验循环** | `experience_snapshot/cases/`（待归档产生） | 归档反馈包 → 未来学习 → 晋升 | empty v0-2026-08-10 |

## orchestrator 何时读它们

只在 **PLANNED 阶段**——冻结本期方案时读一次 `index.json`，把 snapshot_version 写进 plan.json，作为本期"用了哪个版本的知识"的证据。之后不再读，也不写。

## 本轮不启用的东西（用户明确指示）

- 不学新东西
- 不重复造已有的总结
- 但**保留渠道**：`sources` 里的 acquisition_tool 和 `how_it_grows` 说明保留了未来接入方式

## 相关工具（保留、本轮不用）

- **`felo-youtube-subtitling`**（在 `minglue/skill/2.参考的skill/`）—— 拉 YouTube 字幕，外部素材增量时用
- **未来的经验学习 Agent** —— archive 累计 N 期后启动，本轮不写代码

## 归档流水（预告）

`orchestrator archive` 未来会自动做这件事：

```
runs/<EP>/feedback_bundle.json
     ↓ 提取关键字段
main/knowledge/experience_snapshot/cases/<EP>.jsonl  (追加一条)
     ↓ 累积 N 期
main/knowledge/experience_snapshot/challenger/*      (学习 Agent 产出)
     ↓ 离线评测 + 人工晋升
main/knowledge/experience_snapshot/champion/*.json   (可影响下一期候选)
```

本轮 archive 只做**追加 cases**，不启动学习。
