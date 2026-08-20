# EP04 剪辑偏好与试验记录 · v1 → v12

> **状态（2026-08-13）**：这是 Challenger 的学习史和工程复现记录，不是当前生产脚本、`autocut_policy` 或一集新节目的操作入口。
> 
> **正确使用顺序**：先读 `统筹全局/Agent交付流程-从音频到成片.md`、F04–F07 和
> `统筹全局/当前剪辑偏好快照.md`；本文件只用来追溯某条偏好从何而来、复现 EP04 实验或提出下一版 Challenger 假设。
>
> **关键限制**：v12 实验自动处理了长停顿、瞬态和串音等高风险项，且 run identity 仍混有 `EP04-v4`。它不能授权未来 Agent 跳过高风险人审，也不能被称为“当前生产参数”。

---

## 一、EP04-v12 候选与试听参数（Challenger 草案）

### 1.1 长停顿"静音收紧"（silence_collapse）

| 参数 | 值 | 出处 |
|---|---|---|
| 检测 | `ffmpeg silencedetect noise=-30dBFS d=0.5s` | v3 引入，v11 定型 |
| 触发阈值 `trigger_seconds` | 0.6 s | v9 定 |
| 边界安全内缩 `safety_ms` | 50 ms | v10 定 |
| 保留呼吸 `keep_head/tail_ms`（按停顿长度分档） | 0.6-1s: **100/100** ｜ 1-2s: **200/200** ｜ 2-3s: **300/300** ｜ ≥3s: **400/400** | v5 分档，v11 保留 |
| Crossfade | equal-power sin/cos，长度 `min(200ms, cut/2)` | v3 换 equal-power，v8 自适应 |

### 1.2 词表口癖（solo_filler）

| 类别 | 词表 | 何时提名候选 |
|---|---|---|
| **强口癖** | `呃 / 额 / 唔 / 唉 / 哎 / 哦 / uh / um / er / erm` | 单发可提名；是否剪仍取决于跨轨安全、代表性校准与 EDL 来源 |
| **"嗯" 特殊** | `嗯` | **只在 dur ≥ 0.8s 或 5s 内同轨 ≥ 3 个** 时提名（艳馨反馈：保留活人感） |
| **弱口癖（简体）** | `啊 / 那个 / 这个 / 就是 / 然后 / 对` | 短促单发可提名 + 跨轨安全 + 三条护栏 |
| **弱口癖（繁体 & ASR 错识别）** | `那個 / 這個 / 然後 / 對 / 就是說 / 这据 / 這据 / 這據` | 同上（v10 补） |

**弱口癖三条护栏（v11 定型）**：
1. **时长上限 `WEAK_MAX_DURATION_SECONDS = 0.4`**：`dur > 0.4s` 一律保留（承担语气，不是纯口癖）
2. **密集抑制 `WEAK_DENSE_WINDOW_SECONDS = 5.0`，`WEAK_DENSE_MAX_PER_WINDOW = 2`**：同一 track 上 5 秒滑窗内最多剪 2 个
3. **相邻同字合并 `WEAK_ADJACENT_MERGE_GAP_SECONDS = 0.3`**：紧邻同字（gap < 0.3s）保留最后一个作为承接

**下限（v12 定）**：
- `SOLO_MIN_DURATION_SECONDS = 0.10`：`dur < 0.1s` 一律 reject（ASR 时间戳粘连伪触发）

**跨轨安全**：对源轨候选，逐词检查其它每条轨在该 word 时间段内是否有 `activity.classification == "primary"` 的词；有则跳过（`_other_primary_overlap` 见 `render_ep04_v7.py`）。**语义：另一轨在主讲时，源轨的口癖不剪**，避免误删对方语音。

### 1.3 瞬态（transient）

