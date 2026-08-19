"""GET /api/dashboard: Turso workouts + Google Health/Hidrate. No local db."""

from __future__ import annotations

import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from api.auth.session_util import json_bytes, query_first, session_from_headers, signing_secret

# Pi cold/force pull. Incremental 14d only applies when a cache exists; Vercel cache is none.
HEALTH_COLD_DAYS = 90


def _auth_required() -> dict:
    return {
        "ok": False,
        "error": "auth_required",
        "message": "Sign in with Google to view your data.",
        "login": "/api/auth/google/start",
    }


def _load_sessions(user_id: str) -> tuple[list, list[str], str]:
    from rt_dashboard.turso_repo import list_sessions_detailed

    errors: list[str] = []
    source = "turso"
    sessions = []
    try:
        sessions, notes = list_sessions_detailed(user_id)
        errors.extend(notes)
        if not sessions:
            sessions, notes = list_sessions_detailed("default")
            errors.extend(notes)
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
    days = HEALTH_COLD_DAYS
    try:
        health = GoogleHealthClient().fetch_health(days=days)
    except Exception as exc:  # noqa: BLE001
        health = HealthSnapshot(error=f"health_pull: {type(exc).__name__}")
        return health, errors
    try:
        health, meta = overlay_hidrate_hydration(health, days=days)
        hidrate_err = str((meta or {}).get("error") or "").strip()
        if hidrate_err:
            errors.append(f"hidrate: {hidrate_err[:160]}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hidrate: {type(exc).__name__}")
    return health, errors


def _civil_day(value) -> str:
    return str(value or "")[:10]


def _today_consumed(health, today: str) -> dict:
    """Logged kcal/P/C/F for local today. Targets/inventory may be unset."""
    today = _civil_day(today)
    if not today:
        return {}
    logs = [
        f
        for f in (health.food_logs or [])
        if _civil_day(getattr(f, "date", "")) == today
    ]
    day = None
    for n in health.nutrition or []:
        if _civil_day(getattr(n, "date", "")) == today:
            day = n.to_dict() if hasattr(n, "to_dict") else dict(n)
            break
    if day:
        out = {
            "date": today,
            "calories": day.get("calories"),
            "protein_g": day.get("protein_g"),
            "carbs_g": day.get("carbs_g"),
            "fat_g": day.get("fat_g"),
            "source": day.get("source") or "google_health",
            "food_log_count": len(logs),
        }
        if logs and out.get("calories") is None:
            out["calories"] = sum(float(getattr(f, "calories", 0) or 0) for f in logs)
            out["protein_g"] = sum(float(getattr(f, "protein_g", 0) or 0) for f in logs)
            out["carbs_g"] = sum(float(getattr(f, "carbs_g", 0) or 0) for f in logs)
            out["fat_g"] = sum(float(getattr(f, "fat_g", 0) or 0) for f in logs)
            out["source"] = "food_logs"
        return out
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


def request_tz_name(headers, query: str = "") -> str:
    """Viewer IANA zone: ?tz= or X-Viewer-TZ / X-Dashboard-TZ, else env fallback.

    Never uses process TZ (Vercel TZ=UTC). Garbage names are ignored.
    """
    from rt_dashboard.timeutil import resolve_tz_name

    raw = query_first(query, "tz")
    if not raw and headers:
        raw = str(
            headers.get("X-Viewer-TZ")
            or headers.get("X-Dashboard-TZ")
            or headers.get("x-viewer-tz")
            or headers.get("x-dashboard-tz")
            or ""
        ).strip()
    return resolve_tz_name(raw)


