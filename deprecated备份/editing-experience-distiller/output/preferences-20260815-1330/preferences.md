# Mentor 偏好卡 · 从 65 条真人决定归纳

> 生成时间：2026-08-15
> 生成方式：`distill_preferences.py` 扫描全部 human_decisions*.json + 对应 review_package
> 输入范围：EP03 + EP04 全部 run（排除 EP03 主目录的 26 条 bulk_accept）
> 规范：`skills/editing-experience-distiller/references/experience-card-template.md`

## 总览

- 累计 **65** 条真人二态决定
- **24** accept / **41** reject
- 覆盖 reason_key: ['REAUDIT_filler_repetition', 'REAUDIT_filler_weak_topic', 'REAUDIT_immediate_repetition', 'REAUDIT_long_pause', 'cough_like', 'filler_ack_dense', 'filler_ack_long_single', 'filler_hesitation', 'filler_immediate_repetition', 'filler_strong', 'filler_weak', 'filler_weak_pure', 'filler_weak_topic', 'global_long_pause', 'immediate_repetition', 'long_pause', 'mic_bump_like']
- mentor 明显倾向：**保守（reject 比例 63%）**

---

## reason_key: `REAUDIT_filler_repetition`

- **样本数**: 1  |  accept=0  |  reject=1  |  接受率=0%
- **时长分布**: 400ms_1s=1
- **拟处理词分布**（前10）: `?`×1

### ❌ reject 样本 (1)

- `R-S038` (EP04) 拟删:`?` [400ms_1s / track_01 / [clause-mid]]  💬 "不剪"   ⌈prev='' next='' round1 reject '剪辑痕迹明显'⌋

---

## reason_key: `REAUDIT_filler_weak_topic`

- **样本数**: 1  |  accept=1  |  reject=0  |  接受率=100%
- **时长分布**: 400ms_1s=1
- **拟处理词分布**（前10）: `?`×1

### ✅ accept 样本 (1)

- `R-E304` (EP04) 拟删:`?` [400ms_1s / female / [edge-of-track]]   ⌈prev='' next='' round1 accept '剪辑痕迹明显'⌋

---

## reason_key: `REAUDIT_immediate_repetition`

- **样本数**: 1  |  accept=0  |  reject=1  |  接受率=0%
- **时长分布**: 100_400ms=1
- **拟处理词分布**（前10）: `?`×1

### ❌ reject 样本 (1)

- `R-E306` (EP04) 拟删:`?` [100_400ms / female / [edge-of-track]]  💬 "剪辑痕迹明显（剪辑的不够干净，而且这个其实可以不用剪）"   ⌈prev='' next='' round1 accept '剪辑痕迹太明显'⌋

---

## reason_key: `REAUDIT_long_pause`

- **样本数**: 1  |  accept=1  |  reject=0  |  接受率=100%
- **时长分布**: 400ms_1s=1
- **拟处理词分布**（前10）: `?`×1

### ✅ accept 样本 (1)

- `R-S032` (EP04) 拟删:`?` [400ms_1s / track_01 / [clause-mid]]   ⌈prev='' next='' round1 reject '剪辑痕迹明显'⌋

---

## reason_key: `cough_like`

- **样本数**: 2  |  accept=0  |  reject=2  |  接受率=0%
- **时长分布**: 100_400ms=2
- **拟处理词分布**（前10）: `?`×2

### ❌ reject 样本 (2)

- `S025` (EP04) 拟删:`?` [100_400ms / track_02 / ]  💬 "没有咳嗽"   ⌈咳嗽落在讲话流中⌋
- `S081` (EP04) 拟删:`?` [100_400ms / track_02 / ]  💬 "声音很轻
为什么要见"   ⌈极短(0.11s)⌋

---

## reason_key: `filler_ack_dense`

- **样本数**: 2  |  accept=0  |  reject=2  |  接受率=0%
- **时长分布**: <100ms=2
- **拟处理词分布**（前10）: `?`×2

### ❌ reject 样本 (2)

- `N006` (EP04) 拟删:`?` [<100ms / track_03 / [clause-boundary]]   ⌈prev='嗯' next='嗯' ⌋
- `N007` (EP04) 拟删:`?` [<100ms / track_03 / [clause-boundary]]  💬 "保留一些活人感（而且这是对别人的认可，表示自己在听，让播客更有活人感，不是废话）"   ⌈prev='嗯' next='嗯' ⌋

---

## reason_key: `filler_ack_long_single`

- **样本数**: 2  |  accept=0  |  reject=2  |  接受率=0%
- **时长分布**: 1_2s=1, 400ms_1s=1
- **拟处理词分布**（前10）: `?`×2

### ❌ reject 样本 (2)

- `N004` (EP04) 拟删:`?` [1_2s / track_03 / [clause-boundary]]   ⌈prev='嗯' next='嗯' ⌋
- `N005` (EP04) 拟删:`?` [400ms_1s / track_03 / [clause-boundary]]   ⌈prev='嗯' next='嗯' ⌋

