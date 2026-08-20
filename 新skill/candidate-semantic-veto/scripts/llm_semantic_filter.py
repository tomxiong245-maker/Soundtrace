#!/usr/bin/env python3
"""llm_semantic_filter · LLM 判每候选 verdict.

用户 2026-08-19 明确 · LLM 是唯一候选决定者 · 三 Mode 优先级:
  Mode 1 (最优): claude CLI (subprocess · which claude 可用 · 无需 API key)
  Mode 2: Anthropic API (ANTHROPIC_API_KEY + anthropic SDK 存在)
  Mode 3 (fallback): 输出 candidates_with_context.json · workflow subagent 消费
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone

PROMPT_TEMPLATE = """你是资深播客剪辑师。判断这段是否该剪掉。

前 5 秒: {pre}
候选 (提议剪掉): {delete_text}
后 5 秒: {post}
kind: {kind}

判定原则:
- "就是"/"这个"/"那个" 若前后是句子结构词 → REJECT_KEEP · 若纯 filler → KEEP_CUT
- 句号"。"后新句 → REJECT_KEEP (话轮转换 · 非自纠正)
- 跨说话人 → REJECT_KEEP
- 长停顿话轮转换 → REJECT_KEEP

只回 JSON (无其它文字):
{{"verdict": "KEEP_CUT|REJECT_KEEP|NEEDS_REVIEW", "reason": "...", "confidence": "high|medium|low"}}"""

def utc_now(): return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def build_context(cand, transcripts):
    tid = cand.get("source_track_id", "track_01")
    words = transcripts.get(tid, {}).get("words", [])
    start, end = cand.get("start_seconds", 0), cand.get("end_seconds", 0)
    pre = " ".join(w["text"] for w in words if start - 5 <= w["start_seconds"] < start)[:200]
    post = " ".join(w["text"] for w in words if end < w["end_seconds"] <= end + 5)[:200]
    delete_text = cand.get("proposed_delete_text") or \
                  " ".join(w["text"] for w in words if start <= w["start_seconds"] < end)[:100]
    return {"pre": pre, "delete_text": delete_text, "post": post,
            "kind": cand.get("candidate_kind", "unknown")}

def _extract_verdict_json(text: str) -> dict | None:
    """从 CLI 输出里抽取 verdict JSON · 兼容 markdown 代码块和多余文字."""
    if not text:
        return None
    t = text.strip()
    # 剥掉 ```json ... ``` fence
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # 找含 "verdict" 的 { ... } 段 (最外层匹配)
    for m in re.finditer(r"\{[^{}]*\"verdict\"[^{}]*\}", t, re.DOTALL):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    # 直接尝试整段 parse
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None

def is_claude_cli_available() -> bool:
    return shutil.which("claude") is not None

def _child_env() -> dict:
    """给 subprocess claude 一份干净 env · 剥掉父 Claude Code 注入的 host auth 变量.
    这些变量在子进程里无效 · 若不剥 · 子 claude 会误以为已登录并失败."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE"))
           and k not in ("CLAUDE_AGENT_SDK_VERSION", "CLAUDE_EFFORT", "CLAUDE_PID")}
    return env

