# 标签偏好快照 preferences-20260815-label-loop-v1

- 有效记录：**60**（accept=28 / reject=32）
- reason_key：filler_hesitation, global_long_pause, immediate_repetition
- 作用：只影响候选排序与审核提示；不生成 human_accept、不生成 EDL、不授权自动剪。
- 每条规则都保留 case_id 和来源文件 SHA，可回溯。

## 规则信号

- `H-001` `filler_hesitation` `对` [-]：historical_reject (accept=0, reject=2)；cases=EP04-filler-global-pause-v1-r2-20260812::C001::8f89a8b8563d,EP04::EP04-review-3cadf0f352f2::C001
- `H-002` `filler_hesitation` `对对` [-]：historical_accept (accept=2, reject=1)；cases=EP04-filler-global-pause-v1-r2-20260812::C010::8f89a8b8563d,EP03::EP03-review-00cc8692b46c::C001,EP04::EP04-review-3cadf0f352f2::C010
- `H-003` `filler_hesitation` `对这个` [-]：mixed (accept=1, reject=1)；cases=EP03::EP03-review-00cc8692b46c::C011,EP03::EP03-review-00cc8692b46c::C024
- `H-004` `immediate_repetition` `hello` [-]：historical_reject (accept=0, reject=2)；cases=EP04-review-product-v2::C001::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C001
- `H-005` `immediate_repetition` `一条` [-]：historical_reject (accept=1, reject=2)；cases=EP04-review-product-v2::C017::8c90d8262c85,EP03::EP03-review-00cc8692b46c::C009,EP04::EP04-review-294a5930b8a9::C017
- `H-006` `immediate_repetition` `一步` [-]：historical_reject (accept=0, reject=2)；cases=EP04-review-product-v2::C015::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C015
- `H-007` `immediate_repetition` `什么` [-]：historical_reject (accept=0, reject=4)；cases=EP04-review-product-v2::C039::8c90d8262c85,EP04-review-product-v2::C040::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C039,EP04::EP04-review-294a5930b8a9::C040
- `H-008` `immediate_repetition` `会比` [-]：historical_accept (accept=2, reject=0)；cases=EP04-review-product-v2::C047::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C047
- `H-009` `immediate_repetition` `呵呵` [-]：historical_reject (accept=0, reject=2)；cases=EP04-review-product-v2::C014::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C014
- `H-010` `immediate_repetition` `在上面` [-]：historical_accept (accept=2, reject=0)；cases=EP04-review-product-v2::C005::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C005
- `H-011` `immediate_repetition` `工具` [-]：historical_reject (accept=0, reject=2)；cases=EP04-review-product-v2::C041::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C041
- `H-012` `immediate_repetition` `我们` [-]：historical_accept (accept=2, reject=0)；cases=EP04-review-product-v2::C035::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C035
- `H-013` `immediate_repetition` `我自己` [-]：historical_accept (accept=2, reject=0)；cases=EP04-review-product-v2::C053::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C053
- `H-014` `immediate_repetition` `所以` [-]：historical_accept (accept=2, reject=0)；cases=EP04-review-product-v2::C049::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C049
- `H-015` `immediate_repetition` `拿了` [-]：historical_accept (accept=2, reject=0)；cases=EP04-review-product-v2::C043::8c90d8262c85,EP04::EP04-review-294a5930b8a9::C043
- `H-016` `immediate_repetition` `特别` [-]：historical_reject (accept=0, reject=2)；cases=EP03::EP03-review-00cc8692b46c::C021,EP03::EP03-review-00cc8692b46c::C022