---

## reason_key: `filler_hesitation`

- **样本数**: 11  |  accept=5  |  reject=6  |  接受率=45%
- **时长分布**: 100_400ms=3, 400ms_1s=8
- **拟处理词分布**（前10）: `?`×7, `呃`×2, `对`×1, `对对`×1

### ✅ accept 样本 (5)

- `C014` (EP03) 拟删:`?` [400ms_1s / track_01 / ]
- `C024` (EP03) 拟删:`?` [400ms_1s / track_02 / ]
- `C010` (EP04) 拟删:`对对` [400ms_1s / track_01 / ]
- `C002` (EP04) 拟删:`呃` [400ms_1s / track_01 / ]
- `C003` (EP04) 拟删:`呃` [400ms_1s / track_01 / [clause-tail]high]  💬 "很好"

### ❌ reject 样本 (6)

- `C001` (EP03) 拟删:`?` [400ms_1s / track_01 / ]
- `C011` (EP03) 拟删:`?` [400ms_1s / track_02 / ]
- `C001` (EP04) 拟删:`对` [400ms_1s / track_02 / ]
- `E301` (EP04) 拟删:`?` [100_400ms / male / ]  💬 "不要剪，额度是一个词"   ⌈额⌋
- `E302` (EP04) 拟删:`?` [100_400ms / male / ]  💬 "一个完整的单词，为什么要剪"   ⌈er⌋
- `E303` (EP04) 拟删:`?` [100_400ms / female / ]  💬 "这是一个词啊"   ⌈额⌋

---

## reason_key: `filler_immediate_repetition`

- **样本数**: 2  |  accept=0  |  reject=2  |  接受率=0%
- **时长分布**: 100_400ms=1, 400ms_1s=1
- **拟处理词分布**（前10）: `?`×2

### ❌ reject 样本 (2)

- `S038` (EP04) 拟删:`?` [400ms_1s / track_01 / ]  💬 "很明显的剪辑痕迹，而且不需要剪"   ⌈报告报告⌋
- `S056` (EP04) 拟删:`?` [100_400ms / track_01 / ]   ⌈我自己我自己⌋

---

## reason_key: `filler_strong`

- **样本数**: 2  |  accept=0  |  reject=2  |  接受率=0%
- **时长分布**: 100_400ms=2
- **拟处理词分布**（前10）: `?`×2

### ❌ reject 样本 (2)

- `N002` (EP04) 拟删:`?` [100_400ms / track_01 / [clause-mid]]  💬 "剪辑痕迹明显"   ⌈prev='大' next='写' ⌋
- `N003` (EP04) 拟删:`?` [100_400ms / track_01 / [clause-boundary]]  💬 "不要剪（要保证完整性，这个是在句中）"   ⌈prev='色' next='然后' ⌋

---

## reason_key: `filler_weak`

- **样本数**: 2  |  accept=1  |  reject=1  |  接受率=50%
- **时长分布**: 400ms_1s=2
- **拟处理词分布**（前10）: `?`×2

### ✅ accept 样本 (1)

- `E304` (EP04) 拟删:`?` [400ms_1s / female / ]  💬 "识别正确，但是剪辑痕迹明显"   ⌈然后这个⌋

### ❌ reject 样本 (1)

- `E305` (EP04) 拟删:`?` [400ms_1s / female / ]  💬 "不是口癖啊，是完整的词"   ⌈对这个⌋

---

## reason_key: `filler_weak_pure`

- **样本数**: 2  |  accept=0  |  reject=2  |  接受率=0%
- **时长分布**: 400ms_1s=2
- **拟处理词分布**（前10）: `?`×2

### ❌ reject 样本 (2)

- `N010` (EP04) 拟删:`?` [400ms_1s / track_02 / [clause-boundary]]   ⌈prev='合' next='Ge' ⌋
- `N011` (EP04) 拟删:`?` [400ms_1s / track_03 / [clause-boundary]]  💬 "不剪"   ⌈prev='詢' next='d' ⌋

---

## reason_key: `filler_weak_topic`

- **样本数**: 3  |  accept=0  |  reject=3  |  接受率=0%
- **时长分布**: 400ms_1s=3
- **拟处理词分布**（前10）: `?`×3

### ❌ reject 样本 (3)

- `M001` (EP04) 拟删:`?` [400ms_1s / female / [edge-of-track]]  💬 "还是剪辑痕迹明显"   ⌈prev='范' next='花' ⌋
- `N008` (EP04) 拟删:`?` [400ms_1s / track_01 / [clause-boundary]]  💬 "这也是表示对别人的认可（你可以理解为因为楠哥在这里没有说完整的句子，就可以判定为认可的模板）"   ⌈prev='化' next='时候' ⌋
- `N009` (EP04) 拟删:`?` [400ms_1s / track_02 / [clause-mid]]  💬 "不剪，剪辑痕迹很重，而且句中不大需要"   ⌈prev='很多' next='增' ⌋

