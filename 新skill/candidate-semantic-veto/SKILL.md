---
name: candidate-semantic-veto
description: LLM 语义否决 skill · 对 all_candidates.json 里每个候选跑一次 LLM 语义判定 · 输出 verdict ∈ {KEEP_CUT, REJECT_KEEP, NEEDS_REVIEW} · **只由 LLM 决定** · cut-verify 前 4 项已降级为诊断 · 不再参与 EDL · Optuna + NISQA 只做参数优化。触发词：语义否决、LLM 判候选、semantic veto、KEEP_CUT/REJECT_KEEP、llm_verdicts.json、Stage 5 前置 veto。
status: active
owner: challenger
entry_tool: llm_semantic_filter.py
llm:
  primary_model: claude-haiku-4-5-20251001
  mode_priority: [claude_cli, anthropic_api, fallback_subagent]
  temperature: 0
  max_tokens: 300
preconditions:
  - "all_candidates.json 存在 (Stage 4 输出) · 含 candidates[] · 每条有 candidate_id, candidate_kind, source_track_id, start_seconds, end_seconds, text_tracks"
  - "analysis/track_01.transcript.json 与 analysis/track_02.transcript.json 存在 · 含 words[] 带 start_seconds / end_seconds / text"
  - "(Mode 1) `shutil.which('claude')` 命中 · 走 keychain 登录 · 无需 API key / SDK"
  - "(Mode 2) `ANTHROPIC_API_KEY` 环境变量已设 · 且 `pip install anthropic` 已装"
  - "(Mode 3) 前两 mode 均不可用时 · 由外层 workflow subagent 消费 candidates_with_context.json"
postconditions:
  - "**Mode 1 (claude CLI)** · 写 <run>/llm_verdicts.json · schema=llm-semantic-veto-v1 · mode='claude_cli' · llm_model='claude-cli'"
  - "**Mode 2 (Anthropic API)** · 写 <run>/llm_verdicts.json · schema=llm-semantic-veto-v1 · mode='anthropic_api' · llm_model=<--model>"
  - "**Mode 3 (Fallback)** · 写 <run>/candidates_with_context.json · schema=llm-veto-input-v1 · 待 subagent 逐条填 verdict 再合并回 llm_verdicts.json"
  - "**不改** all_candidates.json / 音频 / session_feedback · 只写侧车"
  - "Stage 5 EDL 生成器只保留 verdict=='KEEP_CUT' 的候选"
fail_closed:
  - "LLM 请求异常 (超时/限流/JSON 解析失败) → 该候选 verdict='NEEDS_REVIEW', confidence='low', reason='llm_error:<type>'"
  - "上下文窗口构建失败 (transcript 未覆盖该时段) → verdict='NEEDS_REVIEW', reason='no_transcript_context'"
  - "整批 LLM 全部失败 → 输出仍写盘 · 全 NEEDS_REVIEW · 不阻断 pipeline"
demotions_from_cut_verify:
  - "check1_hallucination · 保留为诊断字段 · 不影响 verdict"
  - "check2_silence_location · 保留为诊断字段 · 不影响 verdict"
  - "check3_rhythm_gap · 保留为诊断字段 · 不影响 verdict"
  - "check4_crossfade_route · 保留为诊断字段 · 不影响 verdict"
  - "cut-verify 输出的 4 项 check 仍写盘但只做审计追溯 · 不再驱动 KEEP/REJECT"
optuna_nisqa_scope:
  - "Optuna 只调 crossfade_ms · post_cut_pause_ms · room_tone_pad_ms · asymmetric_head_pad_ms"
  - "NISQA 只做参数扫描的成本函数 · 不参与语义判定"
covers_claude_md_rules:
  - "§8 · 生成/门控分离"
  - "§17 · 语义否决优先于阈值"
  - "§22 · fail-closed"
---

# candidate-semantic-veto skill

## 1. 定位

**LLM 直接判每一条候选该不该剪** · 语义先行 · 阈值退位。

**为什么**：
- 2026-08-19 明确 · 只用 LLM 决定候选
- cut-verify 4 项 check 是"干净度诊断" · 不是"要不要剪"的判官
- Optuna + NISQA 是"参数优化"工具 · 也不是判官
- 谁判 KEEP_CUT / REJECT_KEEP / NEEDS_REVIEW ? **只有 LLM**

## 2. 何时用

- Stage 4 已产 all_candidates.json
- Stage 5 (EDL 生成) 之前
- 每次 run 跑一次 · 幂等 (输入 sha256 相同 → 输出相同)

## 3. 输入

```
--candidates       <path>   all_candidates.json (Stage 4 输出)
--transcripts-dir  <path>   含 track_01.transcript.json / track_02.transcript.json
--out              <path>   写 llm_verdicts.json
--context-seconds  <float>  默认 5.0 · 前后各取多少秒
--model            <str>    默认 claude-haiku-4-5-20251001
--dry-run          <flag>   只构建上下文 · 不调 LLM · 落 candidates_with_context.json
```

## 4. Mode 优先级 (由高到低)

代码入口 `scripts/llm_semantic_filter.py` 按优先级探测 · 命中第一档即走 · 不回退不重试。

### Mode 1 · claude CLI (subprocess · 首选)

