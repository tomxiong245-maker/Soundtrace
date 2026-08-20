# 端到端剪辑偏好学习 · 实施规范

**skill 归属**：learning-and-experience（第 4 段 · 与"单期偏好学习 / online refresh / 多期案例蒸馏"并列）
**起草日期**：2026-08-18
**起因**：用户 2026-08-18 明确需求 —— "**端到端（已有的人工剪辑和 raw material）要去学习剪辑偏好**"。当前 `extract_gold_cut_features.py` 已存在（能处理"已知 gold EDL 的特征提取"），但**缺前置工具**（从 raw + 成品**自动反推** gold EDL），skill 层也**没写清**这条流程段。
**状态**：**未落地代码 · 本文件是完整实施规范**（含输入契约 + 流水线 + 精度要求 + 兜底策略 + 未闭环项）。

---

## 一、问题描述

### 用户的需求

给定 **一份或多份人工剪辑成品**（比如 mentor 剪好的一集 mp3、外部专业剪辑师的样品、用户自己手工剪的样本）+ **对应的 raw material 原始 N 轨**，机器应该能**反推出这个剪辑师的偏好**：他重视什么、不介意什么、常用的剪切位置和参数是什么。

产出**偏好知识**，喂给 s2 candidate-generation-and-gate 更新候选族与 rules（走 Challenger 提名路径，不直接改生产）。

### 当前缺什么

| 环节 | 现状 |
|---|---|
| **从已知 gold EDL 提取特征** | ✅ 存在：`extract_gold_cut_features.py`（已登记 tools.json + 有 audit） |
| **从 raw + 成品自动反推 gold EDL** | ❌ **不存在** —— EP03/EP04 的 gold EDL 是**人工手动**提供的（**未来路径**：用户只需显式声明"这是人工剪辑的成品"，机器自动视为 gold 反推 · 不再需要人工提供 EDL） |
| **给每条剪切分类** | 🟡 部分存在（分类逻辑埋在 extract_gold_cut_features 里，未抽独立工具） |
| **汇总特征→偏好统计+规则假设** | ❌ **不存在** —— EP04 的 mentor 分析 md 是 workflow 一次性做的，未产品化 |
| **skill 层明确"端到端偏好学习"角色** | ❌ **不存在** —— 本次通过修订 s5 SKILL.md 补上 |

### 为什么这值得单独做一段

- s5 · 第 1 段（单期偏好学习）**需要候选池 + 用户在候选池上的 accept/reject**
- s5 · 第 3 段（多期案例蒸馏）**需要多期已完成人审的 case_store**
- **端到端偏好学习不需要以上任何一样** —— 只要有 raw + 成品这两样，就能学
- 这是"给我一份专业成品，你告诉我他的风格"的能力，是**冷启动学习**（没有历史决定时也能开工）

---

## 二、目标输入 / 输出契约

### 输入（用户必须提供）

- **raw material**：N 轨对齐 mono WAV（跟正常一期节目输入格式完全一致）
- **人工剪辑成品**：mp3 或 wav 一份（**已经过并轨 / 响度归一化后**的成片）
- **人工成品声明**（必填 · 语义门槛）：用户必须**显式声明**这是"人工剪辑的成品 / 成片 / 人剪版 / mentor 剪的" —— CLI 侧通过 `--is-manual-master` flag，或语义等价的自然语言声明。**一旦声明 → 机器自动视为 gold 标准 · 无需二次审批门槛 · 直接走反推流水线**。用户**不需要**明说"gold"这个词；说"成品 / 成片 / 人剪版 / mentor 剪的 / 专业剪辑师剪的"等语义等价表述都算。
- **episode_id**：告诉机器"这个成品来自哪一期 raw"（当前不做自动匹配，靠用户声明）
- （可选 · 加速通道 · **不是必需**）**成品的 EDL**：若用户手上已有 EDL（比如从 DAW 导出的），可以直接提供以跳过反推步骤。**不提供是正常情况** —— 反推 gold_edl.json 正是本工具要产出的东西，绝不作为用户输入契约的一部分强制要求。

