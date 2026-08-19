"""POST /api/ask: grok_ask.ask_about_dashboard. Never Vercel HTML."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from api.auth.session_util import json_bytes, session_from_headers, signing_secret
from api.dashboard import dashboard_body


def _auth_required() -> dict:
    return {
        "ok": False,
        "error": "auth_required",
        "message": "Sign in with Google to view your data.",
        "login": "/api/auth/google/start",
    }


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def ask_body(headers, payload: dict) -> tuple[int, dict]:
    if not signing_secret() or not session_from_headers(headers):
        return 401, _auth_required()

    from rt_dashboard.grok_ask import GrokAskError, ask_about_dashboard, auth_status

    grok = auth_status()
    if not grok.get("ok"):
        return 200, grok

    question = str(payload.get("question") or payload.get("q") or "").strip()
    history = payload.get("history")
    if not isinstance(history, list):
        history = None

    dash_status, dashboard = dashboard_body(headers)
    if dash_status != 200:
        return dash_status, dashboard

    try:
        result = ask_about_dashboard(question, dashboard, history=history)
    except GrokAskError as exc:
        code = exc.status if exc.status and 400 <= int(exc.status) < 600 else 502
        return int(code), {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return 502, {"ok": False, "error": f"ask_failed: {type(exc).__name__}"}

    out = {"ok": True}
    out.update(result)
    return 200, out


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        status, body = ask_body(self.headers, _read_json_body(self))
        raw = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        raw = json_bytes({"ok": False, "error": "method_not_allowed"})
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
