# speaker-diarization-v1 (Challenger)

> 状态：**IMPLEMENTATION_PENDING**（骨架齐 · 权重未下 · venv 未装）
> 日期：2026-08-17 立项 · 2026-08-19 骨架补齐
> 隔离目录：`稳定生产/challengers/speaker-diarization-v1/**`

## 目的

给每个词标注真实说话人 ID（`SPEAKER_00` / `SPEAKER_01` / ...），替代现在的 primary/bleed/ambiguous 能量启发式。用户 2026-08-17 明确批准："第四条主麦混音和跨轨归属你赶紧去做，能用外部的库用外部的"。

## 方案摘要

- 库：`pyannote.audio == 3.4.0`（MIT · 3.x 末端维护版）
- Pipeline：`pyannote/speaker-diarization-3.1`（MIT · AISHELL-4 DER 12.2% · 中文近场对谈 in-domain）
- 硬件：Apple M3 强制 CPU（MPS wontfix · issue #1337 timestamp 伪影）
- 网络：一次性下载 → `HF_HUB_OFFLINE=1` 完全离线
- Word-level 归属：pyannote turn + faster-whisper word timestamp + 时间重叠归属（照抄 WhisperX `assign_word_speakers` 算法）

## 已完成

- **审计**（2026-08-17）：`audits/pyannote-audio-3.4.0.md`。结论：可以进入 Challenger。锁 `pyannote.audio==3.4.0` + `pyannote/speaker-diarization-3.1`。MIT 许可可商用。Apple M3 走 CPU。中文 in-domain 强（AISHELL-4 DER 12.2%）。
- **模型 hash 兜底策略**：官方未公示权重 SHA-256 → 首次下载后自算并落 `models/hashes.txt`；每次加载前 verify，fail-closed。
- **骨架补齐**（2026-08-19）：
  - `scripts/run_diarization.py` argparse + docstring + 函数体 `raise NotImplementedError`（ast.parse 通过）
  - `scripts/assign_word_speakers.py` 算法已实现（WhisperX 归属逻辑）
  - `environment/requirements.txt`（`pyannote.audio==3.4.0` + `torch`）
  - `models/hashes.txt` 占位（等真人下载后填 SHA）
  - `checkpoints/2026-08-19-implementation-plan.md` M1–M4 四道门验收计划

## HF token + 权重下载步骤（用户手动一次性）

`pyannote/speaker-diarization-3.1` 和 `pyannote/segmentation-3.0` 是 HF gated repo —— 必须接受 user conditions 且带 read token 才能拉。**这步是一次性的**，装完后 `HF_HUB_OFFLINE=1` 永久离线。

### 步骤 1 · 到 HuggingFace 网页 accept license（一次性 · 需真人点击）

浏览器登录 [huggingface.co](https://huggingface.co)，逐个访问：

1. https://huggingface.co/pyannote/speaker-diarization-3.1 → 顶部 "Agree and access" 按钮
2. https://huggingface.co/pyannote/segmentation-3.0 → 同上
3. https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM → 同上

三个都要 accept · 缺一份触发下载时会 401。许可证本身是 MIT，accept 只是 HF gated 流程。

### 步骤 2 · 本地 `huggingface-cli login` 输 token

在 `Settings → Access Tokens` 建一个 `read` scope token，然后：

```bash
source 稳定生产/challengers/speaker-diarization-v1/environment/venv/bin/activate
huggingface-cli login
# 粘贴 hf_... token · 按 y 保存到 git credential
```

Token 落 `~/.cache/huggingface/token`；不要写进项目、不要 commit。

### 步骤 3 · 触发一次下载（把三份权重拉到本地缓存）

```bash
source 稳定生产/challengers/speaker-diarization-v1/environment/venv/bin/activate
python3 -c "from pyannote.audio import Pipeline; Pipeline.from_pretrained('pyannote/speaker-diarization-3.1', use_auth_token=True)"
```

首次约 300 MB · 走 hf-mirror 可加速。此命令只做下载 + 缓存，不做推理。

### 步骤 4 · 权重落盘 + 记 SHA

三份权重进 `~/.cache/huggingface/hub/models--pyannote--*/`。**不要**把权重 commit 进本 Challenger 目录（`.gitignore` 已排 `models/*`）。

计算 SHA-256 并覆盖 `models/hashes.txt` 里的占位（`0000...`）：

```bash
cd ~/.cache/huggingface/hub
find . -name 'config.yaml' -o -name 'pytorch_model.bin' | while read f; do
  shasum -a 256 "$f"
done
```

把三行 SHA 落到 `稳定生产/challengers/speaker-diarization-v1/models/hashes.txt`（保持格式 `<sha256>  <relative_path_from_models_dir>`）。之后 `run_diarization.py` 每次启动都会 verify，任一漂移 → exit(2)。

**做完这四步后，`HF_HUB_OFFLINE=1` 生效，脚本完全离线跑。**

## 尚未做（等下一步）

对照 `checkpoints/2026-08-19-implementation-plan.md`：

1. **M1 · 环境 + 权重**（真人一次性 · 上方四步）
2. **M2 · 骨架填充** —— `run_diarization.py` 四个函数体去掉 `NotImplementedError`
3. **M3 · EP04 首跑** —— 三轨 denoised 各出 RTTM + words_with_speaker.json
4. **M4 · 20 段人工听审** —— 词级正确率 ≥ 90% 才进入并联 Challenger

## 严禁

- 不改 `main/tools/tools.json`、`main/orchestrator/*.py`、`main/stages/stage_speaker_role_filter.py`（Champion）
- 不下载权重到项目主 Python 环境；只装到本 Challenger 的 venv
- 不走 MPS（issue #1337 timestamp 伪影 wontfix）
- 不上传任何音频；`HF_HUB_OFFLINE=1` 首次下载完后必设
- 不覆盖 activity 启发式；作为 additive 字段（保留原 primary/bleed/ambiguous）
- 不把权重 commit 进本 Challenger 目录

## 目录

```
稳定生产/challengers/speaker-diarization-v1/
├── README.md                             (本文件)
├── audits/
│   └── pyannote-audio-3.4.0.md           (11 节完整审计)
├── baseline/                              (占位 · EP04 首跑后落对照数据)
├── models/
│   └── hashes.txt                         (SHA lockfile · 下载后填)
├── environment/
│   ├── requirements.txt                   (pyannote.audio==3.4.0 + torch)
│   └── venv/                              (.gitignore · 真人一次性建)
├── scripts/
│   ├── run_diarization.py                 (骨架 · argparse + NotImplementedError)
│   └── assign_word_speakers.py            (算法已实施 · WhisperX 归属逻辑)
├── tests/                                 (M2 补齐单测)
├── checkpoints/
│   └── 2026-08-19-implementation-plan.md  (M1–M4 四门验收)
```

## 上线路径

speaker-diarization 上线不改主流程：
1. 通过 `tool-orchestrator-v2` registry 注册两个新 adapter：
   - `speaker_diarize_adapter`（wraps `run_diarization.py`）
   - `assign_word_speakers_adapter`（wraps `assign_word_speakers.py`）
2. `main/tools/tools.json` 追加两项 tool（不改现有）
3. planner_v2 在候选生成阶段读到"启用 speaker diarization"策略时挂进 plan

这个流程本身是 tool-orchestrator-v2 "反复做无用功"目标的验收样本。
