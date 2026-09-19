"""Sweep: observations → watchlist + pings + dashboard payload. Pure."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from domain import (
    ATTENDED,
    FLAG_STATES,
    GOAL,
    LAYER_GOALS,
    PING_RADAR,
    WIRING_PLANNED,
    apply_observation,
    iso,
    missing_anything,
    new_watch_item,
    one_thing,
    weekly_constraint,
)
from radar import select_candidates
from triggers import evaluate_triggers


def empty_state() -> dict[str, Any]:
    return {
        "goal": dict(GOAL),
        "watchlist": [],
        "radar": {
            "candidates": [],
            "dismissed": {},
            "dismissed_list": [],
            "promoted": [],
        },
        "tracked_targets": [],
        "prepped_event_ids": [],
        "pending_pings": [],
        "emitted_pings": [],
        "weekly_constraint": None,
        "payoff_history": {},
        "last_sweep_at": None,
        "last_snapshot": None,
    }


def run_sweep(
    state: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    now: datetime,
    hour: Optional[int] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Evaluate triggers, apply nag control, refresh radar + weekly constraint."""
    state = dict(state or empty_state())
    prepped = {str(x) for x in (state.get("prepped_event_ids") or [])}
    observations = evaluate_triggers(snapshot, now=now, prepped_event_ids=prepped)
    by_id = {
        str(i.get("id")): dict(i)
        for i in (state.get("watchlist") or [])
        if isinstance(i, dict) and i.get("id")
    }
    pings: list[dict[str, Any]] = []
    watchlist: list[dict[str, Any]] = []
    for obs in observations:
        item_id = str(obs.get("id"))
        prev = by_id.get(item_id) or new_watch_item(
            item_id,
            layer=str(obs.get("layer") or LAYER_GOALS),
            label=str(obs.get("label") or item_id),
            group=str(obs.get("group") or ""),
        )
        prev["layer"] = obs.get("layer") or prev.get("layer")
        prev["group"] = obs.get("group") or prev.get("group")
        prev["label"] = obs.get("label") or prev.get("label")
        updated, ping = apply_observation(
            prev,
            str(obs.get("observed_state") or ATTENDED),
            now=now,
            detail=str(obs.get("detail") or ""),
            severity=str(obs.get("severity") or "medium"),
            source=str(obs.get("source") or ""),
            as_of=obs.get("as_of"),
            wiring_dependency=obs.get("wiring_dependency"),
        )
        watchlist.append(updated)
        if ping:
            pings.append(ping)

    radar_block = dict(state.get("radar") or {})
    dismissed = dict(radar_block.get("dismissed") or {})
    raw_radar = list((snapshot.get("radar") or {}).get("candidates") or [])
    if not raw_radar:
        raw_radar = list(snapshot.get("radar_inputs") or [])
    new_candidates = select_candidates(raw_radar, dismissed=dismissed, now=now)
    radar_block["candidates"] = new_candidates
    radar_block["as_of"] = iso(now)
    time_sensitive = [c for c in new_candidates if c.get("time_sensitive")]
    if time_sensitive:
        titles = ", ".join(str(c.get("title")) for c in time_sensitive[:3])
        pings.append(
            {
                "item_id": "radar.time_sensitive",
                "kind": PING_RADAR,
                "layer": "radar",
                "severity": "high",
                "message": f"Time-sensitive radar: {titles}",
                "at": iso(now),
            }
        )

    constraint, weekly_ping = weekly_constraint(
        watchlist, now=now, prior=state.get("weekly_constraint")
    )
    if weekly_ping:
        pings.append(weekly_ping)

    pending = list(state.get("pending_pings") or [])
    pending.extend(pings)

    new_state = dict(state)
    new_state["goal"] = dict(state.get("goal") or GOAL)
    new_state["watchlist"] = watchlist
    new_state["radar"] = radar_block
    new_state["weekly_constraint"] = constraint
    new_state["pending_pings"] = pending[-100:]
    new_state["last_sweep_at"] = iso(now)
    new_state["last_snapshot"] = snapshot
    new_state["last_snapshot_as_of"] = snapshot.get("as_of") or iso(now)
    hist = dict(state.get("payoff_history") or {})
    for cat in (snapshot.get("financial") or {}).get("categories") or []:
        if not isinstance(cat, dict):
            continue
        try:
            bal = float(cat.get("balance") or 0)
        except (TypeError, ValueError):
            continue
        if bal >= 0:
            continue
        cid = str(cat.get("id") or cat.get("name") or "")
        if not cid:
            continue
        pts = list(hist.get(cid) or [])
        pts.insert(0, {"at": iso(now), "amount": abs(bal)})
        hist[cid] = pts[:60]
    new_state["payoff_history"] = hist

    dashboard = build_dashboard(new_state, now=now, hour=hour)
    return new_state, pings, dashboard


