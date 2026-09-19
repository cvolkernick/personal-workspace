"""Live snapshot adapters. Fail honest — never paint unwired sources as OK.

YNAB uses the existing treasury.ynab_sync auth pattern. Calendar prefers
`hatch_gws_cli` when present. FitDash and invoice-ready stay unwired.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from domain import iso


def _now_iso(now: datetime) -> str:
    return iso(now)


def empty_snapshot(*, now: datetime, reason: str = "") -> dict[str, Any]:
    as_of = _now_iso(now)
    return {
        "as_of": as_of,
        "fitness": {
            "wired": False,
            "as_of": as_of,
            "phase": "cutting",
            "note": "FitDash has no read API yet.",
        },
        "financial": {
            "wired": False,
            "as_of": as_of,
            "error": reason or "not collected",
            "bills": [],
            "categories": [],
            "payoff": [],
        },
        "operations": {
            "as_of": as_of,
            "calendar": {"wired": False, "error": reason or "not collected"},
            "turo_trips": [],
            "training": [],
            "invoice_ready": {"wired": False},
        },
        "radar": {"candidates": [], "as_of": as_of},
    }


def collect_snapshot(
    *,
    now: datetime,
    workspace: Optional[Path] = None,
    payoff_history: Optional[dict[str, Any]] = None,
    prepped_event_ids: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Build a live snapshot. Each sensor is isolated; failures stay local."""
    workspace = Path(workspace) if workspace else Path(__file__).resolve().parents[1]
    snap = empty_snapshot(now=now, reason="")
    snap["financial"] = collect_ynab(now=now, workspace=workspace, payoff_history=payoff_history)
    cal, trips, training = collect_calendar(now=now, prepped_event_ids=prepped_event_ids or set())
    snap["operations"] = {
        "as_of": _now_iso(now),
        "calendar": cal,
        "turo_trips": trips,
        "training": training,
        "invoice_ready": {"wired": False},
    }
    snap["radar"] = {
        "candidates": collect_radar_inputs(now=now, workspace=workspace),
        "as_of": _now_iso(now),
    }
    snap["as_of"] = _now_iso(now)
    return snap


