"""POST /api/ask/grok/start — device code. JSON only. Never client_secret."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.ask._json import require_user, write_json


def grok_start_body(headers):
    user, err = require_user(headers)
    if err:
        return err[0], err[1], []
    from rt_dashboard.grok_oauth import (
        device_set_cookie,
        make_device_ticket,
        public_start_payload,
        start_device_code,
    )

    started = start_device_code()
    extra = []
    if started.get("ok") and started.get("_device_code"):
        ticket = make_device_ticket(
            str(started["_device_code"]), int(started.get("expires_in") or 1800)
        )
        extra.append(
            ("Set-Cookie", device_set_cookie(ticket, int(started.get("expires_in") or 1800)))
        )
    body = public_start_payload(started)
    body["unofficial"] = True
    body["client"] = "grok-cli-device-code"
    return (200 if body.get("ok") else 502), body, extra


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        status, body, extra = grok_start_body(self.headers)
        write_json(self, status, body, extra)

    def do_GET(self) -> None:
        write_json(self, 405, {"ok": False, "error": "method_not_allowed"})

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