def build_dashboard(
    state: dict[str, Any],
    *,
    now: datetime,
    hour: Optional[int] = None,
) -> dict[str, Any]:
    watchlist = list(state.get("watchlist") or [])
    snapshot = state.get("last_snapshot") or {}
    ops = snapshot.get("operations") or {}
    focus = one_thing(watchlist, now=now, hour=hour)
    missing = missing_anything(watchlist, now=now)
    radar = state.get("radar") or {}
    dismissed_list = list(radar.get("dismissed_list") or [])
    promoted = list(radar.get("promoted") or [])

    fitness_chips = [
        _chip(i) for i in watchlist if i.get("group") == "fitness"
    ]
    financial_chips = [
        _chip(i) for i in watchlist if i.get("group") == "financial"
    ]

    today_ops = {
        "turo_trips": list(ops.get("turo_trips") or []),
        "bills_due": _bills_today(snapshot.get("financial") or {}),
        "training": list(ops.get("training") or []),
        "as_of": ops.get("as_of") or state.get("last_sweep_at"),
    }

    return {
        "ok": True,
        "service": "life-compass",
        "goal": state.get("goal") or GOAL,
        "as_of": state.get("last_sweep_at") or iso(now),
        "sweep_as_of": state.get("last_sweep_at"),
        "snapshot_as_of": state.get("last_snapshot_as_of"),
        "one_thing": focus,
        "goals": {
            "fitness": {
                "label": "Fitness",
                "phase": (snapshot.get("fitness") or {}).get("phase") or "cutting",
                "chips": fitness_chips,
                "as_of": (snapshot.get("fitness") or {}).get("as_of"),
            },
            "financial": {
                "label": "Financial",
                "chips": financial_chips,
                "as_of": (snapshot.get("financial") or {}).get("as_of"),
            },
        },
        "operations": today_ops,
        "radar": {
            "candidates": list(radar.get("candidates") or []),
            "promoted": promoted,
            "dismissed": dismissed_list,
            "as_of": radar.get("as_of"),
        },
        "watchlist": [_watch_row(i) for i in watchlist],
        "weekly_constraint": state.get("weekly_constraint"),
        "missing": missing,
        "pending_ping_count": len(state.get("pending_pings") or []),
        "briefing": {
            "radar": list(radar.get("candidates") or []),
            "one_thing": focus,
            "quiet": focus.get("kind") == "all-quiet"
            and not (radar.get("candidates") or []),
        },
    }


def consume_pings(state: dict[str, Any], *, now: Optional[datetime] = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Agent delivery: pull pending pings once. Empty list = stay silent."""
    now = now or datetime.now(timezone.utc)
    pending = list(state.get("pending_pings") or [])
    if not pending:
        return state, []
    emitted = list(state.get("emitted_pings") or [])
    emitted.extend(pending)
    new_state = dict(state)
    new_state["pending_pings"] = []
    new_state["emitted_pings"] = emitted[-200:]
    new_state["pings_consumed_at"] = iso(now)
    return new_state, pending


def _chip(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "label": item.get("label"),
        "state": item.get("state"),
        "as_of": item.get("as_of"),
        "last_checked": item.get("last_checked"),
        "stale": bool(item.get("stale")),
        "wiring_dependency": item.get("wiring_dependency"),
        "detail": item.get("detail"),
    }


def _watch_row(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row["needs_attention"] = (item.get("state") or "") in FLAG_STATES
    row["wiring_planned"] = (item.get("state") or "") == WIRING_PLANNED
    return row


def _bills_today(financial: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in (financial.get("bills") or []) if isinstance(b, dict)]
