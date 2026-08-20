# Benchmark 总入口

> benchmark 是“冻结考试卷”，不是新的生产链，也不是音频素材仓库。

## 两类 benchmark

| 目录 | 回答的问题 | 当前状态 |
| --- | --- | --- |
| `EP03-ASR-mini-gold-v1/` | ASR / VAD / speaker 是否听准 | 本地人工 gold 标注入口与 scorer 骨架已有；尚未填写 gold |
| `asr-public-suite-v1/` | 候选 ASR 引擎在成熟公开语料上是否值得进入本地实验 | 本轮建立 manifest 骨架；尚未下载或跑分 |
| `editing-e2e-v1/` | 从 raw N 轨到人工最终成片，系统是否真的更接近人工且节省时间 | 本轮建立契约骨架；尚未冻结首批节目 |

## 不可破坏的规则

1. 原始音频和人工成片不复制到此目录；只在 manifest 写原路径、SHA 和授权状态。
2. `development` 集可调试；`frozen` 集一旦冻结，不能看结果后继续按它调参。
3. 任何结果必须带工具/模型/规则版本、机器配置、命令和失败情况。
4. benchmark 分数不能授权自动删剪；语义删剪仍由真人审核。
5. 新工具或模型只能先作为 Challenger 比较，不能直接覆盖 Champion。

详细规范见 `../统筹全局/功能说明/F10-基准、能力目录与Skill路线.md`。
