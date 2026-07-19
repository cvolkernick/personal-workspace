"""Lyft driver-mode duty cycle: 12h online then mandatory 6h break.

Platform rule (Lyft Help — “Taking breaks and time limits in driver mode”):
  - You must take a full, uninterrupted 6-hour break for every 12 hours
    in driver mode.
  - The 12 hours do not have to be consecutive.
  - After 12 hours, the app blocks driver mode until the 6-hour break is done.

User model: set “hours already driven in the current 12h block”; the plan
allocates only the *remaining* drive capacity.

Also tracks:
  - stale duty: prompt if last update was more than 6 hours ago
  - break countdown: when driven hits 12h, record when the mandatory 6h offline
    ends so the user knows when they can drive again
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

# Lyft platform defaults
DEFAULT_DRIVE_CAP_MINUTES = 12 * 60  # 12 hours driver mode
DEFAULT_BREAK_MINUTES = 6 * 60  # uninterrupted offline break
# Prompt to refresh duty if no update for this long
STALE_AFTER_MINUTES = 6 * 60


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def duty_defaults_from_target(target: dict[str, Any] | None) -> tuple[int, int]:
    t = target or {}
    cap = _int(t.get("drive_cap_minutes"), DEFAULT_DRIVE_CAP_MINUTES)
    brk = _int(t.get("break_minutes"), DEFAULT_BREAK_MINUTES)
    return max(60, cap), max(0, brk)


def get_lyft_duty(state: dict[str, Any]) -> dict[str, Any]:
    raw = (state or {}).get("lyft_duty") or {}
    driven = max(0, _int(raw.get("driven_minutes"), 0))
    return {
        "driven_minutes": driven,
        "updated_at": raw.get("updated_at"),
        "cap_reached_at": raw.get("cap_reached_at"),
        "note": str(raw.get("note") or ""),
    }


def set_lyft_driven(
    state: dict[str, Any],
    driven_minutes: float | int,
    *,
    note: str = "",
    drive_cap_minutes: int = DEFAULT_DRIVE_CAP_MINUTES,
    break_minutes: int = DEFAULT_BREAK_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Set hours already used in the current 12h driver-mode block (clamped 0…cap).

    When driven reaches the 12h cap, records cap_reached_at (start of mandatory
    6h offline). Clearing driven below cap (or to 0 after a break) clears that.
    """
    out = deepcopy(state) if state else {}
    cap = max(60, int(drive_cap_minutes))
    brk = max(0, int(break_minutes))
    now = now or _now()
    prev = get_lyft_duty(out)
    prev_driven = min(cap, max(0, int(prev.get("driven_minutes") or 0)))
    driven = max(0, min(cap, int(round(float(driven_minutes)))))

    updated_at = now.isoformat(timespec="seconds")
    cap_reached_at = prev.get("cap_reached_at")

    if driven >= cap:
        # First time hitting the cap this cycle — start the break clock
        if prev_driven < cap or not cap_reached_at:
            cap_reached_at = updated_at
        # Keep existing cap_reached_at if already at cap (don't restart break clock)
        elif prev_driven >= cap and cap_reached_at:
            pass
    else:
        # Below cap: not in mandatory break (reset after break or mid-cycle update)
        cap_reached_at = None

    out["lyft_duty"] = {
        "driven_minutes": driven,
        "updated_at": updated_at,
        "cap_reached_at": cap_reached_at,
        "note": (note or "").strip(),
        "drive_cap_minutes": cap,
        "break_minutes": brk,
    }
    return out


