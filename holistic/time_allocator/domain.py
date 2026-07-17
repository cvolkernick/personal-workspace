"""Pure domain operations for the time allocator (no I/O).

Priority: higher integer = more important.

Two layers of work:
  - **targets** — ongoing KPIs / recurring obligations (sleep, workouts, dog walks, fill work)
  - **items** — ad-hoc tasks/goals for the current window

A **rolling 24h plan** reserves non-negotiables first, places ad-hoc by priority,
then pours remaining active time into fill_remainder targets (e.g. Lyft).
"""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Starter personal targets (user intent)
# ---------------------------------------------------------------------------

PERSONAL_TARGETS: list[dict[str, Any]] = [
    {
        "id": "sleep",
        "title": "Sleep (rolling 7-day avg ≥ 8h)",
        "kind": "rolling_avg",
        "window_days": 7,
        "unit": "hours",
        "target": 8.0,
        "priority": 10,
        # Nightly reserve inside each rolling 24h plan
        "reserve_minutes": 480,
    },
    {
        "id": "duchess-walk",
        "title": "Walk Duchess",
        "kind": "daily_duration",
        "minutes": 45,  # default plan block (mid of 30–60)
        "minutes_min": 30,
        "minutes_max": 60,
        "sessions_hint": 1,
        "priority": 9,
        "notes": "30–60 minutes per day.",
    },
    {
        "id": "workout",
        "title": "Workout",
        "kind": "weekly_frequency",
        "min_days": 3,
        "max_days": 5,
        "session_minutes": 60,
        "priority": 8,
        "notes": "3–5 training days per week.",
    },
    {
        "id": "lyft",
        "title": "Lyft driving",
        "kind": "fill_remainder",
        "priority": 3,
        "notes": "Fill remaining active time after fixed targets + ad-hoc.",
    },
]

# Legacy generic starter (kept for CLI seed --generic)
STARTER_ITEMS: list[dict[str, Any]] = [
    {
        "id": "seed-deep-work",
        "title": "Deep work / primary project",
        "kind": "task",
        "priority": 5,
        "minutes": 0,
    },
    {
        "id": "seed-fitness",
        "title": "Fitness / movement",
        "kind": "goal",
        "priority": 4,
        "minutes": 0,
    },
    {
        "id": "seed-admin",
        "title": "Admin / email / chores",
        "kind": "task",
        "priority": 2,
        "minutes": 0,
    },
    {
        "id": "seed-learning",
        "title": "Learning / skill growth",
        "kind": "goal",
        "priority": 3,
        "minutes": 0,
    },
    {
        "id": "seed-rest",
        "title": "Rest / buffer",
        "kind": "task",
        "priority": 1,
        "minutes": 0,
    },
]

TARGET_KINDS = frozenset(
    {"rolling_avg", "daily_duration", "weekly_frequency", "fill_remainder", "fixed"}
)
ITEM_KINDS = frozenset({"task", "goal"})


