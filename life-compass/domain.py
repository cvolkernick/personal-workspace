"""Attention state machine, nag control, one-thing ranking, weekly constraint.

Pure functions — no I/O. State transitions generate pings; the clock does not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

GOAL = {
    "id": "goal_bf47e52a74b7",
    "slug": "life-compass",
    "name": "Life compass",
    "category": "productivity",
}

ATTENDED = "attended"
UNDER_ATTENDED = "under-attended"
UNATTENDED = "unattended"
WIRING_PLANNED = "wiring-planned"
UNAVAILABLE = "unavailable"

ATTENTION_STATES = (
    ATTENDED,
    UNDER_ATTENDED,
    UNATTENDED,
    WIRING_PLANNED,
    UNAVAILABLE,
)
FLAG_STATES = (UNDER_ATTENDED, UNATTENDED)
QUIET_STATES = (ATTENDED, WIRING_PLANNED, UNAVAILABLE)

PING_NEW = "new"
PING_STILL_OPEN = "still-open"
PING_RADAR = "radar"
PING_WEEKLY = "weekly-constraint"

DEFAULT_COOLDOWN_HOURS = 24.0
STALE_HOURS = 36.0
RADAR_DAILY_CAP = 3

LAYER_GOALS = "goals"
LAYER_OPS = "operations"
LAYER_RADAR = "radar"
LAYER_WEEKLY = "weekly"

_SEVERITY_SCORE = {
    "critical": 100,
    "high": 80,
    "medium": 50,
    "low": 25,
    "info": 10,
}


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def hours_since(ts: Any, *, now: datetime) -> Optional[float]:
    dt = parse_timestamp(ts)
    if dt is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def new_watch_item(
    item_id: str,
    *,
    layer: str,
    label: str,
    group: str = "",
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "layer": layer,
        "group": group,
        "label": label,
        "state": ATTENDED,
        "last_checked": None,
        "last_flagged": None,
        "last_ping_kind": None,
        "flag_count": 0,
        "cooldown_hours": float(cooldown_hours),
        "resolved_at": None,
        "severity": "info",
        "detail": "",
        "source": "",
        "wiring_dependency": None,
        "as_of": None,
        "stale": False,
    }


def _severity_rank(item: dict[str, Any]) -> int:
    return int(_SEVERITY_SCORE.get(str(item.get("severity") or "medium").lower(), 50))


def apply_observation(
    item: dict[str, Any],
    observed_state: str,
    *,
    now: datetime,
    detail: str = "",
    severity: str = "medium",
    source: str = "",
    as_of: Optional[str] = None,
    wiring_dependency: Optional[str] = None,
    cooldown_hours: Optional[float] = None,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Apply one observation. Returns (updated_item, ping_or_None).

    Nag control:
      - Flag once when crossing into under-attended / unattended.
      - Quiet during cooldown.
      - After cooldown, re-flag once as "still open". Never the same ping twice.
      - Resolved → attended, flag count reset, no ping.
      - wiring-planned / unavailable never ping.
    """
    observed = (observed_state or ATTENDED).strip().lower()
    if observed not in ATTENTION_STATES:
        observed = ATTENDED
    updated = dict(item)
    checked = iso(now)
    updated["last_checked"] = checked
    updated["as_of"] = as_of or checked
    updated["detail"] = (detail or "")[:800]
    updated["severity"] = (severity or "medium").lower()
    updated["source"] = source or updated.get("source") or ""
    updated["wiring_dependency"] = wiring_dependency
    if cooldown_hours is not None:
        updated["cooldown_hours"] = float(cooldown_hours)
    age = hours_since(updated["as_of"], now=now)
    updated["stale"] = bool(age is not None and age > STALE_HOURS)

    prev_state = (item.get("state") or ATTENDED).lower()
    ping: Optional[dict[str, Any]] = None

    if observed in QUIET_STATES:
        if prev_state in FLAG_STATES and observed == ATTENDED:
            updated["resolved_at"] = checked
        if observed == ATTENDED:
            updated["flag_count"] = 0
            updated["last_ping_kind"] = None
            updated["last_flagged"] = None
        updated["state"] = observed
        return updated, None

    # FLAG_STATES: under-attended / unattended
    cooldown = float(updated.get("cooldown_hours") or DEFAULT_COOLDOWN_HOURS)
    last_flagged = parse_timestamp(item.get("last_flagged"))
    last_kind = item.get("last_ping_kind")
    already_open = prev_state in FLAG_STATES

    updated["state"] = observed
    if not already_open:
        updated["flag_count"] = int(item.get("flag_count") or 0) + 1
        updated["last_flagged"] = checked
        updated["last_ping_kind"] = PING_NEW
        updated["resolved_at"] = None
        ping = _make_ping(updated, PING_NEW, now)
        return updated, ping

    # Still open. Quiet inside cooldown. After cooldown, one "still open".
    if last_kind == PING_STILL_OPEN:
        return updated, None
    elapsed = None
    if last_flagged is not None:
        elapsed = (now - last_flagged).total_seconds() / 3600.0
    if elapsed is None or elapsed < cooldown:
        return updated, None
    updated["flag_count"] = int(item.get("flag_count") or 1) + 1
    updated["last_flagged"] = checked
    updated["last_ping_kind"] = PING_STILL_OPEN
    ping = _make_ping(updated, PING_STILL_OPEN, now)
    return updated, ping


