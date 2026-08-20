# Mentor feedback regression v1

这个目录是一个很小的、**只用于 development** 的回归资产：将两轮已经完成的人审候选、`accept/reject` 和审核备注固定为可重建 JSON。它的作用是让以后 Challenger 改候选规则、排序或试听渲染时，能回看“这类候选过去被如何决定、审核人写了什么”，而不是每次从零开始猜。

它不是训练集、frozen benchmark、自动删剪政策、生产规则，也不改变 EP04 v20、canonical experience snapshot 或任何真实媒体。

## 唯一允许的输入

构建脚本只读取这四份 JSON：

- `main/runs/EP04/EP04-review-mixed-14-20260814-043428/human_decisions_and_feedback__20260814-052319.json`
- `main/runs/EP04/EP04-review-mixed-14-20260814-043428/review_bundle/review_package.json`
- `main/runs/EP04/EP04-review-round2-20260814-1355/human_decisions_and_feedback__20260814-final.json`
- `main/runs/EP04/EP04-review-round2-20260814-1355/review_bundle/review_package.json`

第二轮的 `auto_saved_reviews.json`、非 final 的时间戳快照和 `-partial.json` 都被显式排除。构建器不使用 glob、目录扫描或“最新文件”猜测，因此不会把草稿、局部保存或自动保存静默混进来。

## 产物与验证

- `catalog.json`：逐条保留完整 candidate metadata、完整的人审决定（包括原文 `feedback`）、来源相对路径与文件 SHA、审核包 preview hash 和人审记录的 preview hash。
- `REPORT.md`：记录实际数量、严格 semantic SHA 绑定结果和 preview hash 差异；差异被保留为证据，不会被当作通过。

在项目根目录运行：

```bash
python3 benchmark/editing-e2e-v1/mentor-feedback-regression-v1/build_catalog.py --build
python3 benchmark/editing-e2e-v1/mentor-feedback-regression-v1/build_catalog.py --check
```

`--check` 会要求两轮都“最终决定数 = 审核包候选数”，每条决定的 `candidate_semantic_sha256` 必须精确对应审核包候选，且每个 `feedback` 字段（即使为空）都必须保留。它只读取 JSON；不会打开、解码、复制或重算 WAV/MP3。

没有做反馈关键词分类。若未来添加，必须明确写为 derived，不能替代真人标签或备注。
