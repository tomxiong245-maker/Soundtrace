#!/usr/bin/env python3
"""Build and serve only the safe review bundle on 127.0.0.1."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from validate_mvp import approved_edl, validate_decisions, validate_package


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CHALLENGER = Path(__file__).resolve().parents[1]
RUN = PROJECT_ROOT / "main/runs/EP03-review-product-v1"
BUNDLE = RUN / "review_bundle"
PACKAGE = BUNDLE / "review_package.json"
FFMPEG = Path(os.environ.get(
    "PODCAST_FFMPEG",
    str(PROJECT_ROOT / ".tools/bin/ffmpeg"),
))


def build() -> None:
    cmd = [
        sys.executable, str(Path(__file__).with_name("build_mvp_package.py")),
        "--source-package", str(PROJECT_ROOT / "main/runs/EP03-cross-track-safety-v1/review_package/review_package.json"),
        "--previews-dir", str(PROJECT_ROOT / "main/runs/EP03-cross-track-safety-v1/review_package/previews"),
        "--tracks-manifest", str(CHALLENGER / "tracks.ep03-three-track-compat.json"),
        "--frontend", str(PROJECT_ROOT / "审核前端/challenger-review-product-v1/mvp.html"),
        "--out", str(BUNDLE),
        "--ffmpeg", str(FFMPEG),
    ]
    subprocess.run(cmd, check=True)
    errors = validate_package(PACKAGE)
    if errors:
        raise SystemExit("审核包校验失败：\n- " + "\n- ".join(errors))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BUNDLE), **kwargs)

    def do_POST(self):
        if self.path != "/api/submit":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            pkg = json.loads(PACKAGE.read_text(encoding="utf-8"))
            decisions = payload.get("decisions")
            metrics = payload.get("metrics")
            errors = validate_decisions(pkg, decisions or {})
            if errors:
                self._json(400, {"ok": False, "errors": errors})
                return
            reviewer = str((decisions or {}).get("reviewer", ""))
            target_run = RUN / "e2e" if reviewer.startswith("AUTOMATED_") else RUN
            target_run.mkdir(parents=True, exist_ok=True)
            (target_run / "human_decisions.json").write_text(
                json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            (target_run / "review_session_metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            edl = approved_edl(pkg, decisions)
            (target_run / "approved.edl.draft.json").write_text(
                json.dumps(edl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            self._json(200, {
                "ok": True,
                "automated_test": reviewer.startswith("AUTOMATED_"),
                "decisions_path": str(target_run / "human_decisions.json"),
                "edl_path": str(target_run / "approved.edl.draft.json"),
            })
        except Exception as exc:
            self._json(500, {"ok": False, "errors": [str(exc)]})

    def _json(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    build()
    url = f"http://127.0.0.1:{args.port}/index.html"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"P1 MVP ready: {url}")
    print(f"bundle only: {BUNDLE}")
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
    raise SystemExit(main())
