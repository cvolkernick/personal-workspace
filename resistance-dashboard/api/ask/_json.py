"""JSON-only Vercel helpers for /api/ask/*."""

from __future__ import annotations

from api.auth.session_util import json_bytes, session_from_headers, signing_secret


def auth_required() -> dict:
    return {
        "ok": False,
        "error": "auth_required",
        "message": "Sign in with Google to view your data.",
        "login": "/api/auth/google/start",
    }


def require_user(headers):
    if not signing_secret() or not session_from_headers(headers):
        return None, (401, auth_required())
    return session_from_headers(headers), None


def write_json(handler, status: int, body: dict, extra_headers=None) -> None:
    raw = json_bytes(body)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    for k, v in extra_headers or []:
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(raw)