| 类型 | 阈值 | 版本 |
|---|---|---|
| `mic_bump_like` | 短促峰值 crest，全部 accept | v10 |
| `cough_like` / `thump_like` | `peak > -22 dBFS` 且 `dur < 0.5s` | v10（v3 为 -20 dBFS/-0.4s） |
| 时长上限 | `dur > 0.5s` 一律 reject | v3 |

### 1.4 串音（crosstalk gate）

| 置信度 | 处理 | 版本 |
|---|---|---|
| `high`（源轨无 primary 词） | accept + **gate 源轨**（音量降 0） | v3 |
| `medium`（源轨仍有少量 primary） | accept + gate 源轨（不改整片时长） | v4（v3 是 reject） |

**gate 语义**：equal-power 30ms fade 到 0，另外两条轨完好保留；不改整片时长。

### 1.5 片头片尾音乐

- 素材：`音频参考库/raw material/第三集/片头片尾music.mp3`（60s stereo → 取前 15s）
- Intro：15s music（1.5s 淡入）→ **3s equal-power crossfade** → speech
- Outro：speech → 3s equal-power crossfade → 15s music（末 3s 淡出）
- 净增音乐时长：15 + 15 − 6 = **24 s**

### 1.6 响度归一化（v12 新增）

| 阶段 | 参数 |
|---|---|
| Pass 1 | `loudnorm=I=-16:TP=-1:LRA=11:print_format=json`（measure only） |
| Pass 2 | 用 pass1 measured 值 + `linear=true`，输出 -16 LUFS / -1 dBTP |
| 输出目标 | **Integrated -16 LUFS / True Peak -1 dBTP** 的工程 QC 工作目标；发布规格仍待 Mentor 冻结 |

**Pass 1 完整命令**：
```bash
ffmpeg -hide_banner -i speech_plus_music.wav \
  -af "loudnorm=I=-16:TP=-1:LRA=11:print_format=json" -f null - 2>&1 | tail -20
# 从 stderr 里解析 input_i / input_tp / input_lra / input_thresh / target_offset
```

**Pass 2 完整命令**（把 pass1 数字填进去）：
```bash
ffmpeg -y -i speech_plus_music.wav \
  -af "loudnorm=I=-16:TP=-1:LRA=11:measured_I=<>:measured_TP=<>:measured_LRA=<>:measured_thresh=<>:offset=<>:linear=true:print_format=summary" \
  -ar 48000 -ac 1 -c:a pcm_s16le master.wav
```

**EP04-v12 实测**：input -20.16 LUFS / +1.20 dBTP → output -16.5 LUFS / -1.0 dBTP。这证明两遍响度链路曾运行，不代表“发布可用”。

### 1.7 词级 activity 分类参数（primary/bleed/ambiguous 判定）

| 参数 | 值 | 说明 |
|---|---|---|
| `window_ms` | 20 | 逐窗 RMS 时长 |
| `dominance_db` | 3.0 | 源轨比其它轨强 ≥3 dB → primary；弱 ≥3 dB → bleed；否则 ambiguous |

**脚本**：`稳定生产/challengers/orchestrator-e2e-v1/scripts/classify_activity_local.py`
**产出**：三个 `track_*.classified.json`，每个词有 `activity.classification`。
**注意**：能量启发式**不是**说话人 diarization，只是"哪条轨该时刻声音更大"。未来接真实 diarization（如 pyannote）规则不变，只是替换 activity 字段。

### 1.8 输入源要求

- **raw** 三轨 mono WAV / 24-bit / 48 kHz / **完全同时长**（sample timeline 一致）
- 不用 denoised 中间品（v10 教训：Sophie 音质变了）
- 词级 activity：从 `orchestrator-e2e-v1/scripts/classify_activity_local.py` 生成，含 primary/bleed/ambiguous

---

## 二、v1 → v12 教训与守则（每次迭代学到什么）

### v2b（首版技术验证）
- **产出**：78 段剪 + 30ms linear crossfade
- **问题**：所有剪口都有咔嗒感
- **教训**：**linear crossfade 在人声上不能用**（中点 -6 dB 凹陷）