def call_claude_cli(prompt: str, timeout: int = 120) -> tuple[dict | None, str]:
    """调 claude CLI · 传 prompt · 拿 verdict JSON.
    返回 (verdict_dict_or_None, error_reason)."""
    try:
        result = subprocess.run(
            ["claude", "--print"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return None, f"CLI timeout after {timeout}s"
    except Exception as exc:
        return None, f"CLI subprocess error: {exc}"
    if result.returncode != 0:
        return None, f"CLI returncode={result.returncode} stderr={result.stderr[:200]}"
    v = _extract_verdict_json(result.stdout)
    if v is None:
        return None, f"CLI output not JSON · head={result.stdout[:200]!r}"
    return v, ""

def call_anthropic(client, prompt, model="claude-haiku-4-5-20251001"):
    resp = client.messages.create(model=model, max_tokens=200, temperature=0,
                                   messages=[{"role": "user", "content": prompt}])
    text = resp.content[0].text.strip()
    if text.startswith("```json"): text = text[7:]
    if text.startswith("```"): text = text[3:]
    if text.endswith("```"): text = text[:-3]
    return json.loads(text)

def _summarize(verdicts):
    counts = {"KEEP_CUT": 0, "REJECT_KEEP": 0, "NEEDS_REVIEW": 0}
    for v in verdicts:
        k = v.get("verdict", "NEEDS_REVIEW")
        counts[k] = counts.get(k, 0) + 1
    return counts

def _write_verdicts(out_path: Path, verdicts, model_label: str, mode: str):
    counts = _summarize(verdicts)
    json.dump({
        "schema": "llm-semantic-veto-v1",
        "verdicts": verdicts,
        "summary": {"total": len(verdicts),
                    "keep_cut": counts.get("KEEP_CUT", 0),
                    "reject_keep": counts.get("REJECT_KEEP", 0),
                    "needs_review": counts.get("NEEDS_REVIEW", 0)},
        "llm_model": model_label,
        "mode": mode,
        "computed_at_utc": utc_now(),
    }, open(out_path, "w"), ensure_ascii=False, indent=2)
    return counts

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, type=Path)
    ap.add_argument("--transcripts-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--cli-timeout", type=int, default=120,
                    help="Mode 1 · claude CLI 每候选 timeout (秒)")
    args = ap.parse_args()

    if not args.candidates.is_file():
        print(f"ERROR: candidates 不存在: {args.candidates}", file=sys.stderr); return 1

    cands_doc = json.load(open(args.candidates))
    cands = cands_doc.get("candidates", cands_doc if isinstance(cands_doc, list) else [])

    # 排除 cough_like (用户已 disable)
    cands = [c for c in cands if c.get("candidate_kind") not in ("cough_like", "transient_events")]

    # Load transcripts
    transcripts = {}
    for tf in args.transcripts_dir.glob("track_*.transcript.json"):
        tid = tf.stem.replace(".transcript", "")
        transcripts[tid] = json.load(open(tf))

    args.out.parent.mkdir(parents=True, exist_ok=True)

    # Mode 1 · claude CLI (最优 · workflow subagent 起 CLI · 无需 API key)
    if is_claude_cli_available():
        print(f"[llm_semantic_filter] Mode 1 · claude CLI · {len(cands)} 候选 · "
              f"timeout={args.cli_timeout}s", file=sys.stderr)
        verdicts = []
        for i, c in enumerate(cands, 1):
            ctx = build_context(c, transcripts)
            prompt = PROMPT_TEMPLATE.format(**ctx)
            v, err = call_claude_cli(prompt, timeout=args.cli_timeout)
            if v is None:
                print(f"  [{i}/{len(cands)}] {c.get('candidate_id')} · CLI FAIL · {err}",
                      file=sys.stderr)
                v = {"verdict": "NEEDS_REVIEW",
                     "reason": f"CLI call failed: {err}",
                     "confidence": "low"}
            else:
                print(f"  [{i}/{len(cands)}] {c.get('candidate_id')} · {v.get('verdict')}",
                      file=sys.stderr)
            verdicts.append({
                "candidate_id": c.get("candidate_id"),
                "kind": c.get("candidate_kind"),
                "verdict": v.get("verdict"),
                "reason": v.get("reason"),
                "confidence": v.get("confidence"),
                "llm_model": "claude-cli",
            })
        counts = _write_verdicts(args.out, verdicts, "claude-cli", "claude_cli")
        print(f"[llm_semantic_filter] OK · {args.out} · summary: {counts}", file=sys.stderr)
        return 0

    # Mode 2 · Anthropic API
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        import anthropic
        has_sdk = True
    except ImportError:
        has_sdk = False

    if api_key and has_sdk:
        print(f"[llm_semantic_filter] Mode 2 · Anthropic API · {len(cands)} 候选", file=sys.stderr)
        client = anthropic.Anthropic()
        verdicts = []
        for c in cands:
            ctx = build_context(c, transcripts)
            prompt = PROMPT_TEMPLATE.format(**ctx)
            try:
                v = call_anthropic(client, prompt, args.model)
            except Exception as exc:
                v = {"verdict": "NEEDS_REVIEW", "reason": f"LLM error: {exc}", "confidence": "low"}
            verdicts.append({
                "candidate_id": c.get("candidate_id"),
                "kind": c.get("candidate_kind"),
                "verdict": v.get("verdict"),
                "reason": v.get("reason"),
                "confidence": v.get("confidence"),
                "llm_model": args.model,
            })
        counts = _write_verdicts(args.out, verdicts, args.model, "anthropic_api")
        print(f"[llm_semantic_filter] OK · {args.out} · summary: {counts}", file=sys.stderr)
        return 0

    # Mode 3 · Fallback · 输出 candidates_with_context.json
    print("[llm_semantic_filter] Mode 3 · fallback · no claude CLI / API key / SDK · "
          "输出 candidates_with_context.json · workflow subagent 消费", file=sys.stderr)
    ctx_path = args.out.parent / "candidates_with_context.json"
    ctx_data = []
    for c in cands:
        ctx = build_context(c, transcripts)
        ctx_data.append({**{k: c.get(k) for k in
                            ["candidate_id", "candidate_kind", "source_track_id",
                             "start_seconds", "end_seconds"]}, **ctx})
    json.dump({"schema": "llm-veto-input-v1", "candidates": ctx_data,
               "computed_at_utc": utc_now()},
              open(ctx_path, "w"), ensure_ascii=False, indent=2)
    print(f"[llm_semantic_filter] fallback ctx: {ctx_path}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
