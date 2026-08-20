"""GET /api/ask/status: grok_ask.auth_status JSON. Never Vercel HTML."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.auth.session_util import json_bytes, session_from_headers, signing_secret


def _auth_required() -> dict:
    return {
        "ok": False,
        "error": "auth_required",
        "message": "Sign in with Google to view your data.",
        "login": "/api/auth/google/start",
    }


def ask_status_body(headers) -> tuple[int, dict]:
    if not signing_secret() or not session_from_headers(headers):
        return 401, _auth_required()
    from rt_dashboard.grok_ask import auth_status

    user = session_from_headers(headers) or {}
    return 200, auth_status(user_id=str(user.get("id") or ""))


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = ask_status_body(self.headers)
        raw = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
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
