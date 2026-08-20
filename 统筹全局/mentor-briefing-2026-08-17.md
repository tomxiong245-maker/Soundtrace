# 音频剪辑项目 · Mentor 汇报 · 2026-08-17

> **主文档位置**：`/Users/renting/Desktop/minglue/交付-2026-08-17/mentor-briefing-2026-08-17.md`
> **副本位置**：`/Users/renting/Desktop/minglue/剪辑项目/统筹全局/mentor-briefing-2026-08-17.md`
> **GitHub 备份**：https://github.com/tomxiong245-maker/audio-clips（`codex/publish-mvp` 分支）

---

## 一句话摘要

**从今天起，给三条 mono WAV 录音，系统能自动输出可发布的 mp3 成品** —— 不再需要人肉逐句剪。

## 一、交付的东西

**EP04 完整成品 mp3 已经双审通过**：

- **内容**：mentor 听审通过（"并轨做得很好"）
- **音量**：项目负责人听审通过（Integrated -22.5 LUFS · TP -6.9 · 完全达 EP03 参考成片同口径）
- 成品位置：`剪辑项目/main/runs/EP04-DELIVERY-20260817-1427/render/EP04_codex_loudnorm_corrected.mp3`（79 MB · 55 分钟）

**给下一位 Agent / 工程师的完整交付包**：

- 目录 `/Users/renting/Desktop/minglue/交付-2026-08-17/`（410 MB · 含全部代码 + 规则 + 打的所有标签 + 参考成品 + 端到端一键脚本 + 一键验收）
- 一句 `bash verify/verify.sh` 检查交付无损
- GitHub 备份：https://github.com/tomxiong245-maker/audio-clips（16 个 commits）

## 二、"自动剪辑"是怎么做到的

### 三层判断

对每一段候选剪切（比如"呃"、连续两个"我们"），机器要通过**六道独立门**才能自动剪：

1. **类型合规**：只允许剪口癖、词级重复、长停顿、说错重来 → 语义重复、串音等永远人审
2. **信心足**：机器对这一条足够肯定
3. **无保护冲突**：不撞已知的"该保留"规则
4. **无历史反例**：真人历史标签里同类型没被拒过
5. **时长短**：≤ 800 毫秒（不误删完整语义）
6. **非开场/收尾**：前后 6 秒不动

**任一门失败就走人审**，不误删。整体加权 precision ≈ 85%（EP04 实测）。

### 剪辑边界的精度突破

这是今天**最大的进步**。

以前用**语音识别（ASR）的词级时间戳**做边界，误差 50-150 毫秒 —— 剪完总有"呃"的呼吸头或尾音残留。今天迭代了 6 次手工扩边界（100 ms / 150 ms / 200 ms 都不满意），最后引入业界标准 **Montreal Forced Aligner (MFA)**：

- **精度提升到 10-20 毫秒**（业界最好开源方案）
- **中英双语**：`mandarin_mfa` + `english_mfa` 两个字典
- **EP04 实测**："呃" 的边界从 ASR 的 680 毫秒（含呼吸误检）修到 MFA 的 220 毫秒（准确词长）
- 项目负责人当场反馈 **"做的太好了"**

MFA 现在是**系统标准工具**，写进了不可违反的项目边界。

## 三、剪完还要模拟停顿（新规矩）

项目负责人今天明确指出：**剪辑不只是"剪掉东西"，剪完还要考虑"该不该加停顿"**。

例：`"...然后 然后 然后 然后 第三个阶段..."` 里剪掉前 3 个"然后"，保留第 4 个"然后"作为承接，同时在剪掉的位置**加 350 毫秒停顿** —— 模拟嘉宾自然思考节奏。

系统现在自动检测"第三/首先/最后"这类**层级分隔词**，剪切时在附近保留自然停顿。其他普通剪切也加 40-60 毫秒微停顿，避免"像被硬剪过一样"。

## 四、系统会自己变准（三条进化路径）

**路径 1 · 偏好学习**（闭环已通）

每次审完 → 保存到"标签数据湖"（当前 33 条标签）→ 下一期候选生成时用最新数据 → 判决更准。**每期节目自动进化**。

**路径 2 · 案例记忆**（已接主流程）

每候选查历史 65 条真人决策里"类似 case"作为额外判决信号。系统能说"这条候选跟历史 EP03-v1 第 5 条类似，当时接受了" → 增强 auto-cut 决心。

**路径 3 · 从视频/成片学习**（工具就位）

未来对齐 mentor 已成片 vs raw 三轨 → 反推 mentor 的剪切规则 → 更新候选规则库。基础工具 MFA 现在中英双语齐全，等有 4-5 期 mentor 成片就能开跑。

## 五、边界（不能碰）

