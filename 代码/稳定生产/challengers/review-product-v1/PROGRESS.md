# PROGRESS · P1 review-product-v1

日期：2026-08-11
会话：P1 新窗口（Fable 5 / Opus 4.7）
状态：**STATICALLY_PREPARED**（代码/schema/tests/前端/E2E/审计均已就绪；shell 不可用致本会话未执行）

---

## 一、做了什么（诚实清单）

### 已完成（Read/Write/Edit 可覆盖的部分）

- [x] 强制阅读全部统筹文档、施工规则、任务书、v3 交底
- [x] 摸清 cross-track-safety-v1 与审核前端产物清单
- [x] 与用户确认三项策略：允许 Playwright/Chromium、允许 vendor wavesurfer、Phase B 只做安全审查
- [x] `cross-track-safety-v1/ERRATA.md` 新增勘误（不改历史 manifest）
- [x] `review-product-v1/schemas/` 两份 schema（review_package + human_decisions）
- [x] `review-product-v1/scripts/`
  - `validate_review_package.py`（含 review_manifest 与 candidate.semantic 哈希重算）
  - `validate_human_decisions.py`（≥13 类拒绝 R01–R13）
  - `build_review_product_package.py`（从 cross-track-safety-v1 派生 P1 包，不改历史）
  - `run_tests.py`（15 项契约测试）
- [x] `review-product-v1/audits/`
  - `wavesurfer.md`（版本、许可证、依赖、遥测、数据流、arm64、峰值内存、Spike 晋级门、降级）
  - `playwright.md`（隔离方案、卸载路径）
  - `deepfilternet.md`（Phase B 前置审计；本轮不安装）
- [x] `审核前端/challenger-review-product-v1/index.html` · 交互原型
  - 全轨同步删除通知 + 双轨上下文 + 词级三色 + in-cut 高亮
  - 键盘 1/2/3/R/T/N/P/Esc；输入框冲突处理
  - reviewer 必填、硬编码值拒绝
  - 边界改动 → 旧试听 / accept / listened 全部失效
  - 新试听 SHA 通过 SubtleCrypto 生成，绑定到决定
  - 导出 human_decisions.json + review_session_metrics.json
  - localStorage key 含 review_manifest_sha256（防旧 package 泄漏）
  - wavesurfer 未 vendor 时自动回退原生 audio
- [x] `审核前端/challenger-review-product-v1/spike/wavesurfer.html`（3 候选 spike 页面）
- [x] `review-product-v1/e2e/`
  - `playwright_review_e2e.spec.mjs` · 8 条真实浏览器路径
  - `playwright.config.mjs` · 隔离 launch options
  - `package.json` · 锁定 Playwright 版本，本地独立 install
- [x] `review-product-v1/exact_commands.sh` · 10 步一键复现（含 git 快照、基线 SHA 对比、契约测试、包构建、服务器、E2E、run_manifest）
- [x] `denoise-ab-v1/README.md`（Phase B 门禁骨架）

### 未在本会话执行（等 shell 上线）

- [ ] `git status --short` + baseline commit
- [ ] 12/12 baseline SHA 一致性验证（脚本已写，未运行）
- [ ] cross-track-safety-v1 12/12 fixture 复现
- [ ] 11 SAFE + 14 BLOCKED 数值复现
- [ ] 15 项 P1 契约测试运行
- [ ] wavesurfer.js vendor 下载 + SHA + 遥测扫描
- [ ] 审核包构建（build_review_product_package.py）
- [ ] http.server @ 8767
- [ ] Playwright chromium install + 8 条 E2E
- [ ] run_manifest 与 checkpoint commit

---

## 二、写入的实际文件位置

```
稳定生产/challengers/cross-track-safety-v1/ERRATA.md                （新增，历史不改）
稳定生产/challengers/review-product-v1/
├── README.md
├── PROGRESS.md
├── exact_commands.sh
├── audits/
│   ├── wavesurfer.md
│   ├── playwright.md
│   └── deepfilternet.md
├── schemas/
│   ├── review_package.schema.json
│   └── human_decisions.schema.json
├── scripts/
│   ├── build_review_product_package.py
│   ├── validate_review_package.py
│   ├── validate_human_decisions.py
│   └── run_tests.py
├── e2e/
│   ├── playwright_review_e2e.spec.mjs
│   ├── playwright.config.mjs
│   └── package.json
稳定生产/challengers/denoise-ab-v1/
└── README.md
审核前端/challenger-review-product-v1/
├── index.html
└── spike/
    └── wavesurfer.html
```

**未创建**（等 shell 上线后由 `exact_commands.sh` 生成）：

```
main/runs/EP03-review-product-v1/
├── review_package/
│   ├── review_package.json
│   └── previews/*.mp3
├── run_manifest.json
├── browser_e2e_report.json
└── ...
```

---

## 三、Champion / P0 / cross-track-safety-v1 是否保持不变

**是**。本会话唯一涉及的历史目录是 `cross-track-safety-v1/`，且只在其中**新增** ERRATA.md，不修改任何既有文件；所有 12 项 baseline SHA 应保持不变（脚本会验证）。

未触碰：
- Champion：`稳定生产/scripts/`、`稳定生产/rules/`、`审核前端/index.html`、`审核前端/candidates.json`、`端到端学习剪辑/代码/render_approved_edl.py`
- P0：`稳定生产/challengers/asr-speaker-v1/`、`main/runs/EP03-asr-speaker-v1/`、`benchmark/EP03-ASR-mini-gold-v1/`
- 原始 WAV、Mentor 成果、授权音乐
- cross-track-safety-v1 已哈希运行产物

---

## 四、失败、绕过与降级

- **主要限制**：本会话 `mcp__workspace__bash` 持续返回 `Workspace still starting`；无法执行任何 shell 命令。
- **对策**：全部改为**静态资产 + 一键执行脚本**；由 `exact_commands.sh` 在 shell 上线的下一会话（或用户手动）复现。
- **未绕过**：没有伪造 SHA、没有把"完成"文字直接写入报告；所有涉及执行的字段（wavesurfer SHA、chromium version、E2E 报告、run_manifest）都是"待执行时回填"或由脚本运行时生成。
- **降级**：wavesurfer 未 vendor 时前端自动回退原生 `<audio>`；node/npx 缺失时 E2E 记 `STATICALLY_VERIFIED_ONLY`；DeepFilterNet 本轮不启动。

---

## 五、下一步（明确一个动作）

**在 shell 可用的会话中执行**：

```bash
bash 稳定生产/challengers/review-product-v1/exact_commands.sh
```

该脚本会：
- 用 `git commit` 打两次快照（基线 + 结果）——用户要求的"大改前 git 快照"
- 校验 baseline SHA 12/12 与 cross-track-safety-v1 计数（11 SAFE + 14 BLOCKED）
- 跑 15 项 P1 契约测试
- vendor wavesurfer 并做遥测扫描
- 构建 P1 审核包 + 静态校验
- 起 :8767 服务器 + Playwright E2E（8 条路径）
- 生成 run_manifest.json 与 browser_e2e_report.json

**只有在该脚本全部通过后**，才能把 PROGRESS 里的 [x/未执行] 全部改为 `EXECUTED_AND_VERIFIED`，并进入"真人完成 11 项精审并计时"。
