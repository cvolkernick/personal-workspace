#!/usr/bin/env python3
"""OOMWOO project-status dashboard.

  GET  /api/health  → {ok, service: "oomwoo", port}
  GET  /api/status  → module board + GitHub pulse
  GET  /            → UI

Usage:
  python3 oomwoo/server.py
  python3 oomwoo/server.py --host 127.0.0.1 --port 8798 --no-browser
  python3 oomwoo/server.py --fixture oomwoo/tests/fixtures/status.json --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from status import build_status, load_fixture  # noqa: E402

DEFAULT_PORT = 8798
_BOUND_PORT: int = DEFAULT_PORT
_BOUND_HOST: str = "127.0.0.1"
_FIXTURE: Optional[Path] = None


class OomwooHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PKG_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[oomwoo] " + (fmt % args) + "\n")

    def end_headers(self) -> None:
        path = getattr(self, "path", "") or ""
        if path in ("/", "/index.html") or path.endswith(".html"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            try:
                bound_port = int(self.server.server_address[1])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                bound_port = _BOUND_PORT
            self._json(
                200,
                {
                    "ok": True,
                    "service": "oomwoo",
                    "port": bound_port,
                    "default_port": DEFAULT_PORT,
                    "host": _BOUND_HOST,
                    "fixture": bool(_FIXTURE),
                    "note": "makerspet/oomwoo project-status MVP",
                },
            )
            return
        if path == "/api/status":
            qs = parse_qs(parsed.query or "")
            refresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")
            try:
                if _FIXTURE is not None:
                    payload = load_fixture(str(_FIXTURE))
                else:
                    payload = build_status(refresh=refresh)
                self._json(200, payload)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()
        return super().do_GET()


def main(argv: list[str] | None = None) -> int:
    global _BOUND_PORT, _BOUND_HOST, _FIXTURE
    parser = argparse.ArgumentParser(description="OOMWOO project-status dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="JSON payload for /api/status (skips GitHub)",
    )
    args = parser.parse_args(argv)

    _FIXTURE = args.fixture.resolve() if args.fixture else None
    if _FIXTURE is not None and not _FIXTURE.is_file():
        print(f"fixture not found: {_FIXTURE}", file=sys.stderr)
        return 2
    _BOUND_PORT = int(args.port)
    _BOUND_HOST = str(args.host)

    server = ThreadingHTTPServer((args.host, args.port), OomwooHandler)
    _BOUND_HOST, _BOUND_PORT = server.server_address[0], int(server.server_address[1])
    url = f"http://{_BOUND_HOST}:{_BOUND_PORT}/"
    print(f"OOMWOO status dashboard → {url}")
    print("API: /api/health /api/status")
    if _FIXTURE:
        print(f"Fixture: {_FIXTURE}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
