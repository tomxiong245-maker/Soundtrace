# asr-speaker-v1 (Challenger)

**状态**：Challenger（隔离运行，不动 Champion）；正式多引擎比较仍等待人工 gold，最小 N 轨 faster-whisper baseline 已于本机实跑通过工程门
**日期**：2026-08-11
**任务书**：P0 · ASR / VAD / 说话人 Challenger + 最小 ASR benchmark
**执行者**：Claude Code（本会话运行在 Linux 沙箱 VM，无法直接触达 Apple M3 Metal；见 §5 与 audits/environment.md）

---

## 0. 一句话现状（三档诚实标注）

- **已验证事实**：除原 schemas、adapters、scorer 和研究骨架外，`scripts/p0_mvp.py` 已在本机直接传入三条 48 kHz / 20 秒 WAV 实跑：3/3 有逐词结果、0 非法时间戳、RTF 约 0.18–0.21；支持任意 N 轨和 24-bit PCM extensible WAV。
- **已决定的方向**：三套引擎 (faster-whisper baseline / FunASR 中文组件 / MLX Whisper Turbo) 采用相同 normalized schema；不清理口癖、不用 LLM 后处理；热词是独立实验臂。
- **待验证假设**：三套引擎哪一套在 EP03 上更优 —— **必须**等 12 段人工 gold 完成才能出 verdict；本 Challenger **不**声称赢家。

当前最小产品入口：`审核前端/P0-多轨转写/拖入音轨开始.command`。它按物理文件编号 `track_01...`，不猜男女；能量只作串音提示，不是 speaker gold。三轨兼容夹具的第三轨是既有 `speech_mix`，不代表今天新录音已经验收。

---

## 1. 目标

在 Apple M3 / 16 GB Mac 上比较：

1. **Baseline**：faster-whisper `small` / CPU int8 / beam 5 / VAD on（当前 Champion 的转写栈）。
2. **FunASR 中文组件路线**：Paraformer 中文 ASR（带词/字级时间戳）+ FSMN-VAD + CAM++ 说话人。三个模块**独立保存原始输出**再统一。
3. **MLX Whisper Turbo（Apple Silicon）**：`word_timestamps=True`；量化/Turbo 选择记录在 audits/mlx-whisper.md。

比较的是"哪种组合更适合本项目的中文双轨播客分析"，不是训练模型。**允许最终建议为 A+B 组合**，不追求单一赢家。

---

## 2. 独占写入 & 红线

**只允许写入**：

- `稳定生产/challengers/asr-speaker-v1/**`
- `main/runs/EP03-asr-speaker-v1/**`
- `benchmark/EP03-ASR-mini-gold-v1/{hypotheses,metrics,scorer,schemas}/**`
- `benchmark/EP03-ASR-mini-gold-v1/README.md`（新建）
- `benchmark/EP03-ASR-mini-gold-v1/gold.v2.json` + 迁移报告（**不删** `gold.json`，保留 `.backup`）

**严禁改**：

- `稳定生产/scripts/`、`稳定生产/rules/`（Champion）
- `端到端学习剪辑/代码/`
- `main/runs/EP03/`、`main/runs/EP03-freshrun-20260810-1730/`
- `审核前端/**`（P1 所有权）
- `稳定生产/challengers/cross-track-safety-v1/**`
- `benchmark/EP03-ASR-mini-gold-v1/gold.json`、`label.html`（原文件保留，不动 UI；同目录只加 README.md）
- gold.json 中任何 `reviewer / reviewed_at / gold.transcript / gold.speaker_attribution / gold.missed_sentences` 已经填写的字段（本轮它们全部为空，见迁移报告）

---

## 3. 目录

