# mfa-alignment-v1 · Challenger

**Slot**: 修 ASR 词 boundary 50-150ms 误差问题。用 Montreal Forced Aligner (MFA) 3.4+
音素级 alignment 得到 10-20ms 精度的词边界。

**依据**: 2026-08-17 用户听审 EP04 五次迭代（v20/v21/v22/v23/v24/v25），每次
ASR-based 手工扩 100/200ms 边界 都有残留或误伤，直到 v26 用 MFA 直接给
精确边界一次到位。用户反馈"做的太好了 赶紧写入总控"。

## 产物

- `scripts/mfa_align_and_extract_boundaries.py` · 独立 tool，输入候选 + 3 轨 wav +
  ASR 分析目录，输出 `mfa_boundaries.json`（每候选精确 word/phone start-end）
- `docs/2026-08-17-mfa-v26-integration.md` · 集成决策 + 依赖清单 + 局限
- `tests/test_parse_textgrid.py` · TextGrid parser 单测

## 依赖（外部 · 一次性装好）

    # 1. Conda (miniforge, arm64)
    curl -L -o /tmp/miniforge.sh \
      https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh
    bash /tmp/miniforge.sh -b -p ~/miniforge3

    # 2. MFA 3.4+
    ~/miniforge3/bin/conda install -c conda-forge montreal-forced-aligner -y

    # 3. Chinese tokenization (spacy backend)
    ~/miniforge3/bin/pip install spacy-pkuseg dragonmapper hanziconv

    # 4. Mandarin acoustic + dictionary
    ~/miniforge3/bin/mfa model download acoustic mandarin_mfa
    ~/miniforge3/bin/mfa model download dictionary mandarin_china_mfa

**约束**：字典是纯中文 → 英文 token（如 GoGoFlow 里的"go"）OOV → 本工具自动
剥离非中文字符再喂给 MFA。英文/混合词候选**不能** MFA 精修（回落到手工
扩边界 或 走人审）。

## 用法

    python3 scripts/mfa_align_and_extract_boundaries.py \
      --candidates run/all_candidates.json \
      --tracks raw/Tr1.WAV raw/Tr2.WAV raw/Tr3.WAV \
      --asr-transcript-dir run/analysis/ \
      --context-seconds 5 \
      --head-pad-ms 50 --tail-pad-ms 50 \
      --out run/mfa_boundaries.json

单候选 ~10-20s CPU（局部 10s 段 alignment）。

## 输出 schema

    {
      "schema_version": "mfa-boundaries-v1",
      "refined_count": N,
      "skipped_count": M,
      "refined": [
        {
          "candidate_id": "C007",
          "target_token": "呃",
          "mfa_local_start": 5.160,
          "mfa_local_end":   5.380,
          "mfa_raw_start":   354.240,
          "mfa_raw_end":     354.460,
          "head_pad_ms": 50, "tail_pad_ms": 50,
          "refined_start_raw": 354.190,
          "refined_end_raw":   354.510
        }, ...
      ],
      "skipped": ["C014", ...]   // OOV or MFA didn't match target
    }

下游 EDL 生成器读 `refined_start_raw` / `refined_end_raw` 替代 candidate.start_sample /
end_sample（若可用）。

## EP04 v26 baseline（用户 accept）

- C007 "呃" · ASR 354.080-354.760 (680ms) → **MFA 354.240-354.460** (220ms)
- C023 4 个"然后" · ASR 959.34/959.76/960.08/960.08 → **MFA 959.28/959.67/959.87/960.03**

MFA 精度让"呃"边界从 680ms 缩到 220ms（ASR 呼吸头尾误加），且不会误伤
相邻"就是"起声。

## 边界

- 不覆盖 Champion `main/orchestrator/*.py`
- 不改 `candidate_rules.v19.json`（v19 只声明 `boundary_strategy`；本 tool 是**消费者**）
- 未通过独立复核；晋升 champion 需 EP05+ 数据验证跨期稳定性
