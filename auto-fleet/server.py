#!/usr/bin/env python3
"""Internal Auto Fleet ops dashboard (not TREAD).

  GET  /api/health  → {ok, service: "auto-fleet", port}
  GET  /api/fleet   → roster units + DIMO / Turo / costs strips
  GET  /api/agent/fleet → read-only Helm brief (token / loopback)
  GET  /api/turo-tasks → open Google Tasks on the Turo list
  POST /api/turo-tasks/complete → checkbox write-back to Google Tasks
  GET  /api/turo-inbox-media/<relpath> → ingested Turo mail photo (email ingest only)
  GET  /            → UI

Usage:
  python3 auto-fleet/server.py
  python3 auto-fleet/server.py --host 127.0.0.1 --port 8796 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from agent_fleet import handle_agent_fleet_http  # noqa: E402
from fleet import DATA_DIR, build_fleet, load_roster  # noqa: E402
from turo_inbox import resolve_inbox_path  # noqa: E402
from turo_media import media_dir_for, resolve_media_file  # noqa: E402
from turo_tasks import complete_task, list_open_tasks  # noqa: E402

DEFAULT_PORT = 8796
_BOUND_PORT: int = DEFAULT_PORT
_BOUND_HOST: str = "127.0.0.1"
_ROSTER_PATH: Optional[Path] = None
_NOTES_PATH: Optional[Path] = None
_EXPENSES_PATH: Optional[Path] = None
_INBOX_PATH: Optional[Path] = None
_ENV_PATH: Optional[Path] = None


class AutoFleetHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PKG_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[auto-fleet] " + (fmt % args) + "\n")

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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Auto-Fleet-Service-Token",
        )
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            try:
                bound_port = int(self.server.server_address[1])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                bound_port = _BOUND_PORT
            self._json(
                200,
                {
                    "ok": True,
                    "service": "auto-fleet",
                    "port": bound_port,
                    "default_port": DEFAULT_PORT,
                    "host": _BOUND_HOST,
                    "note": "Internal ops. Not TREAD. Pi is prod.",
                },
            )
            return
        if path == "/api/agent/fleet":
            client_host = (self.client_address or ("", 0))[0]
            code, payload = handle_agent_fleet_http(
                self.headers,
                client_host,
                roster_path=_ROSTER_PATH,
                inbox_path=_INBOX_PATH,
            )
            self._json(code, payload)
            return
        if path == "/api/fleet":
            try:
                payload = build_fleet(
                    roster_path=_ROSTER_PATH,
                    notes_path=_NOTES_PATH,
                    expenses_path=_EXPENSES_PATH,
                    inbox_path=_INBOX_PATH,
                    env_path=_ENV_PATH,
                )
                self._json(200, payload)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/turo-tasks":
            try:
                roster = load_roster(_ROSTER_PATH)
                units = [
                    u for u in (roster.get("units") or [])
                    if isinstance(u, dict) and u.get("id")
                ]
                bookings_by_unit = None
                try:
                    fleet = build_fleet(
                        roster_path=_ROSTER_PATH,
                        notes_path=_NOTES_PATH,
                        expenses_path=_EXPENSES_PATH,
                        inbox_path=_INBOX_PATH,
                        env_path=_ENV_PATH,
                    )
                    bookings_by_unit = {
                        str(u["id"]): (u.get("turo") or {}).get("bookings") or []
                        for u in (fleet.get("units") or [])
                        if isinstance(u, dict) and u.get("id")
                    }
                except Exception:  # noqa: BLE001
                    bookings_by_unit = None
                self._json(
                    200,
                    list_open_tasks(units=units, bookings_by_unit=bookings_by_unit),
                )
            except Exception as exc:  # noqa: BLE001
                self._json(
                    200,
                    {
                        "ok": False,
                        "error": str(exc) or "Google Tasks error",
                        "items": [],
                    },
                )
            return
        if path.startswith("/api/turo-inbox-media/"):
            rel = unquote(path[len("/api/turo-inbox-media/") :])
            inbox = resolve_inbox_path(_INBOX_PATH, DATA_DIR)
            media = media_dir_for(inbox)
            dest = resolve_media_file(media, rel) if media else None
            if dest is None:
                self._json(404, {"ok": False, "error": "photo not found"})
                return
            data = dest.read_bytes()
            mime = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".heic": "image/heic",
                ".heif": "image/heif",
            }.get(dest.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()
        return super().do_GET()

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/turo-tasks/complete":
            self._json(404, {"ok": False, "error": "not found"})
            return
        body = self._read_json()
        task_id = str(body.get("task_id") or body.get("id") or "")
        list_id = str(body.get("list_id") or "") or None
        try:
            result = complete_task(task_id, list_id)
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(exc) or "complete_failed"})
            return
        code = 200 if result.get("ok") else 400
        self._json(code, result)


def main(argv: list[str] | None = None) -> int:
    global _BOUND_PORT, _BOUND_HOST
    global _ROSTER_PATH, _NOTES_PATH, _EXPENSES_PATH, _INBOX_PATH, _ENV_PATH
    parser = argparse.ArgumentParser(description="Auto Fleet internal ops dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--roster", type=Path, default=None)
    parser.add_argument("--notes", type=Path, default=None)
    parser.add_argument("--expenses", type=Path, default=None)
    parser.add_argument("--turo-inbox", type=Path, default=None)
    parser.add_argument("--env", type=Path, default=None, help="DIMO env file (never commit)")
    args = parser.parse_args(argv)

    _ROSTER_PATH = args.roster.resolve() if args.roster else None
    _NOTES_PATH = args.notes.resolve() if args.notes else None
    _EXPENSES_PATH = args.expenses.resolve() if args.expenses else None
    _INBOX_PATH = args.turo_inbox.resolve() if args.turo_inbox else None
    _ENV_PATH = args.env.resolve() if args.env else None
    _BOUND_PORT = int(args.port)
    _BOUND_HOST = str(args.host)

    server = ThreadingHTTPServer((args.host, args.port), AutoFleetHandler)
    _BOUND_HOST, _BOUND_PORT = server.server_address[0], int(server.server_address[1])
    url = f"http://{_BOUND_HOST}:{_BOUND_PORT}/"
    print(f"Auto Fleet dashboard → {url}")
    print(
        "API: /api/health /api/fleet /api/agent/fleet /api/turo-tasks /api/turo-inbox-media/"
    )
    print("Secrets: ~/.config/auto-fleet/env (not in repo)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
