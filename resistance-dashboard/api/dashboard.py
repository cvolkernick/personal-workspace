"""GET /api/dashboard: Turso workouts + Google Health/Hidrate. No local db."""

from __future__ import annotations

import os
import time
from http.server import BaseHTTPRequestHandler

from api.auth.session_util import json_bytes, session_from_headers, signing_secret


def _auth_required() -> dict:
    return {
        "ok": False,
        "error": "auth_required",
        "message": "Sign in with Google to view your data.",
        "login": "/api/auth/google/start",
    }


def _load_sessions(user_id: str) -> tuple[list, list[str], str]:
    from rt_dashboard.turso_repo import list_sessions

    errors: list[str] = []
    source = "turso"
    sessions = []
    try:
        sessions = list_sessions(user_id)
        if not sessions:
            sessions = list_sessions("default")
            if sessions:
                source = "turso-default"
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sqlite_pull: {type(exc).__name__}")
    return sessions, errors, source


def _load_health():
    from rt_dashboard.google_health import GoogleHealthClient
    from rt_dashboard.hidrate_client import overlay_hidrate_hydration
    from rt_dashboard.models import HealthSnapshot

    errors: list[str] = []
    days = 14
    try:
        health = GoogleHealthClient().fetch_health(days=days)
    except Exception as exc:  # noqa: BLE001
        health = HealthSnapshot(error=f"health_pull: {type(exc).__name__}")
        errors.append("health_pull")
        return health, errors
    try:
        health, meta = overlay_hidrate_hydration(health, days=days)
        if meta.get("error"):
            errors.append("hidrate")
    except Exception:  # noqa: BLE001
        errors.append("hidrate")
    return health, errors


def dashboard_body(headers) -> tuple[int, dict]:
    if not os.environ.get("DASHBOARD_TZ") and not os.environ.get("TZ"):
        os.environ["DASHBOARD_TZ"] = "America/New_York"
    if not signing_secret() or not session_from_headers(headers):
        return 401, _auth_required()

    from rt_dashboard.recovery import compute_recovery_status
    from rt_dashboard.timeutil import local_today_iso, local_tz_name

    user = session_from_headers(headers) or {}
    t0 = time.perf_counter()
    sessions, sess_err, source = _load_sessions(str(user.get("id") or "default"))
    health, health_err = _load_health()
    today = local_today_iso()
    recovery = compute_recovery_status(
        weight=health.weight or [],
        sleep=health.sleep or [],
        sessions=sessions,
        as_of=today,
    )
    today_logs = [
        f.to_dict()
        for f in (health.food_logs or [])
        if str(getattr(f, "date", "") or "")[:10] == today
    ]
    errors = sess_err + health_err
    if getattr(health, "error", None):
        errors.append("health")
    payload = {
        "sessions": [s.to_dict() for s in sessions],
        "health": health.to_dict(),
        "recovery": recovery.to_dict(),
        "nutrition_store": {
            "targets": {},
            "inventory": {"ingredients": []},
            "sources": {"inventory": "unset", "targets": "unset"},
            "meal_plan": None,
            "food_logs": [f.to_dict() for f in (health.food_logs or [])],
            "food_logs_today": today_logs,
            "food_logs_recent": [f.to_dict() for f in (health.food_logs or [])[-80:]],
            "today_consumed": None,
        },
        "coach": {},
        "meta": {
            "role": "vercel-preview",
            "source": source,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "load_ms": int((time.perf_counter() - t0) * 1000),
            "cache": "none",
            "timezone": local_tz_name(),
            "local_today": today,
            "error": "; ".join(errors) if errors else None,
        },
    }
    return 200, payload


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, body = dashboard_body(self.headers)
        raw = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_HEAD(self) -> None:
        self.send_response(501)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
