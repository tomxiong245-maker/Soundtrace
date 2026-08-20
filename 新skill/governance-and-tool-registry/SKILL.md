---
name: governance-and-tool-registry
description: 新能力接入门 + tool 注册表登记 + 每工具运行审计 + 装了包必用扫描。任何新 tool / 新脚本 / 新依赖 / 新集成能力要进主线时激活；覆盖"新加 tool 是否登记 tools.json""是否声明 runtime_dependencies""是否有 audits/<name>.md""是否 OWNER_ATTESTED_INTEGRATE 而非 human_accept""新 .py 是否走 tool_lookup.script_for 而非硬拼路径"这五道门。命中关键词：接入门 / tool 登记 / runtime_dependencies / audits / installed-tools-first。
status: active
owner: champion
entry_tool: validate_integration_governance
related_tools: [validate_integration_governance, mfa_align_and_extract_boundaries, spacy_semantic_transcript, generate_ab_clip_learning_driven]
preconditions:
  - 未来 run 尚未产生 reviewer 草稿或正式决定（沿用现 integration-governance preconditions）
  - main/knowledge/integration_governance/owner_attested_mainline.v1.json 存在且 schema_version == "integration-governance-v1"
  - main/tools/tools.json 存在且 schema_version == 1
  - 新接入的 tool 已在 main/tools/tools.json 的 tools[] 数组中登记 name + (script 或 full_path)
postconditions:
  - run 冻结 integration_governance.json（拷贝自 main/knowledge/integration_governance/owner_attested_mainline.v1.json，含 source_sha256 / frozen_sha256 / owner_attested_count / independent_verification_required=true / semantic_edit_gate_unchanged=true）
  - 新接入 tool 在 main/tools/tools.json 里带 runtime_dependencies (string[]) 与 audit_reference (string) 两字段
  - main/tools/audits/<name>-<版本>.md 已存在，段落遵循 ffmpeg-homebrew-9.0.1.md 的四段结构（固定信息 / 本项目使用范围 / 已知限制 / 状态引语）
  - 组件仅进入未来主线 run（OWNER_ATTESTED_INTEGRATE + independent_verification=pending），不生成 human_accept / EDL / 发布授权
covers_decision_points: [new-capability-mainline-adoption, new-tool-registry-entry, runtime-dependency-declaration, per-tool-runtime-audit, installed-tools-first-scan, hardcoded-script-path-rejection]
covers_claude_md_rules: [§6.6, §11, §15, §18]
pre_flight_check: scripts/preflight/check_governance-and-tool-registry.py

## 1. 定位

新能力（新 tool / 新依赖包 / 新 challenger / 新 adapter）进入主线之前必须过的门。只管"接入"这一层：登记 tools.json、声明 runtime_dependencies、放一份 audits/<name>.md、走 OWNER_ATTESTED_INTEGRATE 而不是 human_accept、新 .py 只用 tool_lookup.script_for。**不**管这个能力交付出来的语义对不对（那是 review / champion / mentor 的门）。

原 integration-governance skill 的"两道门"叙述保留在正文，覆盖范围收窄到"接入门 + 注册表"这一半；语义交付门本 skill 只做守望，不做判定。

## 2. 何时激活

trigger（任一命中即激活）：
- 用户或 orchestrator 提到"加一个 tool / 挂一个 challenger / 装了一个包 / 新写了一个脚本 / 集成一个能力 / 上线一个 adapter"
- 新 .py 提交时 grep 到 `subprocess.run(` 或字符串硬拼脚本路径（不是 `script_for(...)`）
- main/tools/tools.json 的 tools[] 数组新增了一条 entry
- main/knowledge/integration_governance/owner_attested_mainline.v1.json 的 mainline[] 数组新增了一条 capability
- 描述里出现"独立学习线 / 混音能力 / 渲染能力 / 上游音频能力 / 编排能力"这五个 kind 之一
- 用户说"这个包装了但没用 / installed-tools-first / 别自由发挥"

上游 postcondition：当前 run 已经绑定 run_identity.json 且未落 reviewer draft / human decision（沿用现 integration-governance 的边界）。若已进入语义决定阶段，本 skill 只能标 REOPENED_ON_ISSUE，不能新增 capability。