### v3
- **改动**：`linear` → **equal-power (sin/cos) 200ms crossfade**
- **教训**：**equal-power ramp（`cos/sin`）保持中点 0 dB**，是所有人声拼接的默认

### v4（从真人标签学阈值）
- **改动**：把 27 条真人 accept/reject + 长停顿 A 档偏好学出阈值，应用到全部 122 条候选
- **当时的假设**：真人给“边界代表样本”后，机器可以把阈值外推到全部候选。
- **后续修正**：这只可能适用于通过安全门、规则明确的低风险口癖/紧邻重复；长停顿、瞬态、串音、说错重来和语义类候选仍必须全量人审。v4 不能作为自动剪辑政策或正式交付证据。

### v5（分档 keep）
- **触发**：v4 里 50 段长停顿有 42 段（84%）剪 <0.4s，用户抽查听不出
- **改动**：长停顿保留呼吸按停顿长度**分档**（100/200/300/400 ms）
- **教训**：**统一 keep 参数不合适**——短停顿要激进保留少，长停顿要保留多才自然

### v6（单发弱口癖 + 跨轨安全）
- **触发**：用户"额/这个/然后没剪"
- **改动**：弱口癖词表加入 `啊/那个/这个/就是/然后/对`；单发也剪；加跨轨安全
- **教训**：**候选生成规则默认过保守**——`filler-global-pause-v1` 只要求"连续重复 ≥2"，会漏掉大量单发口癖

### v7（嗯 保留活人感）
- **触发**：艳馨"楠哥的嗯是否要保留一些活人感"
- **改动**：`嗯` 只在 ≥0.8s 或 5s 内同轨 ≥3 个时剪
- **教训**：**"嗯"是主持人语气标志，不是无脑口癖**；用户对此有明确偏好

### v8（自适应 crossfade）
- **触发**：3:28 mic_bump（0.14s）剪切听出 glitch
- **改动**：`crossfade_ms = min(200, cut_ms/2)` — 短剪切用短 crossfade
- **规则化守则**：**任何两段音频拼接的 crossfade 长度必须 < 两侧较短段的 50%**；否则前段末尾与后段开头在 fade 里被平均叠加，听起来像两个字撞在一起

### v9（弱口癖全剪，但过度激进）
- **触发**：用户说"很多这个没剪"
- **改动**：弱口癖去掉"前后必须有停顿"的门槛
- **副作用**：单发口癖从 79 暴涨到 550，密集剪切导致节奏差
- **教训**：**去掉一条限制之前必须先看数据分布**——`这个` 在词表里 178 次，把它们全剪节目就散了

### v10（raw + 词表繁体）
- **触发**：用户"Sophie 音质变了"（其实是 v2b 起一直用 denoised 中间品）；同时"大量繁体版本口癖漏检"
- **改动**：
  1. 换成 raw 三轨（24-bit / 48kHz mono）
  2. 词表加繁体：`那個 / 這個 / 然後 / 對 / 就是說`
- **规则化守则**：
  - **永远从 raw 开始剪**，不叠加中间处理链（denoise 有一次听感损失，剪辑不该再叠一次）
  - **whisper 中文模型会简繁混用**（同一期节目里两种都可能出现），词表要覆盖两种

### v11（三条护栏）
- **触发**：用户"1:04/1:34/1:37 剪多了"（0.58s "对"、0.62s "然后" 都是承担语气）
- **改动**：弱口癖三条护栏（时长≤0.4s / 5s ≤2 个 / 相邻同字合并）
- **规则化守则**：
  - **短口癖越短越像口癖；长于 0.4s 的"对/然后/这个"通常承担语气**
  - **短时窗内密集剪切累加会造成剪辑痕迹**，5s ≤2 是保守上限
  - **紧邻同字口癖**（"对 对"）**保留最后一个**作为语气承接，避免破坏节奏

