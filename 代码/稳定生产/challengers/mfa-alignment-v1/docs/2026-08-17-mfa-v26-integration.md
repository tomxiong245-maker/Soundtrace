# v26 · MFA 集成决策 · 2026-08-17 20:20

**用户 verdict**: "做的太好了 赶紧写入总控 赶紧完善流程 我们之后主要的调整
就可以基于偏好而非这种基本的东西了"

**含义**：**候选边界精度**从此不再是待解问题（有 MFA 保底），后续迭代重心是
**偏好学习 / case-based memory / rule mining**（see `evolution/README.md`）。

## 事实（[HIGH]）

- MFA 3.4.1 通过 conda-forge 装到 `~/miniforge3/bin/mfa`（依赖 `_kalpy` C 扩展，
  pip 版本无法用）
- mandarin_mfa acoustic + mandarin_china_mfa dictionary 下载完成
- EP04 两个候选局部段 MFA align 用时 ~50s；输出 TextGrid 音素级 boundary
- v26 clips 用户 accept，证明 MFA 精度 20ms 足以取代手工扩 100-200ms 边界

## v26 vs 之前迭代对比

| 版本 | 边界策略 | 用户反馈 |
|---|---|---|
| v20 | ASR 词中间 386ms (原 codex EDL) | "没剪干净" |
| v21 | ASR 整词 680ms + `boundary_lock` 字段（但没实际改数字） | "没剪干净"×2 |
| v22 | 手工扩 100ms | "更明显了，完全没剪辑干净" |
| v23 | 手工扩 100ms + afade 30ms + loudnorm | "声音大小有问题" |
| v24 | 3 轨 amix + 扩 100ms + afade | "位置对了但音量" |
| v25 | 200ms 扩 + fade 精调 + amix + loudnorm | 用户跳过，转 MFA |
| **v26** | **MFA 精确边界 + 头尾 50ms pad + amix + loudnorm** | **"做的太好了"** ✓ |

## 关键洞察

- **ASR 词 timestamp 有 50-150ms 系统误差**（faster-whisper large-v3-turbo 在中文
  快语速下容易把呼吸头/尾算进词内），手工扩阈值找不到统一值
- **MFA 用 kaldi 音素级 GMM-HMM alignment**，中文 mandarin_mfa 模型误差 10-20ms
- MFA 需要中文 tokenizer（spacy-pkuseg + dragonmapper + hanziconv）—— pip 装
- MFA 对**英文词 OOV**：本项目候选偶尔含 "GoGoFlow" 里的 "go" 音节，MFA
  跳过 → 这类候选**永远走人审**（v25 已确定 C014 不剪）

## 集成到主流程

`稳定生产/challengers/e2e-auto-runner-v1/scripts/run_end_to_end.py` 未来加入
**stage 3.5 · MFA precision**：

    if mfa 可用 AND candidate.filler_token 是中文:
        refined = mfa_align_and_extract_boundaries(candidate, ...)
        candidate.start_sample = int(refined.refined_start_raw * 48000)
        candidate.end_sample   = int(refined.refined_end_raw * 48000)
        candidate.boundary_source = "mfa_mandarin_v26"
    else:
        candidate.boundary_source = "asr_extended_100ms"  # fallback

## 未做（[MED]）

- **未集成到 run_end_to_end.py 主流程**（今晚只固化 tool + 文档；下一期节目
  正式挂上）
- **未做双语字典**（"GoGoFlow" 类英文/混合还得走人审）
- **未做 conda env 自动化**（依赖安装 5-6 步，可以写成 setup.sh）
- **未做 CI 契约测试**：需要一份 fixture wav + fixture transcript + 期望
  boundary 校验

## 后续（用户明确的方向）

> "我们之后主要的调整就可以基于偏好而非这种基本的东西了"

MFA 解决了**基础技术层**（边界精度）。接下来 EP05+ 每期节目做完后，用户审
auto-cut → 反馈进 labels_lake → gate 判决自动收窄或放宽 → 系统自适应
你的偏好。

三条进化路径全部就绪（见 `evolution/README.md`），MFA 是路径 3（从视频/成片
学习）的**基础工具依赖** —— 未来做"从 mentor 成片提取规则"时也用它对齐
mentor mp3 vs raw 三轨。

## 相关记忆

- [[minglue-audit-feedback-20260817]]
- [[minglue-project-layout]]
- [[minglue-post-feature-analysis-md]]
