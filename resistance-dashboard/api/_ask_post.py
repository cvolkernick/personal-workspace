"""POST /api/ask: grok_ask.ask_about_dashboard. Never Vercel HTML."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from api.auth.session_util import json_bytes, session_from_headers, signing_secret


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

    user = session_from_headers(headers) or {}
    uid = str(user.get("id") or "")
    grok = auth_status(user_id=uid)
    if not grok.get("ok"):
        return 200, grok

    question = str(payload.get("question") or payload.get("q") or "").strip()
    history = payload.get("history")
    if not isinstance(history, list):
        history = None

    from api.dashboard import dashboard_body

    dash_status, dashboard = dashboard_body(headers)
    if dash_status != 200:
        return dash_status, dashboard

    try:
        result = ask_about_dashboard(question, dashboard, history=history, user_id=uid)
    except GrokAskError as exc:
        code = exc.status if exc.status and 400 <= int(exc.status) < 600 else 502
        return int(code), {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return 502, {"ok": False, "error": f"ask_failed: {type(exc).__name__}"}

    out = {"ok": True}
    out.update(result)
    return 200, out


def _write(handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
    raw = json_bytes(body)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            status, body = ask_body(self.headers, _read_json_body(self))
        except Exception as exc:  # noqa: BLE001
            status, body = 500, {"ok": False, "error": f"ask_failed: {type(exc).__name__}"}
        _write(self, status, body)

    def do_GET(self) -> None:
        _write(self, 405, {"ok": False, "error": "method_not_allowed"})

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