def _make_ping(item: dict[str, Any], kind: str, now: datetime) -> dict[str, Any]:
    label = item.get("label") or item.get("id")
    if kind == PING_STILL_OPEN:
        message = f"Still open: {label}. {item.get('detail') or ''}".strip()
    else:
        message = f"Unattended: {label}. {item.get('detail') or ''}".strip()
    return {
        "item_id": item.get("id"),
        "kind": kind,
        "layer": item.get("layer"),
        "severity": item.get("severity"),
        "message": message[:600],
        "at": iso(now),
    }


def local_hour(now: datetime, tz_name: str = "America/New_York") -> int:
    try:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = now.astimezone(timezone(timedelta(hours=-4)))
    return int(local.hour)


def one_thing(
    items: list[dict[str, Any]],
    *,
    now: datetime,
    hour: Optional[int] = None,
    tomorrow_ops: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Top-ranked unattended item, or explicit All quiet.

    Morning (05–11): what today needs (operations first).
    Midday (12–16): course correction (goal signals first).
    Evening (17–04): what is on deck tomorrow, else remaining unattended.
    """
    hour = int(hour if hour is not None else local_hour(now))
    open_items = [
        i
        for i in items
        if (i.get("state") or "") in FLAG_STATES and not i.get("stale")
    ]
    # Stale unattended still counts — operator should see it — but prefer fresh.
    if not open_items:
        open_items = [i for i in items if (i.get("state") or "") in FLAG_STATES]
    if not open_items:
        return {
            "kind": "all-quiet",
            "item": None,
            "text": "All quiet",
            "hour": hour,
            "as_of": iso(now),
        }

    ops = [i for i in open_items if i.get("layer") == LAYER_OPS]
    goals = [i for i in open_items if i.get("layer") == LAYER_GOALS]
    rest = [i for i in open_items if i.get("layer") not in (LAYER_OPS, LAYER_GOALS)]

    def pick(pool: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not pool:
            return None
        ranked = sorted(
            pool,
            key=lambda i: (-_severity_rank(i), str(i.get("id") or "")),
        )
        return ranked[0]

    chosen: Optional[dict[str, Any]] = None
    window = "midday"
    if 5 <= hour <= 11:
        window = "morning"
        chosen = pick(ops) or pick(goals) or pick(rest)
    elif 12 <= hour <= 16:
        window = "midday"
        chosen = pick(goals) or pick(ops) or pick(rest)
    else:
        window = "evening"
        if tomorrow_ops:
            chosen = pick(
                [i for i in open_items if i.get("id") in {t.get("id") for t in tomorrow_ops}]
            )
        chosen = chosen or pick(ops) or pick(goals) or pick(rest)

    assert chosen is not None
    return {
        "kind": "focus",
        "item": chosen,
        "text": chosen.get("label") or chosen.get("id"),
        "detail": chosen.get("detail") or "",
        "hour": hour,
        "window": window,
        "as_of": iso(now),
    }


def missing_anything(items: list[dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    open_items = [i for i in items if (i.get("state") or "") in FLAG_STATES]
    wiring = [i for i in items if (i.get("state") or "") == WIRING_PLANNED]
    unavailable = [i for i in items if (i.get("state") or "") == UNAVAILABLE]
    if not open_items:
        text = "Nothing unattended."
        if wiring:
            text += f" {len(wiring)} signal(s) wiring planned."
        if unavailable:
            text += f" {len(unavailable)} source(s) unavailable."
        return {
            "missing": False,
            "text": text,
            "items": [],
            "wiring_planned": [
                {"id": i.get("id"), "dependency": i.get("wiring_dependency")}
                for i in wiring
            ],
            "as_of": iso(now),
        }
    ranked = sorted(
        open_items, key=lambda i: (-_severity_rank(i), str(i.get("id") or ""))
    )
    return {
        "missing": True,
        "text": f"{len(ranked)} item(s) unattended or under-attended.",
        "items": ranked,
        "wiring_planned": [
            {"id": i.get("id"), "dependency": i.get("wiring_dependency")}
            for i in wiring
        ],
        "as_of": iso(now),
    }


def iso_week_key(now: datetime) -> str:
    y, w, _ = now.astimezone(timezone.utc).isocalendar()
    return f"{y:04d}-W{w:02d}"


def weekly_constraint(
    items: list[dict[str, Any]],
    *,
    now: datetime,
    prior: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """One bottleneck per ISO week. UNKNOWN is valid. Quiet weeks stay quiet.

    Heuristic: highest-severity unattended/under-attended item. No LLM.
    Nag semantics: flag once per week; still-open only after a week cooldown
    if the same constraint remains.
    """
    week = iso_week_key(now)
    open_items = [i for i in items if (i.get("state") or "") in FLAG_STATES]
    prior = prior or {}
    prior_week = prior.get("iso_week")
    prior_id = prior.get("item_id")
    prior_kind = prior.get("last_ping_kind")

    if not open_items:
        record = {
            "iso_week": week,
            "item_id": None,
            "label": None,
            "kind": "unknown",
            "text": "UNKNOWN — data does not support a clear constraint.",
            "last_ping_kind": None,
            "as_of": iso(now),
        }
        return record, None

    ranked = sorted(
        open_items, key=lambda i: (-_severity_rank(i), str(i.get("id") or ""))
    )
    top = ranked[0]
    record = {
        "iso_week": week,
        "item_id": top.get("id"),
        "label": top.get("label"),
        "kind": "constraint",
        "text": (
            f"Biggest constraint: {top.get('label')}. "
            f"{top.get('detail') or ''}"
        ).strip(),
        "severity": top.get("severity"),
        "last_ping_kind": prior_kind if prior_week == week else None,
        "as_of": iso(now),
    }

    ping: Optional[dict[str, Any]] = None
    if prior_week == week:
        # Already identified this week — no extra ping.
        record["last_ping_kind"] = prior_kind
        return record, None

    same_as_last = prior_id == top.get("id") and prior_kind in (PING_NEW, PING_STILL_OPEN)
    if not same_as_last or prior_kind is None:
        kind = PING_NEW
    elif prior_kind == PING_NEW:
        kind = PING_STILL_OPEN
    else:
        return record, None

    record["last_ping_kind"] = kind
    ping = {
        "item_id": top.get("id"),
        "kind": PING_WEEKLY,
        "weekly_kind": kind,
        "layer": LAYER_WEEKLY,
        "severity": top.get("severity") or "high",
        "message": (
            f"Weekly constraint{' (still open)' if kind == PING_STILL_OPEN else ''}: "
            f"{top.get('label')}. {top.get('detail') or ''}"
        ).strip()[:600],
        "at": iso(now),
    }
    return record, ping
