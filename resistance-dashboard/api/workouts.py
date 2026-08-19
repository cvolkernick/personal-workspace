"""GET /api/workouts — last Turso sessions. POST is read-only (no fake write)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.workout._util import PREVIEW_READ_ONLY, require_user, write_json


def workouts_body(headers):
    user, err = require_user(headers)
    if err:
        return err
    from api.dashboard import _load_sessions

    sessions, errors, source = _load_sessions(str(user.get("id") or "default"))
    out = []
    for s in sessions or []:
        if hasattr(s, "to_dict"):
            out.append(s.to_dict())
        elif isinstance(s, dict):
            out.append(s)
    return 200, {
        "ok": True,
        "readonly": True,
        "sessions": out,
        "session_count": len(out),
        "source": source,
        "error": "; ".join(errors) if errors else None,
        "write": {
            "ok": False,
            "readonly": True,
            "path": None,
            "verified_on_readback": False,
        },
    }


def workouts_write(headers):
    user, err = require_user(headers)
    if err:
        return err
    return 403, dict(PREVIEW_READ_ONLY)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = workouts_body(self.headers)
        write_json(self, status, body)

    def do_POST(self) -> None:
        status, body = workouts_write(self.headers)
        write_json(self, status, body)

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
