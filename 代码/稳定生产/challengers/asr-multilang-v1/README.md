# ASR 多语文本层 Challenger v1

这是一个隔离的对照工具，不是新的生产 ASR，也不会替换 EP04 v13 的转写。

它只验证两件事：

1. 能否从同一份词级转写安全地产生三层旁路文本：`raw_text`、`match_text`、`display_text`；
2. 对同一段音频、同一模型、同一解码参数，仅比较 `language="zh"` 与 `language=None`（自动多语识别）是否更适合中英混说。

## 单一实现与兼容层

三层文本的**唯一实现**是
`main/orchestrator/transcript_text_layers.py`。这里的
`scripts/text_layers.py` 只保留历史命令行兼容层（`--input --out`），直接导入该模块；它不再维护第二份繁简表、归一化逻辑、英文碎片合并或完整性校验。

canonical 输出同时保留原有 `document` / `words` 字段，以及本 Challenger
既有的 `word_layers` / `display_spans` 字段。因此旧调用方可以继续读取
`source_word_ids` 和展示 span，但所有文本归一和来源/时间证据都由同一处代码产生。

### 实现 / 验证证据（2026-08-16）

- 唯一实现：`main/orchestrator/transcript_text_layers.py`；本目录的
  `scripts/text_layers.py` 是无业务逻辑的兼容导入层，A/B runner 也直接导入 canonical 模块。
- `python3 -B main/orchestrator/tests/test_transcript_text_layers.py`：`3/3` 通过。
- `python3 -B 稳定生产/challengers/asr-multilang-v1/tests/test_asr_multilang.py`：`7/7` 通过。
- `python3 -B 稳定生产/challengers/asr-multilang-v1/scripts/run_faster_whisper_ab.py --help`：通过，证明 A/B 入口可加载 canonical 文本层而无需安装/下载模型。

本次只验证文本 sidecar 和导入契约，**没有解码、转写或处理任何真实音频**；也没有改动 EP04 v13、当前审核包、候选、EDL 或生产 ASR。

## 绝对边界

- 不修改 `稳定生产/challengers/asr-speaker-v1/scripts/p0_mvp.py`。
- 不覆盖 `main/runs/EP04/EP04-v13-20260813-2002/analysis/` 的任何文件。
- 不修改原始 WAV、降噪轨、候选、审核决定、EDL 或成片。
- 不做文本纠错、时间戳修复、自动标点、候选生成或删除决定。
- 只使用本机已有、已审计的 `Systran/faster-whisper-small` 多语模型；不下载模型。
- 明确拒绝 `.en` 模型：它是英文专用版本，不是中文/英文混说的合理对照臂。
- 即使 A/B 结果较好，也不能自动替换生产 ASR，必须在同一小型人工 gold 上比较后再提出晋升。

## 三层文本

| 层 | 做什么 | 不能做什么 |
| --- | --- | --- |
| `raw_text` | 保存上游 `word.text` 的原样拼接，作为证据 | 不能被显示优化或繁简转换回写 |
| `match_text` | NFKC、有限且可复现的繁转简、去空白/标点、英文 case-fold，用于事件匹配和历史检索 | 不能当可读稿、不能生成新时间戳 |
| `display_text` | 统一繁简、压缩空白，供审核页面阅读 | 不能代替原始词、不能决定剪辑 |

输出里的 `word_layers` 始终保留一个 `source_word_ids` 映射。为了让英文碎片更好读，`display_spans` 可以在非常保守的条件下合并相邻 ASCII 片段，例如 `S + oph + ie → Sophie`；该 span 会列出全部原始 word ID，且**不创建新 word ID 或新时间戳**。它绝不能成为剪口边界。

`match_text` 的繁简表是一个轻量、确定性的 fallback，不假装覆盖 OpenCC 的全部词典；无法转换的字会保留原样。它的目的是防止 `什麼` / `什么` 这类同一事件被重复判为不同文本，而不是修正 ASR 语义。

## 只做文本层（不重新转写）

对任何现有词级 JSON，都把输出写到一个新文件；默认拒绝覆盖已有 sidecar：

```bash
python3 稳定生产/challengers/asr-multilang-v1/scripts/text_layers.py \
  --input main/runs/EP04/EP04-v13-20260813-2002/analysis/track_01.transcript.json \
  --out /tmp/ep04-track01.text_layers.json
```

输出包含源 JSON 的 SHA、原始 word ID 顺序 SHA、原始文字/时间证据 SHA，以及 `raw_transcript_mutated=false` 和 `timestamps_rewritten=false`。原始输入必须保持只读。

## 运行语言 A/B

先准备一份与 P0 相同结构的 N 轨 manifest；建议只放 3–5 段中英混说的短 WAV，并把 output 放进一个**新的** Challenger run 目录。脚本拒绝非空 output 目录，避免混入旧证据。

实际转写必须用一个已经审计、已经安装 `faster-whisper` 的本地 Python 环境。历史 EP04 v13 使用的临时 venv 已不应被假定还存在；当前普通 `python3` 若没有该包，脚本会明确停止，绝不会自行 `pip install` 或下载模型。先把已审计解释器路径填入变量：

```bash
ASR_PYTHON=/absolute/path/to/audited-asr-venv/bin/python
"$ASR_PYTHON" -B -c 'from faster_whisper import WhisperModel; print("runtime ready")'
```

```bash
"$ASR_PYTHON" -B 稳定生产/challengers/asr-multilang-v1/scripts/run_faster_whisper_ab.py \
  --manifest /absolute/path/to/short-ab-manifest.json \
  --out main/runs/EP04/EP04-asr-multilang-v1-YYYYMMDD-HHMM
```

默认会跑两个 arm：

```text
zh/    language="zh"
auto/  language=None
```

两臂其他参数固定：CPU、`int8`、beam 5、逐词时间戳、VAD 开启、`condition_on_previous_text=false`、同一 local model snapshot 和同一输入 WAV。输出根目录的 `ab_manifest.json` 会记录每条音频 SHA、两个 raw transcript SHA、text-layer SHA、模型路径、检测到的语言、耗时和 RTF。

这个 A/B 只是在产生可比较的假设，不会给出“谁更准”的结论。正确比较流程是：

```text
人戴耳机写同一小段的真实文本（尤其英文术语）
→ 用相同 gold 比较 zh 与 auto 的漏词、错词和专名
→ 记录 reviewer / 时间 / 音频 SHA
→ 冻结小报告
→ 独立复核后才考虑是否提出生产升级
```

在没有上述人工 gold 前，`auto` 只能是 Challenger 结果，不能写入 EP04 v13、当前审核页或正式候选流程。

## 最小测试

无需音频、无需下载模型：

```bash
python3 稳定生产/challengers/asr-multilang-v1/tests/test_asr_multilang.py
python3 main/orchestrator/tests/test_transcript_text_layers.py
```

测试覆盖：原始 JSON 不变、word ID/时间证据不变、繁简匹配、英文碎片 span 映射、重复 word ID fail-closed、sidecar 独立输出、以及 `language=None`/`.en` 模型门禁。