### v12（响度归一化 + solo_filler 下限 + 标签修正）
- **触发**：熊镇正客观测量：+1.1 dBTP 超天花板、-20.2 LUFS 偏安静、CUT_DETAILS 类型 bug
- **改动**：
  1. **loudnorm 两阶段**：pass1 measure → pass2 apply（`linear=true`）→ 输出 -16 LUFS / -1 dBTP
  2. `solo_filler dur ≥ 0.1s` 下限：过滤 ASR 粘连伪触发（v11 里有 9 段 <0.1s）
  3. CUT_DETAILS 按类型正确拆分（silence_collapse / solo_filler / transient / human_filler / gate）
- **规则化守则**：
  - **发布前必须按 Mentor 冻结的规格做响度与真峰值 QC**；`-16 LUFS / -1 dBTP` 是 v12 的工作目标，不是当前项目自动生效的发布授权
  - **任何时长 < 0.1s 的候选都视为 ASR 时间戳噪声**，一律 reject（人耳听感无差）
  - **CUT_DETAILS 的类型列名必须与规则模块名一一对应**，避免下游误读

---

## 三、通用剪切守则（跨版本恒定）

### 3.1 音质守则

1. **crossfade curve 必用 equal-power**（sin/cos），linear 会有 -6 dB 中点凹陷
2. **crossfade 长度 < 两侧较短段的 50%**（避免叠字 glitch）
3. **不叠加中间处理链**：raw → 剪 → 归一化 → 发布；不要在 denoise 之上再剪
4. **发布响度 -16 LUFS / -1 dBTP**（loudnorm 两阶段，`linear=true`）

### 3.2 剪切判定守则

5. **短口癖越短越像口癖**（`dur > 0.4s` 承担语气，保留）
6. **`嗯` 保留活人感**（≥0.8s 或密集才剪）
7. **相邻同字口癖保留最后一个**（"对 对"→ 保留后一个"对"）
8. **5 秒滑窗每轨最多 2 个单发口癖**（密集抑制）
9. **跨轨安全**：另一轨主讲时不剪本轨的口癖（避免误删对方语音）

### 3.3 静音收紧守则

10. **silencedetect 三轨齐死寂才算"真死寂"**（-30dBFS × 0.5s）
11. **保留呼吸长度随停顿长度分档**：短停顿保留 100ms，长停顿保留 400ms
12. **边界内缩 50ms** 避免切进余韵/起音

### 3.4 词表守则

13. **whisper 中文模型简繁混用**，词表两种都要有
14. **"这个/就是/然后/对"在中文口语里是承担叙述结构的**——只有短促单发才算口癖
15. **强口癖（呃/额/唔）与弱口癖（对/这个）分开对待**，前者可优先提名，后者要护栏；两者是否剪仍取决于风险、跨轨安全和审核来源

### 3.5 类型标签守则

16. **CUT_DETAILS 的类型列必须与规则模块一一对应**：
    - `silence_collapse` = silencedetect 检出的三轨齐死寂
    - `solo_filler` = 词表命中的单发口癖
    - `transient` = 瞬态检测（mic_bump / cough）
    - `human_filler` = 真人 accept 的口癖 EDL
    - `crosstalk_gate` = 源轨串音 gate（不改整片时长）
17. **`dur < 0.1s` 的候选一律 reject**（ASR 时间戳噪声，人耳听感无差）

### 3.6 版本管理守则

18. **每一版渲染写独立 run 目录** `main/runs/EP*-v*-<ts>/`，audio 全部 gitignore
19. **每次实验改规则必须在本文件追加一行**（表 §2）+ 更新本文件的实验参数；如要影响新一期，还必须提出并晋升偏好快照更新
20. **未定型的规则不进 Champion**（`稳定生产/rules/` 不改），一律走 Challenger 目录

---