def lyft_duty_status(
    state: dict[str, Any],
    *,
    target: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Snapshot for UI / API: driven, remaining, break countdown, stale prompt."""
    now = now or _now()
    cap, brk = duty_defaults_from_target(target)
    duty = get_lyft_duty(state)
    driven = min(cap, max(0, int(duty["driven_minutes"])))
    remaining = max(0, cap - driven)
    at_limit = remaining <= 0
    pct = round((driven / cap) * 100, 1) if cap else 0

    updated_at = duty.get("updated_at")
    updated_dt = _parse_dt(updated_at)
    minutes_since_update: float | None = None
    stale = False
    if updated_dt is not None:
        minutes_since_update = max(
            0.0, (now - updated_dt.astimezone(now.tzinfo)).total_seconds() / 60.0
        )
        stale = minutes_since_update >= STALE_AFTER_MINUTES
    elif driven > 0:
        # Has driven time but never timestamped — treat as needing an update
        stale = True
        minutes_since_update = None

    # Break countdown from when 12h was logged
    cap_reached_at = duty.get("cap_reached_at")
    cap_reached_dt = _parse_dt(cap_reached_at)
    break_ends_at: str | None = None
    break_remaining_minutes: float | None = None
    break_complete = False
    can_drive_again = not at_limit

    if at_limit and brk > 0:
        # Prefer explicit cap_reached_at; fall back to updated_at when at cap
        start = cap_reached_dt or updated_dt
        if start is not None:
            start = start.astimezone(now.tzinfo)
            ends = start + timedelta(minutes=brk)
            break_ends_at = ends.isoformat(timespec="seconds")
            rem_sec = (ends - now).total_seconds()
            break_remaining_minutes = max(0.0, rem_sec / 60.0)
            break_complete = rem_sec <= 0
            can_drive_again = break_complete
        else:
            # At limit but no timestamp — prompt to log 12h / set cap time
            break_remaining_minutes = float(brk)
            break_complete = False
            can_drive_again = False

    summary_parts = [
        f"{driven // 60}h {driven % 60}m of {cap // 60}h driver-mode used "
        f"({remaining // 60}h {remaining % 60}m left this cycle)"
    ]
    if at_limit:
        if break_complete:
            summary_parts.append(
                f"mandatory {brk // 60}h break complete — reset duty to 0 to start a new cycle"
            )
        elif break_remaining_minutes is not None and break_ends_at:
            hr = int(break_remaining_minutes // 60)
            mn = int(round(break_remaining_minutes % 60))
            summary_parts.append(
                f"{hr}h {mn}m until 6h offline break ends (can drive again after)"
            )
        else:
            summary_parts.append(f"{brk // 60}h uninterrupted break required before driving again")
    if stale:
        if minutes_since_update is not None:
            summary_parts.append(
                f"duty last updated {int(minutes_since_update // 60)}h "
                f"{int(minutes_since_update % 60)}m ago — please refresh hours driven"
            )
        else:
            summary_parts.append("duty needs an update (no recent entry)")

    return {
        "driven_minutes": driven,
        "drive_cap_minutes": cap,
        "break_minutes": brk,
        "remaining_drive_minutes": remaining,
        "at_limit": at_limit,
        "pct_of_cap": pct,
        "updated_at": updated_at,
        "cap_reached_at": cap_reached_at if at_limit else None,
        "minutes_since_update": (
            round(minutes_since_update, 1) if minutes_since_update is not None else None
        ),
        "stale": stale,
        "stale_after_minutes": STALE_AFTER_MINUTES,
        "needs_update_prompt": stale,
        "break_ends_at": break_ends_at,
        "break_remaining_minutes": (
            round(break_remaining_minutes, 1)
            if break_remaining_minutes is not None
            else None
        ),
        "break_complete": break_complete,
        "can_drive_again": can_drive_again,
        "note": duty.get("note") or "",
        "summary": " · ".join(summary_parts),
        "policy": (
            "Lyft: full uninterrupted 6-hour break for every 12 hours in driver mode "
            "(12h need not be consecutive). CA may impose tighter passenger-hour rules."
        ),
    }


def schedule_drive_in_window(
    available_minutes: int,
    driven_minutes: int,
    *,
    drive_cap_minutes: int = DEFAULT_DRIVE_CAP_MINUTES,
    break_minutes: int = DEFAULT_BREAK_MINUTES,
    allow_next_cycle: bool = False,
) -> dict[str, Any]:
    """How much drive (and optional break) fits in available free minutes.

    allow_next_cycle: if True (recommended full-day plan), after finishing the
    current 12h block schedule the mandatory 6h break and a second drive stint.
    If False (remaining work / "now"), only allocate what's left in the current
    12h block so the user can set driven-so-far and see remaining capacity.
    """
    avail = max(0, int(available_minutes))
    cap = max(60, int(drive_cap_minutes))
    brk = max(0, int(break_minutes))
    driven = max(0, min(cap, int(driven_minutes)))
    remaining_cycle = max(0, cap - driven)

    segments: list[dict[str, Any]] = []
    notes: list[str] = []
    left = avail
    total_drive = 0

    if remaining_cycle <= 0:
        if allow_next_cycle and brk > 0 and left >= brk:
            segments.append(
                {
                    "role": "break",
                    "minutes": brk,
                    "title": "Lyft mandatory offline break",
                    "reason": "12h driver-mode cap reached — 6h uninterrupted offline required",
                }
            )
            left -= brk
            notes.append(
                f"Lyft: at 12h cap — scheduled {brk // 60}h mandatory break, then more drive"
            )
            stint = min(cap, left)
            if stint > 0:
                segments.append(
                    {
                        "role": "drive",
                        "minutes": stint,
                        "title": "Lyft driving",
                        "reason": "after mandatory break (new 12h cycle)",
                    }
                )
                total_drive += stint
                left -= stint
        elif brk > 0:
            notes.append(
                f"Lyft: 12h driver-mode used — need {brk // 60}h uninterrupted break "
                f"before more driving (only {left}m free left in window)"
            )
            if allow_next_cycle is False:
                show_break = min(brk, left)
                if show_break > 0:
                    segments.append(
                        {
                            "role": "break",
                            "minutes": show_break,
                            "title": "Lyft mandatory offline break",
                            "reason": "complete 6h offline before driver mode unlocks",
                        }
                    )
        return {
            "drive_minutes": total_drive,
            "segments": segments,
            "notes": notes,
            "remaining_cycle_before": remaining_cycle,
        }

    stint1 = min(remaining_cycle, left)
    if stint1 > 0:
        segments.append(
            {
                "role": "drive",
                "minutes": stint1,
                "title": "Lyft driving",
                "reason": f"{remaining_cycle}m left in current 12h driver-mode block",
            }
        )
        total_drive += stint1
        left -= stint1

    used_full_cycle = stint1 >= remaining_cycle and remaining_cycle > 0
    if allow_next_cycle and used_full_cycle and left > 0 and brk > 0:
        if left >= brk:
            segments.append(
                {
                    "role": "break",
                    "minutes": brk,
                    "title": "Lyft mandatory offline break",
                    "reason": "after completing 12h driver mode",
                }
            )
            left -= brk
            notes.append(
                f"Lyft: after finishing this 12h block, plan includes {brk // 60}h offline break"
            )
            stint2 = min(cap, left)
            if stint2 > 0:
                segments.append(
                    {
                        "role": "drive",
                        "minutes": stint2,
                        "title": "Lyft driving (next cycle)",
                        "reason": "new 12h block after break",
                    }
                )
                total_drive += stint2
                left -= stint2
        else:
            notes.append(
                f"Lyft: {remaining_cycle}m finishes the 12h block; "
                f"not enough free time left for the required {brk // 60}h break + more drive"
            )
    elif not allow_next_cycle and remaining_cycle < avail:
        notes.append(
            f"Lyft: only {remaining_cycle}m left in current 12h duty "
            f"(driven {driven}m of {cap}m) — update duty as you drive; "
            f"6h offline required after the 12h cap"
        )

    return {
        "drive_minutes": total_drive,
        "segments": segments,
        "notes": notes,
        "remaining_cycle_before": remaining_cycle,
    }