---

## reason_key: `global_long_pause`

- **样本数**: 1  |  accept=1  |  reject=0  |  接受率=100%
- **时长分布**: 5s+=1
- **拟处理词分布**（前10）: `?`×1

### ✅ accept 样本 (1)

- `C032` (EP04) 拟删:`?` [5s+ / track_01 / ]

---

## reason_key: `immediate_repetition`

- **样本数**: 28  |  accept=13  |  reject=15  |  接受率=46%
- **时长分布**: 100_400ms=12, 1_2s=2, 400ms_1s=14
- **拟处理词分布**（前10）: `?`×24, `然后`×1, `我们`×1, `也是`×1, `因为`×1

### ✅ accept 样本 (13)

- `C007` (EP03) 拟删:`?` [400ms_1s / track_02 / ]
- `C009` (EP03) 拟删:`?` [100_400ms / track_02 / ]
- `C005` (EP04) 拟删:`?` [400ms_1s / track_01 / ]
- `C035` (EP04) 拟删:`?` [400ms_1s / track_01 / ]
- `C043` (EP04) 拟删:`?` [400ms_1s / track_03 / ]
- `C047` (EP04) 拟删:`?` [400ms_1s / track_03 / ]
- `C049` (EP04) 拟删:`?` [400ms_1s / track_02 / ]
- `C053` (EP04) 拟删:`?` [100_400ms / track_01 / ]
- `E306` (EP04) 拟删:`?` [100_400ms / female / ]  💬 "可以剪辑，但是剪辑痕迹太明显了"   ⌈一个/一个⌋
- `N012` (EP04) 拟删:`?` [400ms_1s / track_02 / [clause-mid]]  💬 "很好"   ⌈prev='这个' next='方' ⌋
- `N014` (EP04) 拟删:`?` [1_2s / track_01 / [clause-mid]]  💬 "很好"   ⌈prev='一些' next='一些' ⌋
- `C012` (EP04) 拟删:`然后` [100_400ms / track_02 / [clause-mid]high]
- `C019` (EP04) 拟删:`我们` [400ms_1s / track_01 / [clause-mid]high]

### ❌ reject 样本 (15)

- `C006` (EP03) 拟删:`?` [400ms_1s / track_02 / ]
- `C013` (EP03) 拟删:`?` [400ms_1s / track_02 / ]
- `C018` (EP03) 拟删:`?` [400ms_1s / track_02 / ]
- `C021` (EP03) 拟删:`?` [100_400ms / track_02 / ]
- `C022` (EP03) 拟删:`?` [400ms_1s / track_02 / ]
- `C001` (EP04) 拟删:`?` [400ms_1s / track_01 / ]
- `C014` (EP04) 拟删:`?` [1_2s / track_01 / ]
- `C015` (EP04) 拟删:`?` [100_400ms / track_03 / ]
- `C017` (EP04) 拟删:`?` [100_400ms / track_03 / ]
- `C039` (EP04) 拟删:`?` [100_400ms / track_02 / ]
- `C040` (EP04) 拟删:`?` [100_400ms / track_01 / ]
- `C041` (EP04) 拟删:`?` [100_400ms / track_02 / ]
- `N013` (EP04) 拟删:`?` [100_400ms / track_01 / [clause-tail]]  💬 "不减不减"   ⌈prev='就是' next='Flow' ⌋
- `C026` (EP04) 拟删:`也是` [100_400ms / track_02 / [clause-mid]high]  💬 "剪辑的时候声音明显小了"
- `C028` (EP04) 拟删:`因为` [400ms_1s / track_01 / [clause-head]high]  💬 "剪辑痕迹很重"

---

## reason_key: `long_pause`

- **样本数**: 3  |  accept=2  |  reject=1  |  接受率=67%
- **时长分布**: 400ms_1s=2, 5s+=1
- **拟处理词分布**（前10）: `?`×3

### ✅ accept 样本 (2)

- `S022` (EP04) 拟删:`?` [400ms_1s / track_02 / ]   ⌈0.71s 静音⌋
- `S047` (EP04) 拟删:`?` [5s+ / track_01 / ]   ⌈6.58s 冷场⌋

### ❌ reject 样本 (1)

- `S032` (EP04) 拟删:`?` [400ms_1s / track_01 / ]  💬 "剪辑痕迹明显"   ⌈0.61s⌋

---

## reason_key: `mic_bump_like`

- **样本数**: 1  |  accept=0  |  reject=1  |  接受率=0%
- **时长分布**: 100_400ms=1
- **拟处理词分布**（前10）: `?`×1

### ❌ reject 样本 (1)

- `S082` (EP04) 拟删:`?` [100_400ms / track_02 / ]  💬 "根本没有"   ⌈轻碰麦⌋