### 输出（写到 `main/runs/E2E-LEARN-<episode>-<ts>/`）

- `raw_alignment.json` —— raw vs 成品对齐质量报告（对齐误差分布 + 未对上段落）
- `gold_edl.json` —— 反推出的剪切清单（每条 start_sample / end_sample / classification）
- `gold_cut_features.jsonl` —— 每条剪切的 WHERE / HOW 特征（现有 `extract_gold_cut_features` 已产出这个格式）
- `preference_analysis.md` —— 偏好统计报告（类别占比、常见 pattern、异常 pattern、规则假设）
- `challenger_task.md` —— 下一版 rules 的提名任务书（供 s2 candidate-generation-and-gate 考虑接入）
- `run_manifest.json` —— run 元数据 + SHA 指纹

### 硬边界

- 只读 raw material（不改原始）
- 产出不改生产 rules · **只作 Challenger 提名**
- 不写 human_decisions / EDL / render 音频
- 至少需要 **≥20 条**剪切样本才能出稳定偏好（避免小样本过拟合）
- 需要 raw 和成品是同一次录音（跨录音源不适用）

---

## 三、需要的新工具（3 个）

以下 3 个工具**当前均不存在**。需要按 s6 governance-and-tool-registry 契约新建 + 登记 + 写 audit。

### 工具 A · `reverse_edl_from_master`（**核心 · 优先做**）

**用途**：从 raw N 轨 wav + 成品 mp3/wav **自动反推 gold EDL**。

- **script 位置**：`main/orchestrator/reverse_edl_from_master.py`（待建）
- **CLI 参数**：
  - `--episode-id <EP>` （必填 · 用户声明）
  - `--raw-wav-dir <path>` （raw N 轨所在目录）
  - `--master-audio <path>` （成品 mp3 或 wav）
  - `--episode-plan <path>` （可选 · 若有 plan.json 复用其 speaker_map + 采样率）
  - `--output-json <path>` （输出 gold_edl.json）
- **技术方案**：
  1. **重采样对齐**：把成品重采样到 48kHz（与 raw 一致）· mono 化
  2. **合成 raw 主导轨**：用 s3 audition-and-delivery 的 automix 逻辑，把 N 轨合成主导轨（如果用户提供 speaker_map 就用；没有就等能量启发式）
  3. **音频对齐**：主导轨 vs 成品做 **cross-correlation**（fftconvolve）或 **动态时间规整 DTW**（librosa.sequence.dtw）· 每一段 (chunk=10s) 找对齐位置
  4. **找剪掉的段**：raw 里未被成品覆盖的时间段 = 被剪掉的段 · 每段作为一条 gold cut
  5. **边界精修**：用 `librosa.onset.onset_detect(backtrack=True)` 把每条 gold cut 的 start/end 精修到 sample 级（≤20 毫秒精度）
- **输出 schema**（`gold_edl.json`）：
  ```
  {
    "schema_version": "gold-edl-v1",
    "episode_id": "EP05",
    "source_raw_dir": "...",
    "source_master": "...",
    "source_master_sha256": "...",
    "sample_rate_hz": 48000,
    "alignment_method": "cross_correlation" | "dtw",
    "alignment_quality": {
      "coverage_ratio": 0.98,       // raw 有多大比例能对上成品
      "mean_error_ms": 8.3,
      "max_error_ms": 21.0,
      "unaligned_segments": [[start_s, end_s], ...]
    },
    "cuts": [
      {
        "cut_id": "GC001",
        "start_sample": 1234567,
        "end_sample": 1345678,
        "start_seconds": 25.72,
        "end_seconds": 28.03,
        "confidence": 0.92,
        "boundary_snap_method": "librosa_onset_backtrack"
      }
    ]
  }
  ```
- **依赖**：`numpy` · `librosa` · `scipy` · `soundfile`
- **精度要求**：
  - 对齐 coverage_ratio >0.95（能对上 95% 以上的 raw）
  - 剪切点识别 recall >0.90（能找到大部分真的剪切）
  - 剪切点识别 precision >0.85（找到的大部分是真剪切）
  - 边界误差 <20ms
