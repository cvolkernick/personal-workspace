"""Assemble GET /api/fleet from roster + expenses snapshot + notes + DIMO + Turo."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from . import car_cards, dimo_client, glance, turo_inbox, turo_tasks
except ImportError:  # script / unittest path
    import car_cards  # type: ignore
    import dimo_client  # type: ignore
    import glance  # type: ignore
    import turo_inbox  # type: ignore
    import turo_tasks  # type: ignore

PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PKG_DIR / "data"
DEFAULT_ROSTER = DATA_DIR / "roster.json"
DEFAULT_NOTES = DATA_DIR / "notes.json"
DEFAULT_EXPENSES = PKG_DIR.parent / "treasury" / "snapshots" / "expenses_latest.json"
DEFAULT_WORKTREE_BASE = Path.home() / "personal-workspace-worktrees"


def _worktree_base(override: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    if override is not None:
        return override
    raw = (env or os.environ).get("PERSONAL_WORKSPACE_WORKTREES") or str(DEFAULT_WORKTREE_BASE)
    return Path(raw).expanduser()


def treasury_worktree_expenses(worktree_base: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """Nakatoshi Slice A lands on the treasury worktree before it merges to this branch."""
    return _worktree_base(worktree_base, env) / "treasury" / "treasury" / "snapshots" / "expenses_latest.json"

VALID_ROLES = {"personal", "turo", "unknown"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_roster(path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or DEFAULT_ROSTER) or {}
    units = data.get("units") if isinstance(data.get("units"), list) else []
    return {"as_of": data.get("as_of"), "units": units, "path": str(path or DEFAULT_ROSTER)}


def load_notes(path: Path | None = None) -> dict[str, Any]:
    data = _read_json(path or DEFAULT_NOTES)
    if not data:
        return {"as_of": None, "stale": True, "units": {}, "path": str(path or DEFAULT_NOTES)}
    units = data.get("units") if isinstance(data.get("units"), dict) else {}
    return {
        "as_of": data.get("as_of"),
        "source": data.get("source"),
        "stale": bool(data.get("stale", True)),
        "disclaimer": data.get("disclaimer"),
        "units": units,
        "path": str(path or DEFAULT_NOTES),
    }


def fleet_tab(expenses: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if not expenses:
        return None
    tabs = expenses.get("tabs")
    if not isinstance(tabs, dict):
        return None
    block = tabs.get("Fleet")
    if not isinstance(block, dict):
        return None
    if (block.get("role") or "") != "fleet_ops" and not block.get("items"):
        return None
    return block


def resolve_expenses_path(
    explicit: Path | None = None,
    *,
    local_default: Path | None = None,
    worktree_base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Pick a snapshot that actually has ``tabs.Fleet``.

    Unit cards never read ``summary.combined_monthly``. Discovery order:
    explicit path, ``AUTO_FLEET_EXPENSES``, then the first of (this checkout,
    treasury worktree) that has a Fleet tab. Costs/notes only — not FCC burn.
    """
    environ = env if env is not None else os.environ
    if explicit is not None:
        return Path(explicit)
    override = (environ.get("AUTO_FLEET_EXPENSES") or "").strip()
    if override:
        return Path(override).expanduser()
    local = Path(local_default) if local_default is not None else DEFAULT_EXPENSES
    candidates = [local, treasury_worktree_expenses(worktree_base, environ)]
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if fleet_tab(_read_json(cand)) is not None:
            return cand
    return local


def load_expenses(path: Path | None = None) -> Optional[dict[str, Any]]:
    return _read_json(resolve_expenses_path(path))


def _item_name(item: Mapping[str, Any]) -> str:
    return str(item.get("item") or "")


def classify_sheet_line(
    item: Mapping[str, Any], units: list[Mapping[str, Any]]
) -> tuple[str, Optional[str]]:
    """Return ('unit', unit_id) or ('shared', reason)."""
    name = _item_name(item).strip()
    name_l = name.lower()
    if not name:
        return "shared", "empty"

    for u in units:
        sheet_item = (u.get("dimo_sheet_item") or "").strip()
        if sheet_item and sheet_item.lower() == name_l:
            return "unit", str(u["id"])

    lender_hits: list[str] = []
    for u in units:
        lender = (u.get("lender") or "").strip()
        if lender and lender.lower() in name_l:
            lender_hits.append(str(u["id"]))
    if len(lender_hits) == 1:
        return "unit", lender_hits[0]
    if len(lender_hits) > 1:
        return "shared", "ambiguous_lender"

    if "rivian" in name_l:
        return "shared", "planned_not_on_roster"
    return "shared", "unassigned"


