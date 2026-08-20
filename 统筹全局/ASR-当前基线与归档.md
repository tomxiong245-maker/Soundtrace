# ASR 当前基线与归档

> 当前生产只保留一条 ASR 路线，避免 Agent 在多个未验证引擎之间漂移。

## 当前正式基线

- 外部 `faster-whisper small`，CPU `int8`。
- EP04 已验证成果：`main/runs/EP04/EP04-v13-20260813-2002/`。
- 模型 revision：`536b0662742c02347bc0e980a01041f333bce120`。
- 词级 JSON、normalized transcript 和 semantic transcript 可在输入 SHA、降噪 SHA、模型和解码参数全部一致时复用。
- ASR 只负责文字和时间定位；是否删除由独立候选/审核模块决定。

## 不再进入普通流程

FunASR/Paraformer、MLX Whisper、SenseVoice 和 language A/B shootout 没有完成同一人工 gold、设备一致性和生产晋升，因此不进入普通 start，不覆盖 faster-whisper，也不作为准确率结论。项目内残留的轻量 adapter/audit 文件只作历史设计证据；运行环境、实验 run 和 shootout 分支已移出项目，不会被普通入口调用。

未采用分支已移至项目外：

`/Users/renting/Desktop/minglue/未采用ASR归档-20260815/ARCHIVE_MANIFEST.md`

恢复任何分支前必须重新审计许可证、模型 SHA、运行设备、时间戳资格和 mini-gold；不能只凭旧实验报告恢复。

## Agent 操作规则

先查 `analysis_reuse_manifest.json`。上游 SHA 或模型配置不一致时，明确新建分析证据；一致时只复用既有结果，不重新转写。不要安装英文 `.en` 包，不要把项目 adapter 称为自研 ASR。
