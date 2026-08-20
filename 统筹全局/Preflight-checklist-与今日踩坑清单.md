# Preflight Checklist & 今日踩坑清单

> 版本：`preflight-v1`
> 更新时间：2026-08-15
> 作用：Agent 每次开工前跑一遍。**不通过就停**，不猜、不硬跑。
> 与 SOP 关系：本文件是 `Agent-SOP-跨模型稳定契约.md` 第 1.1 节引用的 preflight 具体清单。

---

## 使用方式

**每次开工前** 或 **切换阶段前**（比如从"审核"进"渲染"），从上到下跑一遍。任何一条 FAIL 都在解决之后才能继续。

一次跑完的推荐命令（人肉版）见文末 §11。

---

## 1. Python 版本

**为什么重要**：今天踩过。venv-mvp 的 Python 3.11 无法读 24-bit `WAVE_FORMAT_EXTENSIBLE`（`wFormatTag=65534`）的 WAV；DeepFilterNet 生成的降噪 WAV 就是这种格式，跑到 P0 前会炸。Python 3.12+ 的 `wave` 模块才加入 EXTENSIBLE 支持。

**检测**：
```bash
python3 --version
# 期望：Python 3.13.x 或 3.12.x
```

```bash
python3 -c "
import wave
w=wave.open('/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP04/EP04-v13-20260813-2002/denoise/track_01.deepfiltered.wav','rb')
print('ok', w.getnframes())
"
# 期望：ok <int>
# 失败：wave.Error: unknown format: 65534
```

**修复**：用 Python 3.13（系统自带 `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`）；venv 也建在它上面。

---

## 2. `audioop` 模块（Python 3.13 已移除）

**为什么重要**：今天踩过。`p0_mvp.py` 顶端 `import audioop`；Python 3.13 按 PEP 594 移除了这个 stdlib 模块，直接 ImportError。

**检测**：
```bash
<venv-python> -c "import audioop; print('ok')"
# 期望：ok
# 失败：ModuleNotFoundError: No module named 'audioop'
```

**修复**（在**隔离 venv** 里）：
```bash
<venv-python> -m pip install --quiet audioop-lts
```

`audioop-lts` 是 PyPI 上的官方 fork，纯 stdlib 兼容替代品。

---

## 3. `faster-whisper` 可 import

**为什么重要**：今天踩过。项目脚本 `p0_mvp.py` 会 `from faster_whisper import WhisperModel`；系统 Python 默认没有；项目自带 `pipeline/install_mvp.sh` 但只对 `asr-speaker-v1/environment/venv-mvp/` 生效（且那是 Python 3.11，不满足第 1 条）。

**检测**：
```bash
<venv-python> -c "import faster_whisper; print(faster_whisper.__version__)"
# 期望：1.2.x
```

**修复**：
```bash
<venv-python> -m pip install --quiet faster-whisper
```

**推荐 venv 位置**：`/private/tmp/venv-v13-py313/`（缓存目录，可释放；不进 git；不污染其他 venv）。

---

## 4. `ffmpeg` 可执行

**为什么重要**：orchestrator 的 resume / render / recheck-qc 每一步都需要 ffmpeg。Codex 沙盒没有 ffmpeg，误以为 Mac 也缺——**其实 Mac 有一个已装的**：`/Applications/爱问云.app/Contents/Resources/app/node_modules/@plasosdk/plasoffmpeg/ffmpeg`。

**检测**：
```bash
FF='/Applications/爱问云.app/Contents/Resources/app/node_modules/@plasosdk/plasoffmpeg/ffmpeg'
[ -x "$FF" ] && "$FF" -version 2>&1 | head -1
# 期望：ffmpeg version ...
```

**修复**：`brew install ffmpeg` 或 `homebrew/cask-versions`。装完后**必须在 `main/tools/tools.json` 里登记版本、SHA、许可证**（CLAUDE.md 边界 6）。

**注意**：`review-episode-config.json` 里的 `"ffmpeg"` 字段可能写死绝对路径（比如 `/opt/homebrew/Cellar/ffmpeg/9.0.1/bin/ffmpeg`），如果你机器上是别的路径，需要用 orchestrator `--ffmpeg` 覆盖。

