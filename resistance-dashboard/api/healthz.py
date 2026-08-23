"""FitDash Vercel liveness. Role and git SHA come from Vercel build env."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler

_ROLE_BY_VERCEL_ENV = {
    "production": "production",
    "preview": "vercel-preview",
    "development": "development",
}


def _vercel_role() -> str:
    raw = (os.environ.get("VERCEL_ENV") or "").strip().lower()
    if raw in _ROLE_BY_VERCEL_ENV:
        return _ROLE_BY_VERCEL_ENV[raw]
    # Honest: do not invent production (or preview) when the platform env is unset.
    return raw or "unknown"


def _git_sha() -> str | None:
    raw = (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "").strip()
    return raw or None


def healthz_body() -> dict:
    return {
        "ok": True,
        "service": "fitdash",
        "role": _vercel_role(),
        "gitSha": _git_sha(),
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


# Some Vercel Python builders look for a module-level name.
app = handler
application = handler