## 四、新一期（EP05+）该如何继承这些经验

不要把 `render_ep04_v12.py` 改路径后直接运行在新一期。正确流程是：

1. 用户只提供同一期对齐音频；Agent 先按《Agent交付流程-从音频到成片》建立新 run、冻结身份和输入 SHA。
2. Agent 读取本文件对应的偏好来源，但以 `当前剪辑偏好快照.md` 为本期候选/试听 profile；把 profile ID 与 SHA 写入 plan。
3. Agent 冻结全量候选：短口癖/紧邻重复可按代表性样本校准；长停顿、瞬态、串音、说错重来和语义类候选必须进高风险全审。
4. 人工审核后，Agent 只对低风险未决候选写 `machine_proposed_*`，并输出来源明确的双 EDL/双渲染；不能把实验判断写成真人 `accept`。
5. 固定音乐素材必须通过 SHA 校验。`reference-linear-v1` 与 v12 交叉音乐只能作为明确标识的试听选择，最终由整片 QC 决定。
6. 有新反馈时，先写本期 `experience_proposal.json`，再经 benchmark、独立复核和人工晋升更新偏好快照；不能直接把下一版脚本称为生产规则。

---

## 五、代码位置索引

| 版本 | 脚本 |
|---|---|
| v3 | `稳定生产/challengers/orchestrator-e2e-v1/scripts/render_ep04_v3.py`（equal-power 引入） |
| v4 | `render_ep04_v4.py`（学阈值） |
| v5 | `render_ep04_v5.py`（分档 keep） |
| v6 | `render_ep04_v6.py`（单发弱口癖） |
| v7 | `render_ep04_v7.py`（嗯保活人感 · `STRONG_FILLERS_ONLY_LONG_OR_DENSE`） |
| v8 | `render_ep04_v8.py`（自适应 crossfade · `apply_sync_cuts_adaptive`） |
| v9 | `render_ep04_v9.py`（弱口癖全剪，已废弃） |
| v10 | `render_ep04_v10.py`（词表繁体 · `WEAK_FILLERS_V10`） |
| v11 | `render_ep04_v11.py`（三条护栏 · `detect_solo_fillers_v11`） |
| **v12（Challenger 实验）** | **`render_ep04_v12.py`**（loudnorm + solo ≥0.1s + 类型修正；不应直接用于新一期） |

**共用规则常量**：
- `STRONG_FILLERS_ONLY_LONG_OR_DENSE`（v7）
- `STRONG_FILLERS_ALWAYS_V10`（v10）
- `WEAK_FILLERS_V10`（v10）
- `WEAK_MAX_DURATION_SECONDS = 0.4`（v11）
- `WEAK_DENSE_WINDOW_SECONDS = 5.0`（v11）
- `WEAK_DENSE_MAX_PER_WINDOW = 2`（v11）
- `WEAK_ADJACENT_MERGE_GAP_SECONDS = 0.3`（v11）
- `SOLO_MIN_DURATION_SECONDS = 0.10`（v12）
- `NG_LONG_THRESHOLD_SECONDS = 0.8`（v7）

---

## 六、已知未做（下一次要处理）

1. **artist：说错重来（self-correction）** 规则太粗（v1 用句间前缀，播客句间常 5-6s gap），v2 需要下沉到"句内滑窗"
2. **说话人 diarization**：当前 primary/bleed 是能量启发式，未接真实说话人模型
3. **音乐 ducking / automix**：说话时压低音乐、静音时抬回——当前是等权 amix
4. **发布 QC 自动化**：过响、削峰、口癖分布散点图（现在靠人工听感 + 熊镇正测量）
5. **多期节目**：目前只有 EP03/EP04；从 EP05 起继续积累到 10 期才谈"训练排序器"

---

## 七、踩过的坑（下次别再踩）

### 坑 1：一直在 denoised 中间品上剪，Sophie 音质变了（v2b → v9）

