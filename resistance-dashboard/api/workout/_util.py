"""Client workout-route helpers. Underscore file — not a Vercel function."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from api.ask._json import auth_required, require_user, write_json
from api.auth.session_util import query_first

PREVIEW_READ_ONLY = {
    "ok": False,
    "error": "preview_read_only",
    "message": "Vercel preview is read-only. Log workouts and edit goals on the Pi FitDash.",
    "readonly": True,
}

_ROUTES = (
    "goals",
    "available",
    "workouts",
    "generate",
    "inv_add",
    "inv_remove",
    "inv_stock",
    "inv_update",
    "eq_add",
    "eq_remove",
    "eq_update",
    "meal_plan",
    "meal_generate",
    "refresh",
    "daily_tasks",
    "daily_tasks_complete",
    "agent_today",
)
_INV_ROUTES = ("inv_add", "inv_remove", "inv_stock", "inv_update")
_EQ_ROUTES = ("eq_add", "eq_remove", "eq_update")
_MEAL_ROUTES = ("meal_plan", "meal_generate")


def read_json(handler: BaseHTTPRequestHandler) -> dict:
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


def client_route_name(headers, query: str = "", path: str = "") -> str:
    """Resolve client route from rewrite ?_r= or the original URL."""
    route = query_first(query, "_r")
    if route in _ROUTES:
        return route
    blob = " ".join(
        [
            path or "",
            query or "",
            str((headers or {}).get("x-invoke-path") or ""),
            str((headers or {}).get("X-Invoke-Path") or ""),
            str((headers or {}).get("x-matched-path") or ""),
            str((headers or {}).get("X-Matched-Path") or ""),
            str((headers or {}).get("x-vercel-original-path") or ""),
        ]
    )
    if "/workout/goals" in blob:
        return "goals"
    if "/workout/exercise/available" in blob:
        return "available"
    if "/workout-plan/generate" in blob:
        return "generate"
    if "/api/workouts" in blob:
        return "workouts"
    if "/api/inventory/update" in blob:
        return "inv_update"
    if "/api/inventory/add" in blob:
        return "inv_add"
    if "/api/inventory/remove" in blob:
        return "inv_remove"
    if "/api/inventory/stock" in blob:
        return "inv_stock"
    if "/api/equipment/update" in blob:
        return "eq_update"
    if "/api/equipment/add" in blob:
        return "eq_add"
    if "/api/equipment/remove" in blob:
        return "eq_remove"
    if "/api/meal-plan/generate" in blob:
        return "meal_generate"
    if "/api/meal-plan" in blob:
        return "meal_plan"
    if "/api/refresh" in blob:
        return "refresh"
    if "/api/daily-tasks/complete" in blob:
        return "daily_tasks_complete"
    if "/api/daily-tasks" in blob:
        return "daily_tasks"
    if "/api/agent/today" in blob:
        return "agent_today"
    return ""


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


def available_body(headers):
    user, err = require_user(headers)
    if err:
        return err
    from rt_dashboard.workout_store import (
        apply_goals_volume_caps,
        catalog_names,
        load_workspace_catalog,
        load_workspace_goals,
    )

    goals, goals_src = load_workspace_goals()
    catalog, catalog_src = load_workspace_catalog()
    catalog = apply_goals_volume_caps(catalog, goals)
    return 200, {
        "ok": True,
        "readonly": True,
        "catalog": catalog,
        "names": catalog_names(catalog),
        "sources": {"catalog": catalog_src, "goals": goals_src},
        "write": {"ok": False, "readonly": True},
    }


def workouts_body(headers):
    user, err = require_user(headers)
    if err:
        return err
    from api.dashboard import _load_sessions
    from rt_dashboard.turso_http import turso_enabled

    sessions, errors, source = _load_sessions(str(user.get("id") or "default"))
    out = []
    for s in sessions or []:
        if hasattr(s, "to_dict"):
            out.append(s.to_dict())
        elif isinstance(s, dict):
            out.append(s)
    writable = turso_enabled()
    return 200, {
        "ok": True,
        "readonly": not writable,
        "sessions": out,
        "session_count": len(out),
        "source": source,
        "error": "; ".join(errors) if errors else None,
        "write": {
            "ok": False,
            "readonly": not writable,
            "path": "turso" if writable else None,
            "verified_on_readback": False,
        },
    }


def _write_denied(headers):
    user, err = require_user(headers)
    if err:
        return err
    return 403, dict(PREVIEW_READ_ONLY)


def goals_write(headers):
    return _write_denied(headers)


def available_write(headers):
    return _write_denied(headers)


def workouts_write(headers, payload=None):
    """POST /api/workouts — Turso upsert. Same shape as inventory_write.

    Cookie-less 401. Failed persist is 5xx. Missing Turso env is a clear
    error (not preview_read_only, not a fake ok).
    """
    user, err = require_user(headers)
    if err:
        return err
    payload = payload if isinstance(payload, dict) else {}
    uid = str(user.get("id") or "default")
    try:
        from api.dashboard import _load_sessions
        from rt_dashboard.pr_detect import apply_auto_prs
        from rt_dashboard.turso_repo import save_preview_session
        from rt_dashboard.workout_log import parse_log_body

        session = parse_log_body(payload)
        history, _hist_err, _hist_src = _load_sessions(uid)
        apply_auto_prs(session, history)
        result = save_preview_session(uid, session)
        sessions, errors, source = _load_sessions(uid)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or type(exc).__name__
        if "turso env missing" in msg:
            return 503, {
                "ok": False,
                "error": "turso_env_missing",
                "message": (
                    "Workout log needs TURSO_DATABASE_URL and TURSO_AUTH_TOKEN."
                ),
            }
        return 500, {
            "ok": False,
            "error": msg,
            "write": {"ok": False, "source": "turso"},
        }
    pr_names = [e.name for e in session.exercises if e.is_pr]
    head = []
    for s in (sessions or [])[:5]:
        if hasattr(s, "to_dict"):
            head.append(s.to_dict())
        elif isinstance(s, dict):
            head.append(s)
    return 200, {
        "ok": True,
        "write": result,
        "source": source,
        "session_count": len(sessions or []),
        "sessions_head": head,
        "auto_prs": pr_names,
        "session": session.to_dict(),
        "error": "; ".join(errors) if errors else None,
    }


def generate_body(headers, payload=None):
    """Same Grok/honest-empty workout as /api/ask/plan, keyed as plan."""
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


def meal_plan_body(headers, payload=None):
    """GET/POST /api/meal-plan and /api/meal-plan/generate — Pi generate_meal_plan."""
    user, err = require_user(headers)
    if err:
        return err
    from api.dashboard import dashboard_body

    status, dashboard = dashboard_body(headers)
    if status != 200:
        return status, dashboard
    nut = dashboard.get("nutrition_store") or {}
    plan = nut.get("meal_plan") or {}
    src = nut.get("sources") or {}
    return 200, {
        "ok": True,
        "plan": plan,
        "action": "refresh_meal_plan",
        "sources": {
            "inventory": src.get("inventory"),
            "inventory_sot": src.get("inventory_sot"),
            "inventory_fallback": src.get("inventory_fallback"),
        },
    }


def refresh_body(headers, payload=None):
    """GET/POST /api/refresh — Pi reloads dashboard (which regenerates the meal plan)."""
    user, err = require_user(headers)
    if err:
        return err
    from api.dashboard import dashboard_body

    return dashboard_body(headers)


def stamp_quest_list_ids(daily):
    """Copy a known list_id onto leaves that already have a task_id.

    Does not invent quests or task ids. Vercel/GT payloads sometimes hoist
    list_id to the group or daily root only; Today needs it on the leaf.
    """
    if not isinstance(daily, dict):
        return daily
    root_lid = str(daily.get("list_id") or "").strip() or None
    for group in daily.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_lid = str(group.get("list_id") or "").strip() or root_lid
        if group_lid and not group.get("list_id"):
            group["list_id"] = group_lid
        for key in ("items", "open_items"):
            for item in group.get(key) or []:
                if not isinstance(item, dict):
                    continue
                if not item.get("task_id"):
                    continue
                if not str(item.get("list_id") or "").strip() and group_lid:
                    item["list_id"] = group_lid
    return daily


def daily_tasks_body(headers, payload=None):
    """GET /api/daily-tasks — ensure_daily_tasks against the GT Fitness list.

    Vercel uses the Google login session (Tasks scope). Pi keeps the file token.
    Missing Tasks permission fails honest (error + local leaves, no invented ids).
    """
    user, err = require_user(headers)
    if err:
        return err
    from api.auth.session_util import session_google_from_headers
    from api.dashboard import dashboard_body
    from rt_dashboard.daily_plan_tasks import ensure_daily_tasks
    from rt_dashboard.gtasks_session import bound_session_google

    status, dashboard = dashboard_body(headers)
    if status != 200:
        return status, dashboard
    today = ((dashboard.get("coach") or {}).get("today")) or {}
    day = today.get("date") or (dashboard.get("meta") or {}).get("local_today")
    google = session_google_from_headers(headers) or {}
    with bound_session_google(google):
        result = stamp_quest_list_ids(ensure_daily_tasks(today, day=day))
    if not result.get("ok"):
        return 200, {
            "ok": False,
            "error": result.get("error") or "Google Tasks not configured",
            "source": result.get("source") or "local_preview",
            "daily_tasks": result,
        }
    return 200, {"ok": True, "daily_tasks": result}


def daily_tasks_complete_body(headers, payload=None, method="POST"):
    """POST /api/daily-tasks/complete — Pi complete_leaf on the GT Fitness list.

    Same signed-in gate as GET /api/daily-tasks. Failed complete is 4xx/5xx
    JSON (not a silent 200). Cookie-less is 401, never HTML 404.
    """
    user, err = require_user(headers)
    if err:
        return err
    if (method or "POST").upper() != "POST":
        return 405, {"ok": False, "error": "method_not_allowed"}
    payload = payload if isinstance(payload, dict) else {}
    list_id = str(payload.get("list_id") or "").strip()
    task_id = str(payload.get("task_id") or "").strip()
    completed = payload.get("completed", True)
    if isinstance(completed, str):
        completed = completed.lower() in ("1", "true", "yes")
    parent_id = payload.get("parent_id")
    parent_id = str(parent_id).strip() if parent_id else None
    sibling_all_done = payload.get("sibling_all_done")
    from api.auth.session_util import session_google_from_headers
    from rt_dashboard.daily_plan_tasks import complete_leaf
    from rt_dashboard.gtasks_session import bound_session_google

    google = session_google_from_headers(headers) or {}
    try:
        with bound_session_google(google):
            result = complete_leaf(
                list_id,
                task_id,
                completed=bool(completed),
                parent_id=parent_id,
                sibling_all_done=sibling_all_done
                if sibling_all_done is None
                else bool(sibling_all_done),
            )
    except Exception as exc:  # noqa: BLE001
        return 500, {"ok": False, "error": str(exc) or type(exc).__name__}
    if not isinstance(result, dict):
        return 500, {"ok": False, "error": "complete_failed"}
    if not result.get("ok"):
        return 400, result
    from rt_dashboard.quest_workout_log import attach_lift_quest_log, quest_log_context

    uid = str(user.get("id") or "default")
    day, sessions, today_workout = quest_log_context(
        uid, payload, headers=headers
    )
    result = attach_lift_quest_log(
        result,
        payload,
        bool(completed),
        user_id=uid,
        sessions=sessions,
        today=day,
        today_workout=today_workout,
    )
    return 200, result


def inventory_write(headers, route: str, payload=None):
    """Kitchen add/remove/stock/update to Turso. Cookie-less 401. Failed persist is 5xx."""
    user, err = require_user(headers)
    if err:
        return err
    payload = payload if isinstance(payload, dict) else {}
    from rt_dashboard.inventory_store import (
        load_preview_inventory,
        save_preview_inventory,
    )
    from rt_dashboard.nutrition_planner import (
        add_ingredient,
        remove_ingredient,
        set_in_stock,
        update_ingredient,
    )

    uid = str(user.get("id") or "")
    try:
        current, _src = load_preview_inventory(uid)
        if route == "inv_add":
            updated = add_ingredient(current, payload)
        elif route == "inv_remove":
            updated = remove_ingredient(
                current,
                ingredient_id=str(payload.get("id") or ""),
                name=str(payload.get("name") or ""),
            )
        elif route == "inv_stock":
            updated = set_in_stock(
                current,
                ingredient_id=str(payload.get("id") or ""),
                in_stock=bool(payload.get("in_stock", True)),
            )
        elif route == "inv_update":
            updated = update_ingredient(current, payload)
        else:
            return 400, {"ok": False, "error": "unknown_inventory_route"}
        saved = save_preview_inventory(updated, uid)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return 500, {
            "ok": False,
            "error": str(exc) or type(exc).__name__,
            "write": {"ok": False, "source": "turso"},
        }
    return 200, {
        "ok": True,
        "inventory": saved,
        "write": {"ok": True, "source": "turso", "verified_on_readback": True},
    }


def equipment_write(headers, route: str, payload=None):
    """Add/update/remove owned gear + max load. Cookie-less 401. Failed persist is 5xx."""
    user, err = require_user(headers)
    if err:
        return err
    payload = payload if isinstance(payload, dict) else {}
    from rt_dashboard.equipment_store import (
        add_equipment_item,
        load_preview_equipment,
        remove_equipment_item,
        save_preview_equipment,
        update_equipment_item,
    )

    uid = str(user.get("id") or "")
    try:
        current, _src = load_preview_equipment(uid)
        if route == "eq_add":
            updated = add_equipment_item(current, payload)
        elif route == "eq_remove":
            updated = remove_equipment_item(
                current,
                equipment_id=str(payload.get("id") or ""),
                tag=str(payload.get("tag") or ""),
                name=str(payload.get("name") or ""),
            )
        elif route == "eq_update":
            updated = update_equipment_item(current, payload)
        else:
            return 400, {"ok": False, "error": "unknown_equipment_route"}
        saved = save_preview_equipment(updated, uid)
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return 500, {
            "ok": False,
            "error": str(exc) or type(exc).__name__,
            "write": {"ok": False, "source": "turso"},
        }
    return 200, {
        "ok": True,
        "equipment": saved,
        "write": {"ok": True, "source": "turso", "verified_on_readback": True},
    }


route_name = client_route_name
goals_read = goals_body
available_read = available_body
workouts_read = workouts_body


def agent_today_body(headers, query: str = "", client_host=None):
    """Read-only Today brief. Service token / loopback or a signed-in session."""
    from api.auth.session_util import session_from_headers
    from rt_dashboard.agent_today import export_agent_today
    from rt_dashboard.service_auth import service_auth_denied, service_auth_ok

    user = session_from_headers(headers)
    if not user and not service_auth_ok(headers, client_host):
        return 401, service_auth_denied("agents")
    if user:
        from api.dashboard import dashboard_body

        status, payload = dashboard_body(headers, query)
        if status != 200:
            return status, payload
        return 200, export_agent_today(payload)
    return _agent_today_from_stores(headers, query)


def _agent_today_from_stores(headers, query: str = ""):
    """Cookie-less service path: Turso + Hidrate + optional Health. No invented ml."""
    import os

    from api.dashboard import _load_health, _load_sessions, _today_consumed, request_tz_name
    from rt_dashboard.agent_today import assemble_dashboard_slice, export_agent_today
    from rt_dashboard.google_health import GoogleHealthClient
    from rt_dashboard.grok_planner import dashboard_plan_slots
    from rt_dashboard.hidrate_client import hidrate_bottle_charge, hidrate_hydration_samples
    from rt_dashboard.hydration_bars import build_hydration_bars_payload
    from rt_dashboard.models import HealthSnapshot
    from rt_dashboard.recovery import compute_recovery_status
    from rt_dashboard.sleep_battery import sleep_battery_from_fitdash_sleep
    from rt_dashboard.sleep_series import expand_sleep_calendar
    from rt_dashboard.timeutil import local_now, local_today_iso
    from rt_dashboard.workout_store import load_workspace_goals

    uid = (os.environ.get("FITDASH_USER_ID") or "default").strip() or "default"
    tz_name = request_tz_name(headers, query)
    now = local_now(tz_name)
    today = local_today_iso(tz_name, now=now)
    errors: list[str] = []

    sessions, sess_err, _source = _load_sessions(uid, fallback_house=True)
    errors.extend(sess_err)

    health = HealthSnapshot()
    try:
        if GoogleHealthClient().credentials_present():
            health, health_err = _load_health()
            errors.extend(health_err)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"health_pull: {type(exc).__name__}")

    sleep_battery = None
    try:
        real_sleep = [
            s
            for s in (health.sleep or [])
            if float(getattr(s, "sleep_hours", 0) or 0) > 0
            and str(getattr(s, "source", "") or "") != "implied_zero"
        ]
        if real_sleep or list(getattr(health, "sleep_intervals", None) or []):
            sleep_battery = sleep_battery_from_fitdash_sleep(
                real_sleep,
                now=now,
                tz_name=tz_name,
                sleep_target_hours=8.0,
                sleep_intervals=list(getattr(health, "sleep_intervals", None) or []),
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sleep_battery: {type(exc).__name__}")
        sleep_battery = None

    hydration_bars: dict = {"pacing": None}
    try:
        hydration_bars = build_hydration_bars_payload(
            hydration=health.hydration or [],
            samples=hidrate_hydration_samples(),
            weight=health.weight or [],
            sleep_battery=sleep_battery,
            as_of=today,
            now=now,
            tz_name=tz_name,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hydration_bars: {type(exc).__name__}")

    bottle = hidrate_bottle_charge()
    if isinstance(hydration_bars, dict):
        hydration_bars["bottle"] = bottle

    goals, _src = load_workspace_goals()
    had_real_sleep = any(
        float(getattr(s, "sleep_hours", 0) or 0) > 0
        and str(getattr(s, "source", "") or "") != "implied_zero"
        for s in (health.sleep or [])
    )
    recovery_dict: dict = {}
    try:
        sleep_for_recovery = expand_sleep_calendar(
            health.sleep or [],
            as_of=today,
            window_days=90,
            fill_hours=0.0,
            fill_source="implied_zero",
        )
        recovery = compute_recovery_status(
            weight=health.weight or [],
            sleep=sleep_for_recovery,
            sessions=sessions,
            as_of=today,
        )
        recovery_dict = recovery.to_dict() if hasattr(recovery, "to_dict") else {}
        recovery_dict["sparse"] = not had_real_sleep
    except Exception as exc:  # noqa: BLE001
        errors.append(f"recovery: {type(exc).__name__}")
        recovery_dict = {"sparse": not had_real_sleep}

    _meal, workout = dashboard_plan_slots(
        uid,
        sessions=sessions,
        goals=goals,
        recovery=recovery_dict,
        as_of=today,
    )

    nutrition_store: dict = {}
    try:
        from rt_dashboard.meal_plan_store import load_last_good_meal_plan
        from rt_dashboard.nutrition_store import load_workspace_targets

        targets, _t_src = load_workspace_targets()
        consumed = _today_consumed(health, today)
        meal_plan = None
        try:
            meal_plan = load_last_good_meal_plan(uid, today)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"meal_plan: {type(exc).__name__}")
            meal_plan = None
        nutrition_store = {
            "targets": targets,
            "today_consumed": consumed or None,
            "meal_plan": meal_plan if isinstance(meal_plan, dict) else {},
        }
    except Exception as exc:  # noqa: BLE001
        errors.append(f"nutrition: {type(exc).__name__}")

    slice_payload = assemble_dashboard_slice(
        date=today,
        sessions=sessions,
        workout=workout,
        workout_plan=workout,
        hydration_bars=hydration_bars,
        hidrate_bottle=bottle,
        sleep_battery=sleep_battery,
        health=health,
        nutrition_store=nutrition_store,
        goals=goals,
        recovery=recovery_dict,
        meta_error="; ".join(errors) if errors else None,
    )
    return 200, export_agent_today(slice_payload)


def dispatch_client_route(
    headers, query: str, method: str, payload=None, path: str = "", client_host=None
):
    """Existing dashboard/ask functions serve client paths via rewrite."""
    route = client_route_name(headers, query, path)
    method = (method or "GET").upper()
    if route == "goals":
        return goals_write(headers) if method == "POST" else goals_body(headers)
    if route == "available":
        return available_write(headers) if method == "POST" else available_body(headers)
    if route == "workouts":
        return (
            workouts_write(headers, payload)
            if method == "POST"
            else workouts_body(headers)
        )
    if route == "generate":
        return generate_body(headers, payload)
    if route in _MEAL_ROUTES:
        return meal_plan_body(headers, payload)
    if route == "refresh":
        return refresh_body(headers, payload)
    if route == "daily_tasks":
        return daily_tasks_body(headers, payload)
    if route == "daily_tasks_complete":
        return daily_tasks_complete_body(headers, payload, method)
    if route in _INV_ROUTES:
        if method != "POST":
            return 405, {"ok": False, "error": "method_not_allowed"}
        return inventory_write(headers, route, payload or {})
    if route in _EQ_ROUTES:
        if method != "POST":
            return 405, {"ok": False, "error": "method_not_allowed"}
        return equipment_write(headers, route, payload or {})
    if route == "agent_today":
        if method != "GET":
            return 405, {"ok": False, "error": "method_not_allowed"}
        return agent_today_body(headers, query, client_host=client_host)
    return None


__all__ = [
    "PREVIEW_READ_ONLY",
    "auth_required",
    "available_body",
    "available_read",
    "available_write",
    "agent_today_body",
    "client_route_name",
    "dispatch_client_route",
    "generate_body",
    "goals_body",
    "equipment_write",
    "inventory_write",
    "daily_tasks_body",
    "daily_tasks_complete_body",
    "stamp_quest_list_ids",
    "meal_plan_body",
    "refresh_body",
    "goals_read",
    "goals_write",
    "read_json",
    "require_user",
    "workouts_body",
    "workouts_read",
    "workouts_write",
    "write_json",
]
