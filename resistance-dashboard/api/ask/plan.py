"""POST /api/ask/plan — Grok meal/workout, or honest empty. JSON only."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from api.ask._json import require_user, write_json
from api.dashboard import dashboard_body


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
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


def ask_plan_body(headers, payload=None):
    user, err = require_user(headers)
    if err:
        return err
    from rt_dashboard.grok_planner import (
        generate_grok_plans,
        honest_empty_meal,
        honest_empty_workout,
    )

    dash_status, dashboard = dashboard_body(headers)
    if dash_status != 200:
        return dash_status, dashboard
    nut = dashboard.get("nutrition_store") or {}
    rec = dashboard.get("recovery") or {}
    sessions = dashboard.get("sessions") or []
    wo = dashboard.get("workout_store") or {}
    from rt_dashboard.workout_store import brief_sessions

    pack = wo.get("training_pack") or {}
    brief = pack.get("sessions") or brief_sessions(sessions, limit=5)
    result = generate_grok_plans(
        str(user["id"]),
        targets=nut.get("targets") or {},
        consumed=nut.get("today_consumed") or {},
        food_logs_today=nut.get("food_logs_today") or [],
        recovery={
            "label": rec.get("label"),
            "score": rec.get("score"),
            "reasons": (rec.get("reasons") or [])[:4],
            "sparse": rec.get("sparse"),
            "rest_if_recovery_below": (wo.get("goals") or {}).get(
                "rest_if_recovery_below"
            )
            or 40,
        },
        sessions_brief=brief,
        goals=wo.get("goals") or {},
        catalog=wo.get("catalog") or {},
        next_session_type=wo.get("next_session_type") or pack.get("next_session_type"),
    )
    if not result.get("ok"):
        return 200, {
            "ok": False,
            "error": result.get("error"),
            "meal": result.get("meal") or honest_empty_meal(),
            "workout": result.get("workout") or honest_empty_workout(),
        }
    return 200, result


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        from api.workout._util import dispatch_client_route

        parsed = urlparse(getattr(self, "path", "") or "")
        payload = _read_json(self)
        routed = dispatch_client_route(
            self.headers,
            parsed.query,
            "POST",
            payload=payload,
            path=parsed.path,
        )
        if routed is not None:
            status, body = routed
        else:
            status, body = ask_plan_body(self.headers, payload)
        write_json(self, status, body)

    def do_GET(self) -> None:
        from api.workout._util import dispatch_client_route

        parsed = urlparse(getattr(self, "path", "") or "")
        routed = dispatch_client_route(
            self.headers, parsed.query, "GET", payload={}, path=parsed.path
        )
        if routed is not None:
            status, body = routed
            write_json(self, status, body)
            return
        write_json(self, 405, {"ok": False, "error": "method_not_allowed"})

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
