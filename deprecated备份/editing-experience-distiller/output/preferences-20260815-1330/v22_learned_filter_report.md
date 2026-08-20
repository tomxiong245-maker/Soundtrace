# v22 学到的过滤报告

> 生成方式：`apply_learned_filter.py` · 用 65 条真人历史决定过滤 v22 候选池
> 目的：证明历史标签是否真的能影响候选而不是只在前端展示

## 总览

- v22 原始候选池: **12** 条
- 应从历史阻断（不该出现在候选池）: **1** 条
- 应从历史 promote（可自动进 machine_assisted_draft）: **8** 条
- 无历史依据、继续人审: **3** 条
- **理论上真人只需审 3 条**（其余由历史直接判定）

## 🚫 应被历史阻断（不该提名给你） · 1 条

- `C035` · immediate_repetition · 拟删「」 · -
  - 依据: 同类历史 9 accept / 12 reject → 倾向 reject

## ✅ 历史 promote（可自动进机器辅助剪） · 8 条

- `C007` · filler_hesitation · 拟删「呃」 · clause-tail
  - 依据: strong hesitation「呃」+ clause-tail 历史 accept (C003 v20 明确留言'很好')
- `C023` · immediate_repetition · 拟删「然后」 · clause-mid
  - 依据: immediate_repetition + 功能词「然后」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')
- `C034` · immediate_repetition · 拟删「我们」 · clause-mid
  - 依据: immediate_repetition + 功能词「我们」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')
- `C036` · immediate_repetition · 拟删「什麼」 · clause-mid
  - 依据: immediate_repetition + 功能词「什麼」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')
- `C037` · immediate_repetition · 拟删「什么」 · clause-mid
  - 依据: immediate_repetition + 功能词「什么」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')
- `C039` · immediate_repetition · 拟删「一些」 · clause-mid
  - 依据: immediate_repetition + 功能词「一些」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')
- `C042` · immediate_repetition · 拟删「也是」 · clause-mid
  - 依据: immediate_repetition + 功能词「也是」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')
- `C044` · immediate_repetition · 拟删「因为」 · clause-head
  - 依据: immediate_repetition + 功能词「因为」历史 accept (N012 这个/这个、N014 一些/一些 均 '很好')

## ❔ 无同类历史依据（需要真人判断） · 3 条

- `C006` · filler_hesitation · 拟删「对」 · clause-head
- `C009` · filler_hesitation · 拟删「额」 · clause-tail
- `C014` · immediate_repetition · 拟删「go」 · clause-mid
