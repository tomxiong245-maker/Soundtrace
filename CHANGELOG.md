# CHANGELOG

## 2026-08-21 · 开源发布准备

**GitHub**: https://github.com/tomxiong245-maker/Soundtrace (private → 待转 public)
**最新 commit**: `716a263`

### 新增
- `项目主文档/系统架构白皮书-2026-08-21.md` · **顶级技术文档** · 8 节 500+ 行
  - 六层 Skill 每层的代码路径 + 函数签名 + 常量值
  - 端到端 Pipeline · Stage 1 → 7.2 完整清单
  - 12 个外部工具的版本 / URL / 许可证 / 权重 SHA / CLI 参数
  - 9 份关键 JSON schema
  - 关键参数与默认值（Optuna 5 维 / loss 3 项 / Gate 5 门 / LLM 参数）
  - `tools.json` 58 项现状 · `verify.sh` 21 层落地情况
  - 19 条已知偏差（M6 三档诚实标注）
- `与AI的上下文/2026-08-21-2000-pre-讲稿-演讲版.md` · 演讲版归档
- `新产出/EP05-PRE-HTML-2026-8-21/` · EP05 pre 讲稿 HTML 版 + build_pptx.py 生成器（相对路径 · clone 后可跑）
- `CHANGELOG.md` · 本文件

### 修改
- `README.md` · 重写 · 顶部指向系统架构白皮书 · 反映 2026-08-21 收敛状态
- `项目主文档/CLAUDE.md` · `tools.json` 数字更新为 58（原 56 过时）
- `.gitignore` · 扩规则挡音频 / pptx / 内部日志 / 本地 IDE 配置

### 删除 · M2 硬红线（含 verbatim 转写 · 从 git 历史彻底清除）
- `knowledge/learned_examples_EP03_MENTOR.md` (4 MB · 336 行 · 含主持人真名 + 客户业务对话)
- `knowledge/learned_examples_EP03.md` (336 行 verbatim 转写)
- 25 个 EP03/EP04 mp3 音频文件（成品 + audit_clips + AB 对比 + gold 片段）

### 删除 · 弱敏感 / stale（untrack + gitignore · 历史保留但当前不 tracked）
- `deprecated备份/` 30 文件（老 skill 集合：editing-experience-distiller · integration-governance · label-learning-driver · podcast-editing-orchestrator · candidate-family-integration）
- `交付前SHA基线-2026-08-19/` 3 文件（老里程碑）
- `knowledge/cut_parameters.json.v1-with-numbers-superseded`
- `knowledge/experience.md.v1-with-numbers-superseded`
- `verify/sync_delivery.sh.bak`
- `统筹全局/mentor-briefing-2026-08-17.md`（含外部 audio-clips repo 引用 · 该 repo 已 404）
- `统筹全局/Preflight-checklist-与今日踩坑清单.md`（全本机 shell 命令）
- `统筹全局/沙盒迁移-20260814.md`（迁移日志）

### 删除 · 老 run 产物（open source 不需要）
- `新产出/EP03-EXPERIENCE-DELIVERY-20260818-1740/`
- `新产出/EP04-C007-AB-COMPARE-2026-8-19/`
- `新产出/EP04-CUT-VERIFY-2026-8-19/`
- `新产出/EP04-DELIVERY-2026-8-19-GOLD/`
- `新产出/EP04-DELIVERY-V04-STRATEGY-2026-08-19-0030/`
- `参考成品/EP04-DELIVERY-20260817-1427/`
- `参考成品/EP04-GOLD-EDL-20260818-1548/`
- `benchmark/` 全目录

### 脱敏
- 35 个 tracked 文件里的 `/Users/renting/Desktop/minglue/剪辑项目/` → `<PROJECT_ROOT>/`
- 剩余 `/Users/renting/` → `<HOME>/`
- 验证：`grep -rl '/Users/renting'` = 0
- `build_pptx.py` 6 处 hardcode 路径改用 `Path(__file__).parent` · clone 后可直接跑

### 数字对比
| 项 | 清理前 | 清理后 |
|---|---:|---:|
| tracked 文件数 | 776 | 569 |
| 本地 `.git` 大小 | 274 MB | 2.3 MB |
| LFS objects | 24 | 0 |
| verbatim 转写文件 | 2 | 0 |
| 硬编码本机路径 | 100+ | 0 |
| API key 硬编码 | 0 | 0 |
| OS junk (.DS_Store etc.) | 0 | 0 |

### 保留结构
```
项目主文档/    · 10 · CLAUDE.md + 系统架构白皮书 + domain-rules
代码/          · 436 · orchestrator + skills + challengers
统筹全局/      · 44 · 状态摘要 + manifest
新skill/       · 31 · 6 层 skill 定义
knowledge/     · 17 · schema + cut_parameters + tools.json
新落地/        · 6  · verify layer 18/21
verify/        · 5  · verify.sh
审计报告/      · 5
与AI的上下文/  · 4 · 讲稿归档
docs/          · 3
+ README.md / LICENSE / CHANGELOG.md / .gitignore / .gitattributes
```

### 已 force push
- 全 6 commit 用 `git filter-repo` 重写 · SHA 全变
- GitHub 端旧 unreachable objects 会在 GitHub 自动 GC 时清除（几周内）
- 转 public 前建议触发一次 GitHub Support 手动 GC · 更彻底

---

## 2026-08-19 · GOLDEN PATH 冻结
- LLM-first 架构冻结 · 4 Challenger 晋升 Champion pipeline
- 语义规则让位 · 8 门 gate → 5 门
- 详见 `统筹全局/GOLDEN_PATH_FROZEN_2026-08-19.md`

## 2026-08-19 · Round 1 归档
- deprecated 老 skill 归档
- learning-pattern-from-case-v1 challenger skeleton 建

## 2026-08-19 · 初始上传
- LLM-first 架构冻结版
- 首次推 GitHub
