# denoise-ab-v1 · Phase B（DeepFilterNet 当前默认实现）

> 状态：**USER_AUTHORIZED_DIRECT_INTEGRATION__SUBJECTIVE_REVIEW_PENDING**。2026-08-13 项目负责人明确选择 DeepFilterNet `v0.5.6`，并要求它直接替代旧方案。本机固定的官方 arm64 CLI 由 `端到端学习剪辑/代码/deepfilternet_denoise_tracks.py` 调用；它只在新 run 里生成派生轨，不改写 raw 或历史 run。`afftdn` 只保留为历史证据，不是回退工具。
>
> 上游 `--compensate-delay` 固定少 1,440 samples（30 ms）；适配器把原始末尾 30 ms 回填，以保留同一整数 sample 时间线。双轨 24-bit 合成 WAV 与 orchestrator→EDL 绑定已经过实际自检。模型权重商用许可与真实音频听感仍是未完成门，详见 `../review-product-v1/audits/deepfilternet.md`。

---

## 一、启动前门禁

必须依序全部通过后才可以进入实际短片段处理：

1. CLI SHA、版本、48 kHz 单声道输入和固定 1,440-sample 短缺必须全数匹配；任一不符即失败，不自动换版本。
2. 每条输出必须与输入等长，且 manifest 必须绑定 raw 与派生轨 SHA。
3. 真实节目 A/B 必须由真人确认噪声改善与语音损伤；在此之前只能称为“工程接入”，不能称为发布听感通过。
4. 模型权重的商用许可证仍是负责人已知、已授权但未关闭的风险；不得在对外材料中描述为“许可已验证”。

## 二、允许写入路径

- `稳定生产/challengers/denoise-ab-v1/`（本目录）
- `main/runs/EP03-denoise-ab-v1/`（尚未创建；启动前建）

## 三、禁止

- 修改 Champion、历史 `afftdn` 参数或 aligned wav。
- 覆盖 P0 输出、Champion 规则、cross-track-safety-v1 已哈希产物。
- 上传公司音频/转写。

## 四、下一步的最小实验（未来某轮）

```
1. 用真实节目短段做 raw / DeepFilterNet 的等响 A/B，优先检查高频金属感、齿音和讲话末尾。
2. 在一集完整 N 轨输入上记录耗时、内存、输出哈希与三轨 sample 对齐。
3. 真人听完整片后，记录继续使用或暂停该工具的决定；不得静默切回旧算法。
```

## 五、本轮完成物

- `../review-product-v1/audits/deepfilternet.md`（审计、SHA 与授权范围）
- `端到端学习剪辑/代码/deepfilternet_denoise_tracks.py`（适配器）
- `main/orchestrator/delivery_orchestrator.py`（默认调用入口）
