"""Shared JSON + auth helpers for /api/workout* adapters. Never a route file."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from api.ask._json import auth_required, require_user, write_json

PREVIEW_READ_ONLY = {
    "ok": False,
    "error": "preview_read_only",
    "message": "Vercel preview is read-only. Log workouts and edit goals on the Pi FitDash.",
    "readonly": True,
}


def read_json(handler: BaseHTTPRequestHandler) -> dict:
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


__all__ = [
    "PREVIEW_READ_ONLY",
    "auth_required",
    "read_json",
    "require_user",
    "write_json",
]
