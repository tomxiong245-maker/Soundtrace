# EP03 素材清单（Track B 输入 baseline）

> 生成日期：2026-08-17
> 用途：Track B（发布规格实测）与 Track D（automix 真实素材验证）的输入 baseline

## Mentor 成片（发布规格反推目标）

- 路径：`音频参考库/成品/EP03.mp3`
- SHA-256：`8dd95ac34a784c6e0e198805d9ca246843c296d967d4a257c41b231d141353f4`
- 编码/率：mp3 / 48000 Hz / stereo（有损）
- 时长：`00:29:47.496`（1787.496 s）
- 比特率：192000 bps
- 大小：42,899,954 bytes
- mtime：2026-08-07 16:41:02

## 原始轨（automix 输入）

**EP03 是双轨节目**（CLAUDE.md 明确：旧 EP03 以双轨为主；三轨是 2026-08-11 起的新设计）。

- `音频参考库/raw material/第三集/ZOOM0008_Tr1.WAV`
  - SHA-256：`2648ac12d2df3d713e62549f2d52834da806d9ebf95f3294c3a0da163ce3e5dd`
  - pcm_s24le / 48000 / 24-bit / mono / 1786.993 s / 257,359,808 bytes
- `音频参考库/raw material/第三集/ZOOM0008_Tr3.WAV`
  - SHA-256：`3f654fb521681437ea7aded6dd581cec791d4cc16ef6ffcd8fe86379cc2ba0c4`
  - pcm_s24le / 48000 / 24-bit / mono / 1786.993 s / 257,359,808 bytes
- **无 Tr2**：`find -iname "*ZOOM0008*"` 仅返回上述两条

时长匹配：mentor 成片 1787.496 s vs 原始 1786.993 s（差 0.5 s）→ Mentor 剪得很轻，基本对应完整录制时长。

## 固定音乐

- `音频参考库/raw material/第三集/片头片尾music.mp3`
- SHA-256：`3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83` ← 与历史记录一致 ✓
- mp3 / 48000 / stereo / 59.976 s / 192 kbps / 1,439,474 bytes
- Mono 派生：`main/runs/INTRO-OUTRO-LEARN-v1-20260812-1810/work/music.mono.wav`

## Track D 特别注意

EP03 是**双轨**——如果用于 automix 验证，说话人识别在双轨场景是最简化版（每轨主要一个说话人）；EP04 三轨才是完整场景。策略：
- Track D 前期用 EP03 双轨验证 automix 主逻辑（ducking + 主轨选择）
- 后期用 EP04 三轨验证 diarization + 跨轨归属完整链路

## Track B 特别注意

Mentor 成片是 192 kbps mp3，反推的 loudness / TP 数值反映**mp3 编码后**的目标；如果最终发布也是 192 kbps mp3，直接可用；如果目标是 WAV master + MP3 发布双版本，还需要额外确认无损 master 规格（当前不存在公开的 EP03 WAV master）。