- **兜底**：如果 alignment_quality.coverage_ratio <0.85（对不上，可能因为成品加了大量音乐或后处理），**fail closed**：拒绝出 gold_edl.json，写 `alignment_failed.md` 请用户手动提供 EDL

### 工具 B · `classify_gold_cuts`（分类 · 中优先）

**用途**：给每条 gold cut 分类 —— 属于哪一族候选（filler / long_pause / self_correction / semantic_boundary / rhetorical_repeat / other）。

- **script 位置**：`main/orchestrator/classify_gold_cuts.py`（待建 · 或从 `extract_gold_cut_features.py` 抽出）
- **CLI 参数**：`--gold-edl <path>` `--asr-dir <path>` `--output-json`
- **分类逻辑**：
  - 拉每条 cut 时间窗内的 ASR 词序列
  - 靠**词内容 + 时长 + 前后语境**分类：
    - 全是 filler token（呃/嗯/啊）且时长 <500ms → `filler_hesitation`
    - 相同或高度相似的词 chain → `immediate_repetition`
    - 时长 >2s 且是纯静音 → `global_long_pause`
    - 前后语义有明显转折（比如"这个方案 → 不对"）→ `self_correction`
    - 位于句间、有明显停顿边界 → `semantic_boundary`
    - 反复出现的修辞短语（如"我们，我们，我们要"）→ `rhetorical_repeat`
    - 都不满足 → `other`
- **输出**：在 gold_edl.json 每条 cut 上追加 `classification` 字段
- **精度目标**：主分类 accuracy >0.80（少量误分类可接受，因为下游是 Challenger 提名不是自动生产）

### 工具 C · `summarize_preferences`（汇总 · 中优先）

**用途**：把 gold_cut_features.jsonl 汇总成偏好统计 + 规则假设 + Challenger 提名。

- **script 位置**：`main/orchestrator/summarize_preferences.py`（待建）
- **CLI 参数**：`--features <path>` `--output-md` `--challenger-task-md`
- **产出内容**（`preference_analysis.md`）：
  1. **类别占比**：各类候选被剪的比例（比如"71% semantic_boundary / 20% self_correction / 9% filler"）
  2. **参数分布**：各类候选的常见 crossfade / gap / boundary_offset 分布
  3. **异常 pattern**：偏离项目当前 rules 默认值的显著模式（比如"这位剪辑师一次剪 500ms 段的比例 30% · 我们默认认为 >800ms 才有语义"）
  4. **规则假设**：3-5 条可作为下一版 candidate_rules 提名的候选规则（每条含证据 + 反例统计）
  5. **顿悟提醒**：若发现颠覆性 pattern（如 EP04 的"71% semantic_boundary · pure_filler=0" 三顿悟），显式高亮
- **产出 `challenger_task.md`**：可复制到 `稳定生产/challengers/candidate-rules-v19-proposal/task.md` 的任务书骨架
- **样本量兜底**：如果特征 <20 条，产出的偏好统计**必须标注**"样本量不足 · 结论仅作参考不作规则依据"

---

## 四、跟现有资产的关系

