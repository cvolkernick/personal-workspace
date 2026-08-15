"""Clock-following NOW / NEXT / THEN from a filed rolling plan.

P1 flies persisted ``plan_blocks`` as a sequential itinerary. Reserve
(sleep) is parked at the **end** of the window so a midday rebuild does
not highlight sleep as NOW. Calendar busy is a duration chunk, not a
timed waypoint (Phase 2).

Honesty: missing/unparseable/empty/expired plan → stale, ``now`` is
null. Never invent a current task.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

NO_LIVE_PLAN = "no live plan — rebuild"


def _parse_dt(value: Any, *, fallback_tz: timezone | None = None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=fallback_tz or timezone.utc)
    return dt


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc).astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc).astimezone()
    return now


def fly_order(blocks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Active blocks in filed order, then reserve (sleep) last."""
    active: list[dict[str, Any]] = []
    reserve: list[dict[str, Any]] = []
    for block in list(blocks or []):
        try:
            minutes = int(block.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0:
            continue
        if str(block.get("role") or "") == "reserve":
            reserve.append(block)
        else:
            active.append(block)
    return active + reserve


def _leg_view(
    block: dict[str, Any],
    start: datetime,
    end: datetime,
    now: datetime,
) -> dict[str, Any]:
    remaining = max(0, int((end - now).total_seconds()))
    return {
        "id": str(block.get("id") or ""),
        "title": str(block.get("title") or block.get("id") or ""),
        "role": str(block.get("role") or ""),
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
        "remaining_seconds": remaining,
        "minutes": int(block.get("minutes") or 0),
    }


def _empty(generated: datetime) -> dict[str, Any]:
    return {
        "now": None,
        "next": None,
        "then": None,
        "generated_at": generated.isoformat(timespec="seconds"),
        "stale": True,
        "reason": NO_LIVE_PLAN,
    }


def compose_now_next(
    plan: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Derive ``{now, next, then, generated_at, stale}`` from a filed plan.

    Uses the same ``plan`` object the pies persist (``state.plan``). Does
    not rebuild or invent blocks.
    """
    generated = _aware(now)
    if not isinstance(plan, dict):
        return _empty(generated)

    win_start = _parse_dt(plan.get("window_start"), fallback_tz=generated.tzinfo)
    if win_start is None:
        return _empty(generated)
    win_start = win_start.astimezone(generated.tzinfo)

    legs: list[tuple[dict[str, Any], datetime, datetime]] = []
    cursor = win_start
    for block in fly_order(plan.get("blocks")):
        minutes = int(block.get("minutes") or 0)
        start = cursor
        end = cursor + timedelta(minutes=minutes)
        legs.append((block, start, end))
        cursor = end

    if not legs:
        return _empty(generated)

    last_end = legs[-1][2]
    if generated >= last_end:
        return _empty(generated)

    def view_at(index: int) -> dict[str, Any] | None:
        if index < 0 or index >= len(legs):
            return None
        block, start, end = legs[index]
        return _leg_view(block, start, end, generated)

    if generated < win_start:
        return {
            "now": None,
            "next": view_at(0),
            "then": view_at(1),
            "generated_at": generated.isoformat(timespec="seconds"),
            "stale": False,
            "reason": None,
        }

    for i, (_block, start, end) in enumerate(legs):
        if start <= generated < end:
            return {
                "now": view_at(i),
                "next": view_at(i + 1),
                "then": view_at(i + 2),
                "generated_at": generated.isoformat(timespec="seconds"),
                "stale": False,
                "reason": None,
            }

    return _empty(generated)