---

## 5. HuggingFace 权重缓存

**为什么重要**：faster-whisper `small` 权重 783 MB；MLX Whisper `large-v3-turbo` 权重 1.5 GB。首次运行会自动下，之后走缓存。

**检测**：
```bash
ls ~/.cache/huggingface/hub/ | grep -iE 'whisper' 
# 期望至少一个：models--Systran--faster-whisper-small
# 可选：models--mlx-community--whisper-large-v3-turbo
```

**修复**：让下载自然发生（首次调用 `WhisperModel(...)` / `mlx_whisper.transcribe(...)` 会自己下）。**不用 curl 手拉**。

**授权**：从 HuggingFace 下权重是"下载"不是"上传"，符合 CLAUDE.md 边界（不上传真实素材）。但需要在 audits 里记录：URL / snapshot commit / SHA / 许可证。

---

## 6. 磁盘空间

**为什么重要**：v20 一次完整 resume 会写：
- `render_human_approved/`：~2 GB（stems + speech_mix + pre_loudnorm + master.wav + mp3）
- `render_machine_assisted_draft/`：~2 GB（同上，多 2 条机器剪口）
- 单期总占用 4-5 GB

**检测**：
```bash
df -h / | tail -1
# 期望 Avail 至少 15 GB
```

**修复**：见 `Agent-SOP` §4 表格里 "磁盘满" 一行。**用户表达"没空间" → 立刻 kill 后台任务 + 列可释放清单**。

**优先释放**（按大小、按重建代价倒序）：
1. `~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo`（1.5 GB，重建 4.5 分钟）
2. `/private/tmp/venv-*`（1-2 GB，重建几分钟）
3. `~/.cache/huggingface/hub/models--Systran--faster-whisper-small`（783 MB，重建 1-2 分钟）
4. 过渡 run 的 `render_*/*.wav`（每份几百 MB，删了不影响 EDL / 决定）
5. **不删** v20 依赖链上的任何东西（`v13-20260813-2002/denoise`、`semantic-transcript-v1-20260814-120456`）

---

## 7. 音乐素材

**为什么重要**：`reference-linear-v1` 时序模板要求特定音乐 SHA。SHA 不对就 BLOCKED，不能换首相近的。

**检测**：
```bash
python3 -c "
import hashlib
p='/Users/renting/Desktop/minglue/剪辑项目/音频参考库/raw material/第三集/片头片尾music.mp3'
print(hashlib.sha256(open(p,'rb').read()).hexdigest())
"
# 期望：3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83
```

**修复**：不修复。SHA 不对说明源文件被换过；这是**红灯**，停止一切渲染，找出谁改了。

---

## 8. v20 上游依赖链

**为什么重要**：v20 引用了两个上游 run 的产物；上游任一被改会破坏 v20 的 SHA 校验，resume/verify 都会失败。

**检测**：
```bash
python3 -c "
import json, hashlib
run='/Users/renting/Desktop/minglue/剪辑项目/main/runs/EP04/EP04-v20-20260814-1617'
m=json.load(open(run+'/analysis_reuse_manifest.json'))
src=m['source_run_dir']
# 校验 v13-2002 的 processing_manifest 与 denoise_manifest 与 p0 report SHA
for rel, expected in [
    ('processing_manifest.json', m['source_processing_manifest_sha256']),
    ('denoise/denoise_manifest.json', m['source_denoise_manifest_sha256']),
    (m['source_p0_report_relpath'], m['source_p0_report_sha256']),
]:
    actual=hashlib.sha256(open(src+'/'+rel,'rb').read()).hexdigest()
    print(rel, 'OK' if actual==expected else f'MISMATCH! actual={actual[:12]} expected={expected[:12]}')
"
```

**期望**：全部 OK。

**修复**：任何 MISMATCH → 停到 `BLOCKED: UPSTREAM_TAMPERED`；报告哪个文件被改；等用户判断"是我改错了要 revert"还是"确实要接受新上游 SHA"。

---

## 9. `CURRENT_DELIVERY_FACTS` 一致

**为什么重要**：`版本同步与交付事实门.md` 契约要求。跑一次 `check_current_delivery_sync.py --check` 就知道。