def _slug_id(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-") or "item"
    return f"{base[:40]}-{uuid.uuid4().hex[:8]}"


def _as_date(d: date | datetime | str | None) -> date:
    if d is None:
        return datetime.now(timezone.utc).astimezone().date()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def empty_state() -> dict[str, Any]:
    return {
        "version": 2,
        "items": [],
        "targets": [],
        "logs": [],
        "plan": None,
    }


def _migrate_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """In-place-safe migration of known personal targets."""
    out = deepcopy(targets)
    for t in out:
        if str(t.get("id")) != "duchess-walk":
            continue
        # Historical mistake: 130 min; correct range is 30–60.
        mins = int(t.get("minutes") or 0)
        if mins >= 100 or t.get("minutes_min") is None:
            t["minutes"] = 45
            t["minutes_min"] = 30
            t["minutes_max"] = 60
            t["sessions_hint"] = int(t.get("sessions_hint") or 1)
            t["notes"] = "30–60 minutes per day."
            t["title"] = t.get("title") or "Walk Duchess"
    return out


def normalize_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Upgrade v1 stores and fill missing keys."""
    if not raw:
        return empty_state()
    out = deepcopy(raw)
    out.setdefault("items", [])
    out.setdefault("targets", [])
    out.setdefault("logs", [])
    out.setdefault("plan", None)
    if not isinstance(out["items"], list):
        out["items"] = []
    if not isinstance(out["targets"], list):
        out["targets"] = []
    if not isinstance(out["logs"], list):
        out["logs"] = []
    out["targets"] = _migrate_targets(list(out["targets"]))
    out["version"] = max(2, int(out.get("version") or 1))
    return out


def seed_starter(state: dict[str, Any] | None = None, *, personal: bool = True) -> dict[str, Any]:
    """Seed store. Default personal=True uses sleep/workout/Duchess/Lyft targets."""
    out = normalize_state(state)
    if personal:
        out["targets"] = deepcopy(PERSONAL_TARGETS)
        # Keep ad-hoc items empty so the plan is driven by targets + user adds.
        out["items"] = []
    else:
        out["items"] = deepcopy(STARTER_ITEMS)
        if not out["targets"]:
            out["targets"] = deepcopy(PERSONAL_TARGETS)
    out["plan"] = None
    out["version"] = 2
    return out


# ---------------------------------------------------------------------------
# Ad-hoc items
# ---------------------------------------------------------------------------


def list_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(normalize_state(state).get("items") or [])
    return sorted(
        items,
        key=lambda it: (-int(it.get("priority") or 0), str(it.get("title") or "")),
    )


def get_item(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    key_l = (key or "").strip().lower()
    for it in normalize_state(state).get("items") or []:
        if str(it.get("id") or "").lower() == key_l:
            return it
        if str(it.get("title") or "").lower() == key_l:
            return it
    return None


def add_item(
    state: dict[str, Any],
    title: str,
    *,
    kind: str = "task",
    priority: int = 1,
    minutes: int = 0,
    item_id: str | None = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    kind = (kind or "task").strip().lower()
    if kind not in ITEM_KINDS:
        raise ValueError("kind must be 'task' or 'goal'")
    priority = int(priority)
    minutes = max(0, int(minutes))
    if get_item(state, title) is not None:
        raise ValueError(f"item already exists with title: {title}")
    new_id = (item_id or _slug_id(title)).strip()
    if get_item(state, new_id) is not None:
        raise ValueError(f"item already exists with id: {new_id}")
    out = normalize_state(state)
    items = list(out.get("items") or [])
    items.append(
        {
            "id": new_id,
            "title": title,
            "kind": kind,
            "priority": priority,
            "minutes": minutes,
        }
    )
    out["items"] = items
    return out


def remove_item(state: dict[str, Any], key: str) -> dict[str, Any]:
    key = (key or "").strip()
    if not key:
        raise ValueError("key is required")
    found = get_item(state, key)
    if found is None:
        raise KeyError(f"no item matching: {key}")
    rid = found["id"]
    out = normalize_state(state)
    out["items"] = [it for it in (out.get("items") or []) if it.get("id") != rid]
    return out


def set_priority(state: dict[str, Any], key: str, priority: int) -> dict[str, Any]:
    found = get_item(state, key)
    if found is None:
        raise KeyError(f"no item matching: {key}")
    out = normalize_state(state)
    for it in out.get("items") or []:
        if it.get("id") == found["id"]:
            it["priority"] = int(priority)
            break
    return out


def set_minutes(state: dict[str, Any], key: str, minutes: int) -> dict[str, Any]:
    found = get_item(state, key)
    if found is None:
        raise KeyError(f"no item matching: {key}")
    out = normalize_state(state)
    for it in out.get("items") or []:
        if it.get("id") == found["id"]:
            it["minutes"] = max(0, int(minutes))
            break
    return out


def allocate_total(state: dict[str, Any], total_minutes: int) -> dict[str, Any]:
    """Distribute total_minutes across ad-hoc items weighted by priority (legacy)."""
    total = max(0, int(total_minutes))
    out = normalize_state(state)
    items = list(out.get("items") or [])
    if not items:
        return out
    weights = [max(0, int(it.get("priority") or 0)) for it in items]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        base = total // len(items)
        rem = total - base * len(items)
        for i, it in enumerate(items):
            it["minutes"] = base + (1 if i < rem else 0)
        out["items"] = items
        return out

    allocated = 0
    shares: list[int] = []
    for w in weights:
        share = (total * w) // weight_sum
        shares.append(share)
        allocated += share
    remainder = total - allocated
    order = sorted(range(len(items)), key=lambda i: (-weights[i], items[i].get("id") or ""))
    if remainder and order:
        shares[order[0]] += remainder
    for i, it in enumerate(items):
        it["minutes"] = shares[i]
    out["items"] = items
    return out


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def list_targets(state: dict[str, Any]) -> list[dict[str, Any]]:
    targets = list(normalize_state(state).get("targets") or [])
    return sorted(
        targets,
        key=lambda t: (-int(t.get("priority") or 0), str(t.get("title") or "")),
    )


def get_target(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    key_l = (key or "").strip().lower()
    for t in normalize_state(state).get("targets") or []:
        if str(t.get("id") or "").lower() == key_l:
            return t
        if str(t.get("title") or "").lower() == key_l:
            return t
    return None


def add_target(
    state: dict[str, Any],
    title: str,
    *,
    kind: str,
    priority: int = 5,
    target_id: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    kind = (kind or "").strip().lower()
    if kind not in TARGET_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(TARGET_KINDS))}")
    if get_target(state, title) is not None:
        raise ValueError(f"target already exists with title: {title}")
    new_id = (target_id or _slug_id(title)).strip()
    if get_target(state, new_id) is not None:
        raise ValueError(f"target already exists with id: {new_id}")
    row: dict[str, Any] = {
        "id": new_id,
        "title": title,
        "kind": kind,
        "priority": int(priority),
    }
    for k, v in fields.items():
        if v is not None and k not in ("id", "title", "kind", "priority"):
            row[k] = v
    # Sensible defaults per kind
    if kind == "rolling_avg":
        row.setdefault("window_days", 7)
        row.setdefault("unit", "hours")
        row.setdefault("target", 8.0)
        row.setdefault("reserve_minutes", int(float(row["target"]) * 60))
    elif kind == "daily_duration":
        row.setdefault("minutes", 60)
    elif kind == "weekly_frequency":
        row.setdefault("min_days", 3)
        row.setdefault("max_days", 5)
        row.setdefault("session_minutes", 60)
    out = normalize_state(state)
    targets = list(out.get("targets") or [])
    targets.append(row)
    out["targets"] = targets
    return out


def remove_target(state: dict[str, Any], key: str) -> dict[str, Any]:
    key = (key or "").strip()
    if not key:
        raise ValueError("key is required")
    found = get_target(state, key)
    if found is None:
        raise KeyError(f"no target matching: {key}")
    rid = found["id"]
    out = normalize_state(state)
    out["targets"] = [t for t in (out.get("targets") or []) if t.get("id") != rid]
    return out


def update_target(state: dict[str, Any], key: str, patch: dict[str, Any]) -> dict[str, Any]:
    found = get_target(state, key)
    if found is None:
        raise KeyError(f"no target matching: {key}")
    out = normalize_state(state)
    allowed = {
        "title",
        "priority",
        "kind",
        "minutes",
        "session_minutes",
        "min_days",
        "max_days",
        "window_days",
        "unit",
        "target",
        "reserve_minutes",
        "sessions_hint",
        "notes",
        "minutes_min",
        "minutes_max",
    }
    for t in out.get("targets") or []:
        if t.get("id") == found["id"]:
            for k, v in patch.items():
                if k in allowed and v is not None:
                    if k == "kind" and str(v) not in TARGET_KINDS:
                        raise ValueError(f"invalid kind: {v}")
                    t[k] = v
            break
    return out


# ---------------------------------------------------------------------------
# Logs (for KPI progress)
# ---------------------------------------------------------------------------


def add_log(
    state: dict[str, Any],
    target_id: str,
    value: float,
    *,
    on: date | str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Record a metric for a target on a calendar day (local).

    Examples:
      sleep hours → value=7.5
      workout done → value=1
      Duchess minutes → value=45
    """
    tgt = get_target(state, target_id)
    if tgt is None:
        raise KeyError(f"no target matching: {target_id}")
    day = _as_date(on).isoformat()
    out = normalize_state(state)
    logs = list(out.get("logs") or [])
    # Replace same target+day entry (one value per day per target)
    logs = [
        lg
        for lg in logs
        if not (str(lg.get("target_id")) == tgt["id"] and str(lg.get("date")) == day)
    ]
    logs.append(
        {
            "date": day,
            "target_id": tgt["id"],
            "value": float(value),
            "note": (note or "").strip(),
        }
    )
    logs.sort(key=lambda lg: (str(lg.get("date") or ""), str(lg.get("target_id") or "")))
    out["logs"] = logs
    return out


def logs_for_target(
    state: dict[str, Any],
    target_id: str,
    *,
    since: date | None = None,
    until: date | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for lg in normalize_state(state).get("logs") or []:
        if str(lg.get("target_id")) != target_id:
            continue
        d = _as_date(lg.get("date"))
        if since and d < since:
            continue
        if until and d > until:
            continue
        rows.append(lg)
    return rows


def kpi_status(state: dict[str, Any], *, as_of: date | None = None) -> list[dict[str, Any]]:
    """Summarize progress vs each ongoing target."""
    today = _as_date(as_of)
    out: list[dict[str, Any]] = []
    for t in list_targets(state):
        kind = str(t.get("kind") or "")
        tid = str(t.get("id"))
        row: dict[str, Any] = {
            "id": tid,
            "title": t.get("title"),
            "kind": kind,
            "priority": int(t.get("priority") or 0),
            "on_track": None,
            "summary": "",
            "detail": {},
        }
        if kind == "rolling_avg":
            window = int(t.get("window_days") or 7)
            target_val = float(t.get("target") or 0)
            since = today - timedelta(days=window - 1)
            logs = logs_for_target(state, tid, since=since, until=today)
            values = [float(lg.get("value") or 0) for lg in logs]
            avg = (sum(values) / len(values)) if values else None
            row["detail"] = {
                "window_days": window,
                "target": target_val,
                "unit": t.get("unit") or "hours",
                "samples": len(values),
                "average": round(avg, 2) if avg is not None else None,
                "days": [
                    {"date": lg.get("date"), "value": lg.get("value")} for lg in logs
                ],
            }
            if avg is None:
                row["on_track"] = None
                row["summary"] = f"No logs in last {window}d — target {target_val} {t.get('unit') or 'hours'}"
            else:
                row["on_track"] = avg >= target_val
                row["summary"] = (
                    f"{avg:.2f} {t.get('unit') or 'h'} avg over {len(values)} day(s) "
                    f"(target ≥ {target_val:g})"
                )
        elif kind == "daily_duration":
            plan_m = int(t.get("minutes") or 0)
            min_m = int(t.get("minutes_min") if t.get("minutes_min") is not None else plan_m)
            max_m = int(t.get("minutes_max") if t.get("minutes_max") is not None else plan_m)
            logs = logs_for_target(state, tid, since=today, until=today)
            done = int(sum(float(lg.get("value") or 0) for lg in logs))
            row["detail"] = {
                "target_minutes": plan_m,
                "minutes_min": min_m,
                "minutes_max": max_m,
                "done_minutes": done,
            }
            if not logs:
                row["on_track"] = None
                row["summary"] = f"Today 0 min (target {min_m}–{max_m}, plan {plan_m}) — not logged"
            else:
                row["on_track"] = done >= min_m
                row["summary"] = f"Today {done} min (target {min_m}–{max_m})"
        elif kind == "weekly_frequency":
            min_d = int(t.get("min_days") or 3)
            max_d = int(t.get("max_days") or 5)
            since = today - timedelta(days=6)
            logs = logs_for_target(state, tid, since=since, until=today)
            # Count days with value > 0
            days_done = {str(lg.get("date")) for lg in logs if float(lg.get("value") or 0) > 0}
            n = len(days_done)
            row["detail"] = {
                "min_days": min_d,
                "max_days": max_d,
                "days_done": n,
                "dates": sorted(days_done),
            }
            if n < min_d:
                row["on_track"] = False
                row["summary"] = f"{n}/{min_d}–{max_d} days this week — need more sessions"
            elif n > max_d:
                row["on_track"] = True
                row["summary"] = f"{n} days (above max {max_d}) — optional recovery"
            else:
                row["on_track"] = True
                row["summary"] = f"{n}/{min_d}–{max_d} days this week — on track"
        elif kind == "fill_remainder":
            row["summary"] = "Fills leftover active minutes in the rolling 24h plan"
            row["on_track"] = None
        else:
            row["summary"] = kind or "target"
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Rolling 24h plan
# ---------------------------------------------------------------------------


def build_rolling_plan(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    window_minutes: int = 24 * 60,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Build a rolling window plan from targets + ad-hoc items.

    Order of claims on the window:
      1. Sleep / reserve (rolling_avg reserve_minutes or fixed)
      2. daily_duration targets (by priority)
      3. weekly_frequency sessions if behind min_days (by priority)
      4. ad-hoc items with minutes > 0 (by priority); else estimate 30 if priority high
      5. fill_remainder gets everything left in *active* time (window − sleep reserve)
    """
    state = normalize_state(state)
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()
    today = as_of or now.date()
    window = max(0, int(window_minutes))
    end = now + timedelta(minutes=window)

    blocks: list[dict[str, Any]] = []
    notes: list[str] = []

    targets = list_targets(state)
    sleep_reserve = 0
    for t in targets:
        if str(t.get("kind")) == "rolling_avg":
            sleep_reserve += max(0, int(t.get("reserve_minutes") or int(float(t.get("target") or 0) * 60)))
        elif str(t.get("kind")) == "fixed":
            sleep_reserve += max(0, int(t.get("minutes") or 0))

    # Cap reserve so active time is non-negative
    sleep_reserve = min(sleep_reserve, window)
    active_budget = window - sleep_reserve
    remaining_active = active_budget

    # 1) Sleep / reserve blocks
    for t in targets:
        kind = str(t.get("kind") or "")
        if kind == "rolling_avg":
            mins = max(0, int(t.get("reserve_minutes") or int(float(t.get("target") or 0) * 60)))
            mins = min(mins, window)
            if mins:
                blocks.append(
                    {
                        "source": "target",
                        "id": t["id"],
                        "title": t.get("title") or t["id"],
                        "minutes": mins,
                        "role": "reserve",
                        "kind": kind,
                        "priority": int(t.get("priority") or 0),
                    }
                )
        elif kind == "fixed":
            mins = max(0, int(t.get("minutes") or 0))
            if mins:
                blocks.append(
                    {
                        "source": "target",
                        "id": t["id"],
                        "title": t.get("title") or t["id"],
                        "minutes": mins,
                        "role": "reserve",
                        "kind": kind,
                        "priority": int(t.get("priority") or 0),
                    }
                )

    # 2) Daily duration (fixed claims on active time), reduced by today's logs
    daily = [t for t in targets if str(t.get("kind")) == "daily_duration"]
    daily.sort(key=lambda t: (-int(t.get("priority") or 0), str(t.get("id"))))
    for t in daily:
        plan_m = max(0, int(t.get("minutes") or 0))
        min_m = int(t.get("minutes_min") if t.get("minutes_min") is not None else plan_m)
        max_m = int(t.get("minutes_max") if t.get("minutes_max") is not None else plan_m)
        logs = logs_for_target(state, str(t["id"]), since=today, until=today)
        done = int(sum(float(lg.get("value") or 0) for lg in logs))
        # Plan remaining toward the default plan amount; min is the "done enough" bar
        need = max(0, plan_m - done)
        if done >= min_m and need <= 0:
            notes.append(f"{t.get('title')}: already logged {done} min today (target {min_m}–{max_m})")
            continue
        if done >= min_m and need > 0:
            # Optional stretch toward plan/max — still schedule remaining plan minutes lightly
            pass
        if need <= 0:
            notes.append(f"{t.get('title')}: complete for today ({done} min)")
            continue
        take = min(need, remaining_active)
        if take <= 0:
            notes.append(f"Skipped {t.get('title')}: no active time left")
            continue
        if take < need:
            notes.append(f"Shorted {t.get('title')}: {take}/{need} min remaining")
        blocks.append(
            {
                "source": "target",
                "id": t["id"],
                "title": t.get("title") or t["id"],
                "minutes": take,
                "role": "fixed",
                "kind": "daily_duration",
                "priority": int(t.get("priority") or 0),
                "sessions_hint": t.get("sessions_hint"),
                "minutes_min": min_m,
                "minutes_max": max_m,
                "done_today": done,
            }
        )
        remaining_active -= take

    # 3) Weekly frequency — schedule a session if behind minimum
    weekly = [t for t in targets if str(t.get("kind")) == "weekly_frequency"]
    weekly.sort(key=lambda t: (-int(t.get("priority") or 0), str(t.get("id"))))
    for t in weekly:
        min_d = int(t.get("min_days") or 3)
        max_d = int(t.get("max_days") or 5)
        session = max(0, int(t.get("session_minutes") or 60))
        since = today - timedelta(days=6)
        logs = logs_for_target(state, str(t["id"]), since=since, until=today)
        days_done = {str(lg.get("date")) for lg in logs if float(lg.get("value") or 0) > 0}
        n = len(days_done)
        already_today = today.isoformat() in days_done
        # Include if behind min and not already logged today; skip if at/above max
        include = (n < min_d and not already_today) or (
            n < max_d and not already_today and n < min_d
        )
        # Cleaner: include when n < min_d and not today; if on track (min<=n<=max) still optional — include only if behind
        include = n < min_d and not already_today
        if already_today:
            notes.append(f"{t.get('title')}: already logged today ({n} days this week)")
            continue
        if n >= max_d:
            notes.append(f"{t.get('title')}: at max {max_d} days — no session planned")
            continue
        if not include and n >= min_d:
            notes.append(f"{t.get('title')}: on track ({n}/{min_d}–{max_d}) — no forced session")
            continue
        take = min(session, remaining_active)
        if take <= 0:
            notes.append(f"Skipped {t.get('title')} session: no active time left")
            continue
        blocks.append(
            {
                "source": "target",
                "id": t["id"],
                "title": t.get("title") or t["id"],
                "minutes": take,
                "role": "session",
                "kind": "weekly_frequency",
                "priority": int(t.get("priority") or 0),
                "reason": f"behind weekly min ({n}/{min_d})",
            }
        )
        remaining_active -= take

    # 4) Ad-hoc items by priority
    for it in list_items(state):
        mins = max(0, int(it.get("minutes") or 0))
        if mins <= 0:
            # Unsized ad-hoc: soft claim 30 min so they appear in the plan
            mins = 30
            soft = True
        else:
            soft = False
        take = min(mins, remaining_active)
        if take <= 0:
            notes.append(f"Deferred ad-hoc “{it.get('title')}”: no active time left")
            continue
        if take < mins:
            notes.append(f"Shorted ad-hoc “{it.get('title')}”: {take}/{mins} min")
        blocks.append(
            {
                "source": "item",
                "id": it["id"],
                "title": it.get("title") or it["id"],
                "minutes": take,
                "role": "adhoc",
                "kind": it.get("kind") or "task",
                "priority": int(it.get("priority") or 0),
                "soft_estimate": soft,
            }
        )
        remaining_active -= take

    # 5) Fill remainder (Lyft etc.)
    fillers = [t for t in targets if str(t.get("kind")) == "fill_remainder"]
    fillers.sort(key=lambda t: (-int(t.get("priority") or 0), str(t.get("id"))))
    if fillers and remaining_active > 0:
        # All remaining active time to highest-priority filler (usually one)
        primary = fillers[0]
        blocks.append(
            {
                "source": "target",
                "id": primary["id"],
                "title": primary.get("title") or primary["id"],
                "minutes": remaining_active,
                "role": "fill",
                "kind": "fill_remainder",
                "priority": int(primary.get("priority") or 0),
            }
        )
        remaining_active = 0
        for extra in fillers[1:]:
            notes.append(f"Fill target “{extra.get('title')}” unused (primary filler is {primary.get('title')})")
    elif remaining_active > 0:
        notes.append(f"{remaining_active} active min unallocated (add a fill_remainder target like Lyft)")

    total_block = sum(int(b["minutes"]) for b in blocks)
    kpis = kpi_status(state, as_of=today)

    return {
        "window_start": now.isoformat(timespec="seconds"),
        "window_end": end.isoformat(timespec="seconds"),
        "window_minutes": window,
        "sleep_reserve_minutes": sleep_reserve,
        "active_minutes": active_budget,
        "blocks": blocks,
        "total_block_minutes": total_block,
        "unallocated_active_minutes": remaining_active,
        "notes": notes,
        "kpi_status": kpis,
    }


def apply_plan(state: dict[str, Any], plan: dict[str, Any] | None = None, **plan_kwargs: Any) -> dict[str, Any]:
    """Compute plan (if needed), store on state, and mirror ad-hoc minutes from plan blocks."""
    out = normalize_state(state)
    plan = plan or build_rolling_plan(out, **plan_kwargs)
    out["plan"] = plan
    # Update item minutes from adhoc blocks so list view matches plan
    adhoc_mins = {
        b["id"]: int(b["minutes"])
        for b in plan.get("blocks") or []
        if b.get("source") == "item"
    }
    for it in out.get("items") or []:
        if it.get("id") in adhoc_mins:
            it["minutes"] = adhoc_mins[it["id"]]
    return out
