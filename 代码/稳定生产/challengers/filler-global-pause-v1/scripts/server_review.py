#!/usr/bin/env python3
"""Serve a single frozen filler/global-pause review package locally."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from validate_review_package import validate_decisions, validate_package


WRITE_LOCK = threading.Lock()


def write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def make_handler(bundle: Path, run_dir: Path):
    class ReviewHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(bundle), **kwargs)

        def do_POST(self):
            if self.path != "/api/submit":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 5 * 1024 * 1024:
                    self._json(400, {"ok": False, "errors": ["invalid request size"]})
                    return
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                package_path = bundle / "review_package.json"
                package_errors = validate_package(package_path, verify_track_hashes=False)
                if package_errors:
                    self._json(409, {"ok": False, "errors": package_errors})
                    return
                package = json.loads(package_path.read_text(encoding="utf-8"))
                decisions = payload.get("decisions") or {}
                errors = validate_decisions(package, decisions)
                if errors:
                    self._json(400, {"ok": False, "errors": errors})
                    return
                reviewer = str(decisions.get("reviewer", ""))
                target = run_dir / "e2e" if reviewer.startswith("AUTOMATED_") else run_dir
                target.mkdir(parents=True, exist_ok=True)
                with WRITE_LOCK:
                    write_json_atomic(target / "human_decisions.json", decisions)
                    write_json_atomic(target / "review_session_metrics.json", payload.get("metrics") or {})
                self._json(
                    200,
                    {
                        "ok": True,
                        "decisions_path": str(target / "human_decisions.json"),
                        "note": "真人审核决定已固定；本轮不生成 EDL、不渲染、不发布。",
                    },
                )
            except Exception as exc:
                self._json(500, {"ok": False, "errors": [str(exc)]})

        def _json(self, status: int, value: dict) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ReviewHandler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    run_dir = args.run_dir.resolve()
    errors = validate_package(bundle / "review_package.json")
    if errors:
        raise SystemExit("review package invalid:\n- " + "\n- ".join(errors))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(bundle, run_dir))
    print(f"Filler/global-pause review ready: http://127.0.0.1:{args.port}/index.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
