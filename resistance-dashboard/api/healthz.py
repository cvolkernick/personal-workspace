"""Preview-only FitDash liveness. Prod healthz stays on Pi server.py."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler


def healthz_body() -> dict:
    return {
        "ok": True,
        "service": "fitdash",
        "role": "vercel-preview",
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        raw = json.dumps(healthz_body()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        return
