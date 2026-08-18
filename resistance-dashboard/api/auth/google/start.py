"""GET /api/auth/google/start — 302 to Google, or honest JSON if env is missing."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlencode

from api.auth.session_util import (
    AUTH_URL,
    LOGIN_SCOPES,
    json_bytes,
    make_state,
    missing_oauth_env,
    redirect_uri,
)

import os


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        missing = missing_oauth_env()
        if missing:
            raw = json_bytes(
                {
                    "ok": False,
                    "error": "missing_env",
                    "missing": missing,
                    "role": "vercel-preview",
                    "oauth": "unproven",
                }
            )
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        params = {
            "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": " ".join(LOGIN_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "state": make_state(),
        }
        loc = AUTH_URL + "?" + urlencode(params)
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return
