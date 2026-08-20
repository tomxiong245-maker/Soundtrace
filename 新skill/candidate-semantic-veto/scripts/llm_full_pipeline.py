#!/usr/bin/env python3
"""llm_full_pipeline · LLM 一步到位 · 从 transcript 扫出 KEEP_CUT 候选列表.

用户 2026-08-19 明确 (回忆 wpl29rgl6 成功版):
  LLM 完全主导 · **不看 all_candidates.json** (rules 结果不参考)
  一步 · 直接读 transcript · 输出 llm_verdicts.json (schema=llm-semantic-veto-v1)
  输出可直接给 Stage 5 消费 (verdict=='KEEP_CUT' 进 EDL).

Mode 优先级 (复用 llm_semantic_filter.py 的哲学):
  Mode 1 (最优) · claude CLI · which claude 可用
  Mode 2      · Anthropic API · ANTHROPIC_API_KEY + anthropic SDK
  Mode 3 (fallback) · 不调 LLM · 输出 window prompts (candidates_with_context.json) 给 subagent 消费

Window 策略:
  --window-seconds 30 · 每次读一段 transcript
  --overlap-seconds 5  · 相邻 window 重叠 5s · 避免边缘漏抓
  window 内所有 track 合成一段带时间戳的对话文本喂给 LLM
  LLM 输出 JSON list · 每 window 累积 · 最后 dedupe (track+start_seconds ± 0.5s)
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone

PROMPT_TEMPLATE = """你是资深播客剪辑师。这是一段音频转写 · 带时间戳。

任务: **只输出该剪的位置** (KEEP_CUT · 高信心)

5 种候选类型:
1. 填充词/口癖 (呃/嗯/就是/这个)
2. 立即重复 (这个这个 · 就是就是)
3. 长停顿 (前后词间隔 > 1.5s 而无关键内容)
4. 自纠正 (应该是...其实是 · 前后语义相似)
5. 语义重复/冗余/啰嗦 (不同词说同一意思 · 冗余句 · 啰嗦表达) ⭐ rules 抓不到的

**严格原则** (中文):
- "就是"/"这个"/"那个" 是内容词时不剪 (剪掉句子病)
- 句号后新句是话轮转换 · 不剪
- 跨说话人合并的伪自纠正 · 不剪
- 长停顿是自然思考停顿 · 不剪 (思考停顿 = 保留 300-500ms)
- 只输出 **high confidence · 明确该剪** 的

转写:
{transcript_text}

输出 JSON list · 每个候选:
{{
  "candidate_id": "LLM-<track>-<start_ms>",
  "source_track_id": "track_XX",
  "start_seconds": <float>,
  "end_seconds": <float>,
  "kind": "filler | repetition | long_pause | self_correction | semantic_redundant",
  "proposed_delete_text": "剪的具体词",
  "verdict": "KEEP_CUT",
  "reason": "20-40 字理由",
  "confidence": "high | medium | low"
}}

只回 JSON list · 无其它."""


def utc_now(): return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _child_env() -> dict:
    """剥掉父 Claude Code 注入的 host auth 变量 (与 llm_semantic_filter 保持一致)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE"))
           and k not in ("CLAUDE_AGENT_SDK_VERSION", "CLAUDE_EFFORT", "CLAUDE_PID")}
    return env


def is_claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def _extract_json_list(text: str) -> list | None:
    """从 CLI 输出里抽取 JSON list · 兼容 markdown 代码块和多余文字."""
    if not text:
        return None
    t = text.strip()
    # 剥 ```json ... ``` fence
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", t, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试整段
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get("candidates"), list):
            return parsed["candidates"]
    except json.JSONDecodeError:
        pass
    # 找第一个 [ ... ] 段
    bracket = re.search(r"\[[\s\S]*\]", t)
    if bracket:
        try:
            parsed = json.loads(bracket.group(0))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def call_claude_cli(prompt: str, timeout: int = 240) -> tuple[list | None, str]:
    """调 claude CLI · 传 prompt · 拿 JSON list."""
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
    lst = _extract_json_list(result.stdout)
    if lst is None:
        return None, f"CLI output not JSON list · head={result.stdout[:200]!r}"
    return lst, ""