```
稳定生产/challengers/asr-speaker-v1/
├── README.md                       (本文)
├── audits/
│   ├── environment.md              M3 / 16 GB / arm64 运行前提与 VM 沙箱降级
│   ├── faster-whisper.md
│   ├── funasr-paraformer.md
│   ├── funasr-fsmn-vad.md
│   ├── funasr-campp.md
│   ├── mlx-whisper.md
│   ├── jiwer.md
│   ├── pyannote-metrics.md
│   ├── zxkane-audio-transcriber.md
│   └── mimo.md                     明确排除的原因
├── schemas/
│   ├── word_record.schema.json
│   ├── speaker_interval.schema.json
│   ├── normalized_transcript.schema.json
│   └── vad_intervals.schema.json
├── scripts/
│   ├── audit_baseline_sha.py       复用 before_metrics.json 中的 baseline SHA，M3 上二次校验
│   ├── slice_baseline_from_freshrun.py
│                                   把 05_asr/{female,male}.transcript.json 按 12 段时间窗切出
│                                   → normalized/faster_whisper_small/S*/{female,male}.words.json
│   ├── adapters/
│   │   ├── faster_whisper_adapter.py    输入：faster-whisper JSON → normalized
│   │   ├── funasr_paraformer_adapter.py
│   │   ├── funasr_fsmn_vad_adapter.py
│   │   ├── funasr_campp_adapter.py
│   │   └── mlx_whisper_adapter.py
│   ├── run_funasr.py               在 M3 本机执行，raw 保存到 main/runs/.../raw/funasr/
│   ├── run_mlx_whisper.py          在 M3 本机执行
│   ├── build_baseline_metrics.py   before_metrics.json（12 段+baseline SHA 引用）
│   ├── build_hypotheses_layer.py   把 normalized 写入 benchmark/hypotheses/<engine>/S*/
│   ├── score_asr_benchmark.py      CER + sub/del/ins + hallucination insertion + 说话人指标
│   ├── verify_champion_untouched.py
│   ├── rebuild_gold_v2.py          迁移 gold.json → gold.v2.json + gold.json.backup
│   └── run_tests.py
├── tests/
│   ├── fixtures/
│   │   ├── silence_10s.wav          合成，测试 hallucination
│   │   ├── single_speaker.wav
│   │   ├── two_speaker_alternating.wav
│   │   ├── two_speaker_overlap.wav
│   │   ├── loudness_delta_two_tracks/{female,male}.wav
│   │   └── synthetic_gold.json      合成 CER=0 / DER 已知的 fixture
│   ├── test_adapter_contract.py
│   ├── test_hallucination_on_silence.py
│   ├── test_speaker_stability.py
│   ├── test_alternating_speakers.py
│   ├── test_overlap_ambiguous.py
│   ├── test_loudness_not_identity.py
│   ├── test_timestamp_validation.py
│   ├── test_reproducibility_hash.py
│   ├── test_word_level_not_downgraded.py
│   └── test_scorer_synthetic.py
├── environment/
│   ├── requirements.faster-whisper.txt
│   ├── requirements.funasr.txt
│   ├── requirements.mlx-whisper.txt
│   ├── requirements.scorer.txt
│   └── venv_layout.md              每引擎独立 venv 的原因与创建步骤
└── exact_commands.sh               所有真实执行命令（M3 上一条条跑）
```

对应运行输出目录：

```
main/runs/EP03-asr-speaker-v1/
├── before_metrics.json             12 段清单+baseline SHA 冻结+现有 baseline hypothesis 来源
├── baseline_sha256.json            复用 before_metrics 里的 SHA + benchmark 音频的 12×3 段 SHA
├── raw/
│   ├── faster_whisper_small/       (从 freshrun 切出的 baseline 词流)
│   ├── funasr_paraformer/          (M3 上填入)
│   ├── funasr_fsmn_vad/            (M3 上填入)
│   ├── funasr_campp/               (M3 上填入)
│   └── mlx_whisper_turbo/          (M3 上填入)
├── normalized/                     同上 5 组
├── runtime_metrics.json            wall time / 峰值内存 / RTF / OOM 等；未跑段位标 NOT_YET_RUN
├── run_manifest.json               所有产物 SHA-256
└── benchmark_report.md             结论必须包含 WAITING_FOR_HUMAN_GOLD
```

对应 benchmark 目录（本 challenger 负责修好并挂出）：

```
benchmark/EP03-ASR-mini-gold-v1/
├── gold.json                       (旧文件，保留不动)
├── gold.json.backup                (原样复制)
├── gold.v2.json                    (schema v2；只有骨架和 SHA 校验，人工字段一律空)
├── migration_report.md
├── label.html                      (P1 所有权，本轮不动)
├── README.md                       (本轮由本 Challenger 新增；说明 gold 门禁、hypotheses 用法)
├── hypotheses/
│   ├── faster_whisper_small_vad_on/S01..S12/{female,male}.words.json
│   ├── funasr_paraformer/          (WAITING_FOR_M3_RUN)
│   ├── funasr_fsmn_vad/
│   ├── funasr_campp/
│   └── mlx_whisper_turbo/
├── metrics/                        (只有 gold 完成后才写入正式分数)
├── scorer/                         (score_asr_benchmark.py 的软链或引用)
└── schemas/                        (引用 challenger/schemas 的软链或拷贝)
```

