"""POST /api/ask/grok/disconnect — delete sealed Turso row."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.ask._json import require_user, write_json


def grok_disconnect_body(headers):
    user, err = require_user(headers)
    if err:
        return err
    from rt_dashboard.grok_sessions import delete_grok_session

    try:
        delete_grok_session(str(user["id"]))
    except Exception as exc:  # noqa: BLE001
        return 502, {"ok": False, "error": f"disconnect_failed: {type(exc).__name__}"}
    return 200, {"ok": True, "connected": False}


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        status, body = grok_disconnect_body(self.headers)
        write_json(self, status, body)

    def do_GET(self) -> None:
        write_json(self, 405, {"ok": False, "error": "method_not_allowed"})

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
