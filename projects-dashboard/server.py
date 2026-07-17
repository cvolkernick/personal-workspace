#!/usr/bin/env python3
"""Local server for personal-workspace graceful-exit dashboard.

Pre-reset readiness: protect uncommitted/unpushed work and surface Grok
session resume commands so reboots/system updates don't lose context.

  GET /api/projects  — readiness + resume kit + project areas
  GET /api/health

Usage:
  python3 projects-dashboard/server.py
  python3 projects-dashboard/server.py --port 8765 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workspace import WORKSPACE_ROOT, collect_workspace_dashboard  # noqa: E402

DEFAULT_PORT = 8765


class ProjectsHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[projects] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "projects-dashboard",
                    "workspace": str(WORKSPACE_ROOT),
                },
            )
            return

        if path == "/api/projects":
            qs = parse_qs(parsed.query)
            only_touched = (qs.get("only_touched") or ["0"])[0] in (
                "1",
                "true",
                "yes",
            )
            try:
                payload = collect_workspace_dashboard(only_touched=only_touched)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, payload)
            return

        if path in ("/", "/index.html", "/projects", "/projects/"):
            self.path = "/index.html"
        return super().do_GET()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="personal-workspace Projects Dashboard"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args(argv)

    url = f"http://{args.bind}:{args.port}/"
    print(f"Graceful Exit dashboard → {url}")
    print(f"Workspace: {WORKSPACE_ROOT}")
    print("API: GET /api/projects  (readiness + resume kit + areas)")
    httpd = ThreadingHTTPServer((args.bind, args.port), ProjectsHandler)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
