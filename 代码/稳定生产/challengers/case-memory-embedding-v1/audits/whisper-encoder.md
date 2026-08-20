# Audit · faster-whisper 内置 encoder (本机运行)

## 固定信息

- 依赖名：`faster-whisper`（已装，走 CTranslate2 后端）
- 使用组件：模型对象的 encoder 部分 —— 对应 `WhisperModel.model.encode` / 底层 `ctranslate2.models.Whisper.encode`
- Encoder 输出：音频对数梅尔特征经 encoder 后的隐状态张量，shape 为 `(1, T', D)`，其中 D 由所选模型规格决定（tiny=384 / base=512 / small=768 / medium=1024 / large=1280）
- 采样约定：与官方 whisper 一致，16 kHz 单声道，log-mel 80 维，30 s 窗口对齐（不足补零，超出切段）
- License：MIT（faster-whisper） · Whisper 权重遵循 OpenAI 原 License

## 使用范围（本 Challenger 内）

- 只用 encoder 输出，不做 decode、不做 transcribe
- 对每个 case clip：取 `(start, end)` 区间 wav → 16k mono → log-mel → encoder → 沿时间轴 mean-pool 得到 `(D,)` 向量 → L2 归一化 → 作为 embedding 送入 FAISS `IndexFlatIP`
- 骨架期不确定具体模型规格；实现期建议先用与 Champion 同规格模型（避免额外下载）

## 已知限制

- 30 s 窗口硬约束：超过 30 s 的候选片段需切片再池化；不足 30 s 会有 padding，短片段（<3 s）embedding 稳定性下降
- Mean-pool 是最粗的池化策略；若相似度区分不足，考虑 attention-pool / [CLS] 变体（EP05 决策时再定）
- Encoder 隐状态**不保证**跨模型规格可比；索引必须与查询用同一模型
- `ctranslate2` 的 `encode` 接口在不同版本参数名有过变更；实现时以本机 `faster-whisper --version` 为准

## 状态引语

> **SKELETON**：本骨架期未真正调用 encoder，未加载权重，未产生任何 embedding。以上参数与限制仅作实现期契约声明。EP05 实现时须以本机实测数据回填此文档「实测」小节。
