# FFmpeg 9.0.1 · 本机运行审计

> 状态：2026-08-14 为本轮 EP04-v16 的本机渲染依赖。它不是项目内置二进制，也不上传任何节目素材。

## 固定信息

- 可执行文件：`/opt/homebrew/bin/ffmpeg`
- 配套探测器：`/opt/homebrew/bin/ffprobe`
- 安装方式：Homebrew Core 的 arm64 Sequoia bottle，`brew install ffmpeg`
- 版本：`9.0.1`
- 配方许可证：`GPL-3.0-or-later`
- 上游主页：`https://ffmpeg.org/`
- Homebrew bottle SHA-256：`2f3826072b1c1a24a51c167d84167a9ec92523e2427b030aa10657275e426b62`
- 本机 `ffmpeg` SHA-256：`11012f10d9d2eff4df94d760eec5964980880ced20bd4cdbd9f82ec399867e9d`
- 本机 `ffprobe` SHA-256：`6435b1b488afc9af1a22de2b839bfd1ef4a29d932122898f0c79dcffb43bdfb8`

## 本项目使用范围

- 读本地 WAV/MP3，生成 run 内的降噪中间格式、审核 A/B、三轨直混试听、编辑 stem、混音和 MP3；
- 命令由项目 Python 脚本显式调用，输入/输出路径、版本和命令会落在本期 run 的日志；
- 不调用云端 API，不启动遥测，不把真实音频发送到网络；
- 不能覆盖原始 WAV、历史 run、Champion 或 Mentor 成果。

## 已知限制

- Homebrew 的可执行文件位于系统前缀，不是与项目一起复制的独立部署包；升级或重新安装后必须重录版本、SHA 并重新做本机渲染验证。
- FFmpeg 的 GPL 许可及启用的编解码器应在对外分发本工具或二进制前由负责人再做发布层审查。本项目当前只在本机生成音频派生产物。
- 本轮只验证了可执行性、滤镜可用性和真实 run；最终发布规格仍需 Mentor 确认与整片听审。
