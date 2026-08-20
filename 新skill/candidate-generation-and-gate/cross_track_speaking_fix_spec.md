# cross_track_speaking 修正实施规范

**skill 归属**：candidate-generation-and-gate（长停顿跨轨 gate 段）
**起草日期**：2026-08-18
**起源顿悟**：workflow 从 EP04 gold EDL 反向分析发现 `cross_track_speaking` 现有定义**59 次判定 59 次假阳**，等于形同虚设。
**状态**：**未落地** · 本文件是**实施规范**（含分步 + 验证 + 兜底），不是已完成的代码。

---

## 一、问题描述

### 现状（假阳率 100%）

在长停顿候选生成时，我们要判断"该长停顿期间其他轨是不是在说话"——如果在说话，则**不能剪掉这个 long_pause**（否则会把别人正在说的话剪没了 · v207 LG48/51/56 事件的直接原因）。

**现有定义**：其他任一轨在该时间窗内的 RMS 能量超过阈值（简单能量启发式）→ 判定"其他轨在说话"→ 该 long_pause 候选降级为 `human_review_required`。

**实测结果**（EP04 · 59 个长停顿候选）：
- 判定"其他轨在说话"的次数：**59**
- 事后核对（用 ASR 词级 + 人工听）实际"其他轨真的在说话"的次数：**0**
- **假阳率 59/59 = 100%**
- 结果：所有长停顿全部被降级为人审，等于本 gate 没起作用

### 根因

能量启发式无法区分：
1. **主持人 backchannel 应答声**（"嗯/对/是/哦"等短音节 · 能量足够触发阈值但**语义是应答**）
2. **嘉宾 backchannel 应答声**（同理）
3. **嘉宾实词内容**（真正在说话）

三种情况在能量层几乎一致，全部被判为"在说话"。EP04 三轨里几乎每一秒都有某一轨发出短促声音（呼吸/应答/杂音），能量启发式全部命中。

### 影响

- 长停顿 gate 相当于**永远返回 demote_to_review**
- `autocut_gate/summary.json.auto_cut_ratio` 严重虚低
- 用户体验：候选池里几乎没有 long_pause 能通过自动剪，全部堆在人审

---

## 二、修正方案

用 speaker_map 逐轨差异化判定，**取代**能量启发式。

### 新判定逻辑

对每个长停顿候选 `[t_start, t_end]`（source_track_id 是候选发生的轨），检查其他每一轨 `k` 在该窗内的活动：

```
对每条其他轨 k（不含 source_track_id）：
  从 k 的词级 ASR 里取 [t_start, t_end] 内的所有词 words_k

  case 1 · 无词（transcript 里该窗内是空的）：
    → k 在该窗内"沉默"

  case 2 · 有词，但**全部**在 speaker_map[k].backchannel_whitelist 内：
    · 如果 speaker_map[k].role == "host"：
        → k 在该窗内"仅 host backchannel"（不算说话）
    · 如果 speaker_map[k].role in {"guest_A","guest_B",…}：
        → k 在该窗内"guest backchannel"（默认也不算说话 · 兜底见 §五）

  case 3 · 有词，且**至少一个**不在 backchannel_whitelist：
    → k 在该窗内"在说话"（有实词内容）

汇总所有其他轨的结果：
  · 全部沉默 或 全部只发 backchannel → gate_verdict = "pass"（可入 auto_cut）
  · 任一轨有实词 → gate_verdict = "demote_to_review"（降级人审）
```

### 关键差异

| 情况 | 旧（能量） | 新（speaker_map + ASR + backchannel_whitelist） |
|---|---|---|
| 其他轨全静音 | 判"沉默"→ 通过 | 判"沉默"→ 通过 |
| 其他轨只有主持人"嗯/对/是" | **误判为在说话**→ 降级 | 判"仅 host backchannel"→ 通过 |
| 其他轨只有嘉宾"嗯/对/是" | **误判为在说话**→ 降级 | 判"guest backchannel"→ 通过 |
| 其他轨有实词（"这个技术方案我不同意"） | 判"在说话"→ 降级 | 判"在说话"→ 降级 |