## 3. 读什么

- `/Users/renting/Desktop/minglue/剪辑项目/main/knowledge/integration_governance/owner_attested_mainline.v1.json`
  - 顶层：`schema_version` / `registry_id` / `updated_at` / `purpose` / `policy` / `mainline` / `mainline_exclusions` / `current_run_protection` / `owner_attestation`
  - `policy` 子字段：`component_adoption_gate` / `semantic_edit_gate` / `verification_order` / `evidence_labels` (string[]) / `prohibited_conflations` (string[])
  - `mainline[]` 每条：`capability_id` (`^[a-z0-9][a-z0-9_\-]+$`) / `kind` / `status` / `mainline_scope` / `source` / `safety` / `reopen_trigger`
  - `owner_attestation` 子字段：`authority` / `recorded_by` / `recorded_at` / `independent_verification`
- `/Users/renting/Desktop/minglue/剪辑项目/main/tools/tools.json`
  - 顶层：`schema_version` (int, 1) / `description` / `scripts_root_base` / `scripts_root` / `runtime_dependencies` (顶层数组，当前仅 1 条 ffmpeg) / `tools` (array)
  - 顶层 `runtime_dependencies[]` 每条：`name / role / path / version / sha256 / paired_ffprobe_path / paired_ffprobe_sha256 / audit / data_flow`
  - `tools[]` 单条字段全集：`name` / `description` / `params` (string[]) / `script` (相对 scripts_root) 或 `full_path` (相对 project_root) / `reads_only` (bool) / `runtime_dependencies` (string[]，可选) / `audit_reference` (string，可选)
- `/Users/renting/Desktop/minglue/剪辑项目/main/tools/audits/ffmpeg-homebrew-9.0.1.md`
  - 四段结构：状态引语 → ## 固定信息（绝对路径 / 配套工具路径 / 安装方式 / 版本 / 许可证 / 上游 URL / bottle SHA-256 / 本机可执行 SHA-256 / 配套工具 SHA-256） → ## 本项目使用范围 → ## 已知限制
- `/Users/renting/Desktop/minglue/剪辑项目/main/tools/tool_lookup.py`
  - API：`all_tools()` / `tool(name)` / `script_for(name)` / `tool_name_for_script(relpath)` / `verify_manifest()` / `clear_cache()`
  - `PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent`；`TOOLS_JSON = PROJECT_ROOT / "main/tools/tools.json"`；manifest 走 `@lru_cache(maxsize=1)`
  - 标准调用范式：`subprocess.run([sys.executable, str(script_for("label_learning_driver")), ...])`
- `/Users/renting/Desktop/minglue/剪辑项目/main/orchestrator/integration_governance.py`
  - `SCHEMA_VERSION = "integration-governance-v1"`
  - `ALLOWED_STATUS` 六值同 `policy.evidence_labels`
  - 校验规则：`status == "OWNER_ATTESTED_INTEGRATE"` 且 `independent_verification == "PASS"` → 报错 `owner attestation cannot claim independent PASS`
  - `freeze_registry()` 拒绝覆盖已存在目标文件；返回 `source_sha256 / frozen_sha256 / mainline_capabilities / owner_attested_count / independent_verification_required=True / semantic_edit_gate_unchanged=True`
  - `mainline_capabilities()` 只挑 status ∈ {OWNER_ATTESTED_INTEGRATE, EVIDENCE_VERIFIED_INTEGRATE, INTEGRATED_PENDING_REAL_RUN}
- `/Users/renting/Desktop/minglue/剪辑项目/统筹全局/功能说明/F07-统筹Agent与Tool注册表.md`
  - 新能力上线契约（原文 line 18）："新加一个 tool（如 automix / speaker_diarize）不需要改主流程——写一个 adapter contract JSON + tool 脚本，注册进 registry.json 即可。"
  - AdapterBase 四段能力：`validate_inputs / dry_run_plan / invoke / verify_outputs` + `Provenance` + `wraps_script` SHA drift + writes-policy 门禁
- `/Users/renting/Desktop/minglue/剪辑项目/CLAUDE.md` §11 / §15 / §6.6 / §18

## 4. 写什么

