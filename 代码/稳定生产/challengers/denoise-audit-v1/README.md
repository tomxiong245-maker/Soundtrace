# denoise-audit-v1 · Challenger 边界

## 定位

**只做诊断，不做生产**。这个 Challenger 是为了回答一个具体问题：**为什么 EP04 v12 听感差于 EP03？**  
它**不修改**任何既有 raw、EDL、run、成片、`autocut_policy` 或 `human_approved` 状态。

## 已完成

- 追溯 EP04 v12 render manifest / input manifest / denoise manifest：**v12 直接消费 raw，未做逐轨降噪**。
- 生成 6 段 × 3 版本（A/B/C）试听素材，A 与 C 响度匹配到 -16 LUFS。
- 客观测量：response spectrum centroid / HF energy / RMS percentiles / IQR。
- 报告：`main/runs/DENOISE-AUDIT-v1-20260813-1620/denoise_audit_report.md`
- 复听指南：`main/runs/DENOISE-AUDIT-v1-20260813-1620/LISTENING_GUIDE.md`
- Metrics：`main/runs/DENOISE-AUDIT-v1-20260813-1620/metrics/metrics.json`

## 未做（下一步）

- 真人 A/B 盲听 —— 唯一能证明"降噪确实改善听感"的验收步骤。
- `afftdn` 参数扫（nr=4/6/8/10/12），选适合 EP04 的最优参数。
- Artifact 主观分析（金属感、齿音损伤、房间残响衰减）。
- 与其他降噪工具（DeepFilterNet, RNNoise）的横向对比 —— 需先做许可证/延迟审计。
- 新建 v13 render Challenger 试跑"含降噪"版本。

## 边界

- 未修改：`稳定生产/rules/*`, `稳定生产/scripts/*`, EP03 / EP04 delivery run, `main/orchestrator/*`, `音频参考库/*`。
- 未联网、未上传真实音频、未使用云端服务、未用 TTS/AI 音乐。
- `afftdn=nr=8.0:nf=-55.0:tn=1:gs=5` 参数**从 EP03 复用**，不代表适合 EP04。
- 客观测量说明"降噪起作用"，**不**说明"听感改善"。

## 目录结构

```
稳定生产/challengers/denoise-audit-v1/
├── README.md                       (本文件)
├── scripts/
│   ├── gen_ab_c.py                 (生成 6 段 A/B/C + loudnorm)
│   └── spectral_metrics.py         (补充频谱质心/底噪 percentile)
└── docs/                            (为后续扩展预留)

main/runs/DENOISE-AUDIT-v1-20260813-1620/
├── denoise_audit_report.md         (主报告)
├── LISTENING_GUIDE.md              (一页复听说明)
├── previews/                        (6 × 3 = 18 段 wav + 18 段 mp3)
├── metrics/
│   └── metrics.json                (所有客观测量，含 source SHA)
└── logs/                            (中间品清理后为空)
```

## 复现

```bash
cd /Users/renting/Desktop/minglue/剪辑项目
RUN="main/runs/DENOISE-AUDIT-v1-$(date +%Y%m%d-%H%M)"
mkdir -p "$RUN"/{previews,metrics,logs}
python3 稳定生产/challengers/denoise-audit-v1/scripts/gen_ab_c.py --run-dir "$RUN"
python3 稳定生产/challengers/denoise-audit-v1/scripts/spectral_metrics.py --run-dir "$RUN"
```

需要本地 ffmpeg 7.x（延迟补偿常数 1200 samples 依赖此版本）。
