"""Parse a local Turo inbox fixture (JSON / .eml / maildir). Never hits the network.

Live Gmail is a dump file (`~/.config/auto-fleet/turo_inbox.json` or
`AUTO_FLEET_TURO_INBOX`), written by `turo_gmail.py` / a 15m agent poll.
The dashboard does not call Gmail. Default shipped fixture has zero messages
so we cannot invent bookings.

Forward-only: drop mail dated before 2026-08-18T02:00Z (host-inbox forward
start). Do not ingest historical Turo / Jessica / Kia / Spark.

Payout destination is X Money. Payout mail is a cash-landed signal, not a
booking record.
"""

from __future__ import annotations

import json
import mailbox
import os
import re
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

DEFAULT_INBOX_NAME = "turo_inbox.json"
CONFIG_INBOX = Path.home() / ".config" / "auto-fleet" / "turo_inbox.json"
GMAIL_INBOX_ADDR = "cvolkern@gmail.com"
# Live poll: Turo senders after the forward start. Do not OR label:Turo —
# that label is 2024 old-fleet mail.
GMAIL_QUERY = (
    "after:2026/08/18 from:(turo.com OR mail.turo.com OR transactional.turo.com)"
)
FORWARD_SINCE = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
FORWARD_SINCE_ISO = "2026-08-18T02:00:00+00:00"
POLL_INTERVAL_S = 900
CURRENT_HOST_MARK = "mike's vehicle"
_HISTORICAL_HOST_MARKS = ("jessica's vehicle",)

PAYOUT_DESTINATION = "X Money"
PAYOUT_DEST_NOTE = (
    "Payout destination is X Money. "
    "Payout mail is a cash-landed signal, not a booking record."
)

SinceSpec = Union[datetime, bool, None]

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
    r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:to|–|-|through)\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)
