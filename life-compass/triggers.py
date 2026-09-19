"""Evaluate v1 Life Compass triggers against a snapshot. Pure; no I/O.

Canonical trigger list is GitHub issue #761. Changes need user approval.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from domain import (
    ATTENDED,
    LAYER_GOALS,
    LAYER_OPS,
    UNAVAILABLE,
    UNATTENDED,
    UNDER_ATTENDED,
    WIRING_PLANNED,
    iso,
    parse_timestamp,
)

FITDASH_DEP = (
    "FitDash read API / export on fitdash-cvolkernick.vercel.app (#761 dependency)"
)
INVOICE_DEP = "Invoice-ready Turso table Helm writes per #747"
HEALTHKIT_DEP = "Health Connect / HealthKit sync (user enable on Android)"

FITNESS_TRIGGER_IDS = (
    "fitness.resistance_gap",
    "fitness.volume_decline",
    "fitness.weight_stall",
    "fitness.calorie_over",
)


def _obs(
    item_id: str,
    *,
    layer: str,
    group: str,
    label: str,
    state: str,
    detail: str,
    severity: str = "medium",
    source: str = "",
    as_of: Optional[str] = None,
    wiring_dependency: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    checked = iso(now or datetime.now(timezone.utc))
    return {
        "id": item_id,
        "layer": layer,
        "group": group,
        "label": label,
        "observed_state": state,
        "detail": detail,
        "severity": severity,
        "source": source,
        "as_of": as_of or checked,
        "wiring_dependency": wiring_dependency,
    }


def evaluate_triggers(
    snapshot: dict[str, Any],
    *,
    now: datetime,
    prepped_event_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Return one observation per v1 trigger (plus honest wiring/unavailable)."""
    prepped_event_ids = prepped_event_ids or set()
    out: list[dict[str, Any]] = []
    out.extend(_fitness(snapshot.get("fitness") or {}, now))
    out.extend(_financial(snapshot.get("financial") or {}, now))
    out.extend(_operations(snapshot.get("operations") or {}, now, prepped_event_ids))
    return out


def _source_as_of(block: dict[str, Any], now: datetime) -> str:
    return str(block.get("as_of") or iso(now))


