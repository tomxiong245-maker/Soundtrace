# 审计 · Fraunhofer NISQA v2.0（No-Reference Speech Quality Prediction）

**审计目的**：判断 NISQA 是否适合作为 `cut-verify` skill 的 **Check 5 · 感知质量兜底**。
**审计人**：Challenger nisqa-cutverify-v1
**日期**：2026-08-19
**结论摘要**：**可用作补充信号 · 不可替代前 4 项 check** · 需在独立 sandbox 验证后再决定 promote。

---

## 段 1 · 是什么 / 谁做的 / 学术出处

**NISQA**（Non-Intrusive Speech Quality Assessment）是德国 **Fraunhofer HHI / TU Berlin**
Gabriel Mittag 等人发布的开源无参考语音质量预测模型。

- **模型架构**：CNN（帧级特征）+ Bi-LSTM（时序聚合）+ Attention pooling，输出 5 维 MOS：
  - `mos_pred`（整体 MOS）
  - `noi`（noisiness · 越高越静）
  - `col`（coloration · 频响自然度）
  - `dis`（discontinuity · 断裂/顿挫 —— **对剪口最相关**）
  - `loud`（loudness · 响度自然度）
- **训练集**：NISQA Corpus · 90k+ 众包 MOS 样本 · 涵盖编码/传输/降噪/回声/剪辑残留等失真。
- **License**：MIT · 权重公开。
- **代表论文**：Mittag et al., "NISQA: A Deep CNN-Self-Attention Model for Multidimensional Speech Quality Prediction with Crowdsourced Datasets", INTERSPEECH 2021。
- **上游仓库**：`github.com/gabrielmittag/NISQA`

**为什么这个而非 DNSMOS / SIGMOS / UTMOS**：
- **DNSMOS**（Microsoft）：只针对降噪场景 · 只出 1 维 SIG/BAK/OVL · 对剪口不敏感。
- **SIGMOS / UTMOS**：训练集更偏 TTS · 对录音棚人声剪辑覆盖弱。
- **NISQA** 的 `dis` 维度直接对应"discontinuity"，与剪口 butt splice 失败模式**语义对齐**。

---

## 段 2 · 我们要拿它做什么（Check 5 的三条使用契约）

**契约 A · absolute MOS 阈值兜底**：
- render 后每条 clip 独立跑 NISQA → 若 `mos_pred < 3.0` → 打 `HUMAN_REVIEW`。
- 3.0 阈值来源：NISQA 论文 5 档语义中"Fair"下限 · 生产可听但明显不佳。

**契约 B · MOS delta（剪前 vs 剪后）**：
- 对同一片段，取剪口 ±0.5s 上下文，剪前 raw 一段 vs 剪后 rendered 一段各跑 NISQA。
- `delta = after - before < -0.5` → 打 `REJECT`（剪辑本身劣化质量）。
- 0.5 阈值来源：NISQA 论文报告的 test-retest 95% CI ≈ ±0.3，取 0.5 留 0.2 缓冲。

**契约 C · dis 维度独立观察（本次不入判决）**：
- 单独记录 `dis` 分，用于后续离线分析 · 观察 butt_splice vs crossfade_50ms 的分布差。
- 不参与本 Check 5 的 verdict 生成（避免多维阈值调参地狱）。

---

## 段 3 · 边界与已知失效模式

1. **训练集分布外风险**：NISQA 训练素材以英文为主，中文播客场景需 sandbox 验证。剪前 baseline MOS 若已 < 3.5，delta 信号不可靠。
2. **推理成本**：模型 ~40MB，单 clip GPU ~50ms / CPU ~500ms。full-episode（≈ 200 candidate × 2 clip × 500ms）≈ 200s CPU；**只能在 render 后异步跑**，不能进 pre-cut 主链路。
3. **与 Check 3（节奏跳变）的语义重叠**：Check 3 已用 `cut_parameters.json` 阈值判 gap 长度；NISQA `dis` 也会对相同失败模式打低分。**这是冗余不是冲突** —— 冗余用于兜底 · 二者需**同时 REJECT** 才升级为 `REJECT_QUALITY_REGRESSION`，避免单点误判。
4. **不做的事**：不用 NISQA 生成 EDL、不用它选拼接策略、不用它替代 human listening 抽检。它只是**质量层的最后一层客观兜底**。
5. **License 边界**：MIT 允许商用与修改，但发布二进制需保留 attribution · 权重不进本仓库（`.gitignore` 需追加 `**/nisqa_weights/`，本次骨架不建）。

---

## 段 4 · 促入生产的判据（promote checklist）

Promote 到 Champion（进入 cut-verify SKILL.md 的 Check 5 位）需**全部**满足：

- [ ] 独立 sandbox venv 内 NISQA 官方 demo pass（clean speech > 4.0，noisy speech < 2.5）。
- [ ] 3 集真实素材 shadow · 前 4 项 verdict + Check 5 verdict 交叉表 · 无与人耳判断反向的 case。
- [ ] delta 阈值 -0.5 与 absolute 阈值 3.0 · 在 shadow 数据上误报率 < 10%、漏报率 < 20%。
- [ ] 单元测试覆盖率 ≥ 80% · 集成测试跑通 e2e-auto-runner-v1 一次。
- [ ] 与 `filler-global-pause-v1` / `self-correction-v1` 的判决不产生死锁（联合仿真 1 集）。
- [ ] 中文 podcast 领域数据的 test-retest 稳定性 ≥ 0.85 相关。
- [ ] Champion 修改点由 Registry PR review 通过（本 challenger 不直接改 cut-verify）。

**未满足任何一项 · Challenger 保持 SKELETON_CREATED 或 SANDBOX_ONLY 状态 · 不 promote。**
