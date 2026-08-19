"""POST /api/ask/plan — Grok meal/workout, or honest empty. JSON only."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

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
    brief = []
    for s in sessions[:8]:
        if not isinstance(s, dict):
            continue
        brief.append(
            {
                "date": s.get("date"),
                "session_type": s.get("session_type") or s.get("type"),
                "exercises": [
                    {
                        "name": ex.get("name"),
                        "sets": ex.get("sets"),
                        "weight_lbs": ex.get("weight_lbs"),
                        "reps": ex.get("reps"),
                    }
                    for ex in (s.get("exercises") or [])[:6]
                    if isinstance(ex, dict)
                ],
            }
        )
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
        },
        sessions_brief=brief,
        goals=wo.get("goals") or {},
        catalog=wo.get("catalog") or {},
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
        status, body = ask_plan_body(self.headers, _read_json(self))
        write_json(self, status, body)

    def do_GET(self) -> None:
        write_json(self, 405, {"ok": False, "error": "method_not_allowed"})

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
