#!/usr/bin/env python3
"""静态旁路服务器：直接把已有的 review_bundle/ 挂出来。

用途：当 `server_mvp.py` 因缺少 ffmpeg / build 失败而无法启动时，
本脚本只做“静态托管 + /api/submit 落盘”，不重建 bundle、不调 ffmpeg。

不修改任何 Champion / 审核前端 / 现有 Challenger 源码。
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def build_handler(bundle_dir: Path, run_dir: Path,
                  package_path: Path,
                  validate_mod_dir: Path):
    sys.path.insert(0, str(validate_mod_dir))
    from validate_mvp import approved_edl, validate_decisions  # noqa: WPS433

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(bundle_dir), **kwargs)

        def do_POST(self):  # noqa: N802
            if self.path != "/api/submit":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                pkg = json.loads(package_path.read_text(encoding="utf-8"))
                decisions = payload.get("decisions")
                metrics = payload.get("metrics")
                errors = validate_decisions(pkg, decisions or {})
                if errors:
                    self._json(400, {"ok": False, "errors": errors})
                    return
                reviewer = str((decisions or {}).get("reviewer", ""))
                target = run_dir / "e2e" if reviewer.startswith("AUTOMATED_") else run_dir
                target.mkdir(parents=True, exist_ok=True)
                (target / "human_decisions.json").write_text(
                    json.dumps(decisions, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                (target / "review_session_metrics.json").write_text(
                    json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                edl = approved_edl(pkg, decisions)
                (target / "approved.edl.draft.json").write_text(
                    json.dumps(edl, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                self._json(200, {
                    "ok": True,
                    "automated_test": reviewer.startswith("AUTOMATED_"),
                    "decisions_path": str(target / "human_decisions.json"),
                    "edl_path": str(target / "approved.edl.draft.json"),
                })
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "errors": [str(exc)]})

        def _json(self, status: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main(argv: list[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parents[4]
    default_run = project_root / "main/runs/EP04-review-product-v2"
    default_validate_mod = project_root / "稳定生产/challengers/review-product-v1/scripts"

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=str(default_run),
                    help="审核结果落盘目录，默认 EP04-review-product-v2")
    ap.add_argument("--bundle-dir", default=None,
                    help="review_bundle/ 目录；默认 <run-dir>/review_bundle")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--validate-mod-dir", default=str(default_validate_mod),
                    help="包含 validate_mvp.py 的目录（只读）")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()
    bundle_dir = Path(args.bundle_dir).resolve() if args.bundle_dir else (run_dir / "review_bundle")
    package = bundle_dir / "review_package.json"
    if not package.exists():
        raise SystemExit(f"未找到 {package}；请先确认 review_bundle 已存在")

    handler_cls = build_handler(bundle_dir, run_dir, package,
                                Path(args.validate_mod_dir).resolve())
    url = f"http://127.0.0.1:{args.port}/index.html"
    print("=" * 40)
    print("P1 审核（静态旁路）")
    print(f"  bundle : {bundle_dir}")
    print(f"  run    : {run_dir}")
    print(f"  访问   : {url}")
    print("=" * 40)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_cls)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