---

## 三、影响文件 / 函数

### 需要修改的（本次实施）

1. **`main/orchestrator/candidate_family_adapter.py`** 或 **`稳定生产/challengers/filler-global-pause-v1/scripts/build_filler_global_pause_review_source.py`**
   - 找到当前 long_pause 候选生成 + 跨轨判定的函数
   - 替换能量启发式判定为新逻辑
2. **新增 tool（待登记 tools.json）**：`crosstrack_silence_check`
   - `full_path`: `main/orchestrator/crosstrack_silence_check.py`（新建）
   - 输入：候选 jsonl、speaker_map.json、每轨词级 ASR
   - 输出：每候选加 `crosstrack_check_v2` 侧车字段
3. **`稳定生产/challengers/autocut-gate-v1/scripts/apply_autocut_gate.py`**
   - 消费新的 `crosstrack_check_v2.gate_verdict` 而不是旧的能量判定字段

### 需要读取的（已有 · 不改）

- `main/knowledge/speaker_maps/<ep>.speaker_map.json` — 由 s1 已声明的 role + backchannel_whitelist
- `main/runs/<ep>/<run>/asr/track_XX.words.json` — 词级 ASR

### 需要新增的 schema 字段（在候选侧车里）

```
candidate.crosstrack_check_v2 = {
  "schema_version": "crosstrack-check-v2",
  "source_track_id": "track_01",
  "window": [start_s, end_s],
  "other_tracks": [
    {
      "track_id": "track_02",
      "role": "guest_B",
      "words_in_window": [{"word": "嗯", "start_s": ..., "end_s": ...}, ...],
      "has_content_word": false,
      "backchannel_only": true
    },
    ...
  ],
  "gate_verdict": "pass" | "demote_to_review",
  "gate_reason": "all_tracks_silent_or_backchannel_only" | "track_02_has_content_word"
}
```

---

## 四、分步实施

### Step 1 · 建 fixture（EP04 现有数据）

- 拉 EP04 已有 59 个 long_pause 候选（从 EP04-AUTO-VERIFY-20260817-2200 里找）
- 拉 EP04 三轨的 ASR words.json
- 拉 speaker_maps/EP04.speaker_map.json

### Step 2 · 写 `crosstrack_silence_check.py`（新 tool）

按 §二 新判定逻辑实现；只做判定不改候选：
```
python3 main/orchestrator/crosstrack_silence_check.py \
  --candidates <run>/candidate_source.json \
  --speaker-map main/knowledge/speaker_maps/EP04.speaker_map.json \
  --asr-dir <run>/asr/ \
  --out <run>/crosstrack_check_v2_report.json
```

### Step 3 · 登记 tools.json

追加 tool 条目 + 写 audit md（照 §governance-and-tool-registry skill 契约）。

### Step 4 · 挂到候选生成 pipeline

在 `run_end_to_end.py` 里 Stage 3.4（长停顿候选生成后 · autocut_gate 之前）插入 `crosstrack_silence_check` 调用。

### Step 5 · autocut_gate 消费新字段

修改 `apply_autocut_gate.py` 里的长停顿相关 gate（G3 或另开 G8_crosstrack_v2），改从 `candidate.crosstrack_check_v2.gate_verdict` 读判决。旧的能量判定 fallback 保留一版，作为 speaker_map 缺失时的兜底（见 §五）。

---

## 五、兜底策略

- **speaker_map.json 缺失** → 回落到旧能量启发式（fail-safe · 保守判定 · 大概率降级为人审）· 并在候选侧车里写 `crosstrack_check_v2.fallback_reason = "speaker_map_missing"`
- **backchannel_whitelist 未列全** → 用默认白名单 `["嗯","啊","对","对对","是","是的","好","好的","哦","嗯嗯","唉"]`（与 auto_speaker_role.py 已有的白名单同源）
- **词级 ASR 时间戳缺失** → 该轨判定为 "无法判定"、整个候选默认降级为人审
- **guest_A backchannel 也保守** → §二 case 2 里 guest backchannel 是否算说话，取默认 "不算" · 若用户希望更保守，加一个 `guest_backchannel_treated_as_speaking` 配置开关

