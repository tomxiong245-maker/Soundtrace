# nisqa-cutverify-v1 · Challenger 骨架

**状态**：`SKELETON_CREATED`
**创建日期**：2026-08-19
**目标 Champion**：`skills/cut-verify`（4 项独立 check · 一字节不动）
**本 Challenger 定位**：给 cut-verify **追加 Check 5**（无参考质量预测 · No-Reference MOS）

---

## 1. Check 5 是什么

用 Fraunhofer 预训练的 **NISQA v2.0**（CNN-LSTM · 训练集 90k+ 众包 MOS 样本）对
每个 render 后的 clip 打一个 0–5 的 **MOS 预测分**（Mean Opinion Score），
用来在**剪口位置的音频质量层**做客观兜底：

- **absolute mode**：剪后 clip 的 overall MOS < 3.0 → 标 `HUMAN_REVIEW`
- **delta mode**  ：剪后 - 剪前 MOS 下降 > 0.5 → 标 `REJECT`（剪口本身劣化质量）
- 否则 → `PASS`

---

## 2. Check 5 与前 4 项 check 的关系（关键）

> ⚠️ **Check 5 是补充 · 不是替代**。前 4 项 check 全部保留，Check 5 只在它们之后加一层客观质量兜底。

| Check | 层面 | 手段 | Champion 现状 | Challenger 追加 |
|-------|------|------|---------------|-----------------|
| Check 1 · 幻觉检测 | 语义/ASR 置信 | `word.probability` | 已上线 | 不动 |
| Check 2 · 静音位置 | 时域 | `pydub.silence.detect_silence` | 已上线 | 不动 |
| Check 3 · 节奏跳变 | 参数比对 | `cut_parameters.json` 阈值 | 已上线 | 不动 |
| Check 4 · 拼接策略 | policy 路由 | butt_splice vs crossfade | 已上线 | 不动 |
| **Check 5 · MOS 预测** | **感知质量** | **NISQA v2.0（预训练 CNN-LSTM）** | **无** | **本 Challenger 提议追加** |

**执行顺序**（Challenger 提议）：
```
候选 → Check 1 → Check 2 → Check 3 → Check 4 → [render 后] → Check 5 → verified_edl.json
                                                    ↑
                                  仅当前 4 项 verdict ∈ {ok, crossfade_50ms, butt_splice_recommended} 时执行
                                  仅当前 4 项已判 REJECT 时，Check 5 直接跳过（不覆盖前判决）
```

**互斥保证**：
- 前 4 项任何一项 `REJECT_*`：Check 5 **不执行**，直接沿用前判决。
- 前 4 项全部 PASS，Check 5 = REJECT：升级为 `REJECT_QUALITY_REGRESSION`。
- 前 4 项全部 PASS，Check 5 = HUMAN_REVIEW：降级为 `NEEDS_HUMAN_REVIEW`（不否决剪辑逻辑）。
- 前 4 项全部 PASS，Check 5 = PASS：保持原判决。

---

## 3. 目录结构（骨架）

```
nisqa-cutverify-v1/
├── README.md                              ← 本文件 · SKELETON_CREATED
├── TASK_CONTRACT.md                       ← 契约与验收条件
├── audits/
│   └── nisqa-2.0.md                       ← Fraunhofer NISQA 审计（4 段结构）
├── environment/
│   └── requirements.txt                   ← 占位 `nisqa`（注释未启用）
├── scripts/
│   ├── check_nisqa_mos.py                 ← 单 clip MOS 预测（骨架）
│   ├── compute_mos_delta.py               ← 前后 clip 差分（骨架）
│   └── route_by_mos.py                    ← 判决路由（骨架）
├── tests/
│   ├── test_nisqa_wrapper.py              ← unittest + mock · 3 分支
│   └── fixtures/.gitkeep
├── checkpoints/
│   └── 2026-08-19-skeleton-created.md
└── baseline/
    └── champion_sha256_before.txt         ← 空 · 待 promote 前填
```

---

## 4. 现状

- ✅ 目录骨架已生成
- ✅ 三个脚本 argparse + docstring + `NotImplementedError`（`ast.parse` 通过）
- ✅ 单元测试 mock 三分支
- ❌ **未** 安装 `nisqa`（禁止 pip install）
- ❌ **未** 下载权重
- ❌ **未** 触碰 `skills/cut-verify/` 任何内容
- ❌ **未** 改 Champion / `tools.json`

## 5. 后续（不在本次交付内）

1. Sandbox 内独立环境跑通 NISQA v2.0 官方 demo（一次 · 只读）。
2. 在 tests/fixtures 下放 3 条真实剪口前后 clip · 手工 MOS 标注对齐。
3. 在 3 集样本上跑 shadow · 与前 4 项判决交叉核对。
4. 走 promote 流程 → 才可进入 tools.json。
