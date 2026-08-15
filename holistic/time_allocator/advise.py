"""Scored Time Allocator advisor — best action now.

P1: deterministic ranker over live objectives + today's calendar contacts.
No LLM. The filed NOW/NEXT/THEN itinerary is an input, not source of truth.
Missing packets are ``not_loaded``; never invent a person, meeting, or objective.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .now_next import compose_now_next

STATUS_OK = "ok"
STATUS_NOT_LOADED = "not_loaded"
STATUS_STALE = "stale"

CALENDAR_FRESH_HOURS = 4.0
DAY_PLAN_FRESH_HOURS = 4.0
BOARD_FRESH_HOURS = 4.0
BODY_FRESH_HOURS = 24.0
CAPITAL_FRESH_HOURS = 6.0

# Calendar that is happening (or about to) beats gates and flexible work.
SCORE_CAL_NOW = 10_000
SCORE_CAL_SOON = 9_500
LEAD_MINUTES = 15
# Red body/capital gate outranks flexible work, not a live/soon calendar contact.
SCORE_GATE = 9_000
SCORE_DAY_PLAN = 450
SCORE_BOARD_IP = 380
SCORE_BOARD_READY = 220
SCORE_OBJECTIVE = 120
SCORE_TARGET = 90
SCORE_FILL = 40

DEFAULT_FLEX_MINUTES = 30
SOURCE_KEYS = ("calendar", "objectives", "day_plan", "board", "body", "capital")

_WORKOUT_MARKERS = (
    "workout",
    "training",
    "gym",
    "lift",
    "session",
    "exercise",
    "run",
)
_CAPITAL_MARKERS = (
    "dca",
    "treasury",
    "capital",
    "deploy",
    "spot",
    "buy btc",
    "free cash",
    "vault",
)


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


def _source_view(
    status: str,
    *,
    as_of: Any = None,
    detail: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status}
    if as_of:
        out["as_of"] = as_of if isinstance(as_of, str) else str(as_of)
    if detail:
        out["detail"] = detail
    return out


def classify_packet(
    packet: dict[str, Any] | None,
    *,
    now: datetime,
    fresh_hours: float,
    as_of_keys: tuple[str, ...] = ("as_of", "synced_at"),
) -> dict[str, Any]:
    """Map a loaded packet (or None) to ok / not_loaded / stale."""
    if packet is None:
        return _source_view(STATUS_NOT_LOADED)
    if not isinstance(packet, dict):
        return _source_view(STATUS_NOT_LOADED, detail="unparseable")

    as_of_raw = None
    for key in as_of_keys:
        if packet.get(key):
            as_of_raw = packet.get(key)
            break
    fresh = packet.get("fresh_for_hours", fresh_hours)
    try:
        fresh_h = float(fresh)
    except (TypeError, ValueError):
        fresh_h = float(fresh_hours)

    if packet.get("stale") is True or packet.get("ok") is False or packet.get("fetch_ok") is False:
        return _source_view(
            STATUS_STALE,
            as_of=as_of_raw,
            detail=str(packet.get("error") or packet.get("summary") or "marked stale"),
        )

    as_of = _parse_dt(as_of_raw, fallback_tz=now.tzinfo)
    if as_of is None:
        return _source_view(STATUS_OK, as_of=as_of_raw)

    age_h = (now - as_of.astimezone(now.tzinfo)).total_seconds() / 3600.0
    if age_h > fresh_h:
        return _source_view(
            STATUS_STALE,
            as_of=as_of.isoformat(timespec="seconds"),
            detail=f"age {age_h:.1f}h > {fresh_h:g}h",
        )
    return _source_view(STATUS_OK, as_of=as_of.isoformat(timespec="seconds"))


def people_on_event(event: dict[str, Any]) -> list[str]:
    """Attendee names already on the event. Never invent a person."""
    out: list[str] = []
    seen_names: set[str] = set()
    seen_emails: set[str] = set()
    raw = event.get("attendees")
    if raw is None:
        raw = event.get("people")
    if raw is None:
        return out
    rows: list[Any]
    if isinstance(raw, str):
        rows = [raw]
    elif isinstance(raw, list):
        rows = raw
    else:
        return out
    for item in rows:
        name = ""
        email = ""
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            email = str(item.get("email") or "").strip().lower()
            name = str(
                item.get("displayName") or item.get("name") or item.get("email") or ""
            ).strip()
        if email and email in seen_emails:
            continue
        key = name.lower()
        if not name or key in seen_names:
            continue
        if email:
            seen_emails.add(email)
        seen_names.add(key)
        out.append(name)
    return out


def _end_of_civil_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _event_window(
    event: dict[str, Any], *, now: datetime
) -> tuple[datetime, datetime] | None:
    start = _parse_dt(event.get("start") or event.get("full_start"), fallback_tz=now.tzinfo)
    end = _parse_dt(event.get("end") or event.get("full_end"), fallback_tz=now.tzinfo)
    if start is None or end is None or end <= start:
        return None
    return start.astimezone(now.tzinfo), end.astimezone(now.tzinfo)


def _matches_any(text: str, markers: tuple[str, ...]) -> bool:
    blob = text.lower()
    return any(m in blob for m in markers)


def _body_gate(body: dict[str, Any] | None, sleep_battery: dict[str, Any] | None) -> dict[str, Any] | None:
    """Red body gate from Fit packet and/or holistic sleep battery."""
    reasons: list[str] = []
    until: str | None = None

    if isinstance(body, dict):
        rec = str(body.get("train_recommendation") or body.get("session_type") or "").strip().lower()
        if rec in {"rest", "no_train", "hold"}:
            reasons.append(
                body.get("summary")
                or f"train recommendation is {rec}"
            )
        label = str(body.get("recovery_label") or "").strip().lower()
        if label in {"hold", "stop", "red"}:
            reasons.append(f"recovery {body.get('recovery_label')}")
        constraints = body.get("constraints") if isinstance(body.get("constraints"), list) else []
        for c in constraints:
            if not isinstance(c, dict):
                continue
            if str(c.get("severity") or "").lower() != "block":
                continue
            reasons.append(str(c.get("title") or c.get("detail") or "body constraint"))
            until = until or (str(c.get("until")) if c.get("until") else None)

    batt = sleep_battery if isinstance(sleep_battery, dict) else None
    if batt is None and isinstance(body, dict) and isinstance(body.get("sleep_battery"), dict):
        batt = body["sleep_battery"]
    if batt:
        level = str(batt.get("level") or "").strip().lower()
        try:
            pct = float(batt.get("pct_charged") if batt.get("pct_charged") is not None else 100)
        except (TypeError, ValueError):
            pct = 100.0
        # Fit packet stores 0–100 or 0–1; treat ≤1.0 as a fraction when level is set.
        if 0 <= pct <= 1 and level:
            pct = pct * 100.0
        if level in {"critical", "empty", "red"} or pct <= 25:
            reasons.append(
                batt.get("summary")
                or f"sleep battery {pct:.0f}%"
            )
            until = until or (str(batt.get("empty_at")) if batt.get("empty_at") else None)

    if not reasons:
        return None
    return {
        "id": "body-gate",
        "kind": "body",
        "title": "Protect body — red gate",
        "why": "Body: " + reasons[0],
        "until": until,
    }


def _capital_gate(capital: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(capital, dict):
        return None
    reasons: list[str] = []
    if capital.get("red_mode") is True:
        reasons.append("red_mode")
    if str(capital.get("free_cash_gate") or "").lower() in {"block_new_risk", "block"}:
        reasons.append("free-cash gate blocks new risk")
    stress = capital.get("stress")
    overall = ""
    if isinstance(stress, dict):
        overall = str(stress.get("overall") or "")
    elif isinstance(stress, str):
        overall = stress
    if not overall:
        overall = str(capital.get("stress_overall") or "")
    if overall.strip().lower() == "red":
        reasons.append("capital stress red")
    constraints = capital.get("constraints") if isinstance(capital.get("constraints"), list) else []
    for c in constraints:
        if isinstance(c, dict) and str(c.get("severity") or "").lower() == "block":
            reasons.append(str(c.get("title") or c.get("detail") or "capital constraint"))
    if not reasons:
        return None
    return {
        "id": "capital-gate",
        "kind": "capital",
        "title": "Hold new capital risk",
        "why": "Capital: " + reasons[0],
        "until": None,
    }


def _wip_gate(board: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(board, dict):
        return None
    if board.get("wip_overload") is not True:
        return None
    return {
        "id": "wip-gate",
        "kind": "wip",
        "title": "WIP overload — finish in-flight work",
        "why": "Board: WIP overload",
        "until": None,
    }


def _suppressed_by_gates(
    cand: dict[str, Any],
    *,
    body: dict[str, Any] | None,
    capital: dict[str, Any] | None,
    wip: dict[str, Any] | None,
) -> str | None:
    blob = " ".join(
        str(cand.get(k) or "")
        for k in ("id", "title", "role", "target_hint", "source")
    )
    if body and _matches_any(blob, _WORKOUT_MARKERS):
        return body["why"]
    if capital and _matches_any(blob, _CAPITAL_MARKERS):
        return capital["why"]
    if wip and cand.get("kind") == "board_ready":
        return wip["why"]
    return None


def _candidate(
    *,
    cid: str,
    title: str,
    role: str,
    score: int,
    why: str,
    source: str,
    start: datetime | None = None,
    end: datetime | None = None,
    minutes: int | None = None,
    kind: str = "",
    people: list[str] | None = None,
    target_hint: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": cid,
        "title": title,
        "role": role,
        "score": int(score),
        "why": why,
        "source": source,
        "kind": kind or role,
        "minutes": int(minutes or 0),
        "people": list(people or []),
        "target_hint": target_hint,
    }
    if start is not None:
        row["start"] = start.isoformat(timespec="seconds")
        row["_start"] = start
    if end is not None:
        row["end"] = end.isoformat(timespec="seconds")
        row["_end"] = end
        if start is not None:
            row["minutes"] = max(1, int(round((end - start).total_seconds() / 60.0)))
    return row


def _today_calendar_events(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    day_end: datetime,
) -> list[dict[str, Any]]:
    """Timed events that touch remaining today. No invented rows."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        win = _event_window(ev, now=now)
        if win is None:
            continue
        start, end = win
        if end <= now or start >= day_end:
            continue
        people = people_on_event(ev)
        title = str(ev.get("title") or ev.get("id") or "").strip() or "(no title)"
        if start <= now < end:
            score = SCORE_CAL_NOW
            why = f"Calendar contact in progress: {title}"
        else:
            lead = (start - now).total_seconds() / 60.0
            score = SCORE_CAL_SOON if lead <= LEAD_MINUTES else SCORE_CAL_SOON - 500
            why = f"Calendar-fixed: {title}"
        if people:
            why += " with " + ", ".join(people[:4])
        out.append(
            _candidate(
                cid=str(ev.get("id") or title),
                title=title,
                role="calendar",
                score=score,
                why=why,
                source="calendar",
                start=max(start, now) if start < now else start,
                end=end,
                kind="calendar_fixed",
                people=people,
                target_hint=str(ev.get("target_hint") or "") or None,
            )
        )
    out.sort(key=lambda c: (c.get("_start") or day_end, -int(c.get("score") or 0)))
    return out


