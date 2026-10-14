#!/usr/bin/env python3
"""Minimal read-only GET /api/heartbeat for nodes without Orchestra (e.g. finley).

Serves the same payload as orchestra-dashboard's /api/heartbeat from the
node's own latest.json. Read-only, stdlib only, no secrets in the document.

  python3 orchestra/serve_heartbeat.py --workspace ~/personal-workspace --host 127.0.0.1 --port 8799
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ORCHESTRA_DIR = Path(__file__).resolve().parent
if str(ORCHESTRA_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRA_DIR))

from heartbeat import heartbeat_api_payload  # noqa: E402


def make_handler(workspace: Path):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/api/heartbeat":
                self._send(200, heartbeat_api_payload(workspace))
            elif path in ("/api/health", "/api/healthz"):
                self._send(200, {"ok": True, "service": "pi-heartbeat-serve"})
            else:
                self._send(404, {"ok": False, "error": "not_found"})

        def log_message(self, *_a) -> None:
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, default=ORCHESTRA_DIR.parent)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8799)
    a = p.parse_args(argv)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(a.workspace.resolve()))
    print(f"[pi-heartbeat-serve] {a.host}:{a.port}", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