| 现有资产 | 关系 |
|---|---|
| `extract_gold_cut_features.py` | 本流程的 **Step 4** 直接调用它 · 不改。它需要 gold_edl 作输入 · 本流程的 Step 2 (工具 A) 产出该 gold_edl 喂给它 |
| EP04-GOLD-EDL-20260818-1548 目录 | **参考产物**：那里的 `gold_edl.json` + `gold_cut_features.jsonl` + `2026-08-18-1730-mentor-gold-cut-where-how-analysis.md` 是本流程产出的**范本**。差别只在于当时 gold_edl 是人工/workflow 一次性拿到的，本流程要把这一步自动化 |
| s5 · 第 1 段单期偏好学习 | **不重叠**：那段需要候选池 + accept/reject。本段是**冷启动学习**，不需要 |
| s5 · 第 3 段多期案例蒸馏 | **相邻但独立**：多期案例蒸馏消费的是历史 case_store（每 case 是"候选 + 真人决定"）；本段消费的是"raw + 成品"（没有候选、没有决定）。两者可以互补：本段产出的偏好规则假设，可以被案例蒸馏在多期证据上二次验证 |
| s3 audition-and-delivery 的 automix 逻辑 | 工具 A 的 Step 2 复用 · 需要能"独立调用 automix 主导轨合成"而不生成 clip / 成片 —— 可能需要 automix_v1.py 提供一个 `--dry-run-mono-master` 参数 |
| PARAMETER 层（cut_parameters.json） | **不直接改**：本段产出的偏好可能建议更新 PARAMETER（比如"这位剪辑师习惯 100ms crossfade 不是我们默认的 50ms"），但只作 Challenger 提名，走 s6 governance 审批后才能改 |
| PREFERENCE 层（session_feedback jsonl） | **不直接改**：本段的规则假设只写到 `challenger_task.md`，不直接 append。**遵守 §20 单一 SOT + 补丁滥用防线** |

---

## 五、5 步流水线

```
Step 1 · 用户提交
  输入：raw_wav_dir + master_audio + episode_id + is_manual_master 声明（必填 · CLI flag 或语义等价的自然语言）
  可选：master_edl（若提供则跳过 Step 2 反推）
  语义：一旦用户声明"这是人工剪辑成品/成片/人剪版" → 机器自动视为 gold · 无二次审批门槛 · 无需再要求用户提供 gold_edl.json
  ↓
Step 2 · 反推 gold EDL
  工具：reverse_edl_from_master.py（工具 A · 新建）
  产物：gold_edl.json + raw_alignment.json
  兜底：coverage_ratio <0.85 → fail closed 请用户提供 EDL
  ↓
Step 3 · 分类
  工具：classify_gold_cuts.py（工具 B · 新建 · 或从 extract_gold_cut_features 抽出）
  产物：给 gold_edl.json 每条 cut 加 classification 字段
  ↓
Step 4 · 提取特征
  工具：extract_gold_cut_features.py（现有 · 复用）
  产物：gold_cut_features.jsonl
  ↓
Step 5 · 汇总偏好
  工具：summarize_preferences.py（工具 C · 新建）
  产物：preference_analysis.md + challenger_task.md
  ↓
产出目录：main/runs/E2E-LEARN-<episode>-<ts>/
```

---

## 六、边界

- 只读 raw material · 不改原始（FR-01）
- 产出**不写** human_decisions / EDL / render 音频 / Champion / autocut_policy
- 产出**不改** session_feedback / labels_lake / cut_parameters（走 Challenger 提名，需 s6 governance 审批）
- 样本量 <20 条 → 结论必须标注"样本量不足"
- 对齐失败（coverage_ratio <0.85）→ fail closed
- 跨录音源不适用（raw 和成品必须来自同一次录音）
- 单期学习 → 只出建议，不出规则；跨节目验证需另跑（配合 s5 · 第 3 段多期案例蒸馏）
- **人工声明是成品即视为 gold · 无二次审批门槛**：用户显式声明"这是人工剪辑成品 / 成片 / 人剪版 / mentor 剪的"就够了，机器自动走反推流水线；**不**要求用户再说"gold"这个词、**不**要求用户提供 gold_edl.json（那是本工具产出）、**不**引入额外的人工审批 gate。声明本身即是 gold 授权。

---

## 七、验证方法

### 7.1 用 EP04 现有数据作 ground truth 回归

- 已有 EP04 人工/workflow 提供的 `gold_edl.json`（EP04-GOLD-EDL-20260818-1548 目录下）
- 让工具 A 独立跑一遍（不看现有 gold_edl）
- 对比：新反推的 gold_edl vs 现有 gold_edl
- 目标：Precision >0.85 / Recall >0.90 / 边界误差 <20ms