def _sheet_line_view(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "item": item.get("item"),
        "monthly": item.get("monthly"),
        "from": item.get("from"),
        "date": item.get("date"),
    }


def identity_for(unit: Mapping[str, Any]) -> dict[str, Any]:
    role = unit.get("role") or "unknown"
    if role not in VALID_ROLES:
        role = "unknown"
    # Display/ops metadata only. Match key stays year-in-mail-body — never plate.
    plate = unit.get("plate")
    if plate is not None:
        plate = str(plate).strip() or None
    return {
        "year": unit.get("year"),
        "make": unit.get("make"),
        "model": unit.get("model"),
        "vin": unit.get("vin"),
        "plate": plate,
        "host_label": car_cards.host_label_for(unit),
        "host_identity": car_cards.host_identity_for(unit),
        "role": role,
        "lender": unit.get("lender"),
        "account": unit.get("account"),
    }


def finance_for_unit(
    unit: Mapping[str, Any],
    *,
    tab: Optional[Mapping[str, Any]],
    expenses: Optional[Mapping[str, Any]],
    notes: Mapping[str, Any],
    units: list[Mapping[str, Any]],
) -> dict[str, Any]:
    uid = str(unit.get("id") or "")
    portal = (notes.get("units") or {}).get(uid)
    portal_block = None
    if isinstance(portal, dict):
        ptp = portal.get("promise_to_pay") or portal.get("ptp")
        portal_block = {
            **portal,
            "as_of": notes.get("as_of"),
            "stale": True,
            "live": False,
            "principal_is_payoff": bool(
                portal.get("principal_is_payoff", portal.get("principal_is_payoff_quote", False))
            ),
            "ptp": ptp,
            "source": notes.get("source") or "portal_override",
            "disclaimer": notes.get("disclaimer")
            or "Portal snapshot. Does not refresh. Not a live payoff.",
        }

    assigned: list[dict[str, Any]] = []
    if tab is not None:
        assigned = [
            _sheet_line_view(i)
            for i in (tab.get("items") or [])
            if isinstance(i, dict) and classify_sheet_line(i, units) == ("unit", uid)
        ]
    has_sheet = bool(assigned)
    has_tab = tab is not None
    monthly = sum(float(i.get("monthly") or 0) for i in assigned) if has_sheet else None
    locked = car_cards.locked_finance_for(uid)
    return {
        "stale": not has_tab,
        "source": "expenses_sync.tabs.Fleet" if has_tab else "roster_notes",
        "snapshot_as_of": (expenses or {}).get("as_of") if has_tab else notes.get("as_of"),
        "role": "fleet_ops",
        "locked": locked,
        "sheet_lines": assigned,
        "sheet_monthly": round(monthly, 2) if monthly is not None else None,
        "note": (
            "Sheet = planned cash. Portal override is a dated snapshot, not a live payoff."
            if has_sheet
            else (
                "Fleet tab not in expenses snapshot or no lines for this unit; "
                "showing roster/portal notes only. Not a live payoff."
            )
        ),
        "portal": portal_block,
        "portal_override": portal_block,
    }


