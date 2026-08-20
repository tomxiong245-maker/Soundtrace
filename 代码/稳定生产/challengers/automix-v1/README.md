# automix-v1 (Challenger)

> 状态：**SKELETON + FIRST_RUN_PENDING**
> 日期：2026-08-17
> 隔离目录：`稳定生产/challengers/automix-v1/**`

## 目的

N 条 mono 语音轨 → 1 条 stereo mp3 主麦成片。用户 2026-08-17 明确批准："第一条赶紧做"（主麦 automix）。目标是让 preview 混音 = 成片混音，消除现有 `amix=normalize=1` 平均降 9.5 dB 导致的 mentor reject "声音明显小了"（OPT-023）。

## 算法

1. 每 20 ms 窗口对每轨算 RMS
2. 主导轨 = RMS 最高，若最响与次响差 &lt; 3 dB 则视为 ambiguous
3. gain envelope：primary 0 dB / non-primary -12 dB / ambiguous 均分
4. envelope 之间 30 ms crossfade（防咔嚓声）
5. 应用 gain → 相加 → mono speech
6. 拼片头/片尾音乐（`reference-linear-v1` 时序：0-5s 纯音乐、5-16s crossfade、片尾 22s 淡入、37.976s 尾乐）
7. ffmpeg loudnorm → integrated LUFS `-22.2` / TP ≤ `-1.0` dBFS（EP03 mentor 冻结的 release-spec 目标；TP 从 mentor 实测 -0.1 下推到 -1.0 作 safety floor）
8. mp3 192 kbps stereo 48 kHz encode

## 依赖

- Python stdlib: wave / array / math / subprocess
- 外部：ffmpeg（`/opt/homebrew/bin/ffmpeg`）
- **不依赖 numpy / pyannote / 任何新装依赖**
- **不依赖 speaker diarization**——用能量启发式做主导轨判断；等 pyannote 上线后可以让 automix 从 diarization RTTM 读主导 speaker 替换能量启发

## 输入契约

- 每条 mono WAV，48 kHz（其它采样率 fail closed）
- 所有轨等长
- 音乐素材：mp3，长度 ≥ 39s（片头 16s + 尾 37.976s + 1s margin）

## 输出

- `output.mp3`：主麦成片
- `<tmp>/automix_stats.json`：每轨作为 primary 的帧数、ambiguous 比例
- `<tmp>/track_XX.ducked.wav`：中间 ducked mono（可复查）
- `<tmp>/speech.mono.wav`：mix 前的语音 mono（可复查）

## 用法

```bash
python3 scripts/automix_v1.py \
  --tracks /Users/renting/Desktop/minglue/剪辑项目/音频参考库/raw\ material/第三集/ZOOM0008_Tr1.WAV \
           /Users/renting/Desktop/minglue/剪辑项目/音频参考库/raw\ material/第三集/ZOOM0008_Tr3.WAV \
  --music /Users/renting/Desktop/minglue/剪辑项目/音频参考库/raw\ material/第三集/片头片尾music.mp3 \
  --release-spec /Users/renting/Desktop/minglue/剪辑项目/main/orchestrator/release_specs.json \
  --music-template /Users/renting/Desktop/minglue/剪辑项目/main/orchestrator/music_templates.json \
  --template-id reference-linear-v1 \
  --output /path/to/output/EP03.automix.mp3 \
  --tmp-dir /path/to/tmp
```

## 边界

- 不改 `main/tools/tools.json`、`main/orchestrator/*.py`
- 不替换现有 `assemble_program.py`（Champion 保持）
- 主导轨判断当前是能量启发式，不是真正说话人识别；`primary/bleed/ambiguous` 与 F03 里的 activity 启发式性质相同，只用于主麦选择，不产 speaker_id
- ambiguous 帧目前 fallback 到均分 —— 这时听感与旧 amix 平均相似；改进方向是"最近一次 primary 保持一段时间"（sticky 主轨）

## 上线路径

automix-v1 通过 tool-orchestrator-v2 上线，不改主流程：
1. `automix_v1.py` 作为脚本本身（不动）
2. `tool-orchestrator-v2/adapters/registry.json` 追加一条 adapter contract
3. `main/tools/tools.json` 追加一项 tool（不改现有）
4. planner_v2 在渲染阶段挂进 plan