def dashboard_body(headers, query: str = "") -> tuple[int, dict]:
    if not signing_secret() or not session_from_headers(headers):
        return 401, _auth_required()

    from rt_dashboard.analytics import dashboard_payload
    from rt_dashboard.calorie_bars import build_calorie_bars_payload
    from rt_dashboard.coach import build_coach_payload
    from rt_dashboard.daily_plan_tasks import plan_preview
    from rt_dashboard.day_constraints import export_day_constraints_from_dashboard
    from rt_dashboard.hydration_bars import build_hydration_bars_payload
    from rt_dashboard.nutrition_store import load_workspace_targets
    from rt_dashboard.workout_store import (
        apply_goals_volume_caps,
        apply_rest_gate,
        build_training_pack,
        load_workspace_catalog,
        load_workspace_goals,
        next_session_brief,
        rest_gate,
    )
    from rt_dashboard.recovery import compute_recovery_status
    from rt_dashboard.sleep_battery import sleep_battery_from_fitdash_sleep
    from rt_dashboard.sleep_series import expand_sleep_calendar
    from rt_dashboard.timeutil import local_today_iso

    user = session_from_headers(headers) or {}
    t0 = time.perf_counter()
    sessions, sess_err, source = _load_sessions(str(user.get("id") or "default"))
    health, health_err = _load_health()
    tz_name = request_tz_name(headers, query)
    today = local_today_iso(tz_name)
    had_real_sleep = any(
        float(getattr(s, "sleep_hours", 0) or 0) > 0
        and str(getattr(s, "source", "") or "") != "implied_zero"
        for s in (health.sleep or [])
    )
    health.sleep = expand_sleep_calendar(
        health.sleep or [],
        as_of=today,
        window_days=HEALTH_COLD_DAYS,
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
        if _civil_day(getattr(f, "date", "")) == today
    ]
    burned_today = None
    for b in health.calories_burned or []:
        if str(getattr(b, "date", "") or "")[:10] == today:
            try:
                burned_today = float(getattr(b, "calories", None) or 0)
            except (TypeError, ValueError):
                burned_today = None
            break

    errors = list(sess_err + health_err)
    health_msg = str(getattr(health, "error", None) or "").strip()
    if health_msg and health_msg not in errors:
        errors.append(health_msg)

    targets, targets_src = load_workspace_targets()

    payload = dashboard_payload(sessions)
    payload["health"] = health.to_dict()
    payload["recovery"] = recovery_dict
    payload["sleep_battery"] = sleep_battery
    payload["nutrition_store"] = {
        "targets": targets,
        "inventory": {"ingredients": []},
        "sources": {"inventory": "unset", "targets": targets_src},
        "meal_plan": None,
        "food_logs": [f.to_dict() for f in (health.food_logs or [])],
        "food_logs_today": today_logs,
        "food_logs_recent": [f.to_dict() for f in (health.food_logs or [])[-80:]],
        "today_consumed": consumed or None,
        "last_nutrition_date": (
            max(
                (
                    _civil_day(getattr(n, "date", ""))
                    for n in (health.nutrition or [])
                    if _civil_day(getattr(n, "date", ""))
                ),
                default="",
            )
            or max(
                (
                    _civil_day(getattr(f, "date", ""))
                    for f in (health.food_logs or [])
                    if _civil_day(getattr(f, "date", ""))
                ),
                default="",
            )
            or None
        ),
    }
    try:
        payload["calorie_bars"] = build_calorie_bars_payload(
            today_consumed=consumed,
            targets=targets,
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
    from rt_dashboard.grok_planner import dashboard_plan_slots

    meal_plan, workout_plan = dashboard_plan_slots(str(user.get("id") or ""))
    payload["nutrition_store"]["meal_plan"] = meal_plan
    goals, goals_src = load_workspace_goals()
    catalog, catalog_src = load_workspace_catalog()
    # Frankenfit: catalog names/movements only. Set caps from goals, never default_sets=3.
    catalog = apply_goals_volume_caps(catalog, goals)
    gate = rest_gate(goals, recovery_dict)
    nxt = next_session_brief(sessions, goals)
    if isinstance(workout_plan, dict):
        workout_plan = dict(workout_plan)
    else:
        workout_plan = {}
    # Next PPL slot only when the rest gate is off. No empty lift slot on Rest.
    if gate["force_rest"]:
        workout_plan = apply_rest_gate(workout_plan, goals, recovery_dict)
        shown_next = None
        pack = build_training_pack(
            goals, catalog, sessions, next_brief=nxt, limit=5
        )
        pack["next_session_type"] = None
        pack["next_session_line"] = None
        pack["rest"] = True
    else:
        if not (workout_plan.get("exercises") or []):
            workout_plan["session_type"] = nxt["next_session_type"]
            existing = str(workout_plan.get("message") or "").strip()
            line = str(nxt.get("line") or "").strip()
            if line and line not in existing:
                workout_plan["message"] = f"{line}. {existing}".strip() if existing else line
            ctx = dict(workout_plan.get("context") or {})
            ctx["next_session_type"] = nxt["next_session_type"]
            ctx["last_session_type"] = nxt.get("last_session_type")
            workout_plan["context"] = ctx
        workout_plan = apply_rest_gate(workout_plan, goals, recovery_dict)
        shown_next = nxt["next_session_type"]
        pack = build_training_pack(
            goals, catalog, sessions, next_brief=nxt, limit=5
        )
    payload["workout_store"] = {
        "plan": workout_plan,
        "catalog": catalog,
        "goals": goals,
        "sources": {"catalog": catalog_src, "goals": goals_src},
        "next_session_type": shown_next,
        "training_pack": pack,
    }
    try:
        payload["coach"] = build_coach_payload(
            health=health,
            sessions=sessions,
            recovery=recovery,
            targets=targets,
            consumed=consumed,
            meal_plan=meal_plan,
            workout_plan=workout_plan,
            as_of=today,
            sleep_battery=sleep_battery,
            calorie_bars=payload.get("calorie_bars"),
            inventory_dark=True,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"coach: {type(exc).__name__}")
        payload["coach"] = {}
    if isinstance(payload.get("coach"), dict) and consumed:
        today_board = payload["coach"].setdefault("today", {})
        nut = today_board.setdefault("nutrition", {})
        prev = nut.get("consumed") if isinstance(nut.get("consumed"), dict) else {}
        if prev.get("calories") is None and consumed.get("calories") is not None:
            nut["consumed"] = consumed
        elif not prev:
            nut["consumed"] = consumed
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
        "timezone": tz_name,
        "local_today": today,
        "health_days": HEALTH_COLD_DAYS,
        "error": "; ".join(errors) if errors else None,
    }
    return 200, payload


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = urlparse(getattr(self, "path", "") or "").query
        status, body = dashboard_body(self.headers, query)
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
