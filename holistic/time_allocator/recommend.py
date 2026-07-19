"""Next-action / priority suggestions from plan + KPI state."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .domain import build_rolling_plan, kpi_status, list_items, normalize_state
from .sleep_battery import sleep_battery_for_state


def recommend_next(
    state: dict[str, Any],
    *,
    plan: dict[str, Any] | None = None,
    now: datetime | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return ordered suggestions for what to do next in the rolling window.

    Each suggestion:
      id, title, reason, priority, minutes?, role, source, urgency (high|medium|low)
    """
    state = normalize_state(state)
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()
    today = now.date()
    plan = plan or build_rolling_plan(state, now=now, as_of=today)
    kpis = {k["id"]: k for k in kpi_status(state, as_of=today)}
    suggestions: list[dict[str, Any]] = []

    # 1) Incomplete fixed / session blocks from the live plan (highest plan priority first)
    plan_blocks = list(plan.get("blocks") or [])
    actionable = [
        b
        for b in plan_blocks
        if b.get("role") in ("fixed", "session", "adhoc") and int(b.get("minutes") or 0) > 0
    ]
    actionable.sort(key=lambda b: (-int(b.get("priority") or 0), str(b.get("role")), str(b.get("id"))))

    for b in actionable:
        role = str(b.get("role"))
        if role == "fixed":
            urgency = "high"
            reason = b.get("reason") or "Daily target still open in this 24h window"
            if b.get("minutes_min") is not None:
                reason = (
                    f"Daily target: aim {b.get('minutes_min')}–{b.get('minutes_max')} min "
                    f"({b.get('minutes')} min still planned"
                    + (f", {b.get('done_today')} done today" if b.get("done_today") else "")
                    + ")"
                )
        elif role == "session":
            urgency = "high"
            reason = b.get("reason") or "Weekly frequency target needs a session"
        else:
            urgency = "medium" if int(b.get("priority") or 0) >= 5 else "low"
            reason = "Ad-hoc item on the plan"
            if b.get("soft_estimate"):
                reason += " (soft 30m estimate — set real minutes if needed)"
        suggestions.append(
            {
                "id": str(b.get("id")),
                "title": str(b.get("title") or b.get("id")),
                "reason": reason,
                "priority": int(b.get("priority") or 0),
                "minutes": int(b.get("minutes") or 0),
                "role": role,
                "source": str(b.get("source") or ""),
                "urgency": urgency,
            }
        )

    # 2) Sleep battery (rolling 24h hours asleep) + 7d KPI
    battery = sleep_battery_for_state(state, now=now)
    asleep = float(battery.get("asleep_hours") or 0)
    target_h = float(battery.get("target_hours") or 8)
    if battery.get("data_source") == "none" or (
        battery.get("interval_count_stored", 0) == 0 and battery.get("data_source") != "daily_log_approx"
    ):
        suggestions.append(
            {
                "id": "sleep-log",
                "title": "Sync sleep intervals (Google Health)",
                "reason": "No timed sleep data for the rolling 24h battery",
                "priority": 10,
                "minutes": 2,
                "role": "meta",
                "source": "kpi",
                "urgency": "high",
            }
        )
    elif asleep < target_h * 0.75:
        suggestions.append(
            {
                "id": "sleep-protect",
                "title": "Protect sleep — battery low",
                "reason": (
                    f"Only {asleep:.1f}h asleep in the last 24h "
                    f"(target {target_h:g}h). Older sleep falls off the window as time passes."
                ),
                "priority": 10,
                "minutes": int(max(0, (target_h - asleep) * 60)),
                "role": "reserve",
                "source": "sleep_battery",
                "urgency": "high",
            }
        )

    sleep = kpis.get("sleep")
    if sleep is not None and sleep.get("on_track") is False and asleep >= target_h * 0.75:
        suggestions.append(
            {
                "id": "sleep-7d",
                "title": "Rebuild 7-day sleep average",
                "reason": sleep.get("summary") or "Rolling 7-day average still below target",
                "priority": 9,
                "minutes": 480,
                "role": "reserve",
                "source": "kpi",
                "urgency": "medium",
            }
        )

    # 3) Fill work as fallback (only if nothing higher still open)
    fill_blocks = [b for b in plan_blocks if b.get("role") == "fill" and int(b.get("minutes") or 0) > 0]
    if fill_blocks:
        fb = max(fill_blocks, key=lambda b: int(b.get("minutes") or 0))
        suggestions.append(
            {
                "id": str(fb.get("id")),
                "title": str(fb.get("title") or "Fill work"),
                "reason": f"{fb.get('minutes')} min of active capacity left after fixed obligations",
                "priority": int(fb.get("priority") or 0),
                "minutes": int(fb.get("minutes") or 0),
                "role": "fill",
                "source": "target",
                "urgency": "low",
            }
        )

    # De-dupe by id keeping first (highest urgency path)
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    urgency_rank = {"high": 0, "medium": 1, "low": 2}

    def sort_key(s: dict[str, Any]) -> tuple:
        return (
            urgency_rank.get(str(s.get("urgency")), 9),
            -int(s.get("priority") or 0),
            str(s.get("id")),
        )

    for s in sorted(suggestions, key=sort_key):
        sid = str(s.get("id"))
        if sid in seen:
            continue
        seen.add(sid)
        ordered.append(s)
        if len(ordered) >= max(1, int(limit)):
            break

    # If somehow empty, nudge user to seed
    if not ordered:
        if not state.get("targets"):
            ordered.append(
                {
                    "id": "seed",
                    "title": "Load personal targets",
                    "reason": "No ongoing targets configured yet",
                    "priority": 10,
                    "minutes": 0,
                    "role": "meta",
                    "source": "system",
                    "urgency": "high",
                }
            )
        elif not list_items(state) and not any(
            b.get("role") in ("fixed", "session") for b in plan_blocks
        ):
            ordered.append(
                {
                    "id": "lyft",
                    "title": "Lyft driving (fill)",
                    "reason": "No fixed obligations open — fill active time",
                    "priority": 3,
                    "minutes": int(plan.get("active_minutes") or 0),
                    "role": "fill",
                    "source": "target",
                    "urgency": "low",
                }
            )
    return ordered