- **检测**: `shutil.which("claude")` 可用
- **调用**: `subprocess.run(["claude", "--print"], input=prompt, env=_child_env(), timeout=<--cli-timeout, 默认 120s>)`
- **环境变量剥离**: `_child_env()` 剥掉所有 `CLAUDE_CODE_*` / `CLAUDECODE` 前缀 · 以及 `CLAUDE_AGENT_SDK_VERSION` / `CLAUDE_EFFORT` / `CLAUDE_PID` (workflow subagent 起 subprocess 时防父 host auth 污染 · 不剥则子 claude 误判已登录后失败)
- **prompt 输出解析**: `_extract_verdict_json` 支持 markdown 代码块 (```json ... ```) 或裸 JSON 对象
- **落盘**: `mode='claude_cli'` · `llm_model='claude-cli'`
- **优点**: 无需 API key · 无需装 SDK · 走 keychain 登录 · workflow subagent 环境默认可用
- **缺点**: 每候选 1 次 subprocess · 有启动开销 · 无 usage 统计

### Mode 2 · Anthropic API (SDK + KEY)

- **检测**: `os.environ["ANTHROPIC_API_KEY"]` 存在 **且** `import anthropic` 成功
- **调用**: `anthropic.Anthropic().messages.create(model=<--model, 默认 claude-haiku-4-5-20251001>, temperature=0, max_tokens=200)`
- **prompt 输出解析**: 剥 ```json / ``` fence 后 `json.loads`
- **落盘**: `mode='anthropic_api'` · `llm_model=<--model>`
- **优点**: 稳定 · 可控 · 有 usage 统计 · 单进程内 batch 走 HTTP keep-alive
- **缺点**: 需 API key · 需 `pip install anthropic` · 走公网

### Mode 3 · Fallback (candidates_with_context.json)

- **触发**: Mode 1 / Mode 2 均不可用
- **不落 llm_verdicts.json** · 改落 `<out>.parent/candidates_with_context.json` · schema `llm-veto-input-v1` · 每候选带 before_5s / candidate / after_5s / kind / proposed_delete_text
- **消费流程**: 用户或 workflow subagent 手动逐条判 · 填 `llm_verdicts.json` · rerun pipeline Stage 5
- **优点**: 无阻断 · 不 break pipeline · 断网/离线也能推进
- **缺点**: 需人工介入 · 非幂等 (人判可能不一致)

## 5. Prompt 模板

```
你是资深播客剪辑师。判断这段是否该剪掉:
【前 5s】"..."
【候选 · 提议剪掉】"..."
【后 5s】"..."
kind: <candidate_kind>
proposed_delete_text: "..."

判定原则:
- filler "就是" · 若前后是句子结构词 → REJECT_KEEP · 若纯 filler → KEEP_CUT
- 句号"。"后新句 → REJECT_KEEP (话轮转换)
- 跨说话人 → REJECT_KEEP
- 长停顿话轮转换 → REJECT_KEEP

只回 JSON:
{"verdict": "KEEP_CUT" | "REJECT_KEEP" | "NEEDS_REVIEW", "reason": "...", "confidence": "high" | "medium" | "low"}
```

**中文** · 无 few-shot · temperature=0 · max_tokens=300。

## 6. 输出 schema

`llm-semantic-veto-v1`:

```json
{
  "schema": "llm-semantic-veto-v1",
  "run_id": "EP05-...",
  "candidates_source_sha256": "...",
  "llm_model": "claude-haiku-4-5-20251001",
  "prompt_template_sha256": "...",
  "context_seconds": 5.0,
  "computed_at_utc": "2026-08-19T...",
  "verdicts": [
    {
      "candidate_id": "C001",
      "kind": "global_long_pause",
      "start_seconds": 68.05,
      "end_seconds": 68.95,
      "track_id": "track_01",
      "verdict": "KEEP_CUT",
      "reason": "长停顿在句尾 · 无话轮转换 · 剪掉不损语义",
      "confidence": "high",
      "prompt_hash": "...",
      "llm_model": "claude-haiku-4-5-20251001",
      "diagnostics": {
        "before_5s_text": "...",
        "candidate_text": "...",
        "after_5s_text": "..."
      }
    }
  ],
  "summary": {
    "total": 36,
    "keep_cut": 12,
    "reject_keep": 8,
    "needs_review": 16,
    "llm_error": 0
  }
}
```

## 7. 与 Stage 5 EDL 的对接

Stage 5 加载 llm_verdicts.json:
- verdict=='KEEP_CUT' → 进 EDL
- verdict=='REJECT_KEEP' → 不进 EDL
- verdict=='NEEDS_REVIEW' → 走人审通道 · 不进自动 EDL

## 8. 幂等 & 可追溯

- prompt_template_sha256 定死 prompt 版本
- candidates_source_sha256 绑定输入
- 每条 verdict 有 prompt_hash
- 同 sha 组合下 → 结果确定 (temperature=0)

## 9. 失败模式

| 场景 | verdict | reason |
| --- | --- | --- |
| Mode 1 CLI timeout | NEEDS_REVIEW | `CLI timeout after <sec>s` |
| Mode 1 CLI 非零 returncode | NEEDS_REVIEW | `CLI returncode=<n> stderr=...` |
| Mode 1 CLI 输出非 JSON | NEEDS_REVIEW | `CLI output not JSON · head=...` |
| Mode 2 API 超时/限流/异常 | NEEDS_REVIEW | `LLM error: <exc>` |
| transcript 覆盖不到该时段 | NEEDS_REVIEW | no_transcript_context |
| 候选 kind 未识别 | NEEDS_REVIEW | unknown_kind |
| 三 mode 全不可用 | (退 Mode 3 · 不写 verdicts) | 落 candidates_with_context.json 待人审 |

## 10. 相关

- cut-verify · 现降级为诊断
- candidate-generation-and-gate · 上游
- 无对应 Optuna/NISQA skill · 那两个只做参数扫描
