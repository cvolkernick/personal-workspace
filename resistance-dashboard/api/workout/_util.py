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
)
_INV_ROUTES = ("inv_add", "inv_remove", "inv_stock")


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
    if "/api/inventory/add" in blob:
        return "inv_add"
    if "/api/inventory/remove" in blob:
        return "inv_remove"
    if "/api/inventory/stock" in blob:
        return "inv_stock"
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

    sessions, errors, source = _load_sessions(str(user.get("id") or "default"))
    out = []
    for s in sessions or []:
        if hasattr(s, "to_dict"):
            out.append(s.to_dict())
        elif isinstance(s, dict):
            out.append(s)
    return 200, {
        "ok": True,
        "readonly": True,
        "sessions": out,
        "session_count": len(out),
        "source": source,
        "error": "; ".join(errors) if errors else None,
        "write": {
            "ok": False,
            "readonly": True,
            "path": None,
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


def workouts_write(headers):
    return _write_denied(headers)


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


def inventory_write(headers, route: str, payload=None):
    """Kitchen add/remove/stock to Turso. Cookie-less 401. Failed persist is 5xx."""
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


route_name = client_route_name
goals_read = goals_body
available_read = available_body
workouts_read = workouts_body


def dispatch_client_route(headers, query: str, method: str, payload=None, path: str = ""):
    """Existing dashboard/ask functions serve client paths via rewrite."""
    route = client_route_name(headers, query, path)
    method = (method or "GET").upper()
    if route == "goals":
        return goals_write(headers) if method == "POST" else goals_body(headers)
    if route == "available":
        return available_write(headers) if method == "POST" else available_body(headers)
    if route == "workouts":
        return workouts_write(headers) if method == "POST" else workouts_body(headers)
    if route == "generate":
        return generate_body(headers, payload)
    if route in _INV_ROUTES:
        if method != "POST":
            return 405, {"ok": False, "error": "method_not_allowed"}
        return inventory_write(headers, route, payload or {})
    return None


__all__ = [
    "PREVIEW_READ_ONLY",
    "auth_required",
    "available_body",
    "available_read",
    "available_write",
    "client_route_name",
    "dispatch_client_route",
    "generate_body",
    "goals_body",
    "inventory_write",
    "goals_read",
    "goals_write",
    "read_json",
    "require_user",
    "workouts_body",
    "workouts_read",
    "workouts_write",
    "write_json",
]
