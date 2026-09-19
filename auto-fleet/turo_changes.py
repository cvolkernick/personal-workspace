"""Detect Turo trip changes from dump mail and apply calendar/sheet/task close-loop.

Ingest sources (see auto-fleet/README.md):
- Pi dump: auto-fleet-turo-writer.timer → turo_gmail --fetch
- Bot overnight / Gmail MCP: same JSON dump via --from-json
Parser watches formal "changed their trip", booking-modified, and guest-message
threads that carry a reservation id plus a new window or place.
Helm owns live calendar+sheet tokens; this module writes a plan and applies
when clients are injected or tokens exist. Tasks close only after calendar apply.
No Turo UI scrape.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from . import fleet, turo_calendar, turo_inbox, turo_sheet, turo_tasks
except ImportError:  # script / unittest path
    import fleet  # type: ignore
    import turo_calendar  # type: ignore
    import turo_inbox  # type: ignore
    import turo_sheet  # type: ignore
    import turo_tasks  # type: ignore

DEFAULT_PLAN = Path.home() / ".config" / "auto-fleet" / "turo_changes.json"
PKG_DIR = Path(__file__).resolve().parent


def _norm_window(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace(" ", "T")


def _windows_differ(a: Any, b: Any) -> bool:
    left, right = _norm_window(a), _norm_window(b)
    if not left or not right:
        return False
    if left == right:
        return False
    if left[:10] == right[:10] and ("T" not in left or "T" not in right):
        return False
    return True


def _places_differ(new: Any, old: Any) -> bool:
    left = " ".join(str(new or "").lower().split())
    right = " ".join(str(old or "").lower().split())
    if not left:
        return False
    if not right:
        return True
    return left != right


def _message_sort_key(raw: Mapping[str, Any]) -> str:
    dt = turo_inbox.message_datetime(raw)
    if dt is None:
        return str(raw.get("date") or "")
    return dt.isoformat()


def _booking_state(bookings: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest booked/modified window per trip_id, chronological."""
    by_trip: dict[str, dict[str, Any]] = {}
    ordered = sorted(bookings, key=_message_sort_key)
    for rec in ordered:
        status = str(rec.get("status") or rec.get("kind") or "")
        if status not in ("booked", "modified"):
            continue
        trip = str(rec.get("trip_id") or "").strip()
        if not trip:
            continue
        by_trip[trip] = dict(rec)
    return by_trip


def _has_ops_signal(fields: Mapping[str, Any]) -> bool:
    return bool(
        fields.get("start")
        or fields.get("end")
        or fields.get("pickup")
        or fields.get("drop_off")
    )


