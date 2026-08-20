# EP03 ASR mini gold set · benchmark 目录说明

**状态**：`WAITING_FOR_HUMAN_GOLD`
**标注入口（唯一）**：当前目录下的 `label.html`。

> 打开方式（推荐）：Finder 双击 `label.html`；若 `file://` 打不开音频，在项目根执行 `python3 -m http.server 8788 --directory benchmark/EP03-ASR-mini-gold-v1`，浏览器打开 `http://localhost:8788/label.html`。

---

## 1. 你要做什么

对 12 段各约 20 秒的音频（每段有女声 Tr1 / 男声 Tr3 / 合成 speech_mix 三个 WAV），**只根据听感**填三件东西：

1. **正确完整文本**：合成 mix 里你能听清的每句话；标点无所谓；口癖（嗯、啊、就是、然后、对）**保留**；数字、否定、英文名保留。
2. **说话人归属**：每句一行，`female|male|overlap|uncertain`。示例：
   ```
   female: 大家好
   male: 嗯
   female: 我们今天讨论
   overlap: 对对对
   ```
3. **系统漏识别的整句**：ASR 现在完全没识别出来、但你听到了的一整句话。每行一句。

**每段填完**：在页面上确认你就是 reviewer（填一次即可），然后继续下一段。全 12 段填完 → 点导出按钮 → 会下载一个 `gold.json`（新版）。

---

## 2. 严格边界（这条别越）

- 你**不需要**参考页面里"ASR 当前预测（vad_on）"那一块。那是当前 baseline 的预测，只是**参考**；不要照抄。
- 尤其**不要**把 ASR 预测粘贴到"正确完整文本"里。这会污染 gold。
- 如果你实在听不清 → 写下你听到的部分，标点用 `？` 表示不确定；不要空着。

---

## 3. 目录里都有什么

```
benchmark/EP03-ASR-mini-gold-v1/
├── gold.json                    ← 骨架 + 已内嵌 baseline 预测；你的填写通过 label.html 完成
├── gold.json.backup             ← rebuild_gold_v2.py 备份出的原样副本（迁移后才有）
├── gold.v2.json                 ← 剥离预测后的 gold 骨架（迁移后才有）
├── migration_report.md          ← 迁移记录（迁移后才有；重点看"人工字段状态"）
├── label.html                   ← 标注页（本目录唯一入口；本轮 P0 不改动 UI）
├── README.md                    ← 本文
├── segments/S01..S12/           ← 12 段音频（原文件不动，SHA 已冻结在 gold.json）
├── hypotheses/                  ← 各引擎的"参考层"，包括：
│   ├── faster_whisper_small_vad_on/   ← baseline，来自 Champion 切段
│   ├── funasr_paraformer/             ← WAITING_FOR_M3_RUN
│   ├── funasr_fsmn_vad/               ← WAITING_FOR_M3_RUN
│   ├── funasr_campp/                  ← WAITING_FOR_M3_RUN
│   └── mlx_whisper_turbo/             ← WAITING_FOR_M3_RUN
└── metrics/                     ← gold 完成后，score_asr_benchmark.py 才会写入
```

## 4. 为什么现在不能算 CER

- gold.json 的 `gold.transcript / speaker_attribution / missed_sentences / reviewer / reviewed_at` 目前**全为空**。
- scorer 脚本 `稳定生产/challengers/asr-speaker-v1/scripts/score_asr_benchmark.py` 明确**拒绝**在此状态下计算 EP03 指标。
- 只有真人在 label.html 上完整填完 12 段并导出 → 状态变为 `HUMAN_GOLD_FILLED` → scorer 才允许运行。

## 5. 导出后下一步

1. 把下载到的 gold.json 覆盖回本目录的 `gold.json`（老骨架已在 gold.json.backup 里存过）。
2. 再运行一次 `rebuild_gold_v2.py --force`，让 gold.v2.json 把你的人工字段抓取过去（迁移报告会显式列出）。
3. 之后跑 `score_asr_benchmark.py`，metrics 才会写进 `metrics/benchmark_metrics.json`。

---

## 6. 常见困惑

- **两轨说得不一样时怎么办**：以合成 speech_mix 为唯一听感来源；两轨谁 louder 不代表真身份，`overlap` 用于同时说话。
- **不同人对"这一段应该有几句话"分歧**：以你听感为准，脚本按字符级 CER，句数不影响主指标。
- **有段静音 5 秒里 ASR 硬识别了几个字**：写在"系统漏识别的整句"下方另起一段？不用；scorer 会自动用你标出的静音区间统计幻听 insertion（若你想额外强调，可在 note 里备注）。

---

## 7. Champion 与本 Challenger 的分工

- Champion 生产的 faster-whisper 转写与本目录的 baseline hypothesis 通过 SHA 校验绑定（`main/runs/EP03-asr-speaker-v1/before_metrics.json`）。
- 本 Challenger 不改 Champion、不改 label.html、不改 gold.json（写入统一走 rebuild_gold_v2 + 你导出的新 gold）。
- 具体施工范围见 `稳定生产/challengers/asr-speaker-v1/README.md`。
