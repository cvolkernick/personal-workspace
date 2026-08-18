"""GET /api/auth/logout — clear preview session cookie."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.auth.session_util import session_clear_cookie


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Set-Cookie", session_clear_cookie())
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return
