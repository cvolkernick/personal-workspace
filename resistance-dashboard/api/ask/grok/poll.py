"""GET /api/ask/grok/poll — pending|approved|denied|expired. Never tokens."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.ask._json import require_user, write_json
from api.auth.session_util import cookie_from_header


def grok_poll_body(headers):
    user, err = require_user(headers)
    if err:
        return err[0], err[1], []
    from rt_dashboard.grok_oauth import (
        DEVICE_COOKIE,
        device_clear_cookie,
        poll_device_code,
        read_device_ticket,
    )
    from rt_dashboard.grok_sessions import save_grok_session

    cookies = cookie_from_header(headers.get("Cookie") or "")
    device_code = read_device_ticket(cookies.get(DEVICE_COOKIE) or "")
    extra = []
    if not device_code:
        return 200, {"ok": True, "status": "expired", "error": "no_pending_login"}, extra
    result = poll_device_code(device_code)
    tokens = result.pop("_tokens", None)
    if result.get("status") == "approved" and tokens:
        try:
            save_grok_session(str(user["id"]), tokens)
        except Exception as exc:  # noqa: BLE001
            extra.append(("Set-Cookie", device_clear_cookie()))
            return 502, {
                "ok": False,
                "status": "approved",
                "error": f"store_failed: {type(exc).__name__}",
                "connected": False,
            }, extra
        extra.append(("Set-Cookie", device_clear_cookie()))
        return 200, {
            "ok": True,
            "status": "approved",
            "connected": True,
            "email": result.get("email") or tokens.get("email") or None,
        }, extra
    if result.get("status") in ("denied", "expired"):
        extra.append(("Set-Cookie", device_clear_cookie()))
    public = {
        "ok": bool(result.get("ok")),
        "status": result.get("status") or "pending",
    }
    if result.get("error"):
        public["error"] = result["error"]
    if result.get("email"):
        public["email"] = result["email"]
    return 200, public, extra


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body, extra = grok_poll_body(self.headers)
        write_json(self, status, body, extra)

    def do_POST(self) -> None:
        write_json(self, 405, {"ok": False, "error": "method_not_allowed"})

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