- 冻结 run 内 integration_governance.json —— 调 `main/orchestrator/integration_governance.py::freeze_registry()`，产物字段：`source_sha256 / frozen_sha256 / mainline_capabilities / owner_attested_count / independent_verification_required=true / semantic_edit_gate_unchanged=true`；目标为 run_root 下 `run_identity/integration_governance.json`（拒绝覆盖）
- 更新 `/Users/renting/Desktop/minglue/剪辑项目/main/knowledge/integration_governance/owner_attested_mainline.v1.json`
  - 新增 `mainline[]` 一条：`capability_id` / `kind` / `status="OWNER_ATTESTED_INTEGRATE"` / `mainline_scope` / `source` / `safety` / `reopen_trigger`
  - 若组件被暂停，只能改 `status` 到 `DEFERRED` 或 `REOPENED_ON_ISSUE` —— 绝不删除条目（flow_boundary.md 明文）
  - 同步刷新 `updated_at` 与 `registry_id`
- 更新 `/Users/renting/Desktop/minglue/剪辑项目/main/tools/tools.json`
  - 新增 `tools[]` 一条：`name` + (`script` 或 `full_path`) + `description` + `params` + `reads_only` + **必填** `runtime_dependencies` (string[]) + **必填** `audit_reference` (指向 `main/tools/audits/<name>-<版本>.md`)
- 新建 `/Users/renting/Desktop/minglue/剪辑项目/main/tools/audits/<name>-<版本>.md`
  - 四段结构与 `ffmpeg-homebrew-9.0.1.md` 一致：标题 + 状态引语 → ## 固定信息（绝对路径 / 配套工具路径 / 安装方式 / 版本 / 许可证 / 上游主页 URL / 分发 SHA-256 / 本机 SHA-256 / 配套工具 SHA-256）→ ## 本项目使用范围（输入/输出类型 · Python 显式调用 · 落 run 日志 · 不上传 · 不覆盖原始 WAV / 历史 run / Champion / Mentor）→ ## 已知限制（升级需重录版本 SHA · 许可证发布层审查 · 本轮仅验证可执行性 · 最终发布规格待 Mentor 与整片听审）
- 新 .py 里禁止写 `PROJECT_ROOT / "xxx/yyy.py"` 字符串常量，只允许写 `script_for("<tool_name>")`；若不在 tools.json 就先补登记，再回来写代码

## 5. 覆盖 tool

- `validate_integration_governance`（`main/orchestrator/integration_governance.py`）—— **本 skill 的 entry_tool**。校验 `owner_attested_mainline.v1.json` 的 schema_version / ALLOWED_STATUS / OWNER_ATTESTED_INTEGRATE 与 independent_verification=PASS 的互斥；执行 `freeze_registry()` 拷 registry 到 run 目录并算双 SHA。
- `mfa_align_and_extract_boundaries`（tools.json line 435）—— **可参考的 runtime_dependencies 声明范例**：`["miniforge3 (conda arm64)", "conda-forge::montreal-forced-aligner>=3.4", "pip: spacy-pkuseg + dragonmapper + hanziconv", "mfa model download acoustic mandarin_mfa", "mfa model download dictionary mandarin_china_mfa"]`。新 tool 声明依赖时抄这种粒度（conda channel + pip + 模型下载命令都写出来），而不是只写包名。
- `spacy_semantic_transcript`（tools.json line 557）—— **可参考的最简 runtime_dependencies**：`["conda-forge spacy>=3.6", "python -m spacy download zh_core_web_sm"]`。用于说明"模型下载步骤也算依赖，必须落进 string[]"。
- `generate_ab_clip_learning_driven`（tools.json line 580）—— **可参考的音频依赖粒度**：`["conda pydub audioop-lts", "conda librosa 1.0.0"]`。用于说明"librosa 版本必须钉死"。

related_tools 严格限制在 tools.json 里实际存在的 name。Python 侧的 `tool_lookup.script_for` / `verify_manifest` / `freeze_registry` / `mainline_capabilities` 是函数不是 tool，只出现在正文说明，不进 frontmatter。

## 6. 硬化 CLAUDE.md

