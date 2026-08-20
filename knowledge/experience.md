# 剪辑经验 · 单一文件

**只有这一份是最新经验 SOT** · 路径 `交付/2026-08-18-skill重构/knowledge/experience.md`
主项目同步副本在 `main/knowledge/experience.md`
所有 mentor / 真人反馈 / gold cut 学到的东西汇集在这
其他 md/json/jsonl 是来源证据 · 不是给 agent 读的经验

**版本** v2 · 参数与参数背后的思路完全沿用昨天 (EP04-DELIVERY-20260817-1427 · mentor + Sophie 双审通过) · 偏好用今天从 gold + 真人 accept/reject 学到的 · **全文不出现具体数字**(数字属于工具 · 不属于经验)

分两部分:
1. **PARAMETER 参数 · 决定怎么剪** · 沿用昨天成品那一套 · 思路与流程都不动
2. **PREFERENCE 偏好 · 决定剪哪些** · 用今天新学到的判断规则

---

# § 1 · PARAMETER · 参数与背后的思路 · 沿用昨天

## 一 · 核心思路 · 偏好属于人 · 参数属于工具 · Agent 只做搬运和落库

昨天成功的一句话解释 · agent 没有替工具决定任何参数 · 也没有替人决定任何偏好。剪辑相关的所有参数(过渡曲线 · 时长上限 · 开头结尾保护段 · 响度目标 · gate 白黑名单等)全部由已登记工具内部持有;偏好(哪些 kind 允许自动剪 · 哪些永不剪 · 是否采信单条历史证据)由 policy 文件与人审 lake 表达。Agent 的合法动作只有四件 · 调工具 · 传 run 目录 · 写 manifest · 请人批。这条线一旦模糊 · 就会退回到 agent 拿数字凑逻辑 · 也就是施工规则里明令禁止的自由发挥。凡是在提示词里出现具体参数值 · 把工具参数复制到笔记里当"思路" · 或声称"我用了某个时长"的做法 · 都说明参数从工具漏到了 agent 手里 · 是红线。

## 二 · 五步链条概述

一条合法的交付链固定五步。

**第一步 · 候选生成** · 按家族分开的检测器各干各的活 · 填充词走填充词工具 · 即时重复走即时重复工具 · 自我修正走词级跨轨工具 · 不允许一个正则打天下 · 也不允许一个检测器混进不属于自己家族的 kind。

**第二步 · 边界精修** · 走音素级 forced alignment · 直接吃 ASR 词 timestamp 会啃到辅音 · 只有音素对齐才能给出稳定剪口。混合语言词若超出发音词典按人审处理 · 不自动剪。

**第三步 · Gate 判决** · 白名单 · 高置信 · 无 preserve · 历史证据 · 时长 · 开头结尾保护段的级联短路 · 任一闸失败即归人审。

**第四步 · 整片语义并轨与剪辑** · automix 在整片层一次决定这一时刻谁是主 · 然后一次性把 EDL 里全部 cut 从整片拿掉 · 拼口用装好的高级 crossfade 与 room tone splice · A/B 从并轨成品上截屏而不是在切片内部现场并轨。

**第五步 · 响度归一与交付** · 双遍线性放缩 · 内容不动 · 只做整体增益 · 然后写 manifest 请两条审批链各自签字。

**五步的顺序不能倒置** · 尤其是先整片并轨再截 A/B 这一步 · 倒过来 A/B 段就会带上其他嘉宾正在讲的内容 · 反馈全废。

## 三 · 只读边界与内容不变原则

四条只读线必须闭合 · 源 machine_assisted_draft run 不动 · Champion 只读产物不动 · mentor 三轨原始素材不动 · orchestrator 主脚本不动。任何修改先自问要动的路径是不是这四类之一 · 属于就换新 run 目录写。响度修正之所以合法 · 因为它是内容保持的 · 只做整体线性放缩 · 不改波形形状 · 剪辑决策与主麦切换与音乐 timing 全部不变 · mentor 已批的东西继续被信任。若把响度不对当借口引入动态压缩或频响改动 · 就已经跨过内容边界 · 需要重新走内容审。