def _objective_candidates(
    items: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("id") or "").strip()
        if not title:
            continue
        try:
            pri = int(it.get("priority") or 0)
        except (TypeError, ValueError):
            pri = 0
        try:
            minutes = int(it.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0:
            minutes = DEFAULT_FLEX_MINUTES
        cid = str(it.get("id") or title)
        out.append(
            _candidate(
                cid=cid,
                title=title,
                role="adhoc",
                score=SCORE_OBJECTIVE + pri * 20,
                why=f"Objective p{pri}: {title}",
                source="objectives",
                start=now,
                end=now + timedelta(minutes=minutes),
                minutes=minutes,
                kind="objective",
            )
        )
    for tgt in targets:
        if not isinstance(tgt, dict):
            continue
        tid = str(tgt.get("id") or "").strip()
        title = str(tgt.get("title") or tid).strip()
        if not tid or not title:
            continue
        kind = str(tgt.get("kind") or "")
        if tid == "sleep" or kind == "rolling_avg":
            continue
        try:
            pri = int(tgt.get("priority") or 0)
        except (TypeError, ValueError):
            pri = 0
        if kind == "fill_remainder" or tid == "lyft":
            minutes = 60
            score = SCORE_FILL + pri
            role = "fill"
            why = f"Fill capacity: {title}"
        elif kind == "weekly_frequency" or tid == "workout":
            minutes = int(tgt.get("session_minutes") or tgt.get("minutes") or 60)
            score = SCORE_TARGET + pri * 15
            role = "session"
            why = f"KPI session still open: {title}"
        else:
            minutes = int(tgt.get("minutes") or tgt.get("session_minutes") or DEFAULT_FLEX_MINUTES)
            score = SCORE_TARGET + pri * 15
            role = "fixed"
            why = f"Daily target: {title}"
        out.append(
            _candidate(
                cid=tid,
                title=title,
                role=role,
                score=score,
                why=why,
                source="objectives",
                start=now,
                end=now + timedelta(minutes=max(1, minutes)),
                minutes=max(1, minutes),
                kind="target",
                target_hint=tid,
            )
        )
    return out


def _day_plan_candidates(day_plan: dict[str, Any] | None, *, now: datetime) -> list[dict[str, Any]]:
    if not isinstance(day_plan, dict):
        return []
    out: list[dict[str, Any]] = []
    next3 = day_plan.get("next3") if isinstance(day_plan.get("next3"), list) else []
    for i, row in enumerate(next3):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("id") or "").strip()
        if not title:
            continue
        cid = str(row.get("id") or f"day-plan-{i+1}")
        minutes = int(row.get("minutes") or DEFAULT_FLEX_MINUTES)
        out.append(
            _candidate(
                cid=cid,
                title=title,
                role=str(row.get("kind") or row.get("role") or "work"),
                score=SCORE_DAY_PLAN + max(0, 3 - i) * 40,
                why=f"Day plan: {title}",
                source="day_plan",
                start=now,
                end=now + timedelta(minutes=minutes),
                minutes=minutes,
                kind="day_plan",
            )
        )
    return out


