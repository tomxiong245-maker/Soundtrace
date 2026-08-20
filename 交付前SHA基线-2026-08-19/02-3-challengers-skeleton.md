# 3 Challenger 上线 · Skeleton 阶段完成 · 2026-08-19

> **触发**：pre 前用户明确要"上 embedding + NISQA + pyannote"三个外部工具集成。
> **本轮成果**：三个 Challenger 骨架 + tools.json 登记 + v2 skeleton adapter · **不动 Champion · 不装依赖 · 不下载权重**。

## 三个 Challenger 骨架

### A · `case-memory-embedding-v1`（新建）
- **用途**：给 build_case_memory 加 audio embedding · 相似案例检索从"启发式字符串" → "耳朵指纹"
- **依赖**：Whisper encoder（复用 faster-whisper · 已装）+ faiss-cpu（未装 · 只声明）
- **3 个骨架脚本**：build_case_embeddings.py · embed_candidate.py · retrieve_similar_cases.py
- **测试**：契约测试骨架 unittest 通过

### B · `nisqa-cutverify-v1`（新建）
- **用途**：cut-verify Check 5 · Fraunhofer NISQA 无参考 MOS 打分 · 剪前剪后 delta > 0.5 触发 human_review
- **依赖**：nisqa（未装 · 只声明）
- **3 个骨架脚本**：check_nisqa_mos.py · compute_mos_delta.py · route_by_mos.py
- **红线**：Check 5 是**补充**不是**替代**前 4 项 · 前 4 项 REJECT → Check 5 跳过 · 前 4 项 PASS + Check 5 REJECT → 升级为 REJECT_QUALITY_REGRESSION
- **测试**：7 项 unittest 全绿（3 分支 + 3 skeleton raises + 1 parse sanity）

### C · `speaker-diarization-v1`（补齐已有 skeleton）
- **用途**：pyannote-audio 3.4.0 真正 diarization · 替代能量启发式 speaker_role_filter · DER 12.2%（AISHELL-4）
- **依赖**：pyannote.audio==3.4.0 + torch + torchaudio + huggingface_hub（未装 · 只声明）
- **实现状态**：SKELETON_ONLY → **IMPLEMENTATION_PENDING**
- **HF token 步骤（用户手动一次性）已写进 README**：
  1. huggingface.co/pyannote/speaker-diarization-3.1 accept license
  2. `huggingface-cli login`
  3. 触发下载
  4. 权重 SHA 记入 models/hashes.txt
- **验收计划已定**：M1 环境就位 · M2 骨架填充 · M3 EP04 首跑三轨 · M4 20 段等距抽样人工听审（≥90% 词级正确率、≤10% UNKNOWN、无系统性交换）

## Registry 登记（8 项）

### tools.json（+8 · 63 项总）

| 新 tool | reads_only | Challenger |
|---|---|---|
| build_case_embeddings | false | A |
| embed_candidate | true | A |
| retrieve_similar_cases | true | A |
| check_nisqa_mos | true | B |
| compute_mos_delta | true | B |
| route_by_mos | true | B |
| run_diarization | false | C |
| assign_word_speakers | true | C |

全部 `v2_status: "adapter_registered_skeleton"`。

### v2 registry（+7 新 · 1 rename · 61 项总）

- 7 个新 adapter 全部标 `skeleton: true`（dry_run 通过 · invoke 时因脚本 NotImplementedError 会 fail closed）
- `speaker-diarize-v1` adapter 改名 `run-diarization-v1` · tool_name 从 `speaker_diarize` → `run_diarization`（与 tools.json 对齐）

## 验证

```
verify.sh · 全绿
  Layer 12: 63 项 tool full_path 全可达
  Layer 13: skills · active=8 deprecated=5 index=1
  Layer 18: installed vs used · WARN 但不 block
  Layer 21: main/orchestrator/ 全登记或白名单

v2 契约测试 · 22/22 · OK
```

## 严格保留的边界

- `skills/cut-verify/` **一字节未动**（Challenger B 只包 Check 5 骨架 · 不改前 4 项）
- Champion `main/orchestrator/*` `稳定生产/scripts/*` `端到端学习剪辑/代码/*` 未动
- 未 `pip install` 任何依赖 · 未下载任何模型权重
- 骨架脚本全部 `raise NotImplementedError` · 但 `ast.parse` 全绿 · `--help` 可打
- 活代码 `/剪辑项目/` 未动 · 所有改动只在 `交付/最终交付文档/`

## 讲 pre 时可以说

> **"剪口质量验证有三层：**
> 1. 规则层 · 4 项 check（幻觉/静音位置/节奏/路由）· 现装开源工具
> 2. **神经预测层** · Fraunhofer NISQA · 无参考 MOS delta 判决（Challenger B · 骨架已建）
> 3. **案例相似度层** · Whisper encoder + FAISS · 耳朵指纹检索历史 mentor case（Challenger A · 骨架已建）
>
> **说话人判定**从能量启发式升级到 pyannote-audio 3.4.0 · 学界 SOTA · MIT 协议 · 中文会议 DER 12.2%（Challenger C · 骨架已建 · 权重下载待 HF token 一次性动作）
>
> 这四个都按项目 Champion/Challenger 隔离原则做 · 骨架在 Challenger 目录 · 独立复核 + benchmark 冻结 + 人工签字后再进 Champion。**不是纸上谈兵 · 有完整落地路径**。"

## 下一步（EP05 上线时做 · 不是现在）

1. **A**：`pip install faiss-cpu` · 实现 3 个骨架 · 用 EP04 数据跑 baseline 对比启发式检索
2. **B**：`pip install nisqa` · 实现 3 个骨架 · 用 EP04 baseline 跑一遍 · 分析 MOS delta 分布
3. **C**：用户手动跑 HF token 4 步 · 实现 run_diarization.py 4 个函数 · 用 EP04 三轨 denoised 跑 diarization · 抽 20 段人工验边界
4. 独立复核 · benchmark 冻结 · 人工签字 · 晋升 Champion

## 相关记忆

- [[minglue-project-layout]]
- [[minglue-construction-rules-first]]
- [[prefer-agent-over-workflow]]