- **症状**：用户反馈"Sophie 音质和之前不一样"
- **原因**：从 v2b 起一直用 `main/runs/EP04-p0-20260811/04_denoise/track_*.denoised.wav`（别人的降噪中间品），不是 raw
- **修法（v10）**：换成 `音频参考库/raw material/第四集/ZOOM0009_Tr*.WAV`
- **守则**：**永远从 raw 三轨开始剪**，任何一版都不叠加处理链

### 坑 2：审核前端 `.command` 双击打不开（v5 之后）

- **症状**：用户"双击 .command 没反应/浏览器 404"
- **原因**：Gatekeeper + 打开方式默认成"文本编辑" + `fetch("review_package.json")` 在 file:// 被 CORS 挡
- **修法**：数据**内嵌进 HTML**（`const __PKG = {...};`），双击 HTML 就能开
- **守则**：**审核 UI 首选 file:// 双击 + 内嵌 JSON**，不依赖 http server 和 .command

### 坑 3：弱口癖去掉"前后停顿"限制后剪爆（v9）

- **症状**：单发口癖从 79 暴涨到 550，节目节奏散
- **原因**：`这个/就是/然后` 每一个都剪，忽略了它们承担叙述结构的功能
- **修法（v11）**：加三条护栏（时长≤0.4s / 密集抑制 / 相邻合并）
- **守则**：**放宽一条限制之前必须先看词频分布**（`这个` 全片 178 次，"全剪"必爆）

### 坑 4：短剪切用长 crossfade 造成 glitch（v7 3:28）

- **症状**：3:28 附近听得到剪辑异常
- **原因**：mic_bump 剪切 0.14s，但 crossfade 200ms > 剪切长度，前后段在 fade 里被叠加
- **修法（v8）**：`crossfade_ms = min(200, cut_ms/2)`
- **守则**：**crossfade 长度必须 < 两侧较短段的 50%**，否则听感异常

### 坑 5：CUT_DETAILS 类型标签把 solo_filler 标成"死寂"（v11）

- **症状**：熊镇正"表里第一列写'单发口癖'，内容明显是'死寂'"
- **原因**：EDL 里 solo_filler 和 silence_collapse 都放在 `long_pause_params.segments`，md 生成脚本按合并后 `cuts` 表画，把两类混在一起
- **修法（v12）**：**按 `solo_filler=True` 与否拆两张表**，`silence_collapse` 独立表
- **守则**：**类型列命名必须与规则模块一一对应**，不允许"死寂 X.XXs"作为 solo_filler 类型下的内容

### 坑 6：True Peak +1.1 dBTP 超天花板未归一化（v1-v11）

- **症状**：熊镇正测出 True Peak = +1.1 dBTP，播客平台会自动削峰/降响度
- **原因**：v1-v11 完全没做响度归一化，直接 amix + libmp3lame 输出
- **修法（v12）**：loudnorm 两阶段 → -16 LUFS / -1 dBTP
- **守则**：**发布前必须做 loudnorm 两阶段**，不然客观指标不合格

### 坑 7：whisper 中文简繁混用词表漏检（v6-v9）

- **症状**：用户"很多这个没剪" — 用户听到的"这个"其实 ASR 识别成"這個"
- **原因**：whisper small 中文模型在同一节目里会混用简繁体
- **修法（v10）**：词表加 `那個 / 這個 / 然後 / 對 / 就是說` + ASR 错识别 `这据 / 這据 / 這據`
- **守则**：**任何中文口癖词表，简繁两种形式都要覆盖**；同时对 ASR 常见错识别做别名映射

### 坑 8：solo_filler 里出现 0.02s 伪触发（v11）

- **症状**：CUT_DETAILS 里有"死寂 0.02s"这种记录
- **原因**：ASR 时间戳粘连（相邻两个字的边界被误合并成 0.02s 的 word）
- **修法（v12）**：`SOLO_MIN_DURATION_SECONDS = 0.10` 下限过滤
- **守则**：**任何时长 < 0.1s 的候选一律 reject**（人耳听感无差，只增加剪辑痕迹风险）