- **§6.6 新工具审计门** —— 拦"tools.json 里 `tools[]` 新增一条但没写 `runtime_dependencies` 字段 / 没写 `audit_reference` 字段 / audits/ 目录下找不到对应 .md"这三种情况；任一命中 fail closed，不允许该 tool 进入任何 run。
- **§11 禁自由发挥** —— 拦"新 .py 里出现 `subprocess.run([..., "path/to/script.py", ...])` 或 `PROJECT_ROOT / "..."` 字符串常量而不走 `script_for("<name>")`"；同时拦"引用了 tools.json 里不存在的 name"（`tool()` 会 raise `ToolLookupError` 附已知列表，pre-flight 复用它）。
- **§15 装了的包必用** —— 扫 conda list / pip list 与源码 import 的交集与差集：装了没 import 的 warn（installed-but-unused），import 了但没登记 tools.json 顶层或 tool 级 `runtime_dependencies` 的 fail。
- **§18 verify.sh 第 18 层** —— CLAUDE.md 承诺的 installed vs used 扫描器；本 skill 的 pre-flight 是它落地前的过渡实现。

## 7. pre_flight_check

`scripts/preflight/check_governance-and-tool-registry.py` 至少跑下面这些命令，任何一条非 0 退出即 fail closed：

```bash
# 门 1：registry schema 与 evidence label 合法性
python -c "from main.orchestrator.integration_governance import load_registry, ALLOWED_STATUS, SCHEMA_VERSION; r=load_registry(); assert r['schema_version']==SCHEMA_VERSION; assert all(c['status'] in ALLOWED_STATUS for c in r['mainline'])"

# 门 2：OWNER_ATTESTED_INTEGRATE 与 independent_verification=PASS 互斥
python -c "from main.orchestrator.integration_governance import validate_registry; validate_registry()"

# 门 3：tools.json manifest 静态健康（重复 name / 重复 script path / 文件缺失）
python -c "from main.tools.tool_lookup import verify_manifest; errs=verify_manifest(); assert not errs, errs"

# 门 4：每个 tool 必须声明 runtime_dependencies（当前 3/48，缺口清单要打印出来）
python -c "
import json,pathlib
p=pathlib.Path('main/tools/tools.json')
d=json.loads(p.read_text())
missing=[t['name'] for t in d['tools'] if 'runtime_dependencies' not in t]
print('MISSING_RUNTIME_DEPS:',missing);assert not missing, missing"

# 门 5：每个 tool 必须有 audit_reference 且文件真实存在（当前 1/48）
python -c "
import json,pathlib
d=json.loads(pathlib.Path('main/tools/tools.json').read_text())
bad=[]
for t in d['tools']:
    ref=t.get('audit_reference')
    if not ref or not pathlib.Path(ref).exists(): bad.append((t['name'],ref))
print('MISSING_AUDIT:',bad);assert not bad, bad"

# 门 6：新 .py 禁止硬拼脚本路径（installed-tools-first / §11 禁自由发挥）
! grep -rE 'subprocess\.run\(\s*\[[^]]*"[^"]+\.py"' main/ 稳定生产/challengers/
! grep -rnE 'PROJECT_ROOT\s*/\s*"[^"]+\.py"' main/orchestrator/

# 门 7：CLAUDE.md §15 installed-but-unused（过渡实现，等 verify.sh 第 18 层落地）
python scripts/preflight/scan_installed_vs_used.py  # 待建；未建时本条 warn 不 fail
```

## 8. 反馈证据

（口径：本节仅登记"触发本 skill 制定的历史事件"；session_feedback jsonl 里的 kind / verdict 枚举本项目未在事实清单中给出，故此处不臆造字段名，只引用可查的文件与 run 目录事件。）

