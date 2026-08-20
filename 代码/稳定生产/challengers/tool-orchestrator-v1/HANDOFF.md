# HANDOFF · tool-orchestrator-v1

> 日期：2026-08-12
> 由本轮夜间施工 Agent 交给下一个 worker / 项目负责人。

## 当前状态（一句话）

Runner 能静态校验 `main/tools/tools.json`，并以 Challenger adapter 注册表冻结计划、真实 subprocess 调用 Champion `inspect_audio.py` 与 Challenger adapter，在 `HUMAN_REVIEW_REQUIRED` 停止。26/26 自动测试通过。Champion 27 项文件 SHA 未变。未触碰 `main/tools/tools.json`、`main/orchestrator.py`、`main/runs/EP*`、原始 WAV、Mentor 素材、`审核前端/`、其它 Challenger。

## 已完成（有工程证据）

- Phase 0：基线与 Champion SHA 冻结（`baseline/`）。
- Phase 1：注册表校验器，含失败 fixture 与 6/6 测试。
- Phase 2：最小 Runner（读 registry → 冻结 plan → subprocess → state.json / tool_calls.jsonl / run_manifest.json），8/8 测试。
- Phase 3：N 轨契约（`track_id / label / sha256`；2/3/4 轨测试通过）。
- Phase 4：Champion `inspect_audio.py` 通过 Challenger adapter 真实调用；保留了一条真实 FAILED run 作为失败证据。
- Phase 5：11 类失败场景全部有自动测试 (7/7 safety-gates)。
- Phase 6：2 步端到端（Champion inspect_audio → Challenger summarize）真实跑到 `HUMAN_REVIEW_REQUIRED`。
- Phase 7：README / benchmark_report / 优化候选 / exact_commands / HANDOFF 全部落盘。
- 独立审查加固：冻结计划时拒绝 `reads_only=false` 工具；注册表拒绝绝对、
  `..` 或符号链接逃逸的 scripts root；输出只能落在新 run 目录；dry-run 不改变正式 state。

## 明确没做（下一位不要误读为已完成）

- 没有跑真实 EP04 / EP03 三轨 WAV：为遵守“不覆盖既有 run + 不改 Champion”边界，仅用合成 WAV fixture 证明桥接。
- 没有跑 `measure_loudness`（本机无 ffmpeg）；没有跑 `estimate_sync / denoise / transcribe / build_review_package / render_*`。
- 没有做任何 approve / finalize / archive / render / assemble；没有产 EDL、成片、审核决定。
- 没有修改 Champion、`main/tools/tools.json`、`main/orchestrator.py`、`稳定生产/scripts/`、`稳定生产/rules/`。
- 没有更新 `统筹全局/当前项目进度.md` 或 `统筹全局/全局统筹记忆.md`：本轮属于 Challenger 内部通过，未晋升 Champion；不满足更新那两份文档的门槛（“新架构决定”或“新的真实状态”）。

## 已知限制

1. Champion 部分工具的 CLI 参数命名与 `tools.json` 声明的 params 不完全对齐（例：`inspect_audio.py` 用 `--input LABEL=/abs/path`）。Challenger 内已通过 adapter 桥接，Champion 未动；未来任何一次 Champion CLI 微调都需要同步更新 adapter 与 SHA。
2. Runner 只处理 `phase="pre_review"` 的步骤；post_review（人工审核后）与真人闸门仍留给 Champion `orchestrator.py` 与 P1 review-product-v1。
3. Runner 不做候选生成、语义决定或 EDL；这些属于 P1 或人工层。

## 下一步唯一最重要的动作

**在 Champion 未升级前，用真实 EP04 三轨 WAV 走一次 pre_review：**

1. 新建一个 `main/runs/TOOL-ORCH-FIXTURE-tool-orchestrator-v1-<新timestamp>/`；
2. 让 episode config 的 `tracks` 直接引用真实 EP04 WAV 的 `input_path` 与已冻结 SHA（read-only）；
3. steps 只允许 `inspect_audio_adapter` 与 `summarize_inspection_adapter`（或按需再加一个 reads_only 的 adapter）；
4. `human_review_after` 停在最后一步；
5. 结束后核对：三条轨 SHA 与既有 EP04 记录一致、Champion 27 项 SHA 不变、runner 状态在 `HUMAN_REVIEW_REQUIRED`；
6. 更新 `统筹全局/当前项目进度.md` 中 F07 的相关行（“orchestrator 真调 tool”从“未接通”改为“已在只读前置工具上真实接通”），并附上 run 目录。

在 (1)–(5) 全部拿到证据前，不要更新 `统筹全局/全局统筹记忆.md` 的架构表；更不要合并 runner 到 Champion。