def collect_ynab(
    *,
    now: datetime,
    workspace: Path,
    payoff_history: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    as_of = _now_iso(now)
    root = str(workspace)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from treasury.ynab_sync import load_ynab_token, pick_budget, ynab_get
    except Exception as e:  # noqa: BLE001
        return {
            "wired": False,
            "as_of": as_of,
            "error": f"ynab adapter import failed: {e}",
            "bills": [],
            "categories": [],
            "payoff": [],
        }
    token, token_src = load_ynab_token()
    if not token:
        return {
            "wired": False,
            "as_of": as_of,
            "error": "YNAB token missing (~/.config/ynab/token or YNAB_TOKEN)",
            "bills": [],
            "categories": [],
            "payoff": [],
            "token_source": token_src,
        }
    try:
        budgets = (ynab_get("/budgets", token).get("data") or {}).get("budgets") or []
        budget = pick_budget(budgets)
        bid = budget.get("id")
        month = now.astimezone(timezone.utc).strftime("%Y-%m-01")
        month_payload = ynab_get(f"/budgets/{bid}/months/{month}", token)
        month_data = (month_payload.get("data") or {}).get("month") or {}
        categories = []
        for cat in month_data.get("categories") or []:
            if not isinstance(cat, dict) or cat.get("deleted") or cat.get("hidden"):
                continue
            categories.append(
                {
                    "id": cat.get("id"),
                    "name": cat.get("name"),
                    "balance": _milli(cat.get("balance")),
                    "budgeted": _milli(cat.get("budgeted")),
                    "activity": _milli(cat.get("activity")),
                    "goal_under_funded": _milli(cat.get("goal_under_funded")),
                    "goal_target": _milli(cat.get("goal_target")),
                }
            )
        sched_payload = ynab_get(f"/budgets/{bid}/scheduled_transactions", token)
        scheduled = (sched_payload.get("data") or {}).get("scheduled_transactions") or []
        today = now.astimezone(timezone.utc).date()
        horizon = today + timedelta(days=2)
        bills = []
        for txn in scheduled:
            if not isinstance(txn, dict) or txn.get("deleted"):
                continue
            date_next = str(txn.get("date_next") or txn.get("date") or "")[:10]
            try:
                d = datetime.strptime(date_next, "%Y-%m-%d").date()
            except ValueError:
                continue
            amount = _milli(txn.get("amount"))
            # Outflows are negative milliunits in YNAB.
            is_outflow = amount < 0
            if not is_outflow:
                continue
            unscheduled = False  # it is on the scheduled_transactions list
            due_soon = today <= d <= horizon
            bills.append(
                {
                    "id": txn.get("id"),
                    "payee": (txn.get("payee_name") or txn.get("memo") or "scheduled"),
                    "date": date_next,
                    "amount": abs(amount),
                    "scheduled": True,
                    "unscheduled": unscheduled,
                    "due_soon": due_soon,
                    "paid": False,
                    "frequency": txn.get("frequency"),
                }
            )
        payoff = _payoff_rows(categories, payoff_history or {}, now)
        return {
            "wired": True,
            "as_of": as_of,
            "budget_id": bid,
            "budget_name": budget.get("name"),
            "bills": bills,
            "categories": categories,
            "payoff": payoff,
            "token_source": token_src,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "wired": False,
            "as_of": as_of,
            "error": f"YNAB fetch failed: {e}",
            "bills": [],
            "categories": [],
            "payoff": [],
        }


def _milli(value: Any) -> float:
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _payoff_rows(
    categories: list[dict[str, Any]],
    history: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """Behind = negative category balance. amount_14d_ago from compass history."""
    cutoff = now - timedelta(days=14)
    out = []
    for cat in categories:
        bal = float(cat.get("balance") or 0)
        if bal >= 0:
            continue
        cid = str(cat.get("id") or cat.get("name"))
        prev = None
        for point in history.get(cid) or []:
            if not isinstance(point, dict):
                continue
            at = point.get("at")
            try:
                from domain import parse_timestamp

                dt = parse_timestamp(at)
            except Exception:
                dt = None
            if dt is not None and dt <= cutoff:
                prev = point.get("amount")
                break
        out.append(
            {
                "id": cid,
                "name": cat.get("name"),
                "amount": abs(bal),
                "amount_14d_ago": prev,
            }
        )
    return out


def collect_calendar(
    *,
    now: datetime,
    prepped_event_ids: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    as_of = _now_iso(now)
    exe = shutil.which("hatch_gws_cli")
    if not exe:
        return (
            {
                "wired": False,
                "error": "hatch_gws_cli not on PATH",
                "as_of": as_of,
            },
            [],
            [],
        )
    today = now.astimezone(timezone.utc).date().isoformat()
    try:
        proc = subprocess.run(
            [exe, "calendar", "+agenda", "--today", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return (
            {"wired": False, "error": f"calendar cli failed: {e}", "as_of": as_of},
            [],
            [],
        )
    if proc.returncode != 0:
        # Retry without --json for CLIs that only print text.
        try:
            proc2 = subprocess.run(
                [exe, "calendar", "+agenda", "--today"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return (
                {"wired": False, "error": f"calendar cli failed: {e}", "as_of": as_of},
                [],
                [],
            )
        if proc2.returncode != 0:
            err = (proc2.stderr or proc.stderr or "calendar cli error")[:300]
            return (
                {"wired": False, "error": err, "as_of": as_of},
                [],
                [],
            )
        events = _parse_agenda_text(proc2.stdout or "")
    else:
        events = _parse_agenda_json(proc.stdout or "")
        if not events and (proc.stdout or "").strip():
            events = _parse_agenda_text(proc.stdout or "")

    trips: list[dict[str, Any]] = []
    training: list[dict[str, Any]] = []
    for ev in events:
        title = str(ev.get("title") or ev.get("summary") or "")
        eid = str(ev.get("id") or title)
        low = title.lower()
        blob = f"{title} {ev.get('description') or ''}".lower()
        if "[fitdash-gym:" in blob or "[fitdash-gym:" in low:
            training.append(
                {
                    "id": eid,
                    "title": title,
                    "start": ev.get("start"),
                    "today": True,
                }
            )
        is_turo = "turo" in blob or "pickup" in low or "return" in low
        kind = None
        if "pickup" in low:
            kind = "pickup"
        elif "return" in low:
            kind = "return"
        if is_turo and kind:
            trips.append(
                {
                    "id": eid,
                    "title": title,
                    "kind": kind,
                    "start": ev.get("start"),
                    "today": True,
                    "prepped": eid in prepped_event_ids,
                    "date": today,
                }
            )
    return (
        {"wired": True, "as_of": as_of, "event_count": len(events)},
        trips,
        training,
    )


def _parse_agenda_json(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("events", "items", "agenda"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _parse_agenda_text(text: str) -> list[dict[str, Any]]:
    events = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append({"id": line[:80], "title": line, "summary": line})
    return events


def collect_radar_inputs(*, now: datetime, workspace: Path) -> list[dict[str, Any]]:
    """Opportunity lens over existing instruments. Quiet if nothing emerging."""
    as_of = _now_iso(now)
    candidates: list[dict[str, Any]] = []
    # GitHub: open issues labeled opportunity / radar if gh is available.
    gh = shutil.which("gh")
    if gh:
        try:
            proc = subprocess.run(
                [
                    gh,
                    "issue",
                    "list",
                    "--repo",
                    "cvolkernick/personal-workspace",
                    "--state",
                    "open",
                    "--limit",
                    "20",
                    "--json",
                    "number,title,labels,updatedAt",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env={**os.environ, "NO_COLOR": "1", "GH_FORCE_TTY": "0"},
            )
            if proc.returncode == 0 and proc.stdout.strip():
                issues = json.loads(proc.stdout)
                for iss in issues if isinstance(issues, list) else []:
                    labels = [
                        str(x.get("name") or "").lower()
                        for x in (iss.get("labels") or [])
                        if isinstance(x, dict)
                    ]
                    if "radar" in labels or "opportunity" in labels:
                        candidates.append(
                            {
                                "id": f"gh-{iss.get('number')}",
                                "title": iss.get("title"),
                                "why": f"Open GitHub issue #{iss.get('number')} labeled for radar.",
                                "source": "github",
                                "as_of": as_of,
                            }
                        )
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
    return candidates[:8]
