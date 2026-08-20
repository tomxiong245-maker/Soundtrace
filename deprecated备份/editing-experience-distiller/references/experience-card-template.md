# 经验卡模板

> 一张卡只针对一个 `reason_key` 或一个明确的失败模式。它是“下一轮如何验证”的说明，不能直接成为生产规则。

## 1. 查询范围

```text
snapshot: <ACTIVE_SNAPSHOT 指向目录>
query: episode_id=<...> / reason_key=<...>
matched_cases: <n>
case_ids: <最多 10 个>
```

## 2. 已验证事实

- 当前样本数、节目数、审核人数。
- `accept/reject` 分布；是否有必听 A/B；是否已进入 EDL。
- 只写能逐条回到 case_id 的事实。

## 3. 可复用的工作经验

用人话写出“下一次看到什么，应该怎样准备给人看”，例如：

```text
连续重复词只生成候选；若紧邻上下文出现专名、数字或否定，标高风险并提供更长文字上下文。
```

不要写成“以后自动删除”。

## 4. 反例 / 不要做什么

- 哪些 `reject` 说明简单模式匹配会误删？
- 哪些来源或轨道活动证据不可靠？
- 当前样本缺少什么，因而不能下结论？

## 5. Challenger 假设

```text
hypothesis: <一条可证伪的规则或排序假设>
change_scope: <只新增的 Challenger 目录>
expected_benefit: <减少何种审核或返工>
failure_risk: <最坏误删/漏删是什么>
```

## 6. 最小验证计划

1. 合成 fixture：覆盖正确命中、应阻断、边界、跨轨冲突。
2. development episode：跑真实原始素材，但不覆盖 Champion。
3. 产出审核包，人工逐项 `accept/reject`。
4. 端到端 benchmark：至少记录误删、剪口听感、审核/返工时间。
5. 满足门槛前：`NO_PRODUCTION_CHANGE`。

## 7. 结论枚举

只能选其一：

```text
INSUFFICIENT_DATA
READY_FOR_CHALLENGER
CHALLENGER_NEEDS_HUMAN_REVIEW
```

不要输出 `PROMOTE_TO_CHAMPION`；晋升必须由冻结 benchmark、独立复核和人工决定完成。
