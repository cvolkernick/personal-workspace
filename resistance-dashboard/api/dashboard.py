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
    days = 30
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


def _today_consumed(health, today: str) -> dict:
    for n in health.nutrition or []:
        if str(getattr(n, "date", "") or "")[:10] == today:
            return n.to_dict()
    logs = [
        f
        for f in (health.food_logs or [])
        if str(getattr(f, "date", "") or "")[:10] == today
    ]
    if not logs:
        return {}
    return {
        "date": today,
        "calories": sum(float(getattr(f, "calories", 0) or 0) for f in logs),
        "protein_g": sum(float(getattr(f, "protein_g", 0) or 0) for f in logs),
        "carbs_g": sum(float(getattr(f, "carbs_g", 0) or 0) for f in logs),
        "fat_g": sum(float(getattr(f, "fat_g", 0) or 0) for f in logs),
        "source": "food_logs",
        "food_log_count": len(logs),
    }


def dashboard_body(headers) -> tuple[int, dict]:
    if not os.environ.get("DASHBOARD_TZ") and not os.environ.get("TZ"):
        os.environ["DASHBOARD_TZ"] = "America/New_York"
    if not signing_secret() or not session_from_headers(headers):
        return 401, _auth_required()

    from rt_dashboard.analytics import dashboard_payload
    from rt_dashboard.calorie_bars import build_calorie_bars_payload
    from rt_dashboard.coach import build_coach_payload
    from rt_dashboard.daily_plan_tasks import plan_preview
    from rt_dashboard.day_constraints import export_day_constraints_from_dashboard
    from rt_dashboard.hydration_bars import build_hydration_bars_payload
    from rt_dashboard.recovery import compute_recovery_status
    from rt_dashboard.sleep_battery import sleep_battery_from_fitdash_sleep
    from rt_dashboard.sleep_series import expand_sleep_calendar
    from rt_dashboard.timeutil import local_today_iso, local_tz_name

    user = session_from_headers(headers) or {}
    t0 = time.perf_counter()
    sessions, sess_err, source = _load_sessions(str(user.get("id") or "default"))
    health, health_err = _load_health()
    today = local_today_iso()
    had_real_sleep = any(
        float(getattr(s, "sleep_hours", 0) or 0) > 0
        and str(getattr(s, "source", "") or "") != "implied_zero"
        for s in (health.sleep or [])
    )
    health.sleep = expand_sleep_calendar(
        health.sleep or [],
        as_of=today,
        window_days=90,
        fill_hours=0.0,
        fill_source="implied_zero",
    )
    recovery = compute_recovery_status(
        weight=health.weight or [],
        sleep=health.sleep or [],
        sessions=sessions,
        as_of=today,
    )
    sleep_battery = sleep_battery_from_fitdash_sleep(
        [s for s in (health.sleep or []) if float(s.sleep_hours or 0) > 0],
        sleep_target_hours=8.0,
        sleep_intervals=list(getattr(health, "sleep_intervals", None) or []),
    )
    recovery_dict = recovery.to_dict()
    recovery_dict["sleep_battery"] = sleep_battery
    recovery_dict["sparse"] = not had_real_sleep

    consumed = _today_consumed(health, today)
    today_logs = [
        f.to_dict()
        for f in (health.food_logs or [])
        if str(getattr(f, "date", "") or "")[:10] == today
    ]
    burned_today = None
    for b in health.calories_burned or []:
        if str(getattr(b, "date", "") or "")[:10] == today:
            try:
                burned_today = float(getattr(b, "calories", None) or 0)
            except (TypeError, ValueError):
                burned_today = None
            break

    errors = sess_err + health_err
    if getattr(health, "error", None):
        errors.append("health")

    payload = dashboard_payload(sessions)
    payload["health"] = health.to_dict()
    payload["recovery"] = recovery_dict
    payload["sleep_battery"] = sleep_battery
    payload["nutrition_store"] = {
        "targets": {},
        "inventory": {"ingredients": []},
        "sources": {"inventory": "unset", "targets": "unset"},
        "meal_plan": None,
        "food_logs": [f.to_dict() for f in (health.food_logs or [])],
        "food_logs_today": today_logs,
        "food_logs_recent": [f.to_dict() for f in (health.food_logs or [])[-80:]],
        "today_consumed": consumed or None,
    }
    try:
        payload["calorie_bars"] = build_calorie_bars_payload(
            today_consumed=consumed,
            targets={},
            sleep_battery=sleep_battery,
            calories_burned_today=burned_today,
            food_logs=health.food_logs or [],
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"calorie_bars: {type(exc).__name__}")
        payload["calorie_bars"] = {"pacing": None, "delta": None}
    try:
        payload["hydration_bars"] = build_hydration_bars_payload(
            hydration=health.hydration or [],
            weight=health.weight or [],
            sleep_battery=sleep_battery,
            as_of=today,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hydration_bars: {type(exc).__name__}")
        payload["hydration_bars"] = {"pacing": None}
    try:
        payload["coach"] = build_coach_payload(
            health=health,
            sessions=sessions,
            recovery=recovery,
            targets={},
            consumed=consumed,
            meal_plan={},
            workout_plan={},
            as_of=today,
            sleep_battery=sleep_battery,
            calorie_bars=payload.get("calorie_bars"),
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"coach: {type(exc).__name__}")
        payload["coach"] = {}
    payload["workout_store"] = {
        "plan": None,
        "catalog": None,
        "goals": None,
        "sources": {"catalog": "unset", "goals": "unset"},
    }
    today_board = {}
    if isinstance(payload.get("coach"), dict):
        today_board = payload["coach"].get("today") or {}
    try:
        payload["daily_tasks"] = plan_preview(today_board, day=today)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"daily_tasks: {type(exc).__name__}")
        payload["daily_tasks"] = None
    try:
        payload["day_constraints"] = export_day_constraints_from_dashboard(
            payload,
            workspace=None,
            sessions=sessions,
            sleep=health.sleep or [],
            write=False,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"day_constraints: {type(exc).__name__}")
        payload["day_constraints"] = None
    payload["meta"] = {
        "role": "vercel-preview",
        "source": source,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "load_ms": int((time.perf_counter() - t0) * 1000),
        "cache": "none",
        "timezone": local_tz_name(),
        "local_today": today,
        "error": "; ".join(errors) if errors else None,
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
