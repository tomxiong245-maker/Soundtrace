# ASR 公共基准套件 v1

## 目的

复用成熟公开中文 ASR / 会议语音数据，筛选“值得在本地三轨播客上继续验证”的引擎和组件。它不是发布前质量保证，也不会把公开分数直接外推到章鱼 AI 节目。

## 首批候选

| 任务 | 候选语料 | 评测重点 |
| --- | --- | --- |
| 中文基础 ASR | AISHELL-1 | CER、RTF、失败率 |
| 多人 / 重叠 | AliMeeting、AISHELL-4 | CER、VAD、DER/JER、overlap recall |
| 长音频 / 网络域参考 | WenetSpeech | 泛化参考；先核对其测试切分和标注来源 |

## 本轮不做

- 不下载大语料；
- 不把公开语料复制进 Git；
- 不混合公开测试集和本项目训练/调参数据；
- 不根据公开榜单直接替换项目 ASR；
- 不将自动生成的伪标签当人工 gold。

## 正式执行前的步骤

1. 在 `suite.manifest.template.json` 复制出一次真实运行 manifest；
2. 由执行人填官方来源、许可证、版本、split、下载 SHA、存放路径；
3. 固定 engine / model / prompt / decoding 参数和机器配置；
4. 将输出转为项目统一 schema；
5. 用 JiWER 计算 CER；仅在有可信标注时用 pyannote.metrics 计算 DER/JER；
6. 报告质量、RTF、内存、失败和降级；
7. 再拿通过筛选的少数引擎跑本地 mini-gold 与真实三轨素材。

## 目录所有权

只保存 manifest、配置、分数、日志摘要和下载校验。公开语料本体应位于 Git 外的本地数据目录，并在 manifest 中引用。

## 当前研究候选

`candidate_registry.v1.json` 是已核对官方来源、但**尚未下载**的最小候选清单。它把 AISHELL-1、AliMeeting Eval、AISHELL-4 Test 和降噪三问盲听法分开记录，并把许可证、存储和本地 mini-gold 设为进入真实运行前的门；它不构成生产工具替换或自动下载授权。