- **20-pack adapter 事件**：F07 line 5-18 记录 2026-08-17 用户点名 L2-8 后新增 tool-orchestrator-v2 Challenger，一次性把 18 项 Champion tool + 2 项新 Challenger（automix-2track-v1 / speaker-diarize-v1）打包成 20 项 adapter；反推出"新加 tool 不改主流程，只登记 registry.json"这条 F07 line 18 契约 —— 也就是本 skill 的直接输入。
- **audits 目录基线**：`main/tools/audits/` 目前只有 `ffmpeg-homebrew-9.0.1.md` 一份；tools.json 的 `tools[]` 共 ~48 条，即当前 audit 覆盖率 1/48（本 skill 的存在理由之一）。
- **runtime_dependencies 覆盖率基线**：tools.json 里显式声明 tool 级 `runtime_dependencies` 的仅 3 条（`mfa_align_and_extract_boundaries` line 435 / `spacy_semantic_transcript` line 557 / `generate_ab_clip_learning_driven` line 580），即 3/48。
- **CLAUDE.md 承诺未落地**：§15 描述的 verify.sh 第 18 层"installed vs used" 扫描器在项目内 `find … -name verify.sh` 无结果；本 skill pre-flight 第 7 门是过渡实现。
- **2026-08-18 新增待登记 tool · `extract_gold_cut_features.py`**：位置 `main/orchestrator/extract_gold_cut_features.py`；用途"从人工 gold EDL 反向提取 PARAMETER + PREFERENCE 特征"（CLAUDE.md §21 分家的支撑工具）；由 s5 learning-and-experience 案例蒸馏段消费。本 skill 出口检查该 tool 是否已在 tools.json 登记 + 是否有 `main/tools/audits/extract_gold_cut_features-<版本>.md`；**当前均未登记**，是本 skill 首要 backlog 项。

## 9. 三档诚实标注

**已验证事实（可复读复算）：**
- `main/knowledge/integration_governance/owner_attested_mainline.v1.json` 的顶层与 mainline[] 字段清单、`policy.evidence_labels` 六值、`ALLOWED_STATUS` 六值与之一致、OWNER_ATTESTED_INTEGRATE 与 independent_verification=PASS 互斥、`freeze_registry()` 拒绝覆盖并返回 6 项元数据 —— 全部来自事实清单原文（`main/orchestrator/integration_governance.py` + registry JSON）。
- `main/tools/tools.json` 顶层字段与单条 tool 字段全集、当前声明 `runtime_dependencies` 的三条 tool 的名字与行号、`tools[]` 数组末尾为 `feedback_engine`（line 611）—— 事实清单原文。
- `main/tools/tool_lookup.py` 的 6 项 API、`PROJECT_ROOT` 计算式、`TOOLS_JSON` 常量、`@lru_cache(maxsize=1)`、标准调用范式 —— 事实清单原文。
- `main/tools/audits/ffmpeg-homebrew-9.0.1.md` 四段结构 —— 事实清单原文。
- F07 line 18 新能力上线契约原文 —— 事实清单原文。
- `verify.sh` 在项目内不存在 —— 事实清单原文（`find … -name verify.sh` 无结果）。

**已决定的方向（用户上一轮敲定，但未跑通端到端）：**
- 本 skill 由 integration-governance（收窄）+ installed-tools-first（新）合并而成；entry_tool = `validate_integration_governance`；`automix_render_speech` 移出到 s3；`build_case_memory` 移出到 s5。
- 新 .py 提交时"grep subprocess.run / 硬拼路径 → 未先查 tools.json → fail closed"作为 pre-flight 门 6。
- tool 级 `runtime_dependencies` 与 `audit_reference` 从"可选"升级为"必填"（当前 tools.json schema 未强制，pre-flight 门 4 / 门 5 是先行拦截）。
- 每个 tool 必须有 `main/tools/audits/<name>.md`，四段结构照抄 ffmpeg 模板。
- OWNER_ATTESTED_INTEGRATE 严格 ≠ human_accept；semantic_edit_gate 不由本 skill 判定。

**待验证假设（本 skill 上线前必须回答）：**
- `session_feedback` jsonl 的 `kind` / `verdict` 枚举值本项目未在事实清单中给出 —— "反馈证据"章节因此没有落 jsonl 行号引用，等下一轮补录。
- CLAUDE.md 的实际章节号 §6.6 / §11 / §15 / §18 是本轮设计说明给的期望编号，未在事实清单里逐字核对（只核到 §11 / §15 / §18 被提及）；上线前需 grep CLAUDE.md 实际章节号并对齐。
- tools.json 实际 tool 条数：CLAUDE.md §11 说"41 项"、§18 说"49 项"、事实清单说"以文件为准"—— 本 skill 正文按"~48"叙述，pre-flight 门 4 / 门 5 用 `len(d['tools'])` 动态计算不写死。
- `scripts/preflight/check_governance-and-tool-registry.py` 与 `scripts/preflight/scan_installed_vs_used.py` 本身尚未建；本文件里的 shell 是"合同"，落地由后续 commit 完成。
- `verify.sh` 第 18 层的最终形态（shell 还是 Python，接入 pre-commit 还是 orchestrator preflight）—— CLAUDE.md 承诺、未定实现，本 skill 只做过渡拦截。
- run 内 integration_governance.json 的落地路径本 skill 暂定 `run_identity/integration_governance.json`，事实清单未给权威路径，需 F07 或 run_identity 规范确认后再固化。