_LONG_RANGE = re.compile(
    r"from\s+[A-Za-z]+,\s+([A-Za-z]+ \d{1,2}, \d{4})\s+"
    r"\d{1,2}:\d{2}\s*[AP]M\s+to\s+[A-Za-z]+,\s+"
    r"([A-Za-z]+ \d{1,2}, \d{4})",
    re.I,
)
_TRIP_START = re.compile(
    r"trip start\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)
_TRIP_END = re.compile(
    r"trip end\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)
_MONEY = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{2})?)")
_GUEST = re.compile(
    r"(?:guest|renter|booked by)\s*[:\-]?\s*"
    r"([A-Z][A-Za-z.'\-]+(?:[ \t]+[A-Z][A-Za-z.'\-]+){0,3})",
    re.I,
)
_SUBJECT_GUEST = re.compile(
    r"([A-Za-z][A-Za-z.'\-]+(?:\s+[A-Za-z][A-Za-z.'\-]+)?)['’]s trip with your",
    re.I,
)
_VIN = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
_PICKUP = re.compile(r"(?:pickup(?: location)?|pick-up|handoff)\s*[:\-]\s*(.+)", re.I)
_VEHICLE = re.compile(r"vehicle\s*[:\-]\s*(.+)", re.I)
_YOUR_VEHICLE = re.compile(r"your\s+((?:19|20)\d{2}\s+)?([A-Za-z]+(?:\s+[A-Za-z0-9]+){0,3})", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_text(value: str) -> str:
    return (
        (value or "")
        .lower()
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u02bc", "'")
    )


def is_current_host_subject(subject: str) -> bool:
    return CURRENT_HOST_MARK in _norm_text(subject)


def is_historical_host_subject(subject: str) -> bool:
    blob = _norm_text(subject)
    return any(mark in blob for mark in _HISTORICAL_HOST_MARKS)


def message_datetime(raw: Mapping[str, Any]) -> Optional[datetime]:
    val = raw.get("date")
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        dt = val
    else:
        text = str(val).strip()
        dt = None
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            dt = None
        if dt is None:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_since(
    since: SinceSpec = None,
    env: Mapping[str, str] | None = None,
    *,
    default: bool = True,
) -> Optional[datetime]:
    """None + default True → FORWARD_SINCE. False → no cutoff. datetime → that cutoff."""
    if since is False:
        return None
    if isinstance(since, datetime):
        dt = since
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    environ = env if env is not None else os.environ
    raw = environ.get("AUTO_FLEET_TURO_SINCE")
    if raw is not None:
        text = raw.strip()
        if text.lower() in ("", "off", "none", "0", "false"):
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return FORWARD_SINCE if default else None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return FORWARD_SINCE if default else None


def keep_forward_message(
    raw: Mapping[str, Any], since: Optional[datetime]
) -> bool:
    if is_historical_host_subject(str(raw.get("subject") or "")):
        return False
    if since is None:
        return True
    dt = message_datetime(raw)
    if dt is None or dt < since:
        return False
    return True


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
    # Guest-chat mail often repeats "Booked trip" in the body. Subject wins.
    if "sent you a message" in s:
        return None
    if any(w in s for w in ("cancel", "cancelled", "canceled")):
        return "canceled"
    if any(w in s for w in ("modified", "changed", "updated trip", "trip updated")):
        return "modified"
    if any(
        w in s
        for w in (
            "payout",
            "you earned",
            "trip earnings",
            "earnings are on the way",
            "earnings payment",
            "you've been paid",
            "you’ve been paid",
        )
    ):
        return "payout"
    if any(w in s for w in ("booked", "new trip", "reservation confirmed", "trip confirmed")):
        return "booked"
    if "turo" in s and any(w in s for w in ("trip", "reservation", "guest")):
        return "other"
    return None


def _us_to_iso(raw: str) -> str:
    text = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _long_to_iso(raw: str) -> str:
    try:
        return datetime.strptime(raw.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return raw


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
        long_rng = _LONG_RANGE.search(blob)
        if long_rng:
            start, end = _long_to_iso(long_rng.group(1)), _long_to_iso(long_rng.group(2))
        else:
            us = _US_RANGE.search(blob)
            if us:
                start, end = _us_to_iso(us.group(1)), _us_to_iso(us.group(2))
    if start is None:
        sm = _TRIP_START.search(blob)
        if sm:
            start = _us_to_iso(sm.group(1))
    if end is None:
        em = _TRIP_END.search(blob)
        if em:
            end = _us_to_iso(em.group(1))

    guest = None
    gm = _GUEST.search(blob)
    if gm:
        guest = gm.group(1).strip()
    if not guest:
        sg = _SUBJECT_GUEST.search(subject)
        if sg:
            guest = sg.group(1).strip()

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
    if not vehicle:
        ym = _YOUR_VEHICLE.search(blob)
        if ym:
            year = (ym.group(1) or "").strip()
            name = (ym.group(2) or "").strip()
            vehicle = f"{year} {name}".strip() if year else name

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
            "no host inbox file; drop a Gmail dump at "
            f"{CONFIG_INBOX} or set AUTO_FLEET_TURO_INBOX. "
            f"Watching {GMAIL_INBOX_ADDR} every 15m since {FORWARD_SINCE.date().isoformat()}, "
            "not the :8796 process."
        ),
        "message_count": 0,
        "inbox_kind": "missing",
        "payout_destination": PAYOUT_DESTINATION,
        "forward_since": FORWARD_SINCE_ISO,
        "poll_interval_s": POLL_INTERVAL_S,
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


def load_json_messages(
    path: Path,
) -> tuple[list[dict[str, Any]], Optional[str], Optional[dict[str, Any]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"parse error: {exc}", None
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)], None, None
    if isinstance(data, dict):
        meta = {
            "source": data.get("source"),
            "inbox": data.get("inbox"),
            "query": data.get("query"),
            "as_of": data.get("as_of"),
            "note": data.get("note"),
            "forward_since": data.get("forward_since"),
            "poll_interval_s": data.get("poll_interval_s"),
        }
        msgs = data.get("messages")
        if msgs is None:
            return [], None, meta
        if not isinstance(msgs, list):
            return [], "parse error: messages is not a list", meta
        return [m for m in msgs if isinstance(m, dict)], None, meta
    return [], "parse error: expected object or list", None


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


def resolve_inbox_path(
    explicit: Path | None,
    data_dir: Path,
    env: Mapping[str, str] | None = None,
) -> Path:
    if explicit is not None:
        return explicit
    environ = env if env is not None else os.environ
    override = (environ.get("AUTO_FLEET_TURO_INBOX") or "").strip()
    if override:
        return Path(override).expanduser()
    if CONFIG_INBOX.is_file():
        return CONFIG_INBOX
    return data_dir / DEFAULT_INBOX_NAME


def load_inbox(
    path: Path | None,
    *,
    since: SinceSpec = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load JSON list/object, single .eml, or maildir. None / missing → unconfigured."""
    cutoff = resolve_since(since, env)
    if path is None:
        return _unconfigured()
    p = Path(path)
    if not p.exists():
        return _unconfigured()
    meta: Optional[dict[str, Any]] = None
    if p.is_dir():
        raw, err = load_maildir_messages(p)
        kind = "maildir"
    elif p.suffix.lower() == ".eml":
        raw, err = load_eml_message(p)
        kind = "eml"
    else:
        raw, err, meta = load_json_messages(p)
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
    kept = [m for m in raw if keep_forward_message(m, cutoff)]
    dropped = len(raw) - len(kept)
    bookings = parse_records(kept)
    inbox_name = ""
    query = ""
    source = ""
    if isinstance(meta, dict):
        inbox_name = str(meta.get("inbox") or "")
        query = str(meta.get("query") or "")
        source = str(meta.get("source") or "")
    watching = inbox_name or (
        GMAIL_INBOX_ADDR if source.startswith("gmail") else ""
    )
    since_bit = (
        f" every 15m since {cutoff.date().isoformat()}" if cutoff is not None else ""
    )
    if not kept:
        if watching:
            detail = (
                f"watching {watching}{since_bit}"
                + (f" ({query or GMAIL_QUERY})" if watching else "")
                + "; 0 trip events — empty bookings, not invented trips"
            )
        else:
            detail = (
                f"watching Turo every 15m since {cutoff.date().isoformat()}"
                if cutoff is not None
                else "no booking mail in this fixture"
            ) + " — empty bookings, not invented trips"
        if dropped:
            detail += f" ({dropped} historical dropped)"
        return _result(
            bookings=[],
            status="empty",
            detail=detail,
            message_count=0,
            kind=kind,
        )
    if not bookings:
        prefix = f"watching {watching}{since_bit}; " if watching else ""
        return _result(
            bookings=[],
            status="empty",
            detail=(
                f"{prefix}{kind} parsed ({len(kept)} message(s)); "
                "none were trip booked/modified/canceled/payout"
            ),
            message_count=len(kept),
            kind=kind,
        )
    return _result(
        bookings=bookings,
        status="parsed",
        detail=f"{kind} parsed; {len(bookings)} trip event(s){since_bit}",
        message_count=len(kept),
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
    since: SinceSpec = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    loaded = load_inbox(inbox_path, since=since, env=env)
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
        "forward_since": FORWARD_SINCE_ISO if resolve_since(since, env) else None,
        "poll_interval_s": POLL_INTERVAL_S,
    }


def turo_for_unit(unit_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bookings": list((payload.get("by_unit") or {}).get(unit_id) or []),
        "inbox_status": payload.get("inbox_status"),
        "payout_destination": payload.get("payout_destination") or PAYOUT_DESTINATION,
    }
