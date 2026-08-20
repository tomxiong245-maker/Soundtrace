# filler-global-pause-v1

这是一个隔离的 Challenger，只处理两类候选：

1. 明显犹豫音：`嗯 / 呃 / 额 / 唔 / uh / um` 等；
2. 弱口癖的连续重复：例如 `对 对`、`然后 然后`。单独一个 `对 / 然后 / 就是 / 这个 / 那个 / 啊` 不提名。

弱口癖重复会保留最后一个词，只把前面的重复部分交给真人判断。若不同物理麦把同一事件识别成不同长度，会按事件合并，并选择更保守的剪口。

长停顿不是逐轨判断。只有所有物理轨都没有词级活动、且所有原始 WAV 在同一时间段都通过逐帧声学安静检查，才产生候选。候选只压缩中间部分，默认保留约 `0.75 s` 自然停顿；咳嗽、碰麦、音乐或任一轨有明显能量都会阻断。

所有输出均为 `NEEDS_HUMAN_REVIEW`，不会自动接受、不会自动生成正式 EDL、不会覆盖原始音频。全轨长停顿在独立审核页面中必须听完原音频和压缩后音频，才能固定 `accept / reject`。

## 运行

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B \
  '稳定生产/challengers/filler-global-pause-v1/scripts/run_tests.py'
```

先构建候选源：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B \
  '稳定生产/challengers/filler-global-pause-v1/scripts/build_filler_global_pause_review_source.py' \
  --p0-report 'main/runs/<EP>-p0/01_transcripts/p0_mvp_report.json' \
  --episode-id '<EP>' \
  --out 'main/runs/<EP>-filler-global-pause-v1-YYYYMMDD'
```

再用 `scripts/build_review_package.py` 生成 A/B 审核包，最后用
`scripts/server_review.py` 本地打开审核页面。审核结果只落盘到新的 run
目录，待人工确认后再决定是否单独生成同步 EDL。