**检测**：
```bash
cd /Users/renting/Desktop/minglue/剪辑项目
python3 main/orchestrator/check_current_delivery_sync.py --check
# 期望：{"status": "PASS"}
```

**修复**：见 SOP §4 "sync check FAIL" 行。**不静默改 SHA**；先诊断字段差异，再让用户点头选修文档还是修数据。

**已知问题**（截至 2026-08-15）：`当前项目进度.md` 里的 `requirements_checkpoint_sha256` 是 08-14 老值，run 里已经是 08-15 01:30 的新值。这是 Codex 未收尾的账。resume 跑完后一次性更新。

---

## 10. Preview 与最终成片必须同混音方式（尚未加入 orchestrator 校验）

**为什么重要**：今天 mentor reject C026/C028，反馈 "剪辑痕迹重 / 声音明显小了"。溯源发现：
- Preview 用 `ffmpeg amix=normalize=1`（三轨平均降 9.5 dB）+ `qsin` 曲线交叉（中点 -6 dB）。
- 最终成片可能走别的路径。用户听 preview 觉得不满意，成片可能是另一种混音；判断依据错了。

**当前状态**：契约上还没写死 "preview 混音必须等同于最终成片"。这是**已知 gap**，OPT-023 追踪。

**临时应对**：
- Preview 生成脚本（`稳定生产/challengers/review-product-v1/scripts/build_mvp_package.py` 的 `render_ntrack_preview`）明确注释混音方式。
- 每次 refresh_calibration_package 后，抽 1 条 preview 与最终成片同段做人耳 A/B。
- **不承诺** preview 的听感 = 成片的听感，直到 OPT-023 修复。

---

## 11. 一次性 preflight 脚本（人肉版）

**位置**：`main/orchestrator/preflight.sh`（TODO：本 checklist 变成机器脚本后放这）

**当前版本**（复制到终端跑）：

```bash
set -e
cd '/Users/renting/Desktop/minglue/剪辑项目'

# 1. Python 版本
python3 --version | grep -E '3\.(12|13)' && echo '[1/9] python ok' || { echo '[1/9] python FAIL — 需 3.12+'; exit 1; }

# 2. audioop（如果用 venv 就换成 venv 的 python）
PY="${PY:-python3}"
"$PY" -c "import audioop" 2>/dev/null && echo '[2/9] audioop ok' || echo '[2/9] audioop 缺 — 在 venv 里 pip install audioop-lts'

# 3. faster-whisper
"$PY" -c "import faster_whisper" 2>/dev/null && echo '[3/9] faster-whisper ok' || echo '[3/9] faster-whisper 缺'

# 4. ffmpeg
FF='/Applications/爱问云.app/Contents/Resources/app/node_modules/@plasosdk/plasoffmpeg/ffmpeg'
[ -x "$FF" ] && echo '[4/9] ffmpeg ok' || echo '[4/9] ffmpeg 缺'

# 5. 权重
[ -d ~/.cache/huggingface/hub/models--Systran--faster-whisper-small ] && echo '[5/9] whisper-small 权重 ok' || echo '[5/9] whisper-small 权重缺（首次调用会自动下）'

# 6. 磁盘
AVAIL=$(df -k / | tail -1 | awk '{print int($4/1024/1024)}')
[ "$AVAIL" -ge 15 ] && echo "[6/9] 磁盘 ok（${AVAIL}G）" || echo "[6/9] 磁盘紧张（${AVAIL}G < 15G）"

# 7. 音乐 SHA
MUSIC_SHA=$(shasum -a 256 '音频参考库/raw material/第三集/片头片尾music.mp3' | awk '{print $1}')
[ "$MUSIC_SHA" = '3f3a7150c43c21fe5709a8a7b7152590a77579bd4ce87d3ad0e15ed1bb81ed83' ] && echo '[7/9] 音乐 SHA ok' || echo "[7/9] 音乐 SHA 不对: $MUSIC_SHA"

# 8. v20 上游依赖链（简化版，只查目录存在）
[ -d 'main/runs/EP04/EP04-v13-20260813-2002' ] && [ -d 'main/runs/EP04/EP04-semantic-transcript-v1-20260814-120456' ] && echo '[8/9] v20 上游依赖 ok' || echo '[8/9] v20 上游依赖缺'

# 9. sync check
python3 main/orchestrator/check_current_delivery_sync.py --check 2>&1 | grep -q '"status": "PASS"' && echo '[9/9] sync ok' || echo '[9/9] sync FAIL（详情：python3 main/orchestrator/check_current_delivery_sync.py --check）'
```

