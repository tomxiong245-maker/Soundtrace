# EP03 主麦 automix v1 首次试跑分析

> 生成时间：2026-08-17-1230
> 数据来源：`main/runs/EP03-AUTOMIX-v1-20260817-1227/`
> **本文可靠度**：低-中。5 分钟片段结果无法证明全片；单遍 loudnorm 常见 ±1-2 LU 偏差；能量启发式主导轨判断不等于说话人识别。

## 每条陈述可靠度分档

- **[实测数据]** = 本次运行直接测得的客观数值
- **[工程推断]** = 从数据 + 代码逻辑推断出的结论
- **[主观假设]** = 需要真人听审确认的听感判断
- **[已决定方向]** = 用户 2026-08-17 明确批准的方向
- **[未验证]** = 尚未有证据支持，只是待验证假设

## 实验设置

**[已决定方向]** 主麦 automix 通过 `automix-v1` Challenger 上线，不改主流程；套 `reference-linear-v1` 时序 + EP03 mentor 冻结的 release-spec（Integrated -22.2 LUFS / TP ≤ -1.0 dBFS / LRA 7.9 / mp3 192 kbps stereo 48 kHz）。

**[实测数据]** 输入：
- 原始双轨：`ZOOM0008_Tr1.WAV` + `ZOOM0008_Tr3.WAV`（pcm_s24le / 48 kHz / mono / SHA `2648ac12…` + `3f654fb5…`）
- 只取前 300 s 做首次验证
- 固定音乐：`片头片尾music.mp3`（SHA `3f3a7150…`）

**[实测数据]** 算法参数：
- 帧窗口 20 ms
- 主导判定 min_gap_db = 3.0 dB
- 非主导 attenuation = -12 dB
- crossfade = 30 ms
- ambiguous 帧 fallback 到 -3 dB 均分（2 轨）

## 结果

**[实测数据]** 处理耗时：14.5 s 处理 300 s 音频（RTF ≈ 0.048，速度足够）

**[实测数据]** 输出文件：
- 路径：`main/runs/EP03-AUTOMIX-v1-20260817-1227/output/EP03_first5min.automix.mp3`
- SHA-256：`a3c9f615ba3873ece7c0da17d2114f04c0309acb49a7a86a4c9a7e3e846cd999`
- 时长：342.976 s（= 5 s 片头音乐 + 300 s 语音 + 37.976 s 片尾音乐 ✓ 符合 reference-linear-v1）
- 格式：mp3 / 192 kbps / stereo / 48 kHz ✓

**[实测数据]** 主导轨分布（15000 帧共 300 s）：
- Tr1 primary: 5,363 帧 (35.75%)
- Tr3 primary: 7,574 帧 (50.49%)
- ambiguous: 2,063 帧 (13.75%)

**[实测数据]** Loudness：
- Integrated: **-24.9 LUFS**（目标 -22.2 ± 1.0；差 -1.7 LU，超容差 0.7 LU）
- True Peak: **-4.3 dBFS**（目标 ≤ -1.0；符合 safety floor，且远离削波）
- LRA: **4.8 LU**（目标 7.9；差 -3.1 LU）

## 结论

**[工程推断]** 时序契约通过：mp3 时长精确匹配 reference-linear-v1 三段结构（5 s intro + speech + 37.976 s outro）；ffmpeg filter chain 正确执行 intro fade / outro delay。

**[工程推断]** Loudness 差 1.7 LU 属于**已知的单遍 loudnorm 偏差**：ffmpeg loudnorm 单遍常见 ±1-2 LU 精度。修法是双遍 loudnorm（第一遍 measured_I / measured_TP / measured_LRA 反馈到第二遍参数）。这不是 automix 算法错误。

**[工程推断]** LRA 4.8 LU 偏小 3.1 LU：5 分钟片段的动态范围本身就比全片小；全片跑完预计 LRA 会接近目标。也可能是 automix 的 ducking 均匀化了动态。

**[工程推断]** 主导比例 Tr1 35.75% : Tr3 50.49% : ambiguous 13.75% 看起来合理：两个说话人有各自主导时段，双方同时说话或都不说话被判为 ambiguous。**具体是否与真实说话人分布一致，需要人工核对录音；这只是能量启发式，不是 speaker gold**。

**[主观假设]** 剪口自然度：30 ms crossfade 应能避免咔嚓声，但**5 分钟片段是否有明显的主轨切换伪音**必须用户耳朵判断。已发送 mp3 给用户。

**[未验证]** v20 之前 mentor reject "声音明显小了"是否被 automix 解决：本轮实测 -24.9 LUFS 反而比之前的 -29.6 LUFS 高 4.7 LU，方向对；但仍差目标 1.7 LU。等双遍 loudnorm 修正后应达标。

## 已知局限

- **[已决定方向]** ambiguous 帧目前 fallback 到均分。这与旧 amix 平均在 ambiguous 时段听感相同——**没有把两轨都保留声音**只是在有人清晰主导时选择主轨。改进方向：ambiguous 时保持"最近一次 primary 主导"（sticky 主轨），或者引入 pyannote speaker turn 判断。
- **[未验证]** 能量最响的轨 ≠ 该说话人的主轨（有可能是别人串音很响）。真正的说话人主轨判断需要 pyannote speaker diarization。当前主导判断可能在串音区域错选。
- **[未验证]** 5 分钟片段代表性：EP03 是双人对谈，30 分钟不同片段主导比例可能不同；全片跑完才知道分布。
- **[已决定方向]** 只支持等长同采样率的 mono 输入；EP04 三轨需要另写 3-track adapter 或让 automix_v1 支持 N-track flag。

## 下一道门

- **[已决定方向]** 用户听过 5 分钟 mp3 后反馈：主轨切换是否自然、有无明显串音伪音、片头片尾时序是否合理。
- **[待做]** 如果听感 OK，跑完整 30 分钟版本（预计 90 s 处理时间）。
- **[待做]** 修双遍 loudnorm 让 integrated 精确到 -22.2 LUFS。
- **[待做]** pyannote speaker diarization 装好后，让 automix 从 diarization RTTM 读主导，替代能量启发式；这是"真正的跨轨归属"。
- **[待做]** 加 EP04 三轨支持（automix_v1 已支持 N-track，但 adapter 目前是 2-track flag 结构，需另写 3-track adapter 或让 flag 变通用）。

## 相关证据

- Run 目录：`main/runs/EP03-AUTOMIX-v1-20260817-1227/`
- 命令与参数：见 `tmp/automix_stats.json`
- ducked 中间轨：`tmp/track_00.ducked.wav`、`tmp/track_01.ducked.wav`
- 语音 mono（加音乐前）：`tmp/speech.mono.wav`
- 发布 spec 目标来源：`main/orchestrator/release_specs.json`（EP03 mentor 冻结值）
- 音乐时序模板：`main/orchestrator/music_templates.json` → `reference-linear-v1`