def _board_candidates(board: dict[str, Any] | None, *, now: datetime) -> list[dict[str, Any]]:
    if not isinstance(board, dict):
        return []
    out: list[dict[str, Any]] = []

    def add_rows(rows: Any, *, kind: str, score: int, label: str) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            number = row.get("number")
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            heading = f"#{number} {title}" if number is not None else title
            cid = f"board-{number}" if number is not None else f"board-{title[:32]}"
            out.append(
                _candidate(
                    cid=cid,
                    title=heading,
                    role="work",
                    score=score,
                    why=f"Board {label}: {heading}",
                    source="board",
                    start=now,
                    end=now + timedelta(minutes=DEFAULT_FLEX_MINUTES),
                    minutes=DEFAULT_FLEX_MINUTES,
                    kind=kind,
                )
            )

    add_rows(board.get("in_progress"), kind="board_ip", score=SCORE_BOARD_IP, label="in progress")
    add_rows(board.get("ready_top"), kind="board_ready", score=SCORE_BOARD_READY, label="ready")
    return out


def _gate_candidates(
    *,
    body: dict[str, Any] | None,
    capital: dict[str, Any] | None,
    wip: dict[str, Any] | None,
    now: datetime,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for gate in (body, capital, wip):
        if not gate:
            continue
        until = _parse_dt(gate.get("until"), fallback_tz=now.tzinfo)
        end = until.astimezone(now.tzinfo) if until and until > now else now + timedelta(hours=1)
        out.append(
            _candidate(
                cid=str(gate["id"]),
                title=str(gate["title"]),
                role="gate",
                score=SCORE_GATE,
                why=str(gate["why"]),
                source=str(gate["kind"]),
                start=now,
                end=end,
                kind="gate",
            )
        )
    return out


def _public_action(cand: dict[str, Any]) -> dict[str, Any]:
    row = {
        "id": cand.get("id"),
        "title": cand.get("title"),
        "role": cand.get("role"),
        "source": cand.get("source"),
        "why": cand.get("why"),
        "minutes": int(cand.get("minutes") or 0),
        "people": list(cand.get("people") or []),
    }
    if cand.get("start"):
        row["start"] = cand["start"]
    if cand.get("end"):
        row["end"] = cand["end"]
        end = cand.get("_end")
        start_for_remain = cand.get("_start")
        if isinstance(end, datetime) and isinstance(start_for_remain, datetime):
            row["remaining_seconds"] = max(0, int((end - start_for_remain).total_seconds()))
    return row


def _build_schedule(
    *,
    now: datetime,
    day_end: datetime,
    calendar: list[dict[str, Any]],
    flexible: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remaining-day itinerary: calendar waypoints + gap-fill. No invented meetings."""
    fixed = [c for c in calendar if c.get("_start") and c.get("_end")]
    fixed.sort(key=lambda c: c["_start"])
    flex = [c for c in flexible if c.get("kind") != "gate"]
    used: set[str] = set()
    schedule: list[dict[str, Any]] = []
    cursor = now

    def take_fill(gap_end: datetime) -> None:
        nonlocal cursor
        for cand in flex:
            cid = str(cand.get("id"))
            if cid in used:
                continue
            if cursor >= gap_end:
                return
            minutes = max(1, int(cand.get("minutes") or DEFAULT_FLEX_MINUTES))
            end = min(cursor + timedelta(minutes=minutes), gap_end)
            if end <= cursor:
                continue
            used.add(cid)
            block = dict(cand)
            block["_start"] = cursor
            block["_end"] = end
            block["start"] = cursor.isoformat(timespec="seconds")
            block["end"] = end.isoformat(timespec="seconds")
            block["minutes"] = max(1, int(round((end - cursor).total_seconds() / 60.0)))
            schedule.append(_public_action(block))
            cursor = end

    for ev in fixed:
        start: datetime = ev["_start"]
        end: datetime = ev["_end"]
        if start > cursor:
            take_fill(start)
            cursor = max(cursor, start)
        if end > cursor:
            schedule.append(_public_action(ev))
            cursor = end

    if cursor < day_end:
        take_fill(day_end)

    return schedule


def compose_advise(
    inputs: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return ``{now, schedule, sources, generated_at, stale}``.

    ``inputs`` is a loaded snapshot (see :func:`load_advise_inputs`). The
    composer itself does no I/O.
    """
    generated = _aware(now)
    data = inputs if isinstance(inputs, dict) else {}
    events = [e for e in (data.get("calendar_events") or []) if isinstance(e, dict)]
    calendar_meta = data.get("calendar_meta") if isinstance(data.get("calendar_meta"), dict) else {}
    items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
    targets = [t for t in (data.get("targets") or []) if isinstance(t, dict)]
    filed_plan = data.get("filed_plan") if isinstance(data.get("filed_plan"), dict) else None
    day_plan = data.get("day_plan") if isinstance(data.get("day_plan"), dict) else None
    board = data.get("board") if isinstance(data.get("board"), dict) else None
    body_pkt = data.get("body") if isinstance(data.get("body"), dict) else None
    capital_pkt = data.get("capital") if isinstance(data.get("capital"), dict) else None
    sleep_battery = (
        data.get("sleep_battery") if isinstance(data.get("sleep_battery"), dict) else None
    )

    # Calendar: events or a sync stamp mean the source exists; neither → not_loaded.
    if events or calendar_meta.get("synced_at") or calendar_meta.get("as_of"):
        cal_packet = {
            "as_of": calendar_meta.get("synced_at") or calendar_meta.get("as_of"),
            "stale": bool(calendar_meta.get("ok") is False),
            "fresh_for_hours": CALENDAR_FRESH_HOURS,
            "error": calendar_meta.get("error"),
        }
        calendar_src = classify_packet(
            cal_packet, now=generated, fresh_hours=CALENDAR_FRESH_HOURS
        )
    else:
        calendar_src = _source_view(STATUS_NOT_LOADED)

    if items or targets:
        objectives_src = _source_view(STATUS_OK, detail=f"{len(items)} items · {len(targets)} targets")
    else:
        objectives_src = _source_view(STATUS_OK, detail="none")

    day_plan_src = classify_packet(
        day_plan, now=generated, fresh_hours=DAY_PLAN_FRESH_HOURS
    )
    board_src = classify_packet(board, now=generated, fresh_hours=BOARD_FRESH_HOURS)
    # Holistic sleep battery counts as a body signal when no Fit packet exists.
    if body_pkt is not None:
        body_src = classify_packet(body_pkt, now=generated, fresh_hours=BODY_FRESH_HOURS)
    elif sleep_battery and sleep_battery.get("data_source") not in (None, "", "none"):
        body_src = _source_view(STATUS_OK, detail="holistic sleep battery")
    else:
        body_src = _source_view(STATUS_NOT_LOADED)
    capital_src = classify_packet(capital_pkt, now=generated, fresh_hours=CAPITAL_FRESH_HOURS)

    sources = {
        "calendar": calendar_src,
        "objectives": objectives_src,
        "day_plan": day_plan_src,
        "board": board_src,
        "body": body_src,
        "capital": capital_src,
    }

    day_end = _end_of_civil_day(generated)
    body_gate = _body_gate(body_pkt, sleep_battery)
    capital_gate = _capital_gate(capital_pkt)
    wip_gate = _wip_gate(board)

    calendar_cands = _today_calendar_events(events, now=generated, day_end=day_end)
    pool = (
        _objective_candidates(items, targets, now=generated)
        + _day_plan_candidates(day_plan, now=generated)
        + _board_candidates(board, now=generated)
        + _gate_candidates(body=body_gate, capital=capital_gate, wip=wip_gate, now=generated)
    )

    usable: list[dict[str, Any]] = []
    for cand in pool:
        reason = _suppressed_by_gates(
            cand, body=body_gate, capital=capital_gate, wip=wip_gate
        )
        if reason:
            cand["suppressed"] = reason
            continue
        usable.append(cand)

    # Rank NOW: live/soon calendar first, else highest remaining candidate.
    live_cal = [
        c
        for c in calendar_cands
        if int(c.get("score") or 0) >= SCORE_CAL_SOON
        and c.get("_start") is not None
        and c["_start"] <= generated + timedelta(minutes=LEAD_MINUTES)
    ]
    ranked = sorted(
        live_cal + usable,
        key=lambda c: (-int(c.get("score") or 0), str(c.get("start") or ""), str(c.get("id") or "")),
    )

    advise_now = _public_action(ranked[0]) if ranked else None

    filed = compose_now_next(filed_plan, now=generated)
    filed_now = filed.get("now") if isinstance(filed, dict) else None
    disagrees = False
    if advise_now and isinstance(filed_now, dict):
        disagrees = str(advise_now.get("id") or "") != str(filed_now.get("id") or "")
    if advise_now is not None:
        advise_now["disagrees_with_filed"] = disagrees
        if isinstance(filed_now, dict):
            advise_now["filed_now"] = {
                "id": filed_now.get("id"),
                "title": filed_now.get("title"),
            }

    schedule = _build_schedule(
        now=generated,
        day_end=day_end,
        calendar=calendar_cands,
        flexible=usable,
    )

    no_action = advise_now is None
    stale = no_action and (
        calendar_src["status"] != STATUS_OK
        and day_plan_src["status"] != STATUS_OK
        and board_src["status"] != STATUS_OK
        and not items
        and not targets
    )

    reason = None
    if no_action:
        if calendar_src["status"] == STATUS_NOT_LOADED and not items and not targets:
            reason = "no objectives — calendar not loaded"
        else:
            reason = "no objectives"

    return {
        "now": advise_now,
        "schedule": schedule,
        "sources": sources,
        "generated_at": generated.isoformat(timespec="seconds"),
        "stale": bool(stale),
        "reason": reason,
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def default_workspace() -> Path:
    return Path(__file__).resolve().parents[2]


def load_advise_inputs(
    state: dict[str, Any] | None,
    *,
    workspace: Path | str | None = None,
    sleep_battery: dict[str, Any] | None = None,
    packets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble composer inputs from holistic state + optional on-disk packets.

    ``packets`` overrides (keys: day_plan, board, body, capital) let tests
    inject fixtures without touching the workspace.
    """
    state = state if isinstance(state, dict) else {}
    overrides = packets if isinstance(packets, dict) else {}
    root = Path(workspace) if workspace is not None else default_workspace()

    def pick(key: str, *rel: str) -> dict[str, Any] | None:
        if key in overrides:
            value = overrides[key]
            return value if isinstance(value, dict) else None
        return _read_json(root.joinpath(*rel))

    return {
        "calendar_events": list(state.get("calendar_events") or []),
        "calendar_meta": state.get("calendar_meta") if isinstance(state.get("calendar_meta"), dict) else {},
        "items": list(state.get("items") or []),
        "targets": list(state.get("targets") or []),
        "filed_plan": state.get("plan") if isinstance(state.get("plan"), dict) else None,
        "day_plan": pick("day_plan", "orchestra", "data", "day_plan.json"),
        "board": pick("board", "ops", "board", "day_constraints.json"),
        "body": pick("body", "fitness", "data", "day_constraints.json"),
        "capital": pick("capital", "treasury", "day_constraints.json"),
        "sleep_battery": sleep_battery,
    }