def _fitness(block: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    wired = bool(block.get("wired"))
    as_of = _source_as_of(block, now)
    specs = (
        (
            "fitness.resistance_gap",
            "No resistance session in 3+ days",
            "resistance-training frequency",
        ),
        (
            "fitness.volume_decline",
            "Weekly volume declining two weeks running",
            "volume trend per muscle group",
        ),
        (
            "fitness.weight_stall",
            "Weight stalled 2+ weeks into the cut",
            "body-weight trend",
        ),
        (
            "fitness.calorie_over",
            "Calories over target 3+ days in a week",
            "nutrition adherence vs cut targets",
        ),
    )
    if not wired:
        return [
            _obs(
                item_id,
                layer=LAYER_GOALS,
                group="fitness",
                label=label,
                state=WIRING_PLANNED,
                detail=f"Wiring planned: {FITDASH_DEP}.",
                severity="info",
                source="fitdash",
                as_of=as_of,
                wiring_dependency=FITDASH_DEP,
                now=now,
            )
            for item_id, label, _sig in specs
        ]

    out: list[dict[str, Any]] = []
    days = block.get("days_since_resistance")
    if isinstance(days, (int, float)) and days >= 3:
        out.append(
            _obs(
                "fitness.resistance_gap",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[0][1],
                state=UNATTENDED,
                detail=f"No resistance session in {int(days)} days.",
                severity="high",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )
    else:
        out.append(
            _obs(
                "fitness.resistance_gap",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[0][1],
                state=ATTENDED,
                detail="Resistance session inside 3-day window."
                if days is not None
                else "Resistance frequency looks current.",
                severity="info",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )

    if block.get("volume_declining_two_weeks"):
        out.append(
            _obs(
                "fitness.volume_decline",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[1][1],
                state=UNDER_ATTENDED,
                detail="Weekly training volume declined two weeks running.",
                severity="medium",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )
    else:
        out.append(
            _obs(
                "fitness.volume_decline",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[1][1],
                state=ATTENDED,
                detail="Volume trend not declining two weeks running.",
                severity="info",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )

    if block.get("weight_stalled_two_weeks"):
        out.append(
            _obs(
                "fitness.weight_stall",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[2][1],
                state=UNDER_ATTENDED,
                detail="Body weight stalled 2+ weeks into the cut.",
                severity="medium",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )
    else:
        out.append(
            _obs(
                "fitness.weight_stall",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[2][1],
                state=ATTENDED,
                detail="Weight trend not stalled (or cut not in week 2+).",
                severity="info",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )

    over_days = block.get("calorie_over_days_this_week")
    if isinstance(over_days, (int, float)) and over_days >= 3:
        out.append(
            _obs(
                "fitness.calorie_over",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[3][1],
                state=UNATTENDED,
                detail=f"Calories over target {int(over_days)} day(s) this week.",
                severity="high",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )
    else:
        out.append(
            _obs(
                "fitness.calorie_over",
                layer=LAYER_GOALS,
                group="fitness",
                label=specs[3][1],
                state=ATTENDED,
                detail="Calories not over target 3+ days this week.",
                severity="info",
                source="fitdash",
                as_of=as_of,
                now=now,
            )
        )
    return out


def _financial(block: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    as_of = _source_as_of(block, now)
    if block.get("error") and not block.get("wired"):
        detail = f"YNAB source unavailable: {block.get('error')}"
        return [
            _obs(
                item_id,
                layer=LAYER_GOALS,
                group="financial",
                label=label,
                state=UNAVAILABLE,
                detail=detail,
                severity="low",
                source="ynab",
                as_of=as_of,
                now=now,
            )
            for item_id, label in (
                ("financial.bill_due_unscheduled", "Bill due within 2 days, unscheduled"),
                (
                    "financial.category_overspent",
                    "YNAB category overspent or underfunded vs Plan",
                ),
                (
                    "financial.payoff_stalled",
                    "No progress on behind-expense payoff for two weeks",
                ),
            )
        ]

    out: list[dict[str, Any]] = []
    horizon = (now.astimezone(timezone.utc) + timedelta(days=2)).date()
    today = now.astimezone(timezone.utc).date()
    due = []
    for bill in block.get("bills") or []:
        if not isinstance(bill, dict):
            continue
        if bill.get("paid"):
            continue
        due_s = bill.get("date") or bill.get("date_next")
        dt = parse_timestamp(due_s)
        if dt is None:
            try:
                d = datetime.strptime(str(due_s)[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
        else:
            d = dt.date()
        # "Unscheduled" = not yet handled this cycle. A YNAB scheduled_transaction
        # due within 2 days still needs attention until it is paid.
        handled = bool(bill.get("handled"))
        if today <= d <= horizon and not handled:
            due.append(bill)
    if due:
        names = ", ".join(str(b.get("payee") or b.get("name") or "bill") for b in due[:4])
        out.append(
            _obs(
                "financial.bill_due_unscheduled",
                layer=LAYER_GOALS,
                group="financial",
                label="Bill due within 2 days, unscheduled",
                state=UNATTENDED,
                detail=f"{len(due)} bill(s) due within 2 days and unscheduled: {names}.",
                severity="high",
                source="ynab",
                as_of=as_of,
                now=now,
            )
        )
    else:
        out.append(
            _obs(
                "financial.bill_due_unscheduled",
                layer=LAYER_GOALS,
                group="financial",
                label="Bill due within 2 days, unscheduled",
                state=ATTENDED,
                detail="No unscheduled bills due within 2 days.",
                severity="info",
                source="ynab",
                as_of=as_of,
                now=now,
            )
        )

    problems = []
    for cat in block.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        name = str(cat.get("name") or "category")
        try:
            balance = float(cat.get("balance") or 0)
        except (TypeError, ValueError):
            balance = 0.0
        try:
            under = float(cat.get("goal_under_funded") or 0)
        except (TypeError, ValueError):
            under = 0.0
        if balance < 0:
            problems.append(f"{name} overspent ({balance:.2f})")
        elif under > 0:
            problems.append(f"{name} underfunded vs Plan ({under:.2f})")
    if problems:
        out.append(
            _obs(
                "financial.category_overspent",
                layer=LAYER_GOALS,
                group="financial",
                label="YNAB category overspent or underfunded vs Plan",
                state=UNATTENDED if any("overspent" in p for p in problems) else UNDER_ATTENDED,
                detail="; ".join(problems[:6]),
                severity="high" if any("overspent" in p for p in problems) else "medium",
                source="ynab",
                as_of=as_of,
                now=now,
            )
        )
    else:
        out.append(
            _obs(
                "financial.category_overspent",
                layer=LAYER_GOALS,
                group="financial",
                label="YNAB category overspent or underfunded vs Plan",
                state=ATTENDED,
                detail="No overspent or underfunded Plan categories.",
                severity="info",
                source="ynab",
                as_of=as_of,
                now=now,
            )
        )

    stalled = []
    for row in block.get("payoff") or []:
        if not isinstance(row, dict):
            continue
        try:
            amount = float(row.get("amount") or 0)
            prev = row.get("amount_14d_ago")
        except (TypeError, ValueError):
            continue
        if prev is None:
            continue
        try:
            prev_f = float(prev)
        except (TypeError, ValueError):
            continue
        if amount > 0 and amount >= prev_f - 1e-6:
            stalled.append(str(row.get("name") or "behind expense"))
    if stalled:
        out.append(
            _obs(
                "financial.payoff_stalled",
                layer=LAYER_GOALS,
                group="financial",
                label="No progress on behind-expense payoff for two weeks",
                state=UNDER_ATTENDED,
                detail="No payoff progress in 14d: " + ", ".join(stalled[:6]),
                severity="medium",
                source="ynab",
                as_of=as_of,
                now=now,
            )
        )
    else:
        watching = any(
            isinstance(r, dict) and r.get("amount_14d_ago") is None
            for r in (block.get("payoff") or [])
        )
        detail = (
            "Payoff history < 14d — watching, not flagging."
            if watching
            else "Behind-expense payoff is moving or none tracked."
        )
        out.append(
            _obs(
                "financial.payoff_stalled",
                layer=LAYER_GOALS,
                group="financial",
                label="No progress on behind-expense payoff for two weeks",
                state=ATTENDED,
                detail=detail,
                severity="info",
                source="ynab",
                as_of=as_of,
                now=now,
            )
        )
    return out


def _operations(
    block: dict[str, Any],
    now: datetime,
    prepped_event_ids: set[str],
) -> list[dict[str, Any]]:
    as_of = _source_as_of(block, now)
    out: list[dict[str, Any]] = []

    cal = block.get("calendar") or {}
    if cal.get("error") and not cal.get("wired"):
        out.append(
            _obs(
                "ops.turo_today_unprepped",
                layer=LAYER_OPS,
                group="operations",
                label="Turo pickup/return today with nothing prepped",
                state=UNAVAILABLE,
                detail=f"Calendar source unavailable: {cal.get('error')}",
                severity="low",
                source="calendar",
                as_of=as_of,
                now=now,
            )
        )
    else:
        unprepped = []
        for trip in block.get("turo_trips") or []:
            if not isinstance(trip, dict):
                continue
            if not trip.get("today"):
                continue
            eid = str(trip.get("id") or "")
            prepped = bool(trip.get("prepped")) or eid in prepped_event_ids
            if not prepped:
                unprepped.append(trip)
        if unprepped:
            titles = ", ".join(
                str(t.get("title") or t.get("kind") or "trip") for t in unprepped[:4]
            )
            out.append(
                _obs(
                    "ops.turo_today_unprepped",
                    layer=LAYER_OPS,
                    group="operations",
                    label="Turo pickup/return today with nothing prepped",
                    state=UNATTENDED,
                    detail=f"{len(unprepped)} Turo trip(s) today unprepped: {titles}.",
                    severity="critical",
                    source="calendar",
                    as_of=as_of,
                    now=now,
                )
            )
        else:
            out.append(
                _obs(
                    "ops.turo_today_unprepped",
                    layer=LAYER_OPS,
                    group="operations",
                    label="Turo pickup/return today with nothing prepped",
                    state=ATTENDED,
                    detail="No unprepped Turo pickup/return today.",
                    severity="info",
                    source="calendar",
                    as_of=as_of,
                    now=now,
                )
            )

    inv = block.get("invoice_ready") or {}
    if not inv.get("wired"):
        out.append(
            _obs(
                "ops.invoice_ready_unassigned",
                layer=LAYER_OPS,
                group="operations",
                label="Invoice-ready items sitting unassigned",
                state=WIRING_PLANNED,
                detail=f"Wiring planned: {INVOICE_DEP}.",
                severity="info",
                source="turso",
                as_of=as_of,
                wiring_dependency=INVOICE_DEP,
                now=now,
            )
        )
    else:
        items = [x for x in (inv.get("unassigned") or []) if isinstance(x, dict)]
        if items:
            out.append(
                _obs(
                    "ops.invoice_ready_unassigned",
                    layer=LAYER_OPS,
                    group="operations",
                    label="Invoice-ready items sitting unassigned",
                    state=UNATTENDED,
                    detail=f"{len(items)} invoice-ready item(s) unassigned.",
                    severity="high",
                    source="turso",
                    as_of=as_of,
                    now=now,
                )
            )
        else:
            out.append(
                _obs(
                    "ops.invoice_ready_unassigned",
                    layer=LAYER_OPS,
                    group="operations",
                    label="Invoice-ready items sitting unassigned",
                    state=ATTENDED,
                    detail="No unassigned invoice-ready items.",
                    severity="info",
                    source="turso",
                    as_of=as_of,
                    now=now,
                )
            )
    return out
