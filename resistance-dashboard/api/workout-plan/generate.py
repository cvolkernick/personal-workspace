"""POST /api/workout-plan/generate — same Grok/honest-empty plan as /api/ask/plan."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.ask._json import require_user, write_json


def generate_body(headers, payload=None):
    user, err = require_user(headers)
    if err:
        return err
    from api.ask.plan import ask_plan_body

    status, body = ask_plan_body(headers, payload)
    if status != 200:
        return status, body
    plan = body.get("workout") if isinstance(body, dict) else None
    if not isinstance(plan, dict):
        plan = {}
    return 200, {
        "ok": True,
        "plan": plan,
        "error": body.get("error") if isinstance(body, dict) else None,
        "meal": body.get("meal") if isinstance(body, dict) else None,
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        status, body = generate_body(self.headers)
        write_json(self, status, body)

    def do_GET(self) -> None:
        status, body = generate_body(self.headers)
        write_json(self, status, body)

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