def call_anthropic(client, prompt: str, model: str) -> list:
    resp = client.messages.create(
        model=model, max_tokens=4096, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    lst = _extract_json_list(text)
    if lst is None:
        raise ValueError(f"API output not JSON list · head={text[:200]!r}")
    return lst


def load_transcripts(transcripts_dir: Path) -> dict:
    """读所有 track_*.transcript.json · 返回 {track_id: {words: [...]}}."""
    transcripts = {}
    for tf in sorted(transcripts_dir.glob("track_*.transcript.json")):
        tid = tf.stem.replace(".transcript", "")
        try:
            transcripts[tid] = json.load(open(tf))
        except Exception as exc:
            print(f"[llm_full_pipeline] WARN · 读 {tf.name} 失败: {exc}", file=sys.stderr)
    return transcripts


def _duration_of(transcripts: dict) -> float:
    """所有 track 中最大 end_seconds · 决定 window 上界."""
    total = 0.0
    for t in transcripts.values():
        for w in t.get("words", []):
            end = w.get("end_seconds", 0.0)
            if end > total:
                total = end
    return total


def build_window_text(transcripts: dict, w_start: float, w_end: float) -> str:
    """把 [w_start, w_end) 内所有 track 的词按时间排序 · 输出带 [t=start] track_id: 词 的文本."""
    events = []
    for tid, doc in transcripts.items():
        for w in doc.get("words", []):
            s = w.get("start_seconds", 0.0)
            if w_start <= s < w_end:
                events.append((s, w.get("end_seconds", s), tid, w.get("text", "")))
    events.sort(key=lambda e: e[0])
    if not events:
        return ""
    # 相邻同 track 词合成一行 · 加 [t=start] 前缀
    lines = []
    cur_tid, cur_start, cur_end, cur_text = None, None, None, []
    GAP = 0.8  # 词间超过 0.8s 换行
    for s, e, tid, text in events:
        if cur_tid == tid and cur_end is not None and (s - cur_end) <= GAP:
            cur_text.append(text)
            cur_end = e
        else:
            if cur_tid is not None:
                lines.append(f"[t={cur_start:.2f}s-{cur_end:.2f}s {cur_tid}] {''.join(cur_text)}")
            cur_tid, cur_start, cur_end, cur_text = tid, s, e, [text]
    if cur_tid is not None:
        lines.append(f"[t={cur_start:.2f}s-{cur_end:.2f}s {cur_tid}] {''.join(cur_text)}")
    return "\n".join(lines)


def iter_windows(total: float, win: float, overlap: float):
    """(w_start, w_end) 生成器 · 步长 = win-overlap."""
    if win <= 0:
        return
    step = max(1.0, win - overlap)
    s = 0.0
    while s < total:
        yield s, min(s + win, total)
        s += step


def normalize_candidate(raw: dict, w_start: float, w_end: float, transcripts: dict) -> dict | None:
    """校验 + 归一化 LLM 单条输出 · 返回标准 verdict dict or None."""
    if not isinstance(raw, dict):
        return None
    try:
        st = float(raw.get("start_seconds"))
        en = float(raw.get("end_seconds"))
    except (TypeError, ValueError):
        return None
    if en <= st:
        return None
    tid = raw.get("source_track_id") or ""
    if tid not in transcripts:
        # 尝试通用轨道匹配
        if not tid:
            return None
    kind = raw.get("kind") or "unknown"
    verdict = raw.get("verdict") or "KEEP_CUT"
    if verdict != "KEEP_CUT":
        return None  # 一步到位模式 · 只留 KEEP_CUT
    cid = raw.get("candidate_id") or f"LLM-{tid}-{int(round(st * 1000))}"
    return {
        "candidate_id": str(cid),
        "source_track_id": str(tid),
        "start_seconds": round(st, 3),
        "end_seconds": round(en, 3),
        "kind": str(kind),
        "proposed_delete_text": str(raw.get("proposed_delete_text") or ""),
        "verdict": "KEEP_CUT",
        "reason": str(raw.get("reason") or ""),
        "confidence": str(raw.get("confidence") or "medium"),
        "window": [round(w_start, 3), round(w_end, 3)],
    }


def dedupe_verdicts(verdicts: list, tol: float = 0.5) -> list:
    """按 (track, start_seconds ± tol) dedupe · 避免 overlap 重复候选.
    保留 confidence 更高的; 同级保留先出现的."""
    order = {"high": 3, "medium": 2, "low": 1}
    kept: list = []
    for v in verdicts:
        tid = v["source_track_id"]
        st = v["start_seconds"]
        dup_idx = None
        for i, k in enumerate(kept):
            if k["source_track_id"] == tid and abs(k["start_seconds"] - st) <= tol:
                dup_idx = i
                break
        if dup_idx is None:
            kept.append(v)
            continue
        # dedupe · 保留 confidence 更高
        if order.get(v["confidence"], 0) > order.get(kept[dup_idx]["confidence"], 0):
            kept[dup_idx] = v
    kept.sort(key=lambda x: (x["source_track_id"], x["start_seconds"]))
    return kept


def _summarize_kinds(verdicts: list) -> dict:
    counts: dict = {}
    for v in verdicts:
        counts[v["kind"]] = counts.get(v["kind"], 0) + 1
    return counts


def _write_output(out_path: Path, verdicts: list, mode: str, model_label: str,
                  windows_processed: int):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "llm-semantic-veto-v1",
        "verdicts": verdicts,
        "summary": {
            "total": len(verdicts),
            "keep_cut": sum(1 for v in verdicts if v.get("verdict") == "KEEP_CUT"),
            "kinds": _summarize_kinds(verdicts),
        },
        "mode": mode,
        "llm_model": model_label,
        "windows_processed": windows_processed,
        "computed_at_utc": utc_now(),
        "pipeline": "llm_full_pipeline",
        "notes": "LLM 一步扫描 transcript 输出 KEEP_CUT 列表 · 不消费 all_candidates.json",
    }
    json.dump(doc, open(out_path, "w"), ensure_ascii=False, indent=2)
    return doc["summary"]


