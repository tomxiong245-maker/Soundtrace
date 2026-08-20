# review-product-v1 · P1 审核产品 Challenger

> 目的：把真人审核做成安全、可实际操作、能测量耗时的产品原型。
> 状态：**ENGINEERING_E2E_PASS + HUMAN_REVIEW_SAVED + WAITING_FOR_RENDER_QC**。2026-08-11 后续在本机将范围缩成当日双态 MVP，并完成三轨工程实跑；下文原 Phase A 静态清单保留为历史设计，不再代表当前状态。
>
> 隔离范围：只写以下五处目录：
> - `稳定生产/challengers/review-product-v1/`（本目录）
> - `审核前端/challenger-review-product-v1/`
> - `main/runs/EP03-review-product-v1/`
> - `稳定生产/challengers/denoise-ab-v1/`（Phase B，只做审计）
> - `main/runs/EP03-denoise-ab-v1/`（Phase B，只做审计骨架）
>
> 严禁修改：Champion 规则/脚本、正式候选、正式审核页、正式 EDL、正式成片、orchestrator、P0 任何文件、原始 WAV、`cross-track-safety-v1` 已哈希产物（本 Challenger 只**新增** ERRATA）。

## 当前可运行 MVP（覆盖旧静态状态）

- `scripts/build_mvp_package.py`：按 tracks manifest 构建任意 N 轨审核包，支持 24-bit PCM extensible WAV；A/B 由全部输入轨重新生成。
- `scripts/server_mvp.py`：只监听 `127.0.0.1`，后端校验并保存决定；自动测试与真人结果隔离。
- `scripts/server_episode.py`：按显式 episode config 构建/复用任意一期的审核包，不再写死 EP03；旧 bundle 只有在输入、轨道清单和 UI 哈希完全一致时才复用。
- `scripts/render_episode.py`：按同一份 config 定位该期真人 EDL，调用 N 轨渲染器。
- `scripts/validate_mvp.py`：校验源轨、转写、UI、A/B、候选和 manifest 哈希；六点前只接受 `accept/reject`，明确拒绝 `adjust`。
- `scripts/render_ntrack_edl.py`：读取 P1 EDL，同步剪任意 N 轨并输出 stems、混音 WAV 和 MP3；自动 reviewer 默认禁止正式渲染。
- `审核前端/challenger-review-product-v1/启动审核.command`：双击启动当前 EP03 三轨兼容演示。
- `审核前端/challenger-review-product-v1/导出审核后语音.command`：真人完成 11 项后导出。

### 新一期通用入口

1. 复制 `episode-config.example.json` 为该期配置，确保候选包、轨道 manifest 和 `run_dir` 都属于同一 `episode_id`。
2. 只构建并校验：

   `python3 scripts/server_episode.py --config /absolute/path/to/EP04.review.json --build-only`

3. 启动审核：

   `python3 scripts/server_episode.py --config /absolute/path/to/EP04.review.json`

4. 真人点击“完成并保存”后导出：

   `python3 scripts/render_episode.py --config /absolute/path/to/EP04.review.json`

首次启动会构建 `run_dir/review_bundle`。后续启动默认复用并重验哈希，不会重建或覆盖已审核材料；配置与旧 bundle 不一致时会失败并要求换新 `run_dir`。

已实跑证据：3 轨、11 候选、22/22 A/B 浏览器可解码；合法后端提交成功、`adjust` 返回 400；20 秒三轨夹具实际得到 3 条等长 stem、混音 WAV 和 MP3。第三轨是既有 `speech_mix` 兼容夹具，不是今天新收到的真实三轨。EP03 的 11 项真人双态审核已保存，整片渲染与 QC 尚未验收。

2026-08-11 UI 复测：页面改为文本优先，只显示候选来源的原转写和拟删文字；试听收在可选展开区，不再阻塞选择。按钮为“采用剪切（删掉这段）/不剪（保留原音频）”，选中、改选和刷新恢复均通过真实浏览器交互；自动 E2E 输出隔离在 `e2e/`，而 EP03 真人结果另行保存在 run 根目录。轨道显示为物理麦 A/B/C，`female/male` 只注明为旧文件标记，不代表 AI 性别推断。

---

## 一、Phase A 交付物

```
稳定生产/challengers/review-product-v1/
├── README.md                              ← 你在读
├── audits/
│   ├── wavesurfer.md                      wavesurfer.js 官方 URL/版本/许可证/依赖/遥测审计
│   ├── playwright.md                      Playwright + Chromium 安全审计
│   └── deepfilternet.md                   Phase B 安全审计（不下载权重）
├── schemas/
│   ├── review_package.schema.json         审核包契约
│   └── human_decisions.schema.json        决定契约
├── scripts/
│   ├── build_review_product_package.py    从 cross-track-safety-v1 输出构建 v1 审核包（不写正式 EDL）
│   ├── validate_review_package.py         包完整性 + 哈希 + candidate.semantic_sha256
│   ├── validate_human_decisions.py        fail-closed decision 验证器（≥13 类拒绝）
│   ├── replay_preview.py                  adjust 后新试听 stub（ffmpeg 命令占位）
│   └── run_tests.py                       批量运行契约测试
├── tests/
│   ├── test_contracts.py                  13 项 fail-closed 契约测试
│   └── fixtures/
│       ├── valid_package.json
│       ├── valid_decisions.json
│       ├── invalid_missing_field.json
│       ├── invalid_pending.json
│       ├── invalid_wrong_reviewer.json
│       ├── invalid_tampered_candidate.json
│       ├── invalid_stale_preview.json
│       ├── invalid_stale_package.json
│       ├── invalid_adjust_no_reprocess.json
│       ├── invalid_wrong_manifest_sha.json
│       ├── invalid_duplicate_decision.json
│       ├── invalid_unknown_candidate.json
│       └── invalid_out_of_bounds.json
├── e2e/
│   └── playwright_review_e2e.spec.mjs     真实浏览器端到端（8 条路径）
├── exact_commands.sh                      shell 上线后一键复现
└── PROGRESS.md                            本次实施进度（诚实标注）
```

## 二、执行门禁（低风险优先）

1. 每次运行前先 `git status --short` 记录基线，`git add -A && git commit -m "P1 checkpoint <phase>"` 在改动前打本地快照（无远端 push，符合用户要求）。
2. 每次运行后计算所有产物 SHA-256 写入 `main/runs/EP03-review-product-v1/run_manifest.json`。
3. 服务端口固定 8767（避免与 cross-track-safety-v1 的 8766 冲突）。
4. Playwright 与 wavesurfer.js 只 vendor 到 Challenger 独立目录；不安装系统 Python/Node 包。
5. 遇到基线数字对不上、需要读写 P0/Champion/`cross-track-safety-v1` 历史产物、外部依赖许可证不明或需要联网上传音频——**立即停止并报告**。

## 三、诚实分层（禁词）

- 未跑真实浏览器 E2E 前：状态 `STATICALLY_PREPARED`，不写 "E2E verified"。
- 未真人完成 11 项审核前：不写 "人工审核完成"。
- 未运行契约测试前：不写 "全部通过" / "12/12"。
- 未做 DeepFilterNet 客观测量前：不写 "DeepFilterNet 更好 / 可用"。

Champion / P0 / cross-track-safety-v1 产物 SHA 一致性由 `exact_commands.sh` 步骤 0 自动验证；对不上就停下。
