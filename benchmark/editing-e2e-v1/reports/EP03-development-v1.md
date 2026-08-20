# EP03 development 端到端 benchmark：证据说明

## 这份基准现在能做什么

`manifests/EP03-development-v1.episode.manifest.json` 只建立一个本地、可复现的开发期索引：它把两条 EP03 raw、Mentor 参考成片、既有人审候选/决定/EDL，以及历史技术 QC 的位置和既有 SHA 放到同一份清单中。

它明确不是 frozen 集、不是 gold、不是 Champion 对比成绩，也不是一份可发布的整期人审交付。真实音频仍在原位置；本目录没有复制任何媒体。

## 已找到的证据与边界

| 类别 | 可用证据 | 不能据此说的话 |
| --- | --- | --- |
| 两条 raw | `ZOOM0008_Tr1.WAV`、`ZOOM0008_Tr3.WAV` 的历史 SHA、48 kHz/24-bit/mono、87,575,680 sample；当前文件已整理到 `raw material/第三集/` | 本次没有重算移动后音频 SHA，不能声称已经重新验证内容未变 |
| 参考成片 | `音频参考库/成品/EP03.mp3` 的历史 SHA 和规格；旧项目 README 称其为 Mentor 人工成品 | 没有原始人工 EDL、逐剪口说明或 human edit-map |
| 真人审核 | `EP03-review-product-v1` 的 11 条逐项决定、4 条接受剪口和审核事件记录 | 它只覆盖两类候选，不能代表整期人工精剪或召回率 |
| 渲染/QC | 同一人审 run 有 20 秒三轨渲染 fixture；`EP03-freshrun-20260810-1730` 有整期技术渲染/QC | 两者不是同一完整人审交付 run；freshrun 使用自动全接受，不能当真人 gold |

`review_package.json` 里有两种不同 SHA：文件字节 SHA 和 canonical review-manifest SHA。前者用于文件快照，后者用于人审决定绑定；它们不是冲突，也不能互相比较。

## 现在不能计算的指标

以下字段已被 manifest 明确列为 `cannot_compute_yet`：人工删除区间 precision/recall、样本级边界差、候选对人工删剪的覆盖率、整期盲听 verdict、语义误删/突兀剪口数量、净节省时间。

原因很简单：没有原始人工 edit-map，且尚未运行一个与人审和整期 QC 同身份绑定的新 Challenger。把 Mentor MP3 自动对齐后反推的区间只能帮助定位，不能替代人工真值。

## 如何重跑轻量验证

在项目根目录执行：

```bash
python3 benchmark/editing-e2e-v1/validate_manifest.py \
  benchmark/editing-e2e-v1/manifests/EP03-development-v1.episode.manifest.json
```

该命令只读取 manifest JSON、检查引用路径是否存在，并检查 SHA 字段是否为 64 位小写十六进制。它不会打开、解码或重算 WAV/MP3，也不会改变任何 run。

若 raw 的移动后身份需要严格复核，应另行获得对真实媒体做 SHA 重算的允许；不要把本轻量验证误写成媒体内容校验。

## 变成可比较基准前的最小补齐

1. 收到原始人工 EDL，或由人复核产生 `human_edit_map`；
2. 用冻结的 Challenger 在同一两条 raw 上跑出候选、真人决定、EDL、整片成片与 QC；
3. 安排独立整片盲听，记录语义误删、剪口听感、审核/返工/维护时间；
4. 仍只作为 development 调试；要晋升前必须另建不再调参的 frozen 集。