---

## 六、验证方法

### 6.1 定量验证

- 在 EP04 fixture 上跑一遍：预期"其他轨在说话"的判定次数从旧的 59 降到 **接近 0**（应该只有真实嘉宾插话的少数 case）
- Precision/Recall 对照：
  - 用 mentor gold EDL 里保留下来的 long_pause 作为"真的可剪" ground truth
  - 用 mentor reject 掉的 long_pause 作为"确实不能剪"
  - 新判定的 precision 应显著高于旧判定

### 6.2 单元测试

- 三种典型 case 各构造 fixture：全静音 / 只有主持人应答 / 嘉宾插实词
- 每个 case 的 `gate_verdict` 期望值写死在测试里

### 6.3 回归测试

- 在 EP03 数据上跑一遍（EP03 也有 speaker_map？如没有必须先建）
- 与人工听审对比

### 6.4 影响面回归

- `autocut_gate/summary.json` 的 `auto_cut_ratio` 应从 EP04-AUTO-VERIFY 的 0.184 上升（因为原本被降级的 long_pause 现在能通过了）
- 具体数字目标：待跑一次后定 · 但不应低于 0.30（否则说明其他 gate 还有其他假阳）

---

## 七、回滚方案

1. `run_end_to_end.py` Stage 3.4 的 `crosstrack_silence_check` 调用改成可选（配 `--use-crosstrack-v2` 开关，默认 off）
2. `apply_autocut_gate.py` 保留旧能量判定分支
3. tools.json 里 `crosstrack_silence_check` 加 `status: challenger`（未晋升）
4. 出问题时用 `--use-crosstrack-v2=false` 回落到旧行为

---

## 八、Non-goals（本次不做）

- pyannote 说话人分离（那是 F-23 speaker-diarization-v1 challenger · SKELETON_ONLY_NOT_YET_INSTALLED 状态 · 暂不接入）
- 跨轨语义对齐（"其他轨的 backchannel 是不是应答的这段"）
- 主持人 vs 嘉宾角色的动态学习（sticker 到 speaker_map 上）
- 三人以上组合的判定（当前 speaker_map 只支持一个 host + 若干 guest）

---

## 九、三档诚实标注

**已验证事实**
- 顿悟 3 的原文引用：`main/runs/EP04-GOLD-EDL-20260818-1548/2026-08-18-1730-mentor-gold-cut-where-how-analysis.md` 里 "cross_track_speaking 目前定义 59/59 假阳"
- speaker_map schema `speaker-map-v1` 里 `map.<track_id>.role` + `role_rules.host_backchannel_skip` 字段已实测存在（EP04.speaker_map.json）
- auto_speaker_role.py 里已有 backchannel_whitelist 常量（HOST_BACKCHANNEL_TOKENS = 嗯/啊/对/对对/是/是的/好/好的/唉）
- apply_autocut_gate.py 现有 `cross_track_hit_count` 字段是 self_correction 用途，**不是**本文修正的 long_pause 用途——两者别混淆

**已决定的方向**
- 用 speaker_map.role + backchannel_whitelist + 词级 ASR 三源判定 · 不用 pyannote
- guest backchannel 默认视为"不算说话"（可开关）
- 新增 tool `crosstrack_silence_check` 单独负责 · 不直接改候选生成主逻辑
- 保留旧能量启发式作 fallback

**待验证假设**
- EP04 59 个长停顿的 speaker_map 覆盖率（是否每个候选窗内三轨都有 ASR 词级数据）—— 若某段词级缺失需按兜底走
- Precision 目标 30% 是否合理 · 需实测一次校准
- guest backchannel "不算说话" 默认值是否被 mentor 认可 · 首次跑完后需 mentor 复听 20 个 sample 校准
- EP03 是否已建 speaker_map · 若没有需先补
