# N 轨 episode config 契约 · tool-orchestrator-v1

## 核心约定 [已决定的方向]

- 产品层轨道身份使用 `track_id`（例：`track_01`）、`label`（例：`physical_mic_a`）与
  `sha256`。**不使用** `female / male` 或性别相关字段。
- Runner 对轨道数量不设上限；测试覆盖 N=2 / 3 / 4（见
  `tests/test_runner.py::test_ntrack_pipeline_stops_at_human_review`）。
- 每条 track 必须完整声明 `track_id / label / input_path / sample_rate /
  channel_count / duration_seconds / sha256`。缺字段一律 fail closed。

## 现有 Champion 工具对 N 轨的限制 [已验证事实]

`main/tools/tools.json` 中的部分工具在参数层面仍写死了成对轨（例：
`estimate_sync(track_a, track_b, output_json)`、`create_aligned_ab_previews`
显式依赖两条轨）。因此：

- 本 Challenger runner 只对 tool 注册表参数做静态匹配，**不改** Champion 工具
  参数；跨轨类工具接到 N > 2 时，需要在 Challenger 里另写 fan-out adapter，把
  N 条轨两两组合调 Champion。
- 本轮 Phase 4 只接入天然按单条 track 工作的只读工具（`inspect_audio` /
  `measure_loudness`），不触发这一限制；这也是 F07 “先做安全前置”的顺序。

## 示例 config

见 `contracts/episode.example.json`：一条 3 轨 config，全部指向 Challenger 内部
JSON fixture（保证不动真实 WAV），用 mock inspector 完成 3 步、停在
`HUMAN_REVIEW_REQUIRED`。这是 Phase 6 合成 fixture 端到端跑通所用的输入。