def shared_finance(
    tab: Optional[Mapping[str, Any]],
    units: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if tab is None:
        return {"stale": True, "lines": [], "items": [], "monthly": None}
    lines = []
    for item in tab.get("items") or []:
        if not isinstance(item, dict):
            continue
        kind, reason = classify_sheet_line(item, units)
        if kind != "shared":
            continue
        view = _sheet_line_view(item)
        view["reason"] = reason
        lines.append(view)
    return {
        "stale": False,
        "source": "expenses_sync.tabs.Fleet",
        "lines": lines,
        "items": lines,
        "monthly": round(sum(float(i.get("monthly") or 0) for i in lines), 2),
        "note": "Insurance / wash / planned Rivian / unassigned Tesla lines. Planned Rivian cash is not pinned to r1s-2023 yet.",
    }


def _dimo_for_units(
    units: Sequence[Mapping[str, Any]],
    env: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Fetch DIMO per unit. Parallel — sequential SDK round-trips were ~12s on Pi."""
    if not units:
        return []
    if len(units) == 1:
        return [dimo_client.dimo_for_unit(units[0], env)]
    workers = min(8, len(units))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda unit: dimo_client.dimo_for_unit(unit, env), units))


def _attach_invoice_ready(
    assembled: list[dict[str, Any]],
    units: Sequence[Mapping[str, Any]],
    *,
    gt: Any | None = None,
) -> dict[str, Any]:
    """Nest open GT Turo items on each car. Completed stay off. No invented rows."""
    bookings_by_unit = {
        str(row["id"]): (row.get("turo") or {}).get("bookings") or []
        for row in assembled
        if row.get("id")
    }
    listed = turo_tasks.list_open_tasks(
        gt=gt,
        units=list(units),
        bookings_by_unit=bookings_by_unit,
    )
    split = car_cards.attach_invoice_items(
        listed.get("items") or [],
        units,
        bookings_by_unit,
    )
    for row in assembled:
        ready = split["by_unit"].get(str(row["id"]), [])
        row["invoice_ready"] = ready
        turo = row.get("turo")
        if isinstance(turo, dict):
            turo["invoice_ready"] = ready
    return {
        "invoice_unmatched": split["unmatched"],
        "turo_tasks": {
            "ok": listed.get("ok"),
            "source": listed.get("source") or "google_tasks",
            "list_title": listed.get("list_title") or turo_tasks.LIST_TITLE,
            "list_id": listed.get("list_id"),
            "error": listed.get("error"),
            "count": listed.get("count", len(listed.get("items") or [])),
        },
    }


def build_fleet(
    *,
    roster_path: Path | None = None,
    notes_path: Path | None = None,
    expenses_path: Path | None = None,
    inbox_path: Path | None = None,
    env_path: Path | None = None,
    dimo_env: Mapping[str, str] | None = None,
    now: str | None = None,
    gt: Any | None = None,
) -> dict[str, Any]:
    roster = load_roster(roster_path)
    notes = load_notes(notes_path)
    resolved_expenses = resolve_expenses_path(expenses_path)
    expenses = load_expenses(resolved_expenses)
    tab = fleet_tab(expenses)
    units = [u for u in roster["units"] if isinstance(u, dict) and u.get("id")]
    turo = turo_inbox.turo_payload(
        inbox_path=turo_inbox.resolve_inbox_path(inbox_path, DATA_DIR, env=os.environ),
        units=units,
    )
    env = dimo_env if dimo_env is not None else dimo_client.load_dimo_env(env_path)
    now_s = now or _now()
    poll_s = turo.get("poll_interval_s") or turo_inbox.POLL_INTERVAL_S
    dimo_rows = _dimo_for_units(units, env)

    assembled = []
    for unit, dimo in zip(units, dimo_rows):
        turo_unit = turo_inbox.turo_for_unit(str(unit["id"]), turo)
        turo_unit["schedule"] = car_cards.schedule_for_bookings(
            turo_unit.get("bookings") or [], now_s
        )
        turo_unit.setdefault("photos", [])
        row = {
            "id": unit["id"],
            "identity": identity_for(unit),
            "finance": finance_for_unit(
                unit, tab=tab, expenses=expenses, notes=notes, units=units
            ),
            "dimo": dimo,
            "turo": turo_unit,
        }
        row["glance"] = glance.glance_for_unit(
            row, now=now_s, poll_interval_s=poll_s
        )
        assembled.append(row)

    invoice = _attach_invoice_ready(assembled, units, gt=gt)

    return {
        "ok": True,
        "as_of": now_s,
        "roster_as_of": roster.get("as_of"),
        "unit_count": len(assembled),
        "units": assembled,
        "shared_finance": shared_finance(tab, units),
        "turo_unmatched": turo.get("unmatched") or [],
        "turo_photos": turo.get("photo_messages") or [],
        "turo_unmatched_photos": turo.get("unmatched_photos") or [],
        "invoice_unmatched": invoice["invoice_unmatched"],
        "expenses_snapshot": {
            "path": str(resolved_expenses),
            "as_of": (expenses or {}).get("as_of") if expenses else None,
            "has_fleet_tab": tab is not None,
        },
        "sources": {
            "expenses": {
                "path": str(resolved_expenses),
                "as_of": (expenses or {}).get("as_of") if expenses else None,
                "has_fleet_tab": tab is not None,
                "uses_combined_monthly": False,
                "note": (
                    "Unit cards read tabs.Fleet items only as ops cost data. "
                    "Do not use summary.combined_monthly."
                ),
            },
            "notes": {
                "path": notes.get("path"),
                "as_of": notes.get("as_of"),
                "stale": notes.get("stale"),
            },
            "dimo": {
                "configured": dimo_client.is_configured(env),
                "env_path": str(env_path) if env_path else str(dimo_client.default_env_path()),
            },
            "turo": {
                "inbox_status": turo.get("inbox_status"),
                "inbox_kind": turo.get("inbox_kind"),
                "refreshed_at": turo.get("refreshed_at"),
                "forward_since": turo.get("forward_since"),
                "poll_interval_s": turo.get("poll_interval_s"),
                "media_dir": turo.get("media_dir"),
                "photo_count": len(turo.get("photo_messages") or []),
            },
            "turo_tasks": invoice["turo_tasks"],
        },
    }