### 7.2 用 EP03 独立验证

- EP03 有 56 条 gold cuts（人工提供）
- 让工具 A 反推一遍
- 目标同上

### 7.3 顿悟验证

- 让工具 C 跑 EP04 features
- 应该能自动产出"71% semantic_boundary · pure_filler=0"这个顿悟结论
- 如果自动跑出来的 preference_analysis.md 里没有这条，说明汇总逻辑有问题

### 7.4 冷启动验证

- 找一集**从未跑过 pipeline** 的音频（没有候选池、没有 human_decisions）
- 只给 raw + 成品
- 端到端跑一遍，看能否产出可用的 preference_analysis.md

---

## 八、兜底策略

- **对齐失败**：coverage_ratio <0.85 → fail closed · 请用户提供 EDL
- **样本量不足**：cuts <20 → 结论标注"样本量不足 · 仅供参考"
- **分类精度低**：主分类 accuracy <0.60（用 EP04 fixture 测） → 工具 B 拒绝出 classification 字段 · 让下游走"unclassified"
- **成品加了音乐 / 效果处理**：对齐算法可能失败 → 建议用户提供**纯人声轨**成品（不含片头片尾音乐）· 或提供 EDL 手动输入
- **raw material 缺失或不完整**：fail closed · 请用户补齐

---

## 九、回滚方案

1. 3 个工具（A / B / C）都是**新加**·完全没依赖生产 pipeline · 直接不用即可
2. 已产出的 `E2E-LEARN-*` 目录标 `status: challenger_experiment` · 不进生产
3. 未采纳的 `challenger_task.md` 归档保留 · 作为下次尝试的参考

---

## 十、Non-goals（本次不做）

- **不做**"自动匹配成品对应哪一期 raw"（用户手动声明 episode_id 即可）
- **不做**跨语言剪辑偏好学习（当前只中文对谈）
- **不做**训练模型（本 skill 硬边界 · 只出规则假设，不训练）
- **不做**实时端到端学习（当前只离线批处理）
- **不做****直接改 rules**（永远只作 Challenger 提名）

---

## 十一、三档诚实标注

### 已验证事实
- `extract_gold_cut_features.py` 存在并已登记 tools.json（本轮 2026-08-18 新加）
- EP04 gold EDL 已通过 workflow 一次性反向分析得到，产出了 3 顿悟证据
- EP03 有 56 条 gold cuts + EP04 有 3 条 gold cuts（人工提供的 ground truth）

### 已决定的方向
- 端到端偏好学习是 s5 learning-and-experience 第 4 段
- 需要 3 个新工具：`reverse_edl_from_master` / `classify_gold_cuts` / `summarize_preferences`
- 产物**只作 Challenger 提名** · 不改生产 rules / EDL / audio
- 触发条件：**手动**（用户拿到人工成品时主动跑，不在自动 pipeline 里）
- 外部学习循环同时被重新定位：从"产参数"改成"产元知识"（决策心智模型 / 新流派 / 边界情况 / 新工具评估）

### 待验证假设
- 音频对齐算法能否达到 <20ms 精度（librosa cross-correlation vs DTW · 需 benchmark 选型）
- 精度目标 Precision >0.85 / Recall >0.90 是否合理 · 需 EP04 fixture 实测校准
- 需要多少条剪切样本才能出稳定偏好（当前假设 ≥20，未验证）
- 分类靠 ASR 内容 + 时长启发式是否够 · 还是需要额外的语义模型
- `automix_v1.py` 是否能加 `--dry-run-mono-master` 参数复用主导轨合成逻辑 · 或需要单独工具
- 成品加了音乐/后处理时对齐是否可行 · 或必须要求用户提供"纯人声轨"成品
- 用户"成品声明"的语义识别精度未验证 —— 走 CLI flag `--is-manual-master` 是确定性的；走自然语言（"这是人工剪的 / 成片 / mentor 剪的 / 人剪版"）在 agent 侧的识别准召待实测，误判会导致把非成品音频误当 gold 走反推流水线