## 四 · 参数与偏好的责任划分

**参数** 是"怎么剪" · 属于工具版本 · 用工具消费的字段承载 · 改动必须走 Challenger 到 Champion 的晋升 · 在稳定生产的规则目录里冻结基准和回滚方案 · 禁止 agent 在 run 中现场覆盖。

**偏好** 是"剪哪些" · 由 session_feedback 单一 SOT 承载 · 任何决策前先跑 retrieve_before_decision · 把历史反馈注入到候选评估里。

两类知识存储路径必须分开。若参数被写到偏好文件里 · 工具永远读不到;若偏好被烧死到 policy JSON 里 · 会导致同一问题反复问用户。学习顺序也固定 · 先看已有工具能不能解决 · 再看文档层 · 最后才是 session_feedback 补丁 · 顺序倒过来会让 session_feedback 无限膨胀 · 参数责任被稀释成偏好。

## 五 · Gate 是级联多闸门 · 不合并 · 不加权

自动剪的判决是白名单 · 机器高置信 · 无 preserve · 历史证据 · 时长 · 开头结尾保护段的级联 AND · 任一闸失败立刻挂人审 · 报告里保留分证据字段 · 不做单一 score · 不做跨闸补分。白名单外的 kind 一律不自动 · 黑名单 kind 永不放行;单条历史 reject 一票否决 · 即便其他闸都过;历史无负证据但也没有正 signal · 只按安全兜底放行。gate 报告若写"某闸没过但另一闸特别好所以放行" · 就是违反级联 AND 语义 · 直接错。

## 六 · A/B 是从整片截屏 · 不是把剪辑动作重演一遍

A/B clip 的 source 必须指向已交付的最终成品主件 · 原片和剪后片共享同一份响度归一 · 主麦切换 · 音乐 timing · 并轨判定 · 人耳听到的差异只允许是"那处填充词有没有被剪"。变量隔离是 A/B 的第一原则;如果两段的响度 · 混音 · 拼口 · 底噪都不一样 · 反馈信号就同时被这些变量污染 · 拿不到该问题的答案。拼口过渡走已装的高级 crossfade 与 room tone splice · 禁止裸 concat 加静音再加短 fade 的老套硬拼;chain 场景的过渡时长上限由 onset 真辅音起音减安全 margin 约束 · 不是 agent 拍脑袋。单剪工具是研究工具 · 产物是短样本 · 不入交付清单 · 也不承担整片才该做的并轨与响度职责。

## 七 · 双层审批链 · 各签各的域

一次交付需要两条独立签字 · 内容层 mentor 审 EDL · A/B · 并轨判定 · 主麦切换 · 音乐 timing;包装层 project owner 审响度归一 · 采样率 · 容器 · 声道等发布规格。两条链子 scope 互不重叠 · verdict 分别记录 · 不合并成一条整体 OK。scope 若写成"整体"这种非分层措辞 · 或没有引用真人原话 · 或一条 verdict 想覆盖两层 · 都是伪审批。

## 八 · 独立复核 · 输入端说什么不算数

任何"我改了 X 使其合规"的动作之后 · 必须用与修正工具独立的测量脚本在成品上再量一次 · 与目标区间比对写进 verification 段。工具自己 pass1 的测量值只能作参考 · 不能当交付凭证。独立复核缺席 · 就等于让工具自己给自己签字。

## 九 · 一次成功要顺手升级下游

交付写下游影响 · 这次学到的默认参数下沉到哪个已登记工具 · 哪条项目进度指针改指本 run。不写笔记 md · 写进工具默认值加索引指针。这样一次成功从个案变成资产 · 下次 orchestrator 自动命中目标 · 同样的坑不再踩。若交付后没有工具默认值变化也没有指针更新 · 说明这条经验没有被系统吸收 · 只沉淀成了 agent 一次性记忆。

## 十 · 硬约束 · 违反视为破坏契约