---

## 4. 关键设计（写给评审）

1. **不用 LLM 清理转写**。所有 normalized 都保留工具的原始 token / punctuation；口癖不删除；数字/否定/英文专名保持工具输出。
2. **热词是单独实验臂**。若引入 hotwords，热词清单来源必须记录；不得把节目正确答案整段喂给模型。
3. **词级 → sample 时间线**：所有 normalized word 记录都必须能映射回 48 kHz 主时间线（在 benchmark 段内做 offset 加法）。工具级时间戳单调、`end > start`；违反者 adapter 直接抛错。
4. **VAD/说话人**：CAM++ 输出的 `speaker_id` 只是聚类 id，不能等同 female/male。normalize 阶段仅存聚类 id 与置信度；映射到 female/male 由 speaker profile 或人工确认，本 Challenger 不做未经审计的映射。
5. **hallucination 检测**：静音 fixture 与 gold 的 silence 区间上，score 会独立统计 "insertion in true silence" 次数，不允许被总 CER 掩盖。
6. **不清删除**：不做 punctuation 归并、不做同义词替换；raw/normalized 双份持有，normalization 规则版本化，评分同时输出 raw-score 和 normalized-score。

---

## 5. 环境与降级（本轮的关键限制）

任务书要求"在这台 Apple M3 / 16 GB Mac 上"运行三套模型。**本会话的 bash 沙箱是 Linux VM**，无法直接调用 M3 Metal，也不适合在 CPU 上勉强跑 FunASR/MLX（会导致 wall time / 峰值内存与真实 M3 数据完全不一致，是对任务口径的伪装）。

因此：

- 本 Challenger 完整交付**可复现的、经审计的推理脚本**、adapter、schema、scorer、gold 门禁与合成 fixture 单测。
- **真实推理留给 M3 本机**执行：`exact_commands.sh` 逐行标注哪些命令在何处执行（M3 vs 本会话）。
- Baseline `faster-whisper` 的推理可以直接**复用**现有 `main/runs/EP03-freshrun-20260810-1730/05_asr/{female,male}.transcript.json`（SHA 已冻结于 `before_metrics.json`），按 12 段时间窗切片，作为 baseline hypothesis 层。这不重跑，不动 Champion。
- runtime_metrics.json 对未跑段位显式写 `NOT_YET_RUN`，不留任何伪造 wall time。

见 `audits/environment.md`。

---

## 6. 出错与降级路径

- gold.json 迁移：只添加 `gold.v2.json` 与 `migration_report.md`；旧 `gold.json` 只读，另存 `gold.json.backup` 冗余保护。人工填写字段（若发现有）**在迁移报告中列出并保留**。本轮迁移报告显示：`reviewer / reviewed_at / gold.transcript / gold.speaker_attribution / gold.missed_sentences` **全部为空**。
- FunASR / MLX 尚未在 M3 跑通时，`normalized/<engine>/` 写占位文件 `STATUS.json`，`status=WAITING_FOR_M3_RUN`，adapter 与测试提前落地。
- 若人工 gold 完成前有人调用 `score_asr_benchmark.py`，脚本主入口 refuse 并打印 `WAITING_FOR_HUMAN_GOLD`；只有 `--allow-synthetic-only` 才能针对 tests/fixtures 里的合成 gold 跑（用于自测）。

---

## 7. NOT-TODO（本 Challenger 明确不做）

- 不改 Champion、正式转写、正式候选、活动分类、EDL、成片、orchestrator、审核 UI。
- 不重跑正式 faster-whisper（Champion 已冻结，SHA 见 before_metrics.json）；只从其输出切段落作为 baseline hypothesis。
- 不基于任何模型输出自动填 gold；不用 LLM 后处理转写；不把节目正确答案作为 hotwords。
- 不生成候选、不生成 EDL、不生成成片。
- 不接入 P1（审核前端）或 cross-track-safety-v1 的产物。
- 不宣布赢家（gold 未完成前 verdict 恒为 `WAITING_FOR_HUMAN_GOLD`）。
