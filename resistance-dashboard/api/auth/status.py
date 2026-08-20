"""GET /api/auth/status — cookie identity on preview, else honest logged-out."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.auth.session_util import (
    json_bytes,
    missing_oauth_env,
    public_base_url,
    redirect_uri,
    session_from_headers,
    signing_secret,
)


def auth_status_body(headers) -> dict:
    missing = missing_oauth_env()
    user = session_from_headers(headers) if signing_secret() else None
    return {
        "ok": True,
        "authenticated": bool(user),
        "auth_required": True,
        "user": user,
        "public_url": public_base_url() or None,
        "oauth_redirect_uri": redirect_uri() if not missing else None,
        "master_key_ready": False,
        "role": "vercel-preview",
        "oauth": "unproven" if missing else "env_present",
        "missing": missing or None,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        raw = json_bytes(auth_status_body(self.headers))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        return