- 候选生成 · 边界精修 · gate 判决 · 整片混音 · A/B clip 生成必须使用已登记工具 · 找不到工具时按 tools.json 到 skills/SKILL.md 到 challengers/scripts/ 顺序穷尽后才允许自实现 · 且写完必须回填登记表
- 整片语义并轨只允许由 automix 类工具在整片层完成一次 · A/B clip · 审听样本 · 任何后续片段都从整片并轨成品上截取 · 禁止在切片内部临时叠加三轨或现场调用 amix
- 响度归一化只走内容保持的双遍线性路径 · 波形形状不动 · 只做整体增益放缩 · 禁止在响度修正名义下引入动态压缩 · EQ · limiter 或任何改变剪辑决策的动态处理
- 源 run · Champion 只读产物 · mentor 三轨原始素材 · 主脚本 · 旧 run 的 EDL 一律不动 · 修改一律换新 run 目录写
- 参数属于工具版本 · 须走 Challenger 到 Champion 晋升冻结 · 禁止 agent 在 run 中现场覆盖 · 偏好只从 session_feedback 单一 SOT 进入决策链 · 禁止多副本 · 禁止绕过 retrieve_before_decision
- Autocut gate 是级联多闸门 AND 判定 · 白名单 · 高置信 · 无 preserve · 历史证据 · 时长 · 开头结尾保护逐层短路 · 任一闸失败直接归人审 · 禁止跨闸加权 · 禁止用单一 score 或布尔汇总
- 白名单外的 kind 一律不自动 · 黑名单 kind 永不放行 · agent 不改 kind 分类 · 不为某条候选临时白
- 单一证据不足以自动 · 单条历史 reject 一票否决 · 历史无负证据但也无正 signal 只按安全兜底放行 · gate 报告必须逐条保留分证据字段
- 本轨伪影(咳嗽 · 碰麦)只静音单轨 · 必须由 candidate 侧带 cut_scope 声明并在 automix_adapter 消化 · 禁止升级为全轨 EDL cut
- 拼口过渡使用已装的高级 crossfade 与 room tone splice · 禁止裸 concat 加静音硬拼 · chain 场景的过渡上限由 onset 真辅音起音减安全 margin 约束 · 禁止 agent 拍脑袋定时长
- 边界精修走音素级 forced alignment · 禁止直接吃 ASR 词 timestamp 作剪口 · 混合语言词一律送人审
- 主持人 backchannel 由声明式 speaker_map 结合邻近他轨语音判定过滤 · 禁止改用 diarization 推理 · 缺 map 跳过不失败
- 任何修正动作之后 · 必须用与修正工具独立的测量脚本在最终成品上再量一次 · 与目标区间比对写入独立复核段 · 禁止把工具自测当交付凭证
- 交付前必须写下游影响 · 这次参数下沉到哪个已登记工具的默认值 · 哪条项目进度指针改指本 run · 不写笔记 md
- 审批链严格分层 · mentor 只审内容与并轨判定 · project owner 只审包装层参数上线 · scope 必须写清层次 · 禁止一条 verdict 覆盖两层 · 禁止使用整体 OK 这种非分层措辞
- 决策链严格按 TOOL_APPLY 到 DOC_REFERENCE 到 SESSION_FEEDBACK_PATCH 顺序落地 · 禁止跳层直接往 session_feedback 塞规则来解决本该工具或文档层解决的问题
- A/B clip 的 source 字段必须指向交付主件血缘链上的最终成品 · manifest 缺失来源 · 上下文 · 候选 id 视为无效交付
- 参数类知识落到工具消费的字段供工具直接读取 · 偏好类知识落到 current.session_feedback.jsonl 供 retrieve_before_decision 读取 · 两类存储路径不许互串

---

# § 2 · PREFERENCE · 偏好 · 决定剪哪些 · 用今天新学到的

**这些规则影响的是 EDL 内容(第三步 gate 的 cut 列表)· 不影响剪辑动作本身**。

## 一 · 硬负样本 · 永远不剪

**修辞性强调重复** · 连续两次同一实词表强调(如"特别特别 · 非常非常 · 真的真的 · 很很 · 太太 · 超超")· 这不是说错重来 · 是口语强调 · 剪了会削弱语气。来源 · 熊镇正的 accept/reject 决定 · 独讲情景下同类都被 reject。

