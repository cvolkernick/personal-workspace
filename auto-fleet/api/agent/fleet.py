"""Vercel-style GET /api/agent/fleet — token + published snapshot only.

Helm can hit this without Tailscale when the snapshot is published
(``AUTO_FLEET_AGENT_SNAPSHOT`` or ``AUTO_FLEET_AGENT_SNAPSHOT_JSON``).
Cookie-less / token-less public is 401. No write surfaces.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

PKG_DIR = Path(__file__).resolve().parents[2]
ROOT = PKG_DIR.parent
for path in (str(ROOT), str(PKG_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from agent_fleet import handle_agent_fleet_http  # noqa: E402


def _headers_map(raw) -> dict[str, str]:
    if raw is None:
        return {}
    if hasattr(raw, "items"):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def agent_fleet_body(headers, client_host: str | None = None) -> tuple[int, dict[str, Any]]:
    """Vercel / public export: token required. Loopback does not apply."""
    return handle_agent_fleet_http(headers, client_host="")


class handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, X-Auto-Fleet-Service-Token, Content-Type",
        )
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        code, payload = agent_fleet_body(self.headers, "")
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
