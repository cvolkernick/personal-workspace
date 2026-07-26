"""Next-action / priority suggestions from plan + KPI state."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .domain import build_rolling_plan, get_target, kpi_status, list_items, normalize_state
from .lyft_duty import lyft_duty_status
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
        if b.get("role") in ("fixed", "session", "adhoc", "fill")
        and int(b.get("minutes") or 0) > 0
    ]
    actionable.sort(key=lambda b: (-int(b.get("priority") or 0), str(b.get("role")), str(b.get("id"))))

    # Explicit urgency for core personal tasks (overrides role defaults)
    ID_URGENCY = {
        "lyft": "high",
        "workout": "medium",
        "duchess-walk": "low",
    }

    for b in actionable:
        role = str(b.get("role"))
        bid = str(b.get("id") or "")
        loggable = True
        log_mode = "minutes"  # or "session"
        if role == "fixed":
            urgency = "high"
            reason = b.get("reason") or "Daily target still open in this 24h window"
            if b.get("minutes_min") is not None:
                done = int(b.get("done_today") or 0)
                left = int(b.get("minutes") or 0)
                reason = (
                    f"Daily target: aim {b.get('minutes_min')}–{b.get('minutes_max')} min "
                    f"(plan {left + done}m · {done}m logged in last 24h · {left}m still planned)"
                )
        elif role == "session":
            urgency = "high"
            reason = b.get("reason") or "Weekly frequency target needs a session"
            log_mode = "session"
        elif role == "fill":
            urgency = "low"
            reason = (
                f"{b.get('minutes')} min of Lyft drive capacity in this window"
                + (f" — {b.get('lyft_duty_summary')}" if b.get("lyft_duty_summary") else "")
            )
            if b.get("remaining_drive_minutes") is not None:
                reason += (
                    f" ({int(b['remaining_drive_minutes'])}m left in current 12h driver-mode block)"
                )
        elif role == "break":
            urgency = "medium"
            reason = b.get("reason") or "Mandatory offline break (Lyft 6h after 12h driver mode)"
            loggable = False
        else:
            urgency = "medium" if int(b.get("priority") or 0) >= 5 else "low"
            reason = "Ad-hoc item on the plan"
            if b.get("soft_estimate"):
                reason += " (soft 30m estimate — set real minutes if needed)"
            if b.get("done_minutes"):
                reason += f" ({b.get('done_minutes')}m already logged)"
        if bid in ID_URGENCY:
            urgency = ID_URGENCY[bid]
        suggestions.append(
            {
                "id": bid,
                "title": str(b.get("title") or bid),
                "reason": reason,
                "priority": int(b.get("priority") or 0),
                "minutes": int(b.get("minutes") or 0),
                "role": role,
                "source": str(b.get("source") or ""),
                "urgency": urgency,
                "loggable": loggable,
                "log_mode": log_mode,
                "done_today": int(b.get("done_today") or b.get("done_minutes") or 0),
                "remaining_drive_minutes": b.get("remaining_drive_minutes"),
                "drive_cap_minutes": b.get("drive_cap_minutes"),
                "break_minutes": b.get("break_minutes"),
                "lyft_duty_summary": b.get("lyft_duty_summary"),
            }
        )

    # Surface Lyft duty: at-limit break countdown, or stale update prompt
    lyft_tgt = get_target(state, "lyft")
    if lyft_tgt is not None:
        duty = lyft_duty_status(state, target=lyft_tgt)
        has_lyft = any(s.get("id") == "lyft" for s in suggestions)
        if duty.get("at_limit"):
            if duty.get("break_complete"):
                reason = (
                    "Mandatory 6h offline break is complete — reset duty to 0h to start a new 12h cycle"
                )
                urgency = "medium"
            elif duty.get("break_remaining_minutes") is not None:
                total_m = max(0, int(round(float(duty["break_remaining_minutes"]))))
                hr, mn = total_m // 60, total_m % 60
                reason = (
                    f"At 12h cap — {hr}h {mn}m left in mandatory "
                    f"6h offline break before you can drive again"
                )
                urgency = "high"
            else:
                reason = duty.get("summary") or "12h driver-mode used — 6h offline break required"
                urgency = "high"
            if not has_lyft:
                suggestions.insert(
                    0,
                    {
                        "id": "lyft",
                        "title": "Lyft — offline break",
                        "reason": reason,
                        "priority": int(lyft_tgt.get("priority") or 3),
                        "minutes": int(duty.get("break_remaining_minutes") or duty.get("break_minutes") or 0),
                        "role": "break",
                        "source": "target",
                        "urgency": urgency,
                        "loggable": False,
                        "log_mode": "duty",
                        "done_today": duty.get("driven_minutes") or 0,
                        "remaining_drive_minutes": 0,
                        "drive_cap_minutes": duty.get("drive_cap_minutes"),
                        "break_minutes": duty.get("break_minutes"),
                        "break_remaining_minutes": duty.get("break_remaining_minutes"),
                        "break_ends_at": duty.get("break_ends_at"),
                        "break_complete": duty.get("break_complete"),
                        "lyft_duty_summary": duty.get("summary"),
                    },
                )
            else:
                for s in suggestions:
                    if s.get("id") == "lyft":
                        s["reason"] = reason
                        s["urgency"] = urgency
                        s["break_remaining_minutes"] = duty.get("break_remaining_minutes")
                        s["break_ends_at"] = duty.get("break_ends_at")
                        s["break_complete"] = duty.get("break_complete")
                        s["lyft_duty_summary"] = duty.get("summary")
        elif duty.get("needs_update_prompt"):
            # Stale duty — prompt near top
            mins = duty.get("minutes_since_update")
            if mins is not None:
                age = f"{int(mins // 60)}h {int(mins % 60)}m ago"
            else:
                age = "unknown time ago"
            stale_item = {
                "id": "lyft-update",
                "title": "Update Lyft hours driven",
                "reason": (
                    f"Last duty entry was {age} (>6h). "
                    "Refresh hours driven in the current 12h block so the plan stays accurate."
                ),
                "priority": 9,
                "minutes": 1,
                "role": "meta",
                "source": "lyft_duty",
                "urgency": "high",
                "loggable": False,
                "log_mode": "duty",
                "lyft_duty_summary": duty.get("summary"),
            }
            # Dedupe
            if not any(s.get("id") == "lyft-update" for s in suggestions):
                suggestions.insert(0, stale_item)

    # 2) Sleep battery (full at wake, drains over ~16h awake) + 7d KPI
    battery = sleep_battery_for_state(state, now=now)
    pct = float(battery.get("pct_charged") or battery.get("pct_of_target") or 0)
    until_empty = float(battery.get("hours_until_empty") or 0)
    target_h = float(battery.get("sleep_target_hours") or battery.get("target_hours") or 8)
    if battery.get("data_source") == "none" or (
        battery.get("interval_count_stored", 0) == 0
        and battery.get("data_source") != "daily_log_approx"
    ):
        suggestions.append(
            {
                "id": "sleep-log",
                "title": "Sync sleep intervals (Google Health)",
                "reason": "No sleep cycle data for the wake/drain battery",
                "priority": 10,
                "minutes": 2,
                "role": "meta",
                "source": "kpi",
                "urgency": "high",
            }
        )
    elif battery.get("mode") == "awake" and (pct <= 25 or until_empty <= 3):
        suggestions.append(
            {
                "id": "sleep-protect",
                "title": "Protect sleep — battery low",
                "reason": (
                    f"Sleep battery at {pct:.0f}% "
                    f"({until_empty:.1f}h of wake budget left). "
                    f"Plan ~{target_h:g}h sleep to recharge and support the 7d average."
                ),
                "priority": 10,
                "minutes": int(target_h * 60),
                "role": "reserve",
                "source": "sleep_battery",
                "urgency": "high",
            }
        )

    sleep = kpis.get("sleep")
    if sleep is not None and sleep.get("on_track") is False and pct > 25:
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

    # Calendar: surface next commitment / heavy day
    cal_busy = int(plan.get("calendar_busy_minutes") or 0)
    cal_block = next(
        (b for b in plan_blocks if str(b.get("id")) == "calendar"),
        None,
    )
    if cal_block and cal_busy > 0:
        next_ev = None
        for ev in cal_block.get("events") or []:
            next_ev = ev
            break
        reason = cal_block.get("reason") or f"{cal_busy}m of calendar commitments ahead"
        if next_ev:
            reason = (
                f"Next: {next_ev.get('title')} ({next_ev.get('minutes')}m) · "
                f"{cal_busy}m total calendar busy in window"
            )
        suggestions.append(
            {
                "id": "calendar-commitments",
                "title": "Honor calendar commitments",
                "reason": reason,
                "priority": 8,
                "minutes": min(cal_busy, int((cal_block.get("events") or [{}])[0].get("minutes") or cal_busy)),
                "role": "calendar",
                "source": "calendar",
                "urgency": "high" if cal_busy >= 180 else "medium",
                "loggable": False,
            }
        )
    else:
        meta = state.get("calendar_meta") or {}
        if not (state.get("calendar_events") or []) and not meta.get("synced_at"):
            suggestions.append(
                {
                    "id": "calendar-sync",
                    "title": "Sync Google Calendar",
                    "reason": (
                        "Calendar not loaded yet — sync so busy events reduce free "
                        "Lyft/fill time in the 24h plan"
                    ),
                    "priority": 6,
                    "minutes": 1,
                    "role": "meta",
                    "source": "calendar",
                    "urgency": "low",
                    "loggable": False,
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