**孤立语气词** · 只有一个语气词孤立出现 · 没有邻近重复也不属于说错重来的情境(如"呃 · 嗯 · 啊 · 额 · 那个 · 这个 · 就是")· mentor 从来不孤立砍这类词。来源 · mentor gold cut 里从未出现这一类。

**保留末尾的词** · 若形成一连串重复 · **末尾那一次要保留**(如"然后 · 一些 · 什麼 · 因为 · go"等)· 剪的是前面的部分。mentor 反推里这几个词的最后一次都保留了。

**跨话切除** · 候选覆盖时段里另一个说话人正在讲话 · 整轨切除会把另一个人的话一起切掉。这种候选走单轨 duck 或静音 · 不进整片 EDL。

## 二 · 主战场 · 应该剪

**语义边界** · 一段较长的语义偏离 · 打岔 · 换个说法重来 · 前后在自然停顿点或静音区之间。mentor 剪的绝大多数属于这一类 · 目标是"找边界" · 不是"砍语气词"。

**贴身重复** · 同一个实词紧挨着连续两次以上 · 属于说错重来的现场自我修正。保留末尾一次 · 前面全剪。

**说错重来** · 说话人自己标注了修正标记(如"不是 · 不对 · 应该说 · 或者说 · 我意思是")加上前后语义明显漂移。这种整段拿掉 · 不做局部修补 · 全或无。

**长静默** · 纯粹的思考停顿 · 无语气词 · 无说话内容 · 前后都是清晰语音。压缩掉整段静音 · 剪口贴词尾与次词首。

## 三 · 走人审 · 不进 EDL

**含糊填充** · 非典型的 filler 表达(半个词 · 拖长音 · 不完整字)· 单看时长与位置无法判决 · 必走人审 · 不自动。

**跨轨可疑** · 主说话人正在讲 · 另一轨在做背景应答 · 是否要剪主说话人的填充需要人审判定。

---

# § 3 · 决策链

## 决策前 · 查偏好

给一个候选 · 先按 § 2 判 ·

1. 命中硬负样本 → 从 EDL 里剔除 · 不进入第四步剪辑
2. 命中主战场 → 保留进 EDL
3. 命中走人审 → 标记 human_review · 不进 auto_cut

工具入口 · `feedback_engine.retrieve_before_decision(candidate, decision_type, episode_id, knowledge_category='PREFERENCE')`。

## 决策后 · 新答案怎么进来

新数据到手 · 按数据形状路由到三个学习流之一 ·

| 数据形状 | 学习流 | 落地位置 |
|---|---|---|
| 单条真人 chat 反馈 | **feedback-engine analyze** | 更新本文件 § 2 |
| 一批 accept/reject 决定 | **label-learning-driver** | 更新本文件 § 2 |
| Mentor 剪辑成品 (EDL 或成品 mp3) | **editing-experience-distiller** | 更新本文件 § 2 |

**注意** · 三个流都**只更新 § 2 偏好** · **不动 § 1 参数**。参数改动必须通过工具版本晋升 · 不由 experience 学习流改。

三条流的入口与决策树见 `docs/learning-flow-selector.md`。

---

# § 4 · 事故记录

## 事故一 · agent 自写剪辑工具

**现象** · 出现 `generate_comprehensive_cut.py` 版本 · 试图替代唯一合规入口。
**后果** · 参数每次不同 · 剪辑不干净。
**处置** · rename 为 `.deprecated_v219_violated_11` 留证 · 禁止再造。

## 事故二 · 混淆整片剪与单剪

**现象** · 改了偏好之后用**单剪工具**逐候选生成 A/B · 每个 A/B 都是独立算参数。
**后果** · 没有整片语境 · 剪口每次不一致 · 参数越改越乱 · 听感不干净。
**处置** · 明确 A/B 必须从整片成品截屏 · 单剪工具是研究工具 · 不入交付。改偏好只影响 EDL 内容 · 剪辑动作必须走完整五步链条。

## 事故三 · 具体数字塞进 experience

**现象** · 把 gold cut 反推的分布数字(某某中位数 · 某某目标区间)写进 experience 与参数文件。
**后果** · 样本量太小 · 数字被当参数用会误导 · 也架空了工具的默认参数。
**处置** · 清掉全部数字 · 参数改为文字说明 · 一切参数值只在工具源码里。