### 坑 9：git push 沙箱无 GitHub 认证

- **症状**：`fatal: could not read Username for 'https://github.com'`
- **原因**：沙箱没配 PAT/keychain
- **修法**：沙箱只做 `git add + commit`，`git push` 交给用户在 Mac 上跑（Mac 有 keychain）
- **守则**：**沙箱只提交本地 commit，push 一律在用户 Mac 上手动跑**

### 坑 10：ffmpeg 长音频渲染超时（v6 之后）

- **症状**：`Command timed out after 120000ms`
- **原因**：EP04 54 分钟 raw + loudnorm two-pass + amix + acrossfade 一次跑要 90-110s，接近 120s bash 超时
- **修法**：
  - loudnorm pass2 单独跑（60s）
  - amix + mp3 encoding 独立步骤
  - 或者 `nohup ... &` 后台跑（注意会话结束会断，需要检查）
- **守则**：**长音频处理拆多步 bash 调用**，每步 < 60s；或用后台任务并观察产物

### 坑 11：无 EP04 raw 时的 sample timeline 对齐

- **症状**：raw 三轨 24-bit / denoised 三轨 16-bit，但 sample count 相同
- **原因**：denoise 保留了 sample timeline（延迟补偿），所以 EDL sample 位置在 raw 与 denoised 上都能用
- **利用**：**EDL 可以在 raw 上直接跑**，不用重新做 activity 分类（activity 用 denoised 生成，位置也对齐 raw）
- **守则**：**任何"输入检查/降噪/ASR"步骤都必须保持 sample timeline**，否则 EDL 无法迁移

---

## 八、下次 EP05 新期节目施工清单

> **历史复现专用，不是新一期交付流程。**以下命令保留是为了排查或复现 EP04 v12，
> 不是让使用者手工执行，也不能让 Agent 以此绕过高风险审核、双 EDL、run identity
> 校验或最终整片 QC。新一期必须从《Agent交付流程-从音频到成片》开始。

按顺序执行（每一步都能停下来验证）：

### Step 1: 放 raw 三轨
```bash
# 拷 3 条 ZOOM 原始 mono WAV 到项目里（不要改文件名，或改成 track_0X.WAV）
cp /Volumes/YourSDCard/EP05/ZOOM0010_Tr1.WAV 音频参考库/raw\ material/第五集/
cp /Volumes/YourSDCard/EP05/ZOOM0010_Tr2.WAV 音频参考库/raw\ material/第五集/
cp /Volumes/YourSDCard/EP05/ZOOM0010_Tr3.WAV 音频参考库/raw\ material/第五集/
# 验证三条时长完全一致
for f in 音频参考库/raw\ material/第五集/*.WAV; do
  ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$f"
done
```

### Step 2: ASR 词级转写（这一步用现有 P0 Challenger）
```bash
python3 稳定生产/challengers/asr-speaker-v1/scripts/p0_mvp.py \
  --input track_01=$PWD/音频参考库/raw\ material/第五集/ZOOM0010_Tr1.WAV \
  --input track_02=$PWD/音频参考库/raw\ material/第五集/ZOOM0010_Tr2.WAV \
  --input track_03=$PWD/音频参考库/raw\ material/第五集/ZOOM0010_Tr3.WAV \
  --out-dir main/runs/EP05-p0-<ts>/
```

### Step 3: 词级 activity 分类
```bash
python3 稳定生产/challengers/orchestrator-e2e-v1/scripts/classify_activity_local.py \
  --track track_01=<raw wav 1> --track track_02=<raw wav 2> --track track_03=<raw wav 3> \
  --transcript track_01=<canonical json 1> --transcript track_02=<canonical json 2> --transcript track_03=<canonical json 3> \
  --out-dir main/runs/EP05-v12-<ts>/01_inputs/activity \
  --window-ms 20 --dominance-db 3.0
```

