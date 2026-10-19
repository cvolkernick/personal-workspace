"""Token-locked, read-only agent API: ``GET /api/agent/<section>``.

Slice 1 of the FitDash agent read API. One shared agent bearer token
(``FITDASH_AGENT_TOKEN``) gates every section. There is no loopback bypass
and no browser-session bypass: no token, a wrong token, or an unset env var
is a 401. Reuses the header parsing and constant-time compare from
``service_auth`` (#1028 / #1029) instead of inventing a new scheme.

Strictly read-only. Section builders take already-loaded data and never
write. The DB readers below only ``SELECT``; they do not run the seed /
heal paths that ``load_preview_inventory`` / ``load_preview_targets`` use.

Every payload: ``{ok, section, generated_at, as_of, data}``. ``as_of`` is
the freshest source timestamp for that section (or null when unknown).
Unknown history is returned as null with a ``note``, never invented.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

from .service_auth import _token_match, service_token_from_headers

AGENT_TOKEN_ENV = "FITDASH_AGENT_TOKEN"
AGENT_READ_ALERT = "fitdash_agent_api_auth"
SECTIONS = ("targets", "inventory", "logs", "plan", "training", "recovery")
MAX_LOG_DAYS = 92
DEFAULT_LOG_DAYS = 7
MACRO_KEYS = ("protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg")
_QTY_KEYS = ("qty", "quantity", "count", "servings", "stock")


# ---------------------------------------------------------------- auth
def agent_token_configured() -> bool:
    return bool((os.environ.get(AGENT_TOKEN_ENV) or "").strip())


def agent_read_gate(headers) -> Optional[Dict[str, Any]]:
    """None when allowed, else the 401 body. Never echoes the token."""
    expected = (os.environ.get(AGENT_TOKEN_ENV) or "").strip()
    provided = service_token_from_headers(headers)
    if expected and provided and _token_match(provided, expected):
        return None
    if not expected:
        reason = "unconfigured"
        message = f"{AGENT_TOKEN_ENV} is not set on this server; agent reads are locked."
    elif not provided:
        reason = "missing"
        message = "Send Authorization: Bearer <agent token>."
    else:
        reason = "invalid"
        message = "Agent token rejected."
    return {
        "ok": False,
        "error": "auth_required",
        "reason": reason,
        "message": message,
        "alert": AGENT_READ_ALERT,
    }


# ---------------------------------------------------------------- helpers
def utc_now_iso(now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _d(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _max_ts(*vals: Any) -> Optional[str]:
    vs = [str(v) for v in vals if v]
    return max(vs) if vs else None


def _row_get(row: Any, key: str, idx: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        try:
            return row[idx]
        except (IndexError, TypeError):
            return None


def _row_uid(user_id: str) -> str:
    return (user_id or "").strip() or "default"


# ---------------------------------------------------------------- pure DB reads
def read_targets_record(user_id: str = "") -> Dict[str, Any]:
    """SELECT-only read of applied targets. No seed, no heal."""
    from .turso_http import connect, turso_enabled

    if turso_enabled():
        uid = _row_uid(user_id)
        with connect() as conn:
            try:
                row = conn.execute(
                    "SELECT payload, updated_at, updated_by FROM nutrition_targets WHERE user_id = ?",
                    (uid,),
                ).fetchone()
                has_by = True
            except Exception:  # noqa: BLE001 - column / table not there yet
                try:
                    row = conn.execute(
                        "SELECT payload, updated_at FROM nutrition_targets WHERE user_id = ?",
                        (uid,),
                    ).fetchone()
                except Exception:  # noqa: BLE001
                    row = None
                has_by = False
        if row:
            try:
                payload = json.loads(_row_get(row, "payload", 0) or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            return {
                "payload": payload if isinstance(payload, dict) else {},
                "updated_at": _row_get(row, "updated_at", 1),
                "updated_by": _row_get(row, "updated_by", 2) if has_by else None,
                "updated_by_tracked": has_by,
                "source": "turso",
            }
    from .nutrition_store import load_workspace_targets

    payload, src = load_workspace_targets()
    return {
        "payload": payload if isinstance(payload, dict) else {},
        "updated_at": _d(payload).get("updated_at"),
        "updated_by": None,
        "updated_by_tracked": False,
        "source": src,
    }


def read_inventory_record(user_id: str = "") -> Dict[str, Any]:
    """SELECT-only read of the pantry row. No seed, no heal."""
    from .turso_http import connect, turso_enabled

    if turso_enabled():
        from .inventory_store import _inventory_uid

        with connect() as conn:
            try:
                row = conn.execute(
                    "SELECT payload, updated_at FROM nutrition_inventory WHERE user_id = ?",
                    (_inventory_uid(user_id),),
                ).fetchone()
            except Exception:  # noqa: BLE001
                row = None
        if row:
            try:
                payload = json.loads(_row_get(row, "payload", 0) or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict) and isinstance(payload.get("ingredients"), list):
                return {"payload": payload, "updated_at": _row_get(row, "updated_at", 1), "source": "turso"}
    from .inventory_store import load_workspace_inventory

    payload, src = load_workspace_inventory()
    return {"payload": payload, "updated_at": _d(payload).get("updated_at"), "source": src}


# ---------------------------------------------------------------- section builders
def build_targets(rec: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    p = _d(rec.get("payload"))
    notes: List[str] = []
    updated_by = rec.get("updated_by")
    if not updated_by:
        notes.append(
            "updated_by not recorded for this write (tracking added in slice 1; older writes have no history)"
            if rec.get("updated_by_tracked")
            else "updated_by not tracked by this store; history not recoverable"
        )
    if not rec.get("updated_at"):
        notes.append("updated_at unknown")
    if not p.get("phase"):
        notes.append("phase not stored with targets")
    data = {
        "phase": p.get("phase") or None,
        "kcal": _num(p.get("calories")),
        "macros": {k: _num(p.get(k)) for k in MACRO_KEYS},
        "updated_at": rec.get("updated_at") or None,
        "updated_by": updated_by or None,
        "source": rec.get("source"),
        "note": "; ".join(notes) or None,
    }
    return data, data["updated_at"]


def _log_ts(log: Dict[str, Any]) -> Optional[str]:
    day = str(log.get("date") or "")[:10]
    if not day:
        return None
    t = str(log.get("time") or "").strip()
    return f"{day}T{t}" if t else day


def build_inventory(
    rec: Dict[str, Any], food_logs: List[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[str]]:
    p = _d(rec.get("payload"))
    names: List[Tuple[str, Optional[str]]] = [
        (str(f.get("name") or "").lower(), _log_ts(f)) for f in food_logs if isinstance(f, dict)
    ]
    items = []
    for ing in p.get("ingredients") or []:
        if not isinstance(ing, dict):
            continue
        nm = str(ing.get("name") or "").strip()
        qty = None
        for k in _QTY_KEYS:
            if ing.get(k) is not None and not isinstance(ing.get(k), bool):
                qty = ing.get(k)
                break
        key = nm.lower()
        used = [ts for (n, ts) in names if key and ts and key in n]
        items.append(
            {
                "id": ing.get("id"),
                "name": nm or None,
                "category": ing.get("category"),
                "qty": qty,
                "in_stock": ing.get("in_stock") if isinstance(ing.get("in_stock"), bool) else None,
                "updated_at": ing.get("updated_at") or None,
                "last_used_at": max(used) if used else None,
            }
        )
    data = {
        "items": items,
        "count": len(items),
        "in_stock_count": sum(1 for i in items if i["in_stock"]),
        "updated_at": rec.get("updated_at") or None,
        "source": rec.get("source"),
        "note": (
            "per-item updated_at is null unless the writer stamped it; row updated_at is the last pantry write. "
            "last_used_at is the latest food log whose name contains the item name (cached Health logs only)."
        ),
    }
    return data, data["updated_at"]


def parse_range(query: str, today: str) -> Tuple[Optional[Tuple[str, str]], Optional[str]]:
    q = parse_qs(query or "")
    try:
        end = date.fromisoformat((q.get("to") or [today])[0][:10])
        start = date.fromisoformat(
            (q.get("from") or [(end - timedelta(days=DEFAULT_LOG_DAYS - 1)).isoformat()])[0][:10]
        )
    except ValueError:
        return None, "from/to must be YYYY-MM-DD"
    if start > end:
        return None, "from must be <= to"
    if (end - start).days + 1 > MAX_LOG_DAYS:
        return None, f"range too long (max {MAX_LOG_DAYS} days)"
    return (start.isoformat(), end.isoformat()), None


def build_logs(
    data: Dict[str, Any], food_logs: List[Dict[str, Any]], rng: Tuple[str, str], health_as_of: Optional[str]
) -> Tuple[Dict[str, Any], Optional[str]]:
    start, end = rng
    meals = [
        {k: f.get(k) for k in ("date", "time", "name", "meal_type", "calories", "protein_g", "carbs_g", "fat_g", "serving_label", "source", "log_id")}
        for f in food_logs
        if isinstance(f, dict) and start <= str(f.get("date") or "")[:10] <= end
    ]
    sessions = []
    for s in data.get("sessions") or []:
        if not isinstance(s, dict):
            continue
        day = str(s.get("date") or "")[:10]
        if not (start <= day <= end):
            continue
        sessions.append(
            {
                "date": day,
                "session_type": s.get("session_type"),
                "closed_at": s.get("closed_at"),
                "volume": s.get("volume"),
                "exercises": [
                    {"name": e.get("name"), "sets": e.get("sets"), "volume": e.get("volume")}
                    for e in (s.get("exercises") or [])
                    if isinstance(e, dict)
                ],
            }
        )
    meals.sort(key=lambda m: (str(m.get("date")), str(m.get("time") or "")))
    sessions.sort(key=lambda s: s["date"])
    sess_as_of = _max_ts(*[s.get("closed_at") or s["date"] for s in sessions])
    out = {
        "from": start,
        "to": end,
        "meals": meals,
        "sessions": sessions,
        "meals_as_of": health_as_of,
        "sessions_as_of": sess_as_of,
    }
    return out, _max_ts(health_as_of, sess_as_of)


def build_training(agent_today: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    out = dict(agent_today or {})
    week = _d(out.get("week"))
    out["muscle_sets"] = {
        "sets": week.get("muscle_sets"),
        "budget": week.get("muscle_set_budget"),
        "coach": week.get("muscle_set_coach"),
    }
    out.setdefault("guardrails", None)
    as_of = _d(out.get("today")).get("date") or _d(out.get("guardrails")).get("as_of")
    return out, as_of


def build_plan(data: Dict[str, Any], agent_today: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    today = _d(_d(agent_today).get("today"))
    week = _d(_d(agent_today).get("week"))
    nut = _d(data.get("nutrition_store"))
    store = _d(data.get("workout_store"))
    out = {
        "today": {
            "date": today.get("date"),
            "workout": today.get("workout"),
            "meal_plan": nut.get("meal_plan"),
            "nutrition": today.get("nutrition"),
        },
        "week": {
            "start": week.get("start"),
            "end": week.get("end"),
            "workout_plan": store.get("plan"),
            "goals": store.get("goals"),
            "nutrition": week.get("nutrition"),
        },
        "note": "Read-only: never triggers plan generation. An empty workout means no plan is stored yet.",
    }
    return out, today.get("date") or _d(data.get("meta")).get("generated_at")


def build_recovery(data: Dict[str, Any], health_as_of: Optional[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    health = _d(data.get("health"))
    sleep_rows = [s for s in health.get("sleep") or [] if isinstance(s, dict) and _num(s.get("sleep_hours"))]
    last = sleep_rows[-1] if sleep_rows else None
    sleep = (
        {
            "date": last.get("date"),
            "sleep_hours": _num(last.get("sleep_hours")),
            "efficiency_pct": _num(last.get("efficiency_pct")),
            "source": last.get("source"),
            "as_of": last.get("date"),
        }
        if last
        else {"source": None, "as_of": None, "note": "no sleep data cached"}
    )
    rec = _d(data.get("recovery"))
    meta = _d(data.get("meta"))
    readiness = (
        {
            "label": rec.get("label"),
            "score": _num(rec.get("score")),
            "reasons": rec.get("reasons") or [],
            "source": "fitdash_recovery_model",
            "as_of": health_as_of or meta.get("generated_at"),
        }
        if rec
        else {"source": None, "as_of": None, "note": "recovery not computed"}
    )
    pacing = _d(_d(data.get("hydration_bars")).get("pacing"))
    hyd_rows = [h for h in health.get("hydration") or [] if isinstance(h, dict)]
    if pacing:
        hydration = {
            "date": meta.get("local_today"),
            "consumed_ml": _num(pacing.get("consumed")),
            "target_ml": _num(pacing.get("target")),
            "status": pacing.get("status"),
            "source": pacing.get("intake_source") or "health",
            "as_of": health_as_of,
        }
    elif hyd_rows:
        h = hyd_rows[-1]
        hydration = {
            "date": h.get("date"),
            "consumed_ml": _num(h.get("water_ml")),
            "target_ml": None,
            "status": None,
            "source": h.get("source") or "health",
            "as_of": h.get("date"),
        }
    else:
        hydration = {"source": None, "as_of": None, "note": "no hydration data cached"}
    out = {"sleep": sleep, "readiness": readiness, "hydration": hydration}
    return out, _max_ts(sleep.get("as_of"), readiness.get("as_of"), hydration.get("as_of"))


# ---------------------------------------------------------------- dispatcher
class Loaders:
    """Injectable data access so contract tests run without Turso / Health."""

    def __init__(
        self,
        dashboard: Callable[[], Dict[str, Any]],
        targets: Callable[[], Dict[str, Any]] = read_targets_record,
        inventory: Callable[[], Dict[str, Any]] = read_inventory_record,
        health_fetched_at: Callable[[], Optional[float]] = lambda: None,
    ) -> None:
        self.dashboard = dashboard
        self.targets = targets
        self.inventory = inventory
        self.health_fetched_at = health_fetched_at


def _food_logs(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [f for f in _d(data.get("health")).get("food_logs") or [] if isinstance(f, dict)]


def respond_agent_read(
    section: str,
    headers,
    query: str,
    loaders: Loaders,
    *,
    now: Optional[datetime] = None,
) -> Tuple[int, Dict[str, Any]]:
    denied = agent_read_gate(headers)
    if denied:
        return 401, denied
    if section not in SECTIONS:
        return 404, {"ok": False, "error": "not_found", "sections": list(SECTIONS)}
    generated_at = utc_now_iso(now)
    health_as_of = epoch_to_iso(loaders.health_fetched_at())

    if section == "targets":
        body, as_of = build_targets(loaders.targets())
    elif section == "inventory":
        data = loaders.dashboard()
        body, as_of = build_inventory(loaders.inventory(), _food_logs(data))
    else:
        data = loaders.dashboard()
        if section == "logs":
            today = str(_d(data.get("meta")).get("local_today") or generated_at[:10])
            rng, err = parse_range(query, today)
            if err:
                return 400, {"ok": False, "error": "bad_range", "message": err}
            body, as_of = build_logs(data, _food_logs(data), rng, health_as_of)
        elif section == "recovery":
            body, as_of = build_recovery(data, health_as_of)
        else:
            from .agent_today import export_agent_today

            at = export_agent_today(data)
            if section == "training":
                body, as_of = build_training(at)
            else:
                body, as_of = build_plan(data, at)
    return 200, {
        "ok": True,
        "section": section,
        "generated_at": generated_at,
        "as_of": as_of,
        "read_only": True,
        "data": body,
    }