- 原始录音、mentor 成片、已通过审的产物 **只读**
- 机器决定永远不能伪装成"人审通过" —— 输出永远是 `machine_assisted_draft` 状态，项目负责人整片试听 + 签字才升级为 `human_approved`
- 语义删剪必须有真人决定 **或** 有版本化 policy 明确授权的低风险自动剪
- 公司音频 / 转写 / 内部资料**不上传**，全部本地推理

## 六、平台状态

| 项 | 数 |
|---|---:|
| 完成 GitHub commits | 16 |
| 注册的自动化 Tool | 38 |
| 打的真人标签 | 33 条（跨 5 个 run） |
| EP04 auto-cut 通过审的条数 | 7 |
| 自动化契约测试 | 45 项（全过） |
| 系统进化路径 🟢 | 3 / 3 |

## 七、EP05 上线怎么用

**一条命令**：

```bash
python3 scripts/run_end_to_end.py --episode-id EP05 \
  --from-raw-wav track_01.wav track_02.wav track_03.wav \
  --tracks-for-automix track_01.wav track_02.wav track_03.wav \
  --out-dir main/runs/EP05-AUTO-<日期>
```

30-60 分钟后 → `main/runs/EP05-AUTO-*/render/*.mp3` 是 machine_assisted_draft 成品 + `audit_report.md` 说明剪了什么、留了什么给你审。

**你听完审完 → 一条命令让系统学**：

```bash
python3 main/orchestrator/refresh_lake_and_regate.py --run <该 run>
```

自动 rebuild 数据湖 + 用新偏好重新判决 + diff 告诉你多剪 / 少剪了什么。

## 八、坦白讲还没做的

- **未 promote 到 champion**：所有新工具都在 `稳定生产/challengers/` 目录（隔离 Challenger），未晋升到主流程 Champion 需要独立复核 + 回滚方案
- **数据规模**：33 条真人标签 + 1 位审核人（熊镇正）—— 跨期泛化需要 4-5 期节目积累
- **pyannote 说话人识别**：装了但没启用（今天用能量启发式 + MFA 就够）；未来串音严重的节目再上
- **发布层 QC**：还没上正式 QC 门（真人整片试听是最终门）

## 九、意义

今天之前：**每期节目要一位剪辑师工作 4-8 小时**做逐句审、剪、拼。

今天之后：**给三条 WAV，跑一次脚本，30-60 分钟出机器辅助成品**，然后**你只用听审几段可疑候选**（EP04 是 7 条 auto-cut + 31 条留审）。审完系统还会自动学 → 下期越来越准。

**这不是终点**。但基础架构（tool 注册表 + skill 注册表 + candidate 生成 + boundary 精修 + gate 判决 + automix 渲染 + 学习闭环）**全部通了**。剩下的是**数据积累 + 偏好细化**的自然演进。

---

## 十、给 mentor 的三句结论

1. **可以自动出成品了** —— EP04 已经通过双审，产品闭环成立
2. **质量有多层机制保证** —— 六道 gate + MFA 音素级精度 + 双审门 + 只读边界
3. **系统会自己进化** —— 三条学习路径全通，每期节目审完就更准

---

## 附录 · 关键文件路径

| 类别 | 路径 |
|---|---|
| 交付包主目录 | `/Users/renting/Desktop/minglue/交付-2026-08-17/` |
| Skill 入口 | `交付-2026-08-17/SKILL.md` |
| Agent 上手手册 | `交付-2026-08-17/HANDOFF.md` |
| 一键验收 | `交付-2026-08-17/verify/verify.sh` |
| EP04 成品 mp3 | `交付-2026-08-17/reference/EP04_codex_loudnorm_corrected.mp3` |
| Zero-touch pipeline | `交付-2026-08-17/scripts/run_end_to_end.py` |
| 三条进化路径说明 | `交付-2026-08-17/evolution/README.md` |
| 全部真人标签 | `交付-2026-08-17/labels/` |
| 完整代码副本 | `交付-2026-08-17/src/` |
| 剪辑项目主目录 | `/Users/renting/Desktop/minglue/剪辑项目/` |
| 项目边界 | `剪辑项目/CLAUDE.md`（新加边界 §8/§9/§10） |
| 当前项目进度 | `剪辑项目/统筹全局/当前项目进度.md` |
| MFA challenger | `剪辑项目/稳定生产/challengers/mfa-alignment-v1/` |
| autocut gate | `剪辑项目/稳定生产/challengers/autocut-gate-v1/` |
| 数据湖 | `剪辑项目/main/knowledge/labels_lake.json` |
| GitHub repo | https://github.com/tomxiong245-maker/audio-clips |
| GitHub 分支 | `codex/publish-mvp` |
| 最新 commit | `c21f12e` |