### Step 4: 生成"三轨原始混音"用于 silencedetect
```bash
ffmpeg -y \
  -i <raw wav 1> -i <raw wav 2> -i <raw wav 3> \
  -filter_complex "amix=inputs=3:normalize=0" -c:a pcm_s16le -ac 1 -ar 48000 \
  main/runs/EP05-v12-<ts>/01_inputs/EP05-original-mix.wav
```

### Step 5: 跑 v12 历史渲染脚本（仅用于复现，禁止直接套到 EP05）

> `render_ep04_v12.py` 仍写有 EP04/v4 身份，且会自动处理高风险类型。即使改了输入
> 路径，也只能把输出作为工程对比样片；不得当作 `human_approved`、合格的
> `machine_assisted_draft` 或发布候选。
```bash
python3 稳定生产/challengers/orchestrator-e2e-v1/scripts/render_ep04_v12.py \
  --activity-dir "$PWD/main/runs/EP05-v12-<ts>/01_inputs/activity" \
  --track track_01="$PWD/音频参考库/raw material/第五集/ZOOM0010_Tr1.WAV" \
  --track track_02="$PWD/音频参考库/raw material/第五集/ZOOM0010_Tr2.WAV" \
  --track track_03="$PWD/音频参考库/raw material/第五集/ZOOM0010_Tr3.WAV" \
  --filler-edl <EP05 真人 accept 口癖 EDL 或空 json> \
  --merged-candidates <EP05 候选 json，可先跑三新 Challenger 生成> \
  --mix-for-silencedetect "$PWD/main/runs/EP05-v12-<ts>/01_inputs/EP05-original-mix.wav" \
  --intro-outro-music "$PWD/音频参考库/raw material/第三集/片头片尾music.mp3" \
  --long-pause-trigger-seconds 0.6 \
  --long-pause-safety-ms 50 \
  --target-lufs -16 --target-tp -1 --target-lra 11 \
  --out-dir main/runs/EP05-v12-<ts>/
```

**注意**：脚本内部会跑：候选合并 → EDL → 三轨剪切 → amix → 拼片头片尾 → loudnorm 两阶段 → mp3；
这正是它不能作为新一期正式流程的原因——它把候选、批准和渲染混在同一历史脚本中。

### Step 6: 客观指标复核
```bash
# 复测最终 master 的 loudness
ffmpeg -hide_banner -i main/runs/EP05-v12-<ts>/EP04-v12.master.wav \
  -af "loudnorm=I=-16:TP=-1:LRA=11:print_format=json" -f null - 2>&1 | tail -20
# 目标：Output Integrated ≈ -16 LUFS, Output True Peak ≤ -1 dBTP
```

### Step 7: 生成 CUT_DETAILS_v12.md（按类型正确拆表）
从 `main/runs/EP05-v12-<ts>/EP04-v12.edl.json` 读，套用 `main/runs/EP04-v12-20260813-1520/CUT_DETAILS_v12.md` 同款脚本。

### Step 8: 历史抽样试听（不能替代正式人工闸门）

- 开头 3 min + 中段 15-20 min + 结尾 3 min 只可作为工程样片抽查；
- 它不能替代全部高风险候选的人审、无候选区域抽查或最终整片听审；
- 有新问题时写入本期反馈包和 `experience_proposal.json`，按正式晋升流程更新偏好快照，不直接覆盖“当前参数”。

---

> **本文件不是唯一权威来源。**新增反馈可以在这里保留“触发 / 假设 / 实验结果”，
> 但活动偏好应进入 `统筹全局/当前剪辑偏好快照.md`，正式行为以交付契约与 F04–F07
> 为准。不得“只改代码不写证据”，也不得“只改这份实验史就当作生产升级”。