### 开放 backlog（继承自 Plan 防丢失审计 · 合并时不能丢）

- **B-§4 §5 半落地（无系统级 tool 拦截）** —— §4"公司音频/转写/候选不得上传"与 §5"不 curl|sh / 不透明 inference.sh / 不覆盖系统 Python / 不改全局 Skill"当前只有 CLAUDE.md 声明 + 各 flow_boundary "绝不联网"语句，**无自动扫描**。Preflight §1-§5（venv 隔离 + 版本检查）是人肉版。本 skill 应补一个"外发流量/危险命令 grep"扫描（可以并到 scan_installed_vs_used.py 一起）。
- **D-OPT-008 tool 注册表契约、调用器、重试、错误分级** —— partial。tool-orchestrator-v1（`F-24`）26/26 测试通过但未晋升；tool-orchestrator-v2（`F-25`）SKELETON_CREATED + 22 项契约测试全过但未晋升，主流程 `delivery_orchestrator.py`（4640 行）仍走 v1 硬编码路径。**未闭环**——是本 skill 未来的主要跃迁目标。
- **D-OPT-011 Loop（issue / 人工闸门 / artifact / 晋升回滚）** —— open · low priority。F09 Loop 控制平面 SKELETON，等 tool-orchestrator-v2 晋升后再考虑。
- **D-OPT-026 每次改动必须同步四处文档（sync-check FAIL 不阻断 orchestrator）** —— `check_current_delivery_sync.py` 存在，未挂 preflight；`统筹全局/当前项目进度.md` CURRENT_DELIVERY_FACTS marker 与 live run 不一致时目前只 warn 不 fail。本 skill 应把这个门升级为 fail closed。
- **D-gap-2 policy_promotion 4 项 blocker** —— `autocut_policy = NOT_APPROVED`；POLICY-PROMOTION-v1-20260816 剩下 4 项 blocker 未过：独立 benchmark / 独立复核 / 回滚演练 / 明确签署低风险删剪范围（其中"3 期/2 审核人"两条 2026-08-17 已撤除）。本 skill 只是拦"未过 blocker 时禁止改 policy"，不承担闭环。
- **D-gap-6 / D-gap-7 已撤除的 blocker** —— closed。净节省时间从 benchmark 撤除；3 期/2 审核人从 driver + policy_promotion + training_readiness 撤除。作为历史记录，本 skill 只登记不再作为门。
- **D-gap-8 tool-orchestrator-v2** —— SKELETON_CREATED · 22 项契约测试全过 · 未晋升。本 skill 的 §7 门 3 `verify_manifest()` 是 v1 路径；未来 v2 晋升后需切换到 AdapterBase 契约。
- **F-24 tool-orchestrator-v1** —— STATIC_TESTS_PASS + SYNTHETIC_AUDIO_SUBPROCESS_RUN_PASS + NOT_PROMOTED · 26/26 测试。仍在 Challenger 隔离区。
- **F-9 e2e-auto-runner-v1 / F-11 filler-v1 superseded / F-13 intro-outro-music-v1 / F-14 long-pause-v2 / F-17 orchestrator-e2e-v1 无 README** —— 5 个 challenger 状态不明；本 skill 应在下一版把它们全部登记进 owner_attested_mainline.v1.json 的 `mainline_exclusions` 或明标 status。
- **verify.sh 本身在项目中不存在** —— CLAUDE.md §15 承诺的第 18 层 "installed vs used" 扫描器 shell 文件 `find … -name verify.sh` 无结果。本 skill 的 §7 门 7 (`scan_installed_vs_used.py`) 是过渡实现，最终落地形态需另定。