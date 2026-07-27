#!/usr/bin/env python3
"""Horizon seasonal planning dashboard.

  GET  /api/health
  GET  /api/horizon   — season plan + initiatives + ikigai themes
  GET  /api/season
  POST /api/season    — merge-save season fields
  GET  /              — UI

Usage:
  python3 horizon/server.py
  python3 horizon/server.py --host 127.0.0.1 --port 8791 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HORIZON_DIR = Path(__file__).resolve().parent
ROOT = HORIZON_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HORIZON_DIR) not in sys.path:
    sys.path.insert(0, str(HORIZON_DIR))

from store import horizon_payload, load_season, save_season  # noqa: E402

DEFAULT_PORT = 8791


class HorizonHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HORIZON_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[horizon] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "horizon",
                    "workspace": str(ROOT),
                },
            )
            return
        if path in ("/api/horizon", "/api/state", "/api/payload"):
            try:
                self._json(200, horizon_payload(ROOT))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/season":
            try:
                self._json(200, {"ok": True, "season": load_season()})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path in ("/", "/index.html", "/horizon", "/horizon/"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/season":
            body = self._read_json()
            try:
                saved = save_season(body)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "season": saved})
            return
        self._json(404, {"ok": False, "error": f"unknown path {path}"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Horizon seasonal planning dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), HorizonHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Horizon: {url}")
    print(f"API: {url}api/horizon")
    if not args.no_browser:
        try:
            webbrowser.open(url.replace("0.0.0.0", "127.0.0.1"))
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Horizon…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
