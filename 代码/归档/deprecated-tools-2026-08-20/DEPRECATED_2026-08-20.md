# extract_gold_cut_features · DEPRECATED 2026-08-20

## 归档原因
- 输入前提不成立 · mentor 从未给过 gold EDL 手填的 crossfade_ms/reason/category
- EP03 mentor 只留 mp3 · 反推 56 位置有 boundary artifacts (word-middle cut)
- EP04 gold EDL 由用户自己 label · 2026-08-19 已冻结 (.FROZEN_2026-08-19 sidecar)
- 全项目代码消费者 0 · 唯有 EP04 workflow prompt 引用

## 复活条件
- 未来 mentor 真给一份完整 gold EDL (含 crossfade_ms · reason · category 手填字段)
- EP03 boundary artifacts 修正后重新反推

## 原脚本位置
- 原: 最终交付/代码/main/orchestrator/extract_gold_cut_features.py
- 现: 最终交付/代码/归档/deprecated-tools-2026-08-20/extract_gold_cut_features.py

## 关联删除动作(同批 2026-08-20)
- tools.json 三处登记撤销 (main/tools · knowledge · 新落地/tools-json)
- tool-orchestrator-v2 registry adapter (extract-gold-cut-features-v2) 撤销
- 活代码 (剪辑项目/main/) 镜像同步撤销