def _write_fallback_prompts(out_dir: Path, prompts: list):
    """Mode 3 · 写窗口 prompt 给 subagent 消费."""
    p = out_dir / "candidates_with_context.json"
    doc = {
        "schema": "llm-full-pipeline-input-v1",
        "windows": prompts,
        "computed_at_utc": utc_now(),
        "notes": "每 window prompt · subagent 逐条运行 · 返回 JSON list · 汇总回 llm_verdicts.json",
    }
    json.dump(doc, open(p, "w"), ensure_ascii=False, indent=2)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path,
                    help="llm_verdicts.json 输出路径 (兼容 Stage 5 消费)")
    ap.add_argument("--window-seconds", type=float, default=30.0)
    ap.add_argument("--overlap-seconds", type=float, default=5.0)
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--cli-timeout", type=int, default=240,
                    help="Mode 1 · claude CLI 每 window timeout (秒)")
    ap.add_argument("--max-windows", type=int, default=0,
                    help="dry-run 限制 window 数 · 0=全跑")
    args = ap.parse_args()

    if not args.transcripts_dir.is_dir():
        print(f"ERROR: transcripts-dir 不存在: {args.transcripts_dir}", file=sys.stderr)
        return 1

    transcripts = load_transcripts(args.transcripts_dir)
    if not transcripts:
        print(f"ERROR: 目录内没 track_*.transcript.json: {args.transcripts_dir}", file=sys.stderr)
        return 1
    total_dur = _duration_of(transcripts)
    windows = list(iter_windows(total_dur, args.window_seconds, args.overlap_seconds))
    if args.max_windows > 0:
        windows = windows[: args.max_windows]
    print(f"[llm_full_pipeline] transcripts={list(transcripts.keys())} · "
          f"total_duration={total_dur:.1f}s · windows={len(windows)} "
          f"(win={args.window_seconds}s overlap={args.overlap_seconds}s)",
          file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)

    # Mode 1 · claude CLI
    if is_claude_cli_available():
        print(f"[llm_full_pipeline] Mode 1 · claude CLI · timeout={args.cli_timeout}s/window",
              file=sys.stderr)
        all_verdicts: list = []
        for i, (w_s, w_e) in enumerate(windows, 1):
            text = build_window_text(transcripts, w_s, w_e)
            if not text.strip():
                print(f"  [{i}/{len(windows)}] {w_s:.1f}-{w_e:.1f}s · 空 window · skip",
                      file=sys.stderr)
                continue
            prompt = PROMPT_TEMPLATE.format(transcript_text=text)
            lst, err = call_claude_cli(prompt, timeout=args.cli_timeout)
            if lst is None:
                print(f"  [{i}/{len(windows)}] {w_s:.1f}-{w_e:.1f}s · CLI FAIL · {err}",
                      file=sys.stderr)
                continue
            n_before = len(all_verdicts)
            for raw in lst:
                norm = normalize_candidate(raw, w_s, w_e, transcripts)
                if norm is not None:
                    all_verdicts.append(norm)
            print(f"  [{i}/{len(windows)}] {w_s:.1f}-{w_e:.1f}s · LLM raw={len(lst)} · "
                  f"kept={len(all_verdicts) - n_before}", file=sys.stderr)
        deduped = dedupe_verdicts(all_verdicts)
        summary = _write_output(args.out, deduped, "llm_full_pipeline_via_claude_cli",
                                "claude-cli", len(windows))
        print(f"[llm_full_pipeline] OK · {args.out}", file=sys.stderr)
        print(f"[llm_full_pipeline] raw={len(all_verdicts)} deduped={len(deduped)} · "
              f"summary: {summary}", file=sys.stderr)
        return 0

    # Mode 2 · Anthropic API
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        import anthropic
        has_sdk = True
    except ImportError:
        has_sdk = False

    if api_key and has_sdk:
        print(f"[llm_full_pipeline] Mode 2 · Anthropic API · model={args.model}",
              file=sys.stderr)
        client = anthropic.Anthropic()
        all_verdicts = []
        for i, (w_s, w_e) in enumerate(windows, 1):
            text = build_window_text(transcripts, w_s, w_e)
            if not text.strip():
                continue
            prompt = PROMPT_TEMPLATE.format(transcript_text=text)
            try:
                lst = call_anthropic(client, prompt, args.model)
            except Exception as exc:
                print(f"  [{i}/{len(windows)}] API FAIL · {exc}", file=sys.stderr)
                continue
            for raw in lst:
                norm = normalize_candidate(raw, w_s, w_e, transcripts)
                if norm is not None:
                    all_verdicts.append(norm)
            print(f"  [{i}/{len(windows)}] {w_s:.1f}-{w_e:.1f}s · raw={len(lst)}",
                  file=sys.stderr)
        deduped = dedupe_verdicts(all_verdicts)
        summary = _write_output(args.out, deduped,
                                "llm_full_pipeline_via_anthropic_api",
                                args.model, len(windows))
        print(f"[llm_full_pipeline] OK · {args.out} · summary: {summary}", file=sys.stderr)
        return 0

    # Mode 3 · Fallback
    print("[llm_full_pipeline] Mode 3 · fallback · 写 window prompts · "
          "subagent 逐条运行后合并回 llm_verdicts.json", file=sys.stderr)
    prompts = []
    for i, (w_s, w_e) in enumerate(windows, 1):
        text = build_window_text(transcripts, w_s, w_e)
        if not text.strip():
            continue
        prompts.append({
            "window_index": i,
            "window_start_seconds": round(w_s, 3),
            "window_end_seconds": round(w_e, 3),
            "prompt": PROMPT_TEMPLATE.format(transcript_text=text),
        })
    fb = _write_fallback_prompts(args.out.parent, prompts)
    print(f"[llm_full_pipeline] fallback prompts: {fb} · windows={len(prompts)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