def _change_record(
    *,
    source: str,
    raw: Mapping[str, Any],
    fields: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    units: Sequence[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    trip = str(fields.get("trip_id") or (prior or {}).get("trip_id") or "").strip()
    start = fields.get("start") or (prior or {}).get("start")
    end = fields.get("end") or (prior or {}).get("end")
    pickup = fields.get("pickup") or (prior or {}).get("pickup")
    drop_off = fields.get("drop_off") or (prior or {}).get("drop_off")
    prior_start = (prior or {}).get("start")
    prior_end = (prior or {}).get("end")
    prior_pickup = (prior or {}).get("pickup")
    prior_drop = (prior or {}).get("drop_off")
    time_changed = _windows_differ(fields.get("start"), prior_start) or _windows_differ(
        fields.get("end"), prior_end
    )
    place_changed = _places_differ(fields.get("pickup"), prior_pickup) or _places_differ(
        fields.get("drop_off"), prior_drop
    )
    if prior is not None and not time_changed and not place_changed:
        return None
    if prior is None and not _has_ops_signal(fields):
        return None
    if source == "guest_message" and prior is None and not _has_ops_signal(fields):
        return None
    if source == "guest_message" and prior is None:
        # Guest chat without a booked baseline is only a change when it names
        # a new window or place — still emit so Helm can apply.
        if not (fields.get("start") or fields.get("end") or fields.get("pickup") or fields.get("drop_off")):
            return None
    rec = {
        "source": source,
        "trip_id": trip or None,
        "guest": fields.get("guest") or (prior or {}).get("guest"),
        "vehicle": fields.get("vehicle") or (prior or {}).get("vehicle"),
        "start": start,
        "end": end,
        "pickup": pickup,
        "drop_off": drop_off,
        "prior_start": prior_start,
        "prior_end": prior_end,
        "prior_pickup": prior_pickup,
        "prior_drop_off": prior_drop,
        "time_changed": time_changed if prior is not None else bool(fields.get("start") or fields.get("end")),
        "place_changed": place_changed if prior is not None else bool(fields.get("pickup") or fields.get("drop_off")),
        "message_id": raw.get("id") or raw.get("message_id") or raw.get("message-id"),
        "subject": str(raw.get("subject") or ""),
        "date": raw.get("date"),
        "payout": (prior or {}).get("payout") if source != "formal_change" else fields.get("payout") or (prior or {}).get("payout"),
    }
    unit_id = turo_inbox.match_unit(rec, list(units)) if units else None
    rec["unit_id"] = unit_id
    dt = turo_inbox.message_datetime(raw)
    if dt is not None:
        rec["detected_on"] = dt.astimezone(turo_inbox.FLEET_TZ).date().isoformat()
    return rec


def detect_changes(
    raw_messages: Sequence[Mapping[str, Any]],
    bookings: Sequence[Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Formal change + booking-modified + guest-thread window/place vs latest booked."""
    roster = list(units or [])
    state = _booking_state(
        [b for b in bookings if str(b.get("status") or b.get("kind") or "") == "booked"]
    )
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in sorted(raw_messages, key=_message_sort_key):
        if not isinstance(raw, dict):
            continue
        subject = str(raw.get("subject") or "")
        source = turo_inbox.classify_change_source(subject)
        fields = turo_inbox.extract_ops_fields(raw)
        trip = str(fields.get("trip_id") or "").strip()
        status = turo_inbox.classify_subject(subject)
        if status == "booked" and trip:
            merged = dict(state.get(trip) or {})
            merged.update({k: fields.get(k) for k in ("trip_id", "start", "end", "pickup", "drop_off", "guest", "vehicle", "payout")})
            merged["status"] = "booked"
            state[trip] = merged
        if source is None:
            continue
        if source == "guest_message" and not _has_ops_signal(fields):
            continue
        prior = state.get(trip) if trip else None
        change = _change_record(
            source=source,
            raw=raw,
            fields=fields,
            prior=prior,
            units=roster,
        )
        if change is None:
            continue
        key = (
            str(change.get("trip_id") or ""),
            str(change.get("end") or ""),
            str(change.get("pickup") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(change)
        if trip:
            merged = dict(prior or {})
            merged.update(
                {
                    "trip_id": trip,
                    "start": change.get("start"),
                    "end": change.get("end"),
                    "pickup": change.get("pickup"),
                    "drop_off": change.get("drop_off"),
                    "guest": change.get("guest"),
                    "vehicle": change.get("vehicle"),
                    "status": "modified",
                }
            )
            state[trip] = merged
    return out


def write_plan(
    changes: Sequence[Mapping[str, Any]],
    path: Path | None = None,
    *,
    applied: Sequence[Mapping[str, Any]] | None = None,
) -> Path:
    dest = Path(path) if path is not None else DEFAULT_PLAN
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of": datetime.now(turo_inbox.FLEET_TZ).isoformat(),
        "source": "turo_changes",
        "calendar_id": turo_calendar.TURO_CALENDAR_ID,
        "rivian_sheet_id": turo_sheet.RIVIAN_SHEET_ID,
        "rivian_tab": turo_sheet.TURO_BOOKINGS_TAB,
        "changes": [dict(c) for c in changes],
        "applied": [dict(a) for a in (applied or [])],
    }
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest


def apply_change(
    change: Mapping[str, Any],
    *,
    calendar: Any | None = None,
    sheets: Any | None = None,
    gt: Any | None = None,
) -> dict[str, Any]:
    """Calendar both events, then Rivian sheet, then GT close-loop.

    Tasks do not close when calendar was skipped — calendar must be correct first.
    """
    cal_result = turo_calendar.apply_calendar_change(change, calendar)
    sheet_result: dict[str, Any]
    if turo_sheet.is_rivian_change(change):
        sheet_result = turo_sheet.apply_rivian_sheet(change, sheets)
    else:
        sheet_result = {"ok": True, "skipped": True, "reason": "not_rivian"}
    if cal_result.get("ok"):
        task_result = turo_tasks.close_or_rewrite_stale(change, gt=gt)
    else:
        task_result = {
            "ok": False,
            "skipped": True,
            "reason": "calendar_not_applied",
            "calendar_error": cal_result.get("error") or cal_result.get("skipped"),
        }
    return {
        "ok": bool(cal_result.get("ok"))
        and bool(sheet_result.get("ok"))
        and (bool(task_result.get("ok")) or bool(task_result.get("skipped"))),
        "trip_id": change.get("trip_id"),
        "source": change.get("source"),
        "calendar": cal_result,
        "sheet": sheet_result,
        "tasks": task_result,
    }


def apply_all(
    changes: Sequence[Mapping[str, Any]],
    *,
    calendar: Any | None = None,
    sheets: Any | None = None,
    gt: Any | None = None,
) -> list[dict[str, Any]]:
    return [
        apply_change(ch, calendar=calendar, sheets=sheets, gt=gt) for ch in changes
    ]


def sync_from_dump(
    inbox_path: Path,
    *,
    roster_path: Path | None = None,
    plan_path: Path | None = None,
    calendar: Any | None = None,
    sheets: Any | None = None,
    gt: Any | None = None,
    apply: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    roster = fleet.load_roster(roster_path)
    units = [u for u in roster["units"] if isinstance(u, dict) and u.get("id")]
    payload = turo_inbox.turo_payload(inbox_path=inbox_path, units=units, env=env)
    changes = list(payload.get("changes") or [])
    dest = Path(plan_path) if plan_path is not None else Path(inbox_path).expanduser().resolve().parent / "turo_changes.json"
    applied: list[dict[str, Any]] = []
    if apply and changes:
        if calendar is None:
            calendar = turo_calendar.load_calendar(env=env)
        if sheets is None:
            sheets = turo_sheet.load_sheets(env=env)
        applied = apply_all(changes, calendar=calendar, sheets=sheets, gt=gt)
    write_plan(changes, dest, applied=applied)
    return {
        "ok": True,
        "changes": changes,
        "applied": applied,
        "plan_path": str(dest),
        "inbox_path": str(inbox_path),
    }


def maybe_sync_from_dump(inbox_path: Path, *, env: Mapping[str, str] | None = None) -> None:
    """Best-effort writer hook. Never fail Gmail dump."""
    try:
        environ = dict(env) if env is not None else dict(os.environ)
        apply = str(environ.get("AUTO_FLEET_TURO_APPLY") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        calendar = turo_calendar.load_calendar(env=environ) if apply else None
        sheets = turo_sheet.load_sheets(env=environ) if apply else None
        gt = None
        if apply:
            try:
                import gtasks as gtb
            except ImportError:
                gtb = None  # type: ignore
            if gtb is not None and gtb.credentials_status().get("ok"):
                gt = gtb.load_google_tasks()
        # Always write the plan. Apply only when AUTO_FLEET_TURO_APPLY is on
        # and clients exist. Helm `--apply` is the ops layer.
        sync_from_dump(
            inbox_path,
            env=environ,
            apply=apply,
            calendar=calendar,
            sheets=sheets,
            gt=gt,
        )
    except Exception:
        return


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-dump",
        type=Path,
        default=turo_inbox.CONFIG_INBOX,
        help="turo_inbox.json path",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="plan JSON path (default next to dump)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply calendar + Rivian sheet + GT close-loop when clients/tokens exist",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = sync_from_dump(
        args.from_dump,
        plan_path=args.plan,
        apply=args.apply,
    )
    n = len(result.get("changes") or [])
    print(f"detected {n} change(s); plan={result.get('plan_path')}")
    if args.apply:
        print(f"applied {len(result.get('applied') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
