"""Preview-only session probe. Always logged out. No Google, no cookies."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler


def auth_status_body() -> dict:
    public = (os.environ.get("VERCEL_URL") or os.environ.get("FITDASH_PUBLIC_URL") or "").strip()
    if public and not public.startswith("http"):
        public = "https://" + public
    return {
        "ok": True,
        "authenticated": False,
        "auth_required": True,
        "user": None,
        "public_url": public.rstrip("/") if public else None,
        "oauth_redirect_uri": None,
        "master_key_ready": False,
        "role": "vercel-preview",
        "oauth": "unproven",
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        raw = json.dumps(auth_status_body()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        return
