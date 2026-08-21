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
6. 拼片头/片尾音乐 (2026-08-20 起改用 EP03-learned 时序 · 参考 §音乐拼接时序 一节)
7. ffmpeg loudnorm → integrated LUFS `-22.2` / TP ≤ `-1.0` dBFS（EP03 mentor 冻结的 release-spec 目标；TP 从 mentor 实测 -0.1 下推到 -1.0 作 safety floor）
8. mp3 192 kbps stereo 48 kHz encode

## 音乐拼接时序 (2026-08-20 更新 · 学 EP03)

**背景**: 2026-08-19 用户明确要求：(1) 更早进入人声；(2) 片头/片尾音乐要淡入淡出；(3) 人说最后一句话时开始淡入片尾曲 (EP03 里没有 · 额外加)。

**参数** (全部在 `music_templates.json` 里的 template 条目下 · 配置化 · 不写死):

- `voice_start_seconds`: 语音在成片中的起始时间(秒) · 语音之前是片头曲
- `intro_fade_in_duration_seconds`: 片头曲从静音淡入的时长(秒) · 0 = 立即满音量
- `intro_fade_out_start_seconds`: 片头曲开始淡出的绝对时间(秒) · 通常 = voice_start
- `intro_fade_out_end_seconds`: 片头曲淡出结束时间(秒) · 达到 music_gain_db 背景残留
- `intro_music_gain_db`: 片头曲峰值电平(dB) · 默认 0 dB
- `music_gain_db`: 语音段背景音乐残留电平(dB) · 片头曲淡出目标 · reference-linear-v1 用 -60 (静音)
- `outro_fade_in_before_speech_end_seconds`: **从人说完最后一句往前 X 秒开始淡入片尾曲** · 人说最后一句时片尾曲已在淡入 · 这条是本次新增的核心逻辑
- `outro_fade_in_duration_seconds`: 片尾曲从静音升到峰值的时长(秒) · 0 = 硬进
- `outro_music_gain_db`: 片尾曲峰值电平(dB) · 默认 0 dB
- `outro_music_tail_seconds`: 人说完最后一句之后片尾曲继续播放的时长(秒)
- `outro_fade_out_duration_seconds`: 成片末尾片尾曲淡出到静音的时长(秒) · 0 = 硬结束
- `outro_fade_in_lead_seconds`: [DEPRECATED · fallback 兼容旧模板] 仅在未声明 `outro_fade_in_before_speech_end_seconds` 时生效 · 保留旧的常量音量硬进入行为

**EP03 学到的时序** (`reference-linear-v1` 当前默认):

| 阶段 | 参数 | 值 (秒/dB) | 语义 |
|------|------|-----------|------|
| 片头淡入 | intro_fade_in_duration_seconds | 1.5 | 片头曲从静音升起 |
| 语音进入 | voice_start_seconds | 3.0 | 比旧模板 5.0s 更早 · 学 EP03 头 3-6s 就有人声 |
| 片头淡出起 | intro_fade_out_start_seconds | 3.0 | 语音一到就开始淡片头 |
| 片头淡出终 | intro_fade_out_end_seconds | 10.0 | 比旧模板 16s 更快清干净 |
| 片头峰值 | intro_music_gain_db | 0.0 | 满音量 |
| 语音段背景 | music_gain_db | -60.0 | 语音段几乎无背景音乐 (旧模板 -12 dB 会残留) |
| **片尾淡入起** | **outro_fade_in_before_speech_end_seconds** | **3.0** | **从人说完最后一句往前 3 秒开始淡入** |
| 片尾淡入时长 | outro_fade_in_duration_seconds | 6.0 | 6 秒线性升起 (3s 覆盖语音末 + 3s 语音后) |
| 片尾峰值 | outro_music_gain_db | 0.0 | 满音量 |
| 尾乐时长 | outro_music_tail_seconds | 37.976 | 人说完后继续播 |
| 片尾淡出 | outro_fade_out_duration_seconds | 5.0 | 末尾 5s 线性到静音 |

**"人说完最后一句"取值**: 当前实现里, "人说完" = `speech_wav` 的 duration (由 ffprobe 读入)。上游 EDL 已剔除末尾静音 · 所以 `speech_wav` 末端就是最后一个 word 的 `end_seconds`。若上游未剔尾静, 未来可扩展从 EDL / transcript 读 `last_word.end_seconds` 传入 · 此接口留白。

**向后兼容**: `reference-linear-v1-legacy` 模板保留旧参数 (voice_start=5.0 / intro_fade_end=16.0 / outro_fade_in_lead=22.0 / music_gain_db=-12.0) · 走旧的常量音量分支。旧模板文件如果没有新参数, 代码通过 `.get()` fallback 到旧行为不会崩。

**AST**: `ast.parse` OK · `ffmpeg -f lavfi` 过滤器语法通过。

---

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
  --tracks <PROJECT_ROOT>/音频参考库/raw\ material/第三集/ZOOM0008_Tr1.WAV \
           <PROJECT_ROOT>/音频参考库/raw\ material/第三集/ZOOM0008_Tr3.WAV \
  --music <PROJECT_ROOT>/音频参考库/raw\ material/第三集/片头片尾music.mp3 \
  --release-spec <PROJECT_ROOT>/main/orchestrator/release_specs.json \
  --music-template <PROJECT_ROOT>/main/orchestrator/music_templates.json \
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
