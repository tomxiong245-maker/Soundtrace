# checkpoint · speaker-diarization-v1 实施 & 验收计划
> 生成日期：2026-08-19
> 状态：**IMPLEMENTATION_PENDING**（骨架齐 · 权重未下 · venv 未装）
> 前置文档：`../audits/pyannote-audio-3.4.0.md` · `../README.md`

## 一、总目标

用 `pyannote.audio == 3.4.0` + `pyannote/speaker-diarization-3.1` pipeline
产出真实说话人 diarization，在 **EP04** 三轨材料上首跑，并抽 **20 段**
人工听审验证 word→speaker 归属正确率，作为进入 Champion 的必要条件。

**不改** `stage_speaker_role_filter`（Champion 能量启发式），只作 additive
字段挂进 words JSON。

## 二、里程碑（顺序执行 · 前置门必须过）

### M1 · 环境就位（真人操作 · 一次性）
- [ ] `cd environment && python3.11 -m venv venv && source venv/bin/activate`
- [ ] `pip install -r requirements.txt`
- [ ] `pip freeze | grep -E "pyannote|torch"` 记录到 `environment/pip-freeze-2026-08-19.txt`
- [ ] HF token + accept license + 触发下载（README §"HF token" 四步）
- [ ] 三份权重落 `~/.cache/huggingface/`（不入库）
- [ ] `shasum -a 256` 三份权重 → 覆盖 `models/hashes.txt` 占位

**门 M1**：`hashes.txt` 三行非全零；`pip freeze` 中 `pyannote.audio==3.4.0`。

### M2 · 骨架填充（本 Challenger 代码 · Agent 可做）
- [ ] `scripts/run_diarization.py` 四个函数体去掉 NotImplementedError
- [ ] `verify_weight_hashes` 用 `hashlib.sha256` chunk 读 · 逐行比对
- [ ] `load_pipeline` 强制 `os.environ["HF_HUB_OFFLINE"] = "1"` + `pipeline.to(cpu)`
- [ ] 单元测试 `tests/test_run_diarization_unit.py` 覆盖 SHA fail-closed 路径
- [ ] `tests/test_assign_word_speakers.py` 已有算法覆盖率补齐（overlap 边界 case）

**门 M2**：`pytest tests/` 全绿；`ast.parse` 无报错。

### M3 · EP04 首跑（真数据）
输入：`main/runs/EP04/denoised/{host,guest1,guest2}.wav`（三轨 · 各 ~30 min）
- [ ] 对每轨跑 `run_diarization.py --input-wav <track> --output-rttm <track>.rttm --models-dir <abs>`
- [ ] 拿 EP04 已有 faster-whisper word timestamp JSON
- [ ] 对每轨跑 `assign_word_speakers.py --rttm <track>.rttm --transcript <words.json> --output <words_with_speaker.json>`
- [ ] 记录 RTF、内存峰值、每轨 speaker cluster 数
- [ ] 落 run 目录 `main/runs/EP04/challenger_diarization/2026-08-19/`

**门 M3**：三轨各出 RTTM + words_with_speaker.json；`speaker_id ≠ UNKNOWN` 词占比 ≥ 90%。

### M4 · 20 段人工听审（真人 · 抽样验证）
抽样规则：
- 每轨等距抽 **7 段**（3 × 7 = 21，取 20，去尾 1 段）
- 每段 ~15 s，覆盖：单人清晰 / 快速交替 / 有 backchannel（嗯/对）
- 用 Audacity/Reaper 拉出该段音频 + 该段 words_with_speaker.json 并列

真人核对：
- [ ] 每段列表机器分配的 speaker_id vs 真实说话人（host/guest1/guest2）
- [ ] 记录到 `checkpoints/2026-08-19-audit-20segments.md`（每段一行）
- [ ] 计算词级正确率 = correctly_assigned_words / total_words

**门 M4**（作为进入 Champion 的必要门槛，非本次任务目标）：
- 词级正确率 ≥ **90%**
- UNKNOWN 词占比 ≤ **10%**
- 无系统性交换（host 词大批被贴 guest 标签）

## 三、验收报告输出

跑完 M3 + M4 后，在 `checkpoints/2026-08-19-EP04-first-run-report.md` 记：
1. 实际命令 + 环境 SHA + pip freeze
2. 三轨 RTTM / words_with_speaker.json 输出 SHA
3. 20 段人工核对表 + 词级正确率
4. RTF / 内存峰值 / cluster 数
5. 失败样本（若有）+ 归因
6. 下一道门：是否进入并联 Challenger、tools.json 追加两项 adapter

## 四、严禁（本次任务边界）
- 不改 `main/tools/tools.json`（等 M4 过后另开任务）
- 不改 Champion `stage_speaker_role_filter`
- 不动 `main/orchestrator/*.py`
- 不上传任何音频（本地跑）
- 不绕过 SHA verify（fail-closed）

## 五、当前 checkpoint 状态

| 里程碑 | 状态 | 备注 |
| --- | --- | --- |
| 骨架文件补齐 | **DONE 2026-08-19** | 本次任务交付 |
| M1 环境就位 | pending | 需真人做一次性 HF login + 下载 |
| M2 骨架填充 | pending | 依赖 M1 |
| M3 EP04 首跑 | pending | 依赖 M2 |
| M4 20 段人工听审 | pending | 依赖 M3 |
