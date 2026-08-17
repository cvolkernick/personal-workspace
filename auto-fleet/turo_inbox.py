"""Parse a local Turo inbox fixture (JSON / .eml / maildir). Never hits the network.

Live Gmail is out of process until Chris forwards the host inbox. Default
shipped fixture has zero messages so the dashboard cannot invent bookings.

Payout destination is X Money (current). Mercury ACH is historical only
(May 2026). Payout mail is a cash-landed signal, not a booking record.
"""

from __future__ import annotations

import json
import mailbox
import re
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

DEFAULT_INBOX_NAME = "turo_inbox.json"

# Live destination. Mercury ACH (May 2026, cvolkern+mercury@gmail.com) is historical.
PAYOUT_DESTINATION = "X Money"
PAYOUT_DEST_NOTE = (
    "Payout destination is X Money. "
    "Mercury ACH is historical only (May 2026). "
    "Payout mail is a cash-landed signal, not a booking record."
)

_TRIP_ID = re.compile(
    r"(?:trip|reservation|booking)\s+(?:id|number|#)\s*[:#]?\s*([A-Z0-9-]{4,})",
    re.I,
)
_ISO_RANGE = re.compile(
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?)\s*(?:to|–|-|through)\s*"
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?)",
    re.I,
)
_US_RANGE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4})\s*(?:to|–|-|through)\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.I,
)
_MONEY = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{2})?)")
_GUEST = re.compile(
    r"(?:guest|renter)\s*[:\-]\s*([A-Z][A-Za-z.'\-]+(?:[ \t]+[A-Z][A-Za-z.'\-]+){0,3})",
    re.I,
)
_VIN = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
_PICKUP = re.compile(r"(?:pickup(?: location)?|pick-up|handoff)\s*[:\-]\s*(.+)", re.I)
_VEHICLE = re.compile(r"vehicle\s*[:\-]\s*(.+)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _header_str(msg: Message, name: str) -> str:
    raw = msg.get(name) or ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001
        return str(raw)


def _body_text(msg: Message) -> str:
    if msg.is_multipart():
        parts: list[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    parts.append(payload.decode(charset, errors="replace"))
                except LookupError:
                    parts.append(payload.decode("utf-8", errors="replace"))
        return "\n".join(parts)
    payload = msg.get_payload(decode=True)
    if payload is None:
        return str(msg.get_payload() or "")
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def classify_subject(subject: str) -> Optional[str]:
    s = (subject or "").lower()
    if any(w in s for w in ("cancel", "cancelled", "canceled")):
        return "canceled"
    if any(w in s for w in ("modified", "changed", "updated trip", "trip updated")):
        return "modified"
    if any(w in s for w in ("payout", "you earned", "trip earnings")):
        return "payout"
    if any(w in s for w in ("booked", "new trip", "reservation confirmed", "trip confirmed")):
        return "booked"
    if "turo" in s and any(w in s for w in ("trip", "reservation", "guest")):
        return "other"
    return None


def _us_to_iso(raw: str) -> str:
    try:
        dt = datetime.strptime(raw.strip(), "%m/%d/%Y")
    except ValueError:
        return raw
    return dt.date().isoformat()


def parse_message(raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Turn one JSON/maildir message into a booking record, or None if not Turo-ops."""
    subject = str(raw.get("subject") or "")
    body = str(raw.get("body") or "")
    sender = str(raw.get("from") or "")
    blob = f"{subject}\n{body}"
    status = classify_subject(subject)
    sender_l = sender.lower()
    is_turo = (
        "turo.com" in sender_l
        or "turo" in subject.lower()
        or "turo" in body.lower()
    )
    if status is None and not is_turo:
        return None
    if status is None:
        return None

    trip = None
    m = _TRIP_ID.search(blob)
    if m:
        trip = m.group(1)

    start = end = None
    rng = _ISO_RANGE.search(blob)
    if rng:
        start, end = rng.group(1), rng.group(2)
    else:
        us = _US_RANGE.search(blob)
        if us:
            start, end = _us_to_iso(us.group(1)), _us_to_iso(us.group(2))

    guest = None
    gm = _GUEST.search(blob)
    if gm:
        guest = gm.group(1).strip()

    vin = None
    vm = _VIN.search(blob)
    if vm:
        vin = vm.group(1)

    pickup = None
    pm = _PICKUP.search(blob)
    if pm:
        pickup = pm.group(1).strip().splitlines()[0][:160]

    payout = None
    if status == "payout":
        money = _MONEY.search(blob)
        if money:
            try:
                payout = float(money.group(1).replace(",", ""))
            except ValueError:
                payout = None

    vehicle = raw.get("vehicle")
    if not vehicle:
        vm2 = _VEHICLE.search(blob)
        if vm2:
            vehicle = vm2.group(1).strip().splitlines()[0]
    if not vehicle:
        for label in (
            "2022 Tesla Model 3",
            "2020 Tesla Model 3",
            "2024 Toyota Corolla",
            "2022 Toyota Corolla",
            "Tesla Model 3",
            "Toyota Corolla",
        ):
            if label.lower() in blob.lower():
                vehicle = label
                break

    rec = {
        "message_id": raw.get("id") or raw.get("message_id") or raw.get("message-id"),
        "subject": subject,
        "from": sender,
        "date": raw.get("date"),
        "kind": status,
        "status": status,
        "trip_id": trip,
        "guest": guest,
        "vehicle": vehicle,
        "vin": vin,
        "start": start,
        "end": end,
        "pickup": pickup,
        "payout": payout,
    }
    return rec


def parse_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        parsed = parse_message(rec)
        if parsed:
            out.append(parsed)
    return out


def _message_from_email(msg: Message, fallback_id: str) -> dict[str, Any]:
    return {
        "id": msg.get("Message-ID") or fallback_id,
        "subject": _header_str(msg, "Subject"),
        "from": _header_str(msg, "From"),
        "date": _header_str(msg, "Date"),
        "body": _body_text(msg),
    }


def _annotate_payout_dest(detail: str) -> str:
    base = (detail or "").rstrip()
    if PAYOUT_DESTINATION in base:
        return base
    if not base:
        return PAYOUT_DEST_NOTE
    return f"{base}. {PAYOUT_DEST_NOTE}"


def _unconfigured() -> dict[str, Any]:
    return {
        "bookings": [],
        "inbox_status": "unconfigured",
        "inbox_detail": (
            "no host inbox configured; live Gmail is not the Turo host inbox "
            "(last transactional mail 2025-03-27). Host mail is not forwarded."
        ),
        "message_count": 0,
        "inbox_kind": "missing",
        "payout_destination": PAYOUT_DESTINATION,
    }


def _result(
    *,
    bookings: list[dict[str, Any]],
    status: str,
    detail: str,
    message_count: int,
    kind: str,
    error: Optional[str] = None,
) -> dict[str, Any]:
    out = {
        "bookings": bookings,
        "inbox_status": status,
        "inbox_detail": detail,
        "message_count": message_count,
        "inbox_kind": kind,
    }
    if error:
        out["error"] = error
    return out


def load_json_messages(path: Path) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"parse error: {exc}"
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)], None
    if isinstance(data, dict):
        msgs = data.get("messages")
        if msgs is None:
            return [], None
        if not isinstance(msgs, list):
            return [], "parse error: messages is not a list"
        return [m for m in msgs if isinstance(m, dict)], None
    return [], "parse error: expected object or list"


def load_maildir_messages(path: Path) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        box = mailbox.Maildir(str(path), create=False)
    except Exception as exc:  # noqa: BLE001
        return [], f"parse error: {exc}"
    out: list[dict[str, Any]] = []
    try:
        for key, msg in box.iteritems():
            out.append(_message_from_email(msg, key))
    except Exception as exc:  # noqa: BLE001
        return out, f"parse error: {exc}"
    finally:
        try:
            box.close()
        except Exception:  # noqa: BLE001
            pass
    return out, None


def load_eml_message(path: Path) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        raw = path.read_bytes()
        msg = message_from_bytes(raw, policy=policy.default)
    except Exception as exc:  # noqa: BLE001
        return [], f"parse error: {exc}"
    return [_message_from_email(msg, path.name)], None


def resolve_inbox_path(explicit: Path | None, data_dir: Path) -> Path:
    if explicit is not None:
        return explicit
    return data_dir / DEFAULT_INBOX_NAME


def load_inbox(path: Path | None) -> dict[str, Any]:
    """Load JSON list/object, single .eml, or maildir. None / missing → unconfigured."""
    if path is None:
        return _unconfigured()
    p = Path(path)
    if not p.exists():
        return _unconfigured()
    if p.is_dir():
        raw, err = load_maildir_messages(p)
        kind = "maildir"
    elif p.suffix.lower() == ".eml":
        raw, err = load_eml_message(p)
        kind = "eml"
    else:
        raw, err = load_json_messages(p)
        kind = "json"
        if err and "parse error" in err:
            head = p.read_text(encoding="utf-8", errors="replace")[:1]
            if head not in ("{", "["):
                alt, alt_err = load_eml_message(p)
                if not alt_err and alt:
                    raw, err, kind = alt, None, "eml"

    if err:
        return _result(
            bookings=[],
            status="error",
            detail=err,
            message_count=0,
            kind=kind,
            error=err,
        )
    bookings = parse_records(raw)
    if not raw:
        return _result(
            bookings=[],
            status="empty",
            detail="no 2026 booking mail in this fixture — empty bookings, not invented trips",
            message_count=0,
            kind=kind,
        )
    if not bookings:
        return _result(
            bookings=[],
            status="empty",
            detail=f"{kind} parsed ({len(raw)} message(s)); none were trip booked/modified/canceled/payout",
            message_count=len(raw),
            kind=kind,
        )
    return _result(
        bookings=bookings,
        status="parsed",
        detail=f"{kind} parsed; {len(bookings)} trip event(s)",
        message_count=len(raw),
        kind=kind,
    )


def match_unit(booking: Mapping[str, Any], units: list[Mapping[str, Any]]) -> Optional[str]:
    vin = (booking.get("vin") or "").strip().upper()
    if vin:
        for u in units:
            if (u.get("vin") or "").upper() == vin:
                return str(u["id"])
    vehicle = (booking.get("vehicle") or "").lower()
    if not vehicle:
        return None
    hits: list[str] = []
    for u in units:
        label = f"{u.get('year')} {u.get('make')} {u.get('model')}".lower()
        short = f"{u.get('make')} {u.get('model')}".lower()
        year = str(u.get("year") or "")
        if label and label in vehicle:
            hits.append(str(u["id"]))
        elif short in vehicle and year and year in vehicle:
            hits.append(str(u["id"]))
    if len(hits) == 1:
        return hits[0]
    return None


def bookings_for_unit(
    bookings: Sequence[Mapping[str, Any]], unit: Mapping[str, Any]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in bookings:
        if not isinstance(raw, dict):
            continue
        if match_unit(raw, [unit]) == str(unit.get("id")):
            out.append(dict(raw))
    return out


def turo_payload(
    *,
    inbox_path: Path | None,
    units: list[Mapping[str, Any]],
) -> dict[str, Any]:
    loaded = load_inbox(inbox_path)
    bookings: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for parsed in loaded.get("bookings") or []:
        uid = match_unit(parsed, units)
        rec = dict(parsed)
        rec["unit_id"] = uid
        if uid:
            bookings.append(rec)
        else:
            unmatched.append(rec)

    status = loaded.get("inbox_status") or "empty"
    detail = loaded.get("inbox_detail") or ""
    by_unit: dict[str, list[dict[str, Any]]] = {str(u["id"]): [] for u in units}
    for b in bookings:
        by_unit[str(b["unit_id"])].append(b)
    return {
        "inbox_status": _annotate_payout_dest(detail or status),
        "inbox_state": status,
        "inbox_detail": _annotate_payout_dest(detail),
        "inbox_kind": loaded.get("inbox_kind"),
        "inbox_path": str(inbox_path) if inbox_path else None,
        "refreshed_at": _now(),
        "by_unit": by_unit,
        "unmatched": unmatched,
        "bookings": bookings + unmatched,
        "message_count": loaded.get("message_count", 0),
        "payout_destination": PAYOUT_DESTINATION,
    }


def turo_for_unit(unit_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bookings": list((payload.get("by_unit") or {}).get(unit_id) or []),
        "inbox_status": payload.get("inbox_status"),
        "payout_destination": payload.get("payout_destination") or PAYOUT_DESTINATION,
    }