---

## 12. 今天（2026-08-15）实际踩过的坑（作为 preflight 存在的证据）

按遇到顺序：

1. **`sys.executable`（Python 3.13）缺 faster-whisper** → 走 `pipeline/install_mvp.sh` 结果 install 脚本路径不存在（是 `稳定生产/challengers/asr-speaker-v1/pipeline/install_mvp.sh`，不是 `pipeline/install_mvp.sh`）。**Preflight #3 抓这个**。
2. **`venv-mvp`（Python 3.11）读不了 24-bit EXTENSIBLE WAV** → wave.Error: unknown format 65534。**Preflight #1 抓这个**。
3. **Python 3.13 装了 faster-whisper 但缺 audioop** → PEP 594 移除。**Preflight #2 抓这个**。
4. **ASR shootout 骨架依赖 FunASR / SenseVoice 权重** → 权重目录不存在，脚本 exit 3。**未接入 preflight**（暂不用 shootout 时跳过）。
5. **sync check FAIL（文档 SHA vs run 实际不一致）** → Codex 08-14 建契约后，08-15 01:30 又更新过 requirements_checkpoint，没同步文档。**Preflight #9 抓这个**。
6. **磁盘紧张** → 用户主动喊停。**Preflight #6 抓这个**（阈值 15 G）。
7. **审核前端不显示保存路径** → 用户不知道 draft/decisions 存哪。**Preflight 抓不到**（属于 UX 层），OPT-022 追踪。
8. **preview 混音方式与最终成片可能不一致** → mentor reject 反馈不对得上成片。**Preflight #10 记录**，OPT-023 追踪。
9. **`serve-review` 子命令名写成 `serve`** → argparse 拒绝。**属于命令拼写错误**，SOP §2.2#5 覆盖（modelfail 后立刻自决改）。

---

## 13. 修订规则

新增 preflight 项时必须：

1. 说明触发场景（哪次会话 / 什么用户反馈）
2. 检测命令（可复制到终端跑）
3. 通过判据（明确 stdout / exit code）
4. 修复动作（明确命令，不含歧义）
5. 一次会话结束时把踩坑加到 §12

## 14. AI 接手时的偏好读取（2026-08-15 加入）

**每次 Agent 会话开始时（读完权威文档之后、动候选/规则之前）必查**：

1. `skills/editing-experience-distiller/output/preferences-20260815-1330/preferences_for_agent.md` — 11+ 条 P-XX 偏好规则清单，覆盖"哪些候选该出、哪些不该出、剪辑质量特征"
2. `F04-候选生成与跨轨安全.md` §"边界陷阱清单" — 从 mentor "剪辑痕迹" reject 归纳的 dBFS 阈值
3. `Agent-SOP-跨模型稳定契约.md` §9 — AI 反思学习循环的具体动作

**检测命令**：
```bash
ls -la '统筹全局/Agent-SOP-跨模型稳定契约.md' \
      'skills/editing-experience-distiller/output/preferences-20260815-1330/preferences_for_agent.md' \
      'skills/editing-experience-distiller/output/preferences-20260815-1330/aggregated.json'
```

**通过判据**：三份文件都存在且 `preferences_for_agent.md` 里含至少 P-01..P-11 条目。

**用户提交新审核后必做**：
```bash
python3 skills/editing-experience-distiller/output/preferences-20260815-1330/distill_preferences.py
```
重新聚合 65+N 条决定，检查是否有新的 feedback 模式需要写进 preferences_for_agent.md 或 F04。

**违规判据**：Agent 会话结束前若没读 preferences_for_agent.md，或用户新审核后没跑 distiller → SOP 违规，下次会话开工前必须补做。