## 事故四 · retrieve_before_decision 优先级排序 bug

**现象** · 两次 sort 冲突 · 后一个 timestamp DESC 覆盖了 verdict priority · never_cut 高优规则被后来的 timestamp 挤掉。
**处置** · 合并排序为单次 composite key · 已修。

## 事故五 · is_never_cut 匹配太宽

**现象** · 只按 reason_key 命中 · 修辞规则的 text_pattern 未被解析 · 所有 immediate_repetition 候选都被误标 never_cut。
**处置** · 改严格 · 必须 filler_token 或 text_pattern 具体命中 · 且 context 若指定必须匹配。

## 事故六 · candidate 已自带精确参数被忽略

**现象** · candidate 里 `boundary_lock` `boundary_snap` `post_cut_pause_ms` `artifact_risk` 明写 · agent 却用 ASR word range 覆盖。
**后果** · 参数每次不同 · 违反 boundary_lock 契约。
**处置** · 明确 agent 只透传 candidate 字段给工具 · 不重算。参数就在工具内 · 不需要 agent 再算一遍。

---

# § 5 · 生成本文件的原则

- **不复制工具内的数字** · 数字只在工具源码
- **不写反推的分布数字** · 样本小 · 会误导
- **只写规则性判断** · 剪哪些 · 不剪哪些 · 走什么流程
- **每次学习后重生成** · 追加到本文件 · 不再另起 md
- **交付时同步到 delivery** · 保持单一 SOT

**本文件是唯一 agent 读的经验 SOT · 其他 md/json/jsonl 都是来源证据。**

---

# § 6 · 五步链条具体用哪个工具

**用户 2026-08-18 明确要求 "里面用到的具体的工具也要写进剪辑经验里面 · 我怕后面找不到"**。以下按五步顺序列已登记工具及其登记位置。**参数值不在这里** · 只在工具源码。

## 第一步 · 候选生成

- `main/orchestrator/build_filler_global_pause_review_source.py` (Champion in `稳定生产/challengers/filler-global-pause-v1/scripts/`) · 填充词候选 · 含 sentence_position_gate / boundary_lock / english_fragment_context_guard
- `main/orchestrator/detect_self_correction_wordlevel.py` · 词级 3-gram + 跨轨 edit_ratio · 自我修正候选
- `main/orchestrator/candidate_family_adapter.py` · 家族分类 + cut_scope 声明 (含 cough_like / mic_bump_like 走 source_track_gate)
- 立即重复走 `immediate_repetition` 独立 detector (词级 3-gram · 不共用 filler 逻辑)

## 第二步 · 边界精修

- `mfa_align_and_extract_boundaries.py` (Champion in `稳定生产/challengers/mfa-alignment-v1/`) · 音素级 forced alignment · 中文 mandarin_mfa · 英文 english_mfa · `--language auto`
- 混合语言词若超出发音词典 → 直接标 `NEEDS_HUMAN_REVIEW` 不 auto-cut

## 第三步 · Gate 判决

- `apply_autocut_gate.py` (`稳定生产/challengers/autocut-gate-v1/scripts/`) · **唯一判决入口**
- 消费三层 signal · `labels_lake` + `case_memory` + `wordlevel_cross_track`
- 六道门级联 AND · whitelist / high-confidence tier / no-preserve / history-lake / duration / opening-closing 保护
- G7 · reject-on-never-cut-feedback · 消费 `session_feedback current.jsonl` 里 `verdict==never_cut` 反馈 hard reject

## 第四步 · 整片语义并轨 + 剪辑

