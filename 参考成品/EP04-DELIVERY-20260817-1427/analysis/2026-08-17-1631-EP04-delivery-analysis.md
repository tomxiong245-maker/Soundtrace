# EP04 交付分析 · 2026-08-17 16:31

**可靠度声明**：本文陈述基于 ffmpeg 双次实测（pass1 分析 + 独立复测）与本 run 目录中 SHA256 已归档的两份 mp3 文件；不基于估计或引用。

## 事实（[HIGH]）

- codex 于 2026-08-17 15:04 生成 EP04 machine-assisted-draft mp3（SHA `c58bc296…`，79.5 MB，3313.6s）；mentor 已听审并认可"并轨做得很好"。[HIGH]
- 该 mp3 编码/容器全部达标（mp3 · 48000 Hz · stereo · 192 kbps · 3313.593s）。[HIGH]
- 该 mp3 响度**偏离目标**：Integrated -16.38 LUFS（目标 -22.2 差 +5.82 LU），True Peak -0.84 dBFS（超 safety floor -1.0 dBFS 0.16 dB）。[HIGH]
- 用双遍 loudnorm 修正（method=linear，一次 pass1 measurement + 一次 pass2 apply，20 秒），产出修正版 mp3（SHA `35adf2f2…`，同大小 79.5 MB，同 3313.6s，同编码参数）。[HIGH]
- 修正版**独立复测**：Integrated -22.46 LUFS（差目标 0.26 LU，在 ±1 容忍内），TP -6.91 dBFS（比 safety floor 低 5.91 dB），LRA 6.30 LU（在 ±2 容忍内）。**verdict = PASS_ALL_TARGETS**。[HIGH]
- 用户 2026-08-17 16:31 起听审后批准 loudnorm 修正版："好的，加进流程里面"。[HIGH]
- 修正是**线性归一化**（linear=true），只对全片做统一增益放缩；波形形状、剪辑决策、主麦切换、音乐 timing 与 codex 原版**逐 sample 一致**（信息层无变化，仅电平变化）。[HIGH]

## 判断（[MED]）

- codex draft 之所以 -16.38 LUFS 偏响，是因为 codex 的 automix 流程用了 orchestrator 里默认的**单遍 loudnorm**，遇到 EP04 这类高动态内容单遍常见 ±5 LU 误差（比 EP03 5-min 试跑那次 1.7 LU 更严重，原因是 EP04 内容 LRA 更小、平均电平更高）。**双遍 loudnorm 是修法**。[MED]
- 修正后 TP -6.91 dBFS 远低于 safety floor -1.0，说明源信号本来是高电平；线性 -5.8 LU 放缩把 TP 也拉下来了。不是"过度保守"，是内容层的自然结果。[MED]
- 修正版 LRA 6.30 稍低于目标 7.9，源于源内容动态本身就窄（人声主导对谈，无大动态变化）。未来若换成有大段音乐/环境音的节目，LRA 会自然回升。[MED]

## 已固化到流程的改动

1. `稳定生产/challengers/automix-v1/scripts/automix_v1.py`：
   - `ffmpeg_wrap_music_and_loudnorm` 从单遍改为**双遍 loudnorm**（`--loudnorm-passes 2` 默认）
   - True Peak 参数自动取 `min(target_true_peak_dbfs, target_true_peak_dbfs_safety_floor)`，即 -1.0 dBFS 而非 mentor 实测的 -0.1 踩线值
   - 新增 `--edl <path>`：读 `human_approved.edl.json::render_sync_cuts` 按 sample 精度应用剪切，两侧对称 linear crossfade（xf_half samples 各一侧）
   - 保留 `--loudnorm-passes 1` 用作 legacy 兼容与单元测试
2. `main/runs/EP04-DELIVERY-20260817-1427/DELIVERY_MANIFEST.json`：完整交付链路（mentor 内容审核 → user 响度审核）+ pass1/pass2 参数 + 独立复测数字 + 边界声明
3. `统筹全局/当前项目进度.md::CURRENT_DELIVERY_FACTS`：新增 `latest_delivered_master` 字段指向本 run（另立字段，不覆盖历史 `best_local_delivery`）

## 未做（诚实交代）

- 未晋升 codex draft 从 `MACHINE_ASSISTED_DRAFT_RENDERED` 到 `HUMAN_APPROVED` —— codex draft run 目录**只读**保持不动，本次批准动作记在**新交付 run**，不修改历史。
- 未跑 pyannote 版本对比（Python 3.11 venv 已装好、3 份权重已下 464 MB 缓存在 `~/.cache/huggingface/hub/`），因为 mentor 已认可 codex 的能量启发式主麦切换，无需盲跑 diarization。pyannote 环境保留作为下一期节目的备用工具。
- 未修改 `main/orchestrator/delivery_orchestrator.py`（4640 行 Champion 主流程）：本次交付走独立 challenger 目录 + 手工命令；晋升 orchestrator 走独立复核。
- 未删除 codex draft mp3（`main/runs/EP04/EP04-machine-assisted-draft-20260817-002/render_machine_assisted_draft/*.mp3` 79.5 MB + 同名 wav 636 MB + master_pre_loudnorm.wav 954 MB + speech_mix.wav 941 MB）—— 保留为源产物。若磁盘紧张可另开清理任务。

## 建议（[MED]）

1. 用户下一期节目（EP05+）应用 `automix_v1.py --loudnorm-passes 2` 作为默认；不再依赖事后手工修正。
2. codex 生成 machine_assisted_draft 时的 loudnorm 参数需要在 orchestrator 里明确改为双遍。位置：`main/orchestrator/delivery_orchestrator.py` 里调 loudnorm 的函数（Champion 边界，需独立复核晋升）。
3. 建立 QC 自动化：每次成品渲染后自动跑一次独立 ffmpeg loudnorm probe，vs 发布规格逐项 diff，diff 超容忍度即 fail-closed。可加入 `main/orchestrator/tests/` 作为渲染后契约测试。

## 相关记忆

- [[minglue-project-layout]]
- [[minglue-post-feature-analysis-md]]
- [[minglue-analysis-md-tracks]]
- [[minglue-audio-context-first]]
