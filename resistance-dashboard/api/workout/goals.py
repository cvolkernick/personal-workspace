"""GET/POST /api/workout/goals — goals.json + caps. Cookie required. Read-only."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from api.ask._json import require_user, write_json


def goals_body(headers):
    user, err = require_user(headers)
    if err:
        return err
    from rt_dashboard.workout_store import load_workspace_goals

    goals, src = load_workspace_goals()
    return 200, {
        "ok": True,
        "goals": goals,
        "source": src,
        "readonly": True,
        "write": {"ok": False, "readonly": True},
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = goals_body(self.headers)
        write_json(self, status, body)

    def do_POST(self) -> None:
        status, body = goals_body(self.headers)
        write_json(self, status, body)

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
