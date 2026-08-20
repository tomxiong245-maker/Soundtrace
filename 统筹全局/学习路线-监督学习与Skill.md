# 监督学习与 Skill：本项目学习路线

> 目的：用最少阅读建立正确直觉，避免把“规则、Skill、案例库、benchmark、模型训练”混成一件事。

## 先记住这张关系图

```text
案例（发生过什么）
    ↓ 归纳
Skill（该怎么做、不能做什么）
    ↓ 固化
Rule / Tool（当前实际执行什么）
    ↓ 考试
Benchmark（新版本真的更好吗）
    ↓ 数据足够时才出现
监督学习模型（给候选排序，不替人删内容）
```

## 1. 本项目的结论

### 当前：Skill-first

当前最有价值的“学习”不是训练模型，而是把：

- 公开教程与官方资料；
- 每期人工审核的 accept / reject；
- 真实失败、返工、误删风险；

沉淀为可复用 Skill、规则字典、风险清单和 benchmark。它们能立刻帮助下一期 Agent 更稳定地工作。

### 未来：监督学习只做候选排序

最早值得训练的模型不是“端到端从 WAV 自动剪出成片”，而是二元候选排序器：

```text
输入：候选文本、上下文、重复/停顿特征、跨轨信息、风险词、历史相似案例
输出：这条候选更可能被人工 accept 的排序分数
```

它的作用是让人优先看到更可能值得剪的候选；它不能自动批准 EDL 或删除音频。

当前的 27 条案例远远不够训练可靠模型，但已经够用于熟悉数据结构、统计人工接受率和总结 Skill。

### `adjust` 与 `review_mode` 的当前位置

- 当前 MVP 固定为 `accept / reject`，`adjust` 暂不做。
- `adjust` 将来主要帮助学习“边界应该往哪改”，即边界回归；它不是二元候选排序研究的前置条件。
- `review_mode` 暂时只保留为兼容元数据，不阻塞案例库或规则分析。
- 旧 bulk accept 仍然排除，不是因为字段缺失，而是因为它没有逐项人工判断。

## 2. 你应先学什么

### 第 1 轮：约 90 分钟，建立监督学习工程直觉

1. **Google Machine Learning Problem Framing**
   - 看点：什么时候根本不该用 ML；如何定义输入、标签和业务指标。
   - 对应项目：为什么“减少审核和返工时间”比 accuracy 更重要。

2. **scikit-learn：Supervised learning / Model selection**
   - 看点：训练集、验证集、测试集、过拟合、交叉验证、分类指标。
   - 对应项目：为什么不能用同一批 EP03/EP04 案例既调规则又宣布它更好。

3. **Rules of Machine Learning（Google）**
   - 看点：先做可解释规则与数据管线，再考虑模型。
   - 对应项目：为什么现在先做 Skill、规则和 benchmark 是正确顺序。

### 第 2 轮：约 90 分钟，理解生产化

4. **The ML Test Score: A Rubric for ML Production Readiness**
   - 看点：模型上线前不仅看分数，还看数据、测试、监控、复现和回滚。
   - 对应项目：Champion / Challenger、冻结 benchmark 和回滚门。

5. **Hidden Technical Debt in Machine Learning Systems**
   - 看点：模型权重常常是最小部分；数据、依赖、特征、指标和外部系统才是长期成本。
   - 对应项目：为什么 Tool 注册表、SHA、样本时间线、审核包绑定不是“杂活”。

### 第 3 轮：约 90 分钟，理解 Skill / Agent 的本质

6. **ReAct: Synergizing Reasoning and Acting in Language Models**
   - 看点：语言模型如何在推理和工具调用之间切换。
   - 对应项目：顶层 Skill + Tool 注册表 + 人工暂停。

7. **Toolformer: Language Models Can Teach Themselves to Use Tools**
   - 看点：工具调用可以成为一种可学习、可评估的行为。
   - 对应项目：未来不是让模型“会剪辑”，而是先让统筹层正确选择 ASR、审核包、渲染等工具。

8. **Voyager: An Open-Ended Embodied Agent with Large Language Models**
   - 看点：把成功经验保存为可检索、可组合的 Skill library。
   - 对应项目：为什么经验先沉淀为 Skill，而不是急着改模型权重。

9. **Reflexion: Language Agents with Verbal Reinforcement Learning**
   - 看点：把失败总结为文字记忆，让下一次任务变好，而不必重新训练模型。
   - 对应项目：人工 reject、返工原因和 Mentor 反馈如何成为下一期的经验规则。

## 3. 每篇材料对应项目中的哪个问题

| 材料 | 你读完应能回答的问题 |
| --- | --- |
| Problem Framing | “我们要优化的是准确率，还是净节省时间？” |
| scikit-learn | “为什么 development / frozen benchmark 要分开？” |
| Rules of ML | “为什么现在不要急着训练？” |
| ML Test Score | “什么条件下 Challenger 才值得进入生产？” |
| Hidden Technical Debt | “为什么模型外的 pipeline 更关键？” |
| ReAct | “认知层与 Tool 层如何合作？” |
| Toolformer | “工具调用如何被记录、比较和优化？” |
| Voyager | “Skill library 如何从历史经验中长出来？” |
| Reflexion | “为什么文字总结也算一种有效学习？” |

## 4. 以后什么时候真的启动监督学习

满足下面条件再立项，不要提前：

```text
至少约 10 期独立节目
至少约 500 条逐项人工 accept / reject 决定
冻结一批完全不参与调参的端到端 benchmark
有人工最终成片或 EDL 可比较
能测出高风险误删、审核时间和返工时间
有 Challenger、人工晋升和回滚
```

这不是“500 条就必然能训练”的科学定律，而是避免用 27 条、两期节目训练出一个只会记住当前说话人和当前口癖的模型。

## 5. 阅读时不要混淆的概念

| 概念 | 在本项目中的对应物 | 现在有吗 |
| --- | --- | --- |
| X（输入特征） | 候选文本、上下文、停顿、重复、轨道活动、风险信息 | 有一部分 |
| y（监督标签） | 人工 accept / reject；以后可有 adjust 边界 | 有 27 条二态案例 |
| train set | 用来学习/调参的旧案例 | 暂不建立正式训练集 |
| dev set | 允许反复调试的节目 | 将随 benchmark v1 建立 |
| frozen test set | 不许按结果调参的考试卷 | 尚未建立 |
| model | 预测/排序候选的参数化函数 | 没有 |
| Skill | 文字化流程、工具选择、风险与边界 | 已有雏形 |

## 6. 最短实践练习（不训练模型）

当你读完第 1 轮，可以用现有案例做一个纯分析练习：

```text
按 reason_key 分组
→ 看 accept / reject 分布
→ 人工写出一个“什么时候不应提名”的 Skill 规则
→ 在新的 Challenger 上验证
→ 在 frozen episode 上再看是否减少审核负担
```

这比立刻写 `train.py` 更贴近当前产品，也能让你完整理解监督学习真正依赖什么。

## 7. 本地关联文件

- 经验案例库：`稳定生产/challengers/experience-ingestion-v1/`
- 经验报告：`稳定生产/challengers/experience-ingestion-v1/reports/`
- 外部知识：`从视频学习经验/`
- 顶层 Skill：`SKILL.md`
- 能力目录：`统筹全局/能力目录.md`
- benchmark 方案：`统筹全局/功能说明/F10-基准、能力目录与Skill路线.md`