- `automix_v1.py` 或封装 `main/orchestrator/automix_adapter.py` · **整片语义并轨唯一入口** · RMS 主导 + 侧链 duck + 双遍 loudnorm · **run-local · 只做电平不改 EDL**
- 整片 EDL 剪辑走 codex machine-assisted-draft flow (或等价 EDL 应用器) · 一次性拿掉全部 cut
- 拼口 · 装了的高级过渡组合优先级 · `pydub.AudioSegment.crossfade` + room tone splice > `ffmpeg acrossfade` + room tone > 单独 `ffmpeg acrossfade` > 裸 concat(禁用)
- chain 保留末尾 · 用 `librosa.onset.onset_detect(y, backtrack=True)` 保护真辅音起音 · 过渡时长以此为上限
- 主持人 backchannel 过滤 · `run_end_to_end.py Stage 3.4 stage_speaker_role_filter` + `main/knowledge/speaker_maps/<episode>.speaker_map.json`

## 第五步 · 响度归一 + 交付 + A/B 切片

- `ffmpeg loudnorm` 双遍 · `linear=true` · pass1 测 I/TP/LRA/thresh · pass2 应用 measured_* + linear=true · 内容不变
- 独立复核用另一次响度测量脚本比对目标区间(不复用 pass1 数字)
- A/B 切片 · `main/orchestrator/make_edl_ab_clips.py` · **从 render 主件 mp3 切** · schema `audit-clips-v1` · source_mp3 必须指向 DELIVERY_MANIFEST 血缘链上的最终 corrected 主件
- `DELIVERY_MANIFEST.json` v1 schema · 记 approval_chain / boundaries_respected / independent_verification / downstream_pipeline_impact

## 反馈闭环 · 决策前 retrieve · 决策后 analyze

- Skill · `skills/feedback-engine/SKILL.md` · **反馈闭环唯一 skill**
- 主入口 · `main/orchestrator/feedback_engine.py`
  - `retrieve_before_decision(candidate, decision_type, episode_id, knowledge_category)` · 决策前查偏好
  - `analyze_feedback(candidate, verdict, note)` · 决策后三级路由 TOOL_APPLY 到 DOC_REFERENCE 到 SESSION_FEEDBACK_PATCH
  - `is_never_cut(candidate, episode_id)` · 快速判定
  - `load_cut_parameters()` · 加载 PARAMETER 类知识
- 存储 · `main/knowledge/session_feedback/current.session_feedback.jsonl` (**单一 SOT**) + `main/knowledge/cut_parameters.json` (PARAMETER 存储)

## 三条学习流 · 新答案怎么进来

- Skill · `skills/feedback-engine/SKILL.md` · 单条 chat 反馈 · analyze
- Skill · `skills/label-learning-driver/SKILL.md` · 一批 accept/reject 决定 · shadow prediction + preference_snapshot
- Skill · `skills/editing-experience-distiller/SKILL.md` · Mentor gold EDL / 成品 mp3 · 经验卡 + Challenger 假设
- 决策树 · `docs/learning-flow-selector.md`

## Online 学习闭环 · 人审后自动回填

- `refresh_label_learning_snapshot.py` · 每次人审 save 后触发 · 更新 preference_snapshot
- `main/orchestrator/refresh_lake_and_regate.py --run <active>` · lake 增量后 gate 自适应 · 无需重跑候选生成层

## ASR + 降噪 · 前置分析层

- ASR · `faster-whisper` (small · large 视 run 需求) · 词级时间戳
- 降噪 · `DeepFilterNet` · 谱减前置(可选)
- 说话人角色 · 声明式 speaker_map (不用 pyannote 推理 · 无 HF token)

## 工具登记表 · 找工具顺序固定

**§ 11 施工规则** · 找工具顺序 · `main/tools/tools.json` (登记表) → `skills/*/SKILL.md` → `稳定生产/challengers/*/scripts/` → 都无才允许自实现。**自实现完必须回填 tools.json**(description / params / full_path / reads_only)才算完成 · 否则视为破坏契约(v219 事故已记录)。

verify.sh 第 18 层扫描 installed vs used 缺口 · 装了没 import 的包会 warn。

## 输出目录规则(§ 20 单 SOT)

- A/B clip 输出目录只保留 `main/runs/<run>/current_audit_clips/` (每次覆盖 · 不再 v215/v216/v217 平行目录)
- session_feedback 只维护 `current.session_feedback.jsonl` · 旧 EP04/ALL 归档只作历史证据不再读
- experience.md 只有一份 · 每次学习重生成


