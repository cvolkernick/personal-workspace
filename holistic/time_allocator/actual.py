"""Actual (logged) time use inside the trailing 24h window."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .domain import get_target, list_items, list_targets, logs_for_target, normalize_state
from .sleep_battery import _overlap_seconds, _parse_dt, normalize_intervals


def build_actual_allocation(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    window_minutes: int = 24 * 60,
) -> dict[str, Any]:
    """Minutes actually logged / measured in [now − window, now].

    Sources:
      - sleep_intervals (timed)
      - confirmed Duchess walks (timed activity_reviews)
      - target daily logs for local dates overlapping the window (minus timed already counted)
      - workout days → session_minutes when logged in window
      - ad-hoc done_minutes
      - remainder of window → unaccounted
    """
    state = normalize_state(state)
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    window = max(0, int(window_minutes))
    win_start = now - timedelta(minutes=window)
    win_end = now
    local_tz = now.tzinfo or timezone.utc

    # Local calendar dates that intersect the window
    d0 = win_start.astimezone(local_tz).date()
    d1 = win_end.astimezone(local_tz).date()
    dates_in_window: list[date] = []
    cur = d0
    while cur <= d1:
        dates_in_window.append(cur)
        cur += timedelta(days=1)

    blocks: list[dict[str, Any]] = []
    notes: list[str] = []

    # --- Sleep (timed intervals) ---
    sleep_sec = 0.0
    for row in normalize_intervals(list(state.get("sleep_intervals") or [])):
        st = _parse_dt(row.get("start"))
        en = _parse_dt(row.get("end"))
        if not st or not en:
            continue
        if en > win_end:
            en = win_end
        ov = _overlap_seconds(st, en, win_start, win_end)
        sleep_sec += ov
    sleep_min = int(round(sleep_sec / 60.0))
    if sleep_min > 0:
        blocks.append(
            {
                "id": "sleep",
                "title": "Sleep (measured)",
                "minutes": sleep_min,
                "role": "reserve",
                "kind": "rolling_avg",
                "source": "sleep_intervals",
            }
        )

    # --- Timed confirmed Duchess walks ---
    timed_by_target: dict[str, int] = {}
    for rev in state.get("activity_reviews") or []:
        if str(rev.get("status")) != "confirmed_duchess":
            continue
        st = _parse_dt(rev.get("start"))
        en = _parse_dt(rev.get("end"))
        if not st or not en:
            continue
        ov = _overlap_seconds(st, en, win_start, win_end)
        if ov <= 0:
            continue
        mins = int(round(ov / 60.0))
        tid = str(rev.get("target_hint") or "duchess-walk")
        timed_by_target[tid] = timed_by_target.get(tid, 0) + mins

    for tid, mins in timed_by_target.items():
        tgt = get_target(state, tid)
        title = (tgt or {}).get("title") or tid
        blocks.append(
            {
                "id": tid,
                "title": f"{title} (confirmed walks)",
                "minutes": mins,
                "role": "fixed",
                "kind": "daily_duration",
                "source": "activity_reviews",
            }
        )

    # --- Lyft duty cycle (user-set driven minutes in current 12h block) ---
    lyft_tgt = get_target(state, "lyft")
    if lyft_tgt is not None:
        from .lyft_duty import get_lyft_duty

        driven = int(get_lyft_duty(state).get("driven_minutes") or 0)
        if driven > 0:
            # Count duty-cycle driven time as actual Lyft (capped to window)
            blocks.append(
                {
                    "id": "lyft",
                    "title": "Lyft driving (duty cycle)",
                    "minutes": min(driven, window),
                    "role": "fill",
                    "kind": "fill_remainder",
                    "source": "lyft_duty",
                }
            )
            timed_by_target["lyft"] = timed_by_target.get("lyft", 0) + min(driven, window)

    # --- Daily logs for targets (dates overlapping window), net of timed ---
    for t in list_targets(state):
        tid = str(t.get("id"))
        kind = str(t.get("kind") or "")
        if kind == "rolling_avg":
            continue  # sleep handled via intervals
        if kind == "weekly_frequency":
            # Count session days in window as session_minutes each
            session = max(0, int(t.get("session_minutes") or 60))
            days_hit = 0
            for day in dates_in_window:
                logs = logs_for_target(state, tid, since=day, until=day)
                if any(float(lg.get("value") or 0) > 0 for lg in logs):
                    days_hit += 1
            if days_hit:
                blocks.append(
                    {
                        "id": tid,
                        "title": t.get("title") or tid,
                        "minutes": session * days_hit,
                        "role": "session",
                        "kind": kind,
                        "source": "logs",
                        "sessions": days_hit,
                    }
                )
            continue

        total_log = 0
        for day in dates_in_window:
            logs = logs_for_target(state, tid, since=day, until=day)
            total_log += int(sum(float(lg.get("value") or 0) for lg in logs))

        # Avoid double-count: logs include confirmed walk minutes
        timed = timed_by_target.get(tid, 0)
        net = max(0, total_log - timed)
        if net <= 0:
            continue
        role = "fill" if kind == "fill_remainder" else "fixed"
        blocks.append(
            {
                "id": tid,
                "title": (t.get("title") or tid) + (" (manual log)" if timed else ""),
                "minutes": net,
                "role": role,
                "kind": kind,
                "source": "logs",
            }
        )

    # --- Ad-hoc done_minutes ---
    for it in list_items(state):
        done = int(it.get("done_minutes") or 0)
        if done <= 0:
            continue
        blocks.append(
            {
                "id": it.get("id"),
                "title": it.get("title") or it.get("id"),
                "minutes": done,
                "role": "adhoc",
                "kind": it.get("kind") or "task",
                "source": "items",
            }
        )

    # Merge same id blocks
    merged: dict[str, dict[str, Any]] = {}
    for b in blocks:
        bid = str(b.get("id"))
        if bid in merged:
            merged[bid]["minutes"] = int(merged[bid]["minutes"]) + int(b["minutes"])
            # keep richer title
        else:
            merged[bid] = dict(b)
    from .order import sort_allocation_blocks

    blocks = list(merged.values())
    total = sum(int(b.get("minutes") or 0) for b in blocks)
    unaccounted = max(0, window - total)
    if unaccounted > 0:
        blocks.append(
            {
                "id": "_unaccounted",
                "title": "Unaccounted / free",
                "minutes": unaccounted,
                "role": "unaccounted",
                "kind": "residual",
                "source": "computed",
            }
        )
        notes.append(
            f"{unaccounted} min of the window has no logged activity yet"
        )
    blocks = sort_allocation_blocks(blocks)

    return {
        "window_start": win_start.isoformat(timespec="seconds"),
        "window_end": win_end.isoformat(timespec="seconds"),
        "window_minutes": window,
        "blocks": blocks,
        "total_logged_minutes": total,
        "unaccounted_minutes": unaccounted,
        "notes": notes,
    }


def allocation_delta(
    planned: dict[str, Any],
    actual: dict[str, Any],
) -> list[dict[str, Any]]:
    """Per-id planned vs actual minutes (positive gap = still need more vs plan)."""
    p_map = {
        str(b.get("id")): b
        for b in (planned.get("blocks") or [])
        if str(b.get("id")) != "_unaccounted"
    }
    a_map = {
        str(b.get("id")): b
        for b in (actual.get("blocks") or [])
        if str(b.get("id")) != "_unaccounted"
    }
    from .order import id_sort_key

    ids = sorted(set(p_map) | set(a_map), key=id_sort_key)
    rows: list[dict[str, Any]] = []
    for i in ids:
        pb = p_map.get(i) or {}
        ab = a_map.get(i) or {}
        pm = int(pb.get("minutes") or 0)
        am = int(ab.get("minutes") or 0)
        rows.append(
            {
                "id": i,
                "title": pb.get("title") or ab.get("title") or i,
                "planned_minutes": pm,
                "actual_minutes": am,
                "delta_minutes": pm - am,  # >0 means under vs plan
                "role": pb.get("role") or ab.get("role") or "",
            }
        )
    return rows
