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

import html as html_lib
import json
import mailbox
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

try:
    from . import turo_media
except ImportError:  # script / unittest path
    import turo_media  # type: ignore

DEFAULT_INBOX_NAME = "turo_inbox.json"
CONFIG_INBOX = Path.home() / ".config" / "auto-fleet" / "turo_inbox.json"
# Nest dump + dash label. SoT is panamerica; personal cvolkern Turo copies
# are forwards through this inbox — do not double-ingest.
GMAIL_INBOX_ADDR = "panamerica.cars@gmail.com"
# Live poll: Turo senders after the forward start. Do not OR label:Turo —
# that label is 2024 old-fleet mail.
GMAIL_QUERY = (
    "after:2026/08/18 from:(turo.com OR mail.turo.com OR transactional.turo.com)"
)
FORWARD_SINCE = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
FORWARD_SINCE_ISO = "2026-08-18T02:00:00+00:00"
POLL_INTERVAL_S = 900
FLEET_TZ = ZoneInfo("America/New_York")
# Clock fragment in Turo bodies: "3:00 pm", "6:00 PM", "6:00pm".
_CLOCK = r"\d{1,2}:\d{2}\s*[AaPp][Mm]"
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
# Bare reservation hash in Turo bodies, e.g. "#60615645".
_TRIP_HASH = re.compile(r"(?:^|[\s(])#\s*(\d{6,8})\b")
_HTML_TAG = re.compile(r"<[^>]+>")
_YEAR_MAKE_MODEL = re.compile(
    r"\b((?:19|20)\d{2})\s+(toyota\s+corolla|tesla\s+model\s+3|rivian\s+r1s)\b",
    re.I,
)
_MAKE_MODEL_YEAR = re.compile(
    r"\b(toyota\s+corolla|tesla\s+model\s+3|rivian\s+r1s)\s+((?:19|20)\d{2})\b",
    re.I,
)
_ISO_RANGE = re.compile(
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?)\s*(?:to|–|-|through)\s*"
    r"(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?)",
    re.I,
)
_US_RANGE = re.compile(
    rf"(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}(?:\s+{_CLOCK})?)\s*(?:to|–|-|through)\s*"
    rf"(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}(?:\s+{_CLOCK})?)",
    re.I,
)
_LONG_RANGE = re.compile(
    rf"from\s+[A-Za-z]+,\s+([A-Za-z]+ \d{{1,2}}, \d{{4}}(?:,?\s+{_CLOCK})?)\s+"
    rf"to\s+[A-Za-z]+,\s+"
    rf"([A-Za-z]+ \d{{1,2}}, \d{{4}}(?:,?\s+{_CLOCK})?)",
    re.I,
)
_TRIP_START = re.compile(
    rf"trip start\s*:?\s*(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}(?:\s+{_CLOCK})?)",
    re.I,
)
_TRIP_END = re.compile(
    rf"trip end\s*:?\s*(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}(?:\s+{_CLOCK})?)",
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
_HOST_LABEL = re.compile(r"\(([^)]+['\u2019]s)\s+vehicle\)", re.I)
_DROP_OFF = re.compile(
    r"(?:drop[-\s]?off(?: location)?|return location)\s*[:\-]\s*(.+)",
    re.I,
)
_DELIVERY = re.compile(
    r"(?:delivery(?: location)?|meet(?:ing)?(?: at)?)\s*[:\-]\s*(.+)",
    re.I,
)
_LOCATION_CHANGED = re.compile(
    r"(?:pickup|pick-up|drop[\s-]?off|return|delivery|handoff|location)\s+"
    r"(?:has been |was )?(?:changed|updated|moved)\s+to\s+(.+)",
    re.I,
)
_FBO_PLACE = re.compile(
    r"\b((?:[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s+(?:Airport(?:\s+FBO)?|FBO))\b"
)
_WEEKDAY_WHEN = re.compile(
    rf"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s+"
    rf"([A-Za-z]{{3,9}})\.?\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?(?:,?\s+({_CLOCK}))?",
    re.I,
)
_EXTEND_UNTIL = re.compile(
    rf"(?:extend(?:ed|ing)?|until|through)\s+"
    rf"(?:to |until |through |by )?"
    rf"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s+[A-Za-z]{{3,9}}\.?\s+\d{{1,2}}"
    rf"(?:,?\s+\d{{4}})?(?:,?\s+{_CLOCK})?)",
    re.I,
)
_PHONE = re.compile(
    r"(?:phone|mobile|cell|tel)\s*[:\-]\s*([+\d().\-\s]{7,22})",
    re.I,
)
_EXTRA_DRIVER = re.compile(
    r"(?:extra|additional)\s+drivers?\s*[:\-]?\s*"
    r"([A-Z][A-Za-z.'\-]+(?:[ \t]+[A-Z][A-Za-z.'\-]+){0,3})"
    r"(?:\s*\(([^)]*verified[^)]*)\))?",
    re.I,
)
_GUEST_ASK = re.compile(
    r"(?:guest\s+ask|please\s+call|call\s+the\s+guest|phone\s+tap)\s*[:\-]?\s*(.+)",
    re.I,
)


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


def flatten_mail_text(value: str) -> str:
    """Turn HTML-ish Gmail bodies into matchable text. Leaves plain text alone."""
    text = value or ""
    if "<" in text and ">" in text:
        text = html_lib.unescape(_HTML_TAG.sub(" ", text))
    # Turo mail uses U+202F (narrow no-break space) before AM/PM.
    text = re.sub(r"[\u00a0\u202f\u2007\u2009\u200a]", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _message_blob(raw: Mapping[str, Any]) -> str:
    subject = str(raw.get("subject") or "")
    body = flatten_mail_text(str(raw.get("body") or ""))
    snippet = flatten_mail_text(str(raw.get("snippet") or ""))
    return "\n".join(part for part in (subject, body, snippet) if part)


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


def is_guest_message_subject(subject: str) -> bool:
    return "sent you a message" in (subject or "").lower()


def classify_change_source(subject: str) -> Optional[str]:
    """Ops change class. Guest-chat is not a booking; it can still move a window."""
    s = (subject or "").lower()
    if is_guest_message_subject(subject):
        return "guest_message"
    if any(w in s for w in ("cancel", "cancelled", "canceled")):
        return None
    if "changed their trip" in s or "has changed their trip" in s:
        return "formal_change"
    if any(
        w in s
        for w in (
            "modified",
            "changed",
            "updated trip",
            "trip updated",
            "was updated",
            "dates changed",
            "booking was modified",
            "trip was modified",
        )
    ):
        return "booking_modified"
    return None


def classify_subject(subject: str) -> Optional[str]:
    s = (subject or "").lower()
    # Guest-chat mail often repeats "Booked trip" in the body. Subject wins.
    if is_guest_message_subject(subject):
        return None
    if any(w in s for w in ("cancel", "cancelled", "canceled")):
        return "canceled"
    if any(
        w in s
        for w in (
            "modified",
            "changed",
            "updated trip",
            "trip updated",
            "was updated",
            "dates changed",
        )
    ):
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


def _norm_clock_text(raw: str) -> str:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    return re.sub(r"\b([AaPp])[Mm]\b", lambda m: m.group(1).upper() + "M", text)


def _iso_out(dt: datetime, *, has_time: bool) -> str:
    """Date-only YYYY-MM-DD, or timezone-aware America/New_York ISO when timed."""
    if not has_time:
        return dt.date().isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FLEET_TZ)
    else:
        dt = dt.astimezone(FLEET_TZ)
    return dt.isoformat()


def _try_strptime(text: str, formats: tuple[str, ...]) -> Optional[tuple[datetime, bool]]:
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        has_time = "%I" in fmt or "%H" in fmt
        return dt, has_time
    return None


def _us_to_iso(raw: str) -> str:
    text = _norm_clock_text(raw)
    parsed = _try_strptime(
        text,
        (
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%y %I:%M %p",
            "%m/%d/%Y %I:%M%p",
            "%m/%d/%y %I:%M%p",
            "%m/%d/%Y",
            "%m/%d/%y",
        ),
    )
    if parsed:
        dt, has_time = parsed
        return _iso_out(dt, has_time=has_time)
    return text


def _long_to_iso(raw: str) -> str:
    text = _norm_clock_text(raw)
    parsed = _try_strptime(
        text,
        (
            "%B %d, %Y %I:%M %p",
            "%B %d, %Y %I:%M%p",
            "%B %d, %Y, %I:%M %p",
            "%B %d, %Y, %I:%M%p",
            "%B %d, %Y",
        ),
    )
    if parsed:
        dt, has_time = parsed
        return _iso_out(dt, has_time=has_time)
    return text


def _iso_piece_to_stored(raw: str) -> str:
    """Keep date-only ISO; attach ET when the mail fragment has a clock."""
    text = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return text
    has_time = "T" in text.replace(" ", "T")[10:] or " " in text
    if not has_time:
        return dt.date().isoformat()
    return _iso_out(dt, has_time=True)


_MONTH_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def parse_when_fragment(
    raw: str, *, year: Optional[int] = None
) -> Optional[str]:
    """Parse a date/time fragment from Turo mail or guest-thread copy.

    Returns stored ISO (date-only or ET timed). None if unparseable.
    Does not invent a clock when the fragment has none.
    """
    text = _norm_clock_text(raw or "")
    if not text:
        return None
    iso_m = re.search(
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?)?",
        text,
    )
    if iso_m:
        return _iso_piece_to_stored(iso_m.group(0))
    us_m = re.search(
        rf"\d{{1,2}}/\d{{1,2}}/\d{{2,4}}(?:\s+{_CLOCK})?",
        text,
    )
    if us_m:
        stored = _us_to_iso(us_m.group(0))
        if stored and stored != us_m.group(0):
            return stored
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T.*)?", stored or ""):
            return stored
    long_m = re.search(
        rf"[A-Za-z]+ \d{{1,2}}, \d{{4}}(?:,?\s+{_CLOCK})?",
        text,
    )
    if long_m:
        stored = _long_to_iso(long_m.group(0))
        if stored and stored != long_m.group(0):
            return stored
    wd = _WEEKDAY_WHEN.search(text)
    if not wd:
        return None
    month_s = (wd.group(1) or "").strip().lower().rstrip(".")
    month = _MONTH_NUM.get(month_s)
    if not month:
        return None
    try:
        day = int(wd.group(2))
    except (TypeError, ValueError):
        return None
    year_s = wd.group(3)
    try:
        y = int(year_s) if year_s else int(year or datetime.now(FLEET_TZ).year)
    except (TypeError, ValueError):
        return None
    clock = _norm_clock_text(wd.group(4) or "")
    if clock:
        parsed = _try_strptime(
            f"{month:02d}/{day:02d}/{y} {clock}",
            ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M%p"),
        )
        if parsed:
            dt, has_time = parsed
            return _iso_out(dt, has_time=has_time)
        return None
    try:
        dt = datetime(y, month, day)
    except ValueError:
        return None
    return _iso_out(dt, has_time=False)


def _clip_place(raw: str) -> str:
    return (raw or "").strip().splitlines()[0][:160].strip()


def extract_ops_fields(
    raw: Mapping[str, Any], *, year: Optional[int] = None
) -> dict[str, Any]:
    """Reservation id + window + place from any Turo mail, including guest chat."""
    subject = str(raw.get("subject") or "")
    body = flatten_mail_text(str(raw.get("body") or raw.get("snippet") or ""))
    blob = _message_blob(raw) or f"{subject}\n{body}"
    dt = message_datetime(raw)
    y = year
    if y is None and dt is not None:
        y = dt.astimezone(FLEET_TZ).year

    trip = None
    m = _TRIP_ID.search(blob)
    if m:
        trip = m.group(1)
    if not trip:
        hm = _TRIP_HASH.search(blob)
        if hm:
            trip = hm.group(1)

    start = end = None
    rng = _ISO_RANGE.search(blob)
    if rng:
        start, end = _iso_piece_to_stored(rng.group(1)), _iso_piece_to_stored(
            rng.group(2)
        )
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
    if end is None:
        ext = _EXTEND_UNTIL.search(blob)
        if ext:
            end = parse_when_fragment(ext.group(1), year=y)

    guest = None
    gm = _GUEST.search(blob)
    if gm:
        guest = gm.group(1).strip()
    if not guest:
        sg = _SUBJECT_GUEST.search(subject)
        if sg:
            guest = sg.group(1).strip()
    if not guest and is_guest_message_subject(subject):
        rest = subject
        if " - " in rest:
            rest = rest.split(" - ", 1)[-1]
        first = rest.split(" has sent you a message", 1)[0].strip()
        first = first.split(")")[-1].strip() or first
        if first and len(first.split()) <= 4:
            guest = first

    vin = None
    vm = _VIN.search(blob)
    if vm:
        vin = vm.group(1)

    pickup = None
    pm = _PICKUP.search(blob)
    if pm:
        pickup = _clip_place(pm.group(1))
    drop_off = None
    dm = _DROP_OFF.search(blob)
    if dm:
        drop_off = _clip_place(dm.group(1))
    if not pickup:
        dv = _DELIVERY.search(blob)
        if dv:
            pickup = _clip_place(dv.group(1))
    loc_ch = _LOCATION_CHANGED.search(blob)
    if loc_ch:
        place = _clip_place(loc_ch.group(1))
        kind = loc_ch.group(0).lower()
        if any(w in kind for w in ("drop", "return")):
            drop_off = drop_off or place
        else:
            pickup = pickup or place
    if not pickup:
        fbo = _FBO_PLACE.search(blob)
        if fbo:
            pickup = _clip_place(fbo.group(1))

    vehicle = raw.get("vehicle")
    if not vehicle:
        vm2 = _VEHICLE.search(blob)
        if vm2:
            vehicle = vm2.group(1).strip().splitlines()[0]
    year_vehicle = _vehicle_from_blob(blob)
    if year_vehicle:
        vehicle = year_vehicle
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
            year_bit = (ym.group(1) or "").strip()
            name = (ym.group(2) or "").strip()
            vehicle = f"{year_bit} {name}".strip() if year_bit else name

    extra_drivers: list[dict[str, Any]] = []
    for em in _EXTRA_DRIVER.finditer(blob):
        name = (em.group(1) or "").strip()
        if not name:
            continue
        verified_bit = (em.group(2) or "").strip().lower()
        extra_drivers.append(
            {
                "name": name,
                "turo_verified": bool(verified_bit and "verified" in verified_bit),
            }
        )
    guest_asks: list[str] = []
    for am in _GUEST_ASK.finditer(blob):
        ask = (am.group(1) or "").strip().splitlines()[0][:200]
        if ask:
            guest_asks.append(ask)

    host_label = None
    hm_host = _HOST_LABEL.search(subject)
    if hm_host:
        host_label = hm_host.group(1).replace("\u2019", "'").strip()

    phone = None
    ph = _PHONE.search(blob)
    if ph:
        phone = re.sub(r"\s+", " ", ph.group(1)).strip()

    return {
        "trip_id": trip,
        "start": start,
        "end": end,
        "guest": guest,
        "vin": vin,
        "pickup": pickup,
        "drop_off": drop_off,
        "vehicle": vehicle,
        "extra_drivers": extra_drivers,
        "guest_asks": guest_asks,
        "host_label": host_label,
        "phone": phone,
        "body": body,
        "subject": subject,
        "blob": blob,
    }


def _vehicle_from_blob(blob: str) -> Optional[str]:
    """Year + make/model, either order. Yearless make/model is not enough."""
    ym = _YEAR_MAKE_MODEL.search(blob)
    if ym:
        return f"{ym.group(1)} {ym.group(2)}".strip()
    my = _MAKE_MODEL_YEAR.search(blob)
    if my:
        return f"{my.group(2)} {my.group(1)}".strip()
    return None


def parse_message(raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Turn one JSON/maildir message into a booking record, or None if not Turo-ops."""
    subject = str(raw.get("subject") or "")
    body = flatten_mail_text(str(raw.get("body") or raw.get("snippet") or ""))
    sender = str(raw.get("from") or "")
    blob = _message_blob(raw) or f"{subject}\n{body}"
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

    fields = extract_ops_fields(raw)
    trip = fields.get("trip_id")
    start = fields.get("start")
    end = fields.get("end")
    guest = fields.get("guest")
    vin = fields.get("vin")
    pickup = fields.get("pickup")
    drop_off = fields.get("drop_off")
    phone = fields.get("phone")
    extra_drivers = list(fields.get("extra_drivers") or [])
    guest_asks = list(fields.get("guest_asks") or [])
    host_label = fields.get("host_label")
    vehicle = fields.get("vehicle")

    payout = None
    if status == "payout":
        money = _MONEY.search(blob)
        if money:
            try:
                payout = float(money.group(1).replace(",", ""))
            except ValueError:
                payout = None

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
        "body": body,
    }
    if drop_off:
        rec["drop_off"] = drop_off
    if phone:
        rec["phone"] = phone
    if extra_drivers:
        rec["extra_drivers"] = extra_drivers
    if guest_asks:
        rec["guest_asks"] = guest_asks
    if host_label:
        rec["host_label"] = host_label
    attachments = turo_media.normalize_attachments(raw.get("attachments"))
    if attachments:
        rec["attachments"] = attachments
    if turo_media.claims_photos(subject, body, str(raw.get("snippet") or "")):
        rec["claims_photos"] = True
        if not attachments:
            rec["photos_missing"] = True
    return rec


def _trip_and_vehicle_from_blob(blob: str) -> tuple[Optional[str], Optional[str]]:
    trip = None
    m = _TRIP_ID.search(blob)
    if m:
        trip = m.group(1)
    if not trip:
        hm = _TRIP_HASH.search(blob)
        if hm:
            trip = hm.group(1)
    return trip, _vehicle_from_blob(blob)


def parse_photo_message(raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Photo-bearing (or 'Contains photo(s)') mail. Not a booking record."""
    subject = str(raw.get("subject") or "")
    body = flatten_mail_text(str(raw.get("body") or raw.get("snippet") or ""))
    snippet = flatten_mail_text(str(raw.get("snippet") or ""))
    attachments = turo_media.normalize_attachments(raw.get("attachments"))
    claims = turo_media.claims_photos(subject, body, snippet)
    if not attachments and not claims:
        return None
    blob = _message_blob(raw) or f"{subject}\n{body}"
    trip, vehicle = _trip_and_vehicle_from_blob(blob)
    if not vehicle:
        vehicle = raw.get("vehicle")
    rec: dict[str, Any] = {
        "message_id": raw.get("id") or raw.get("message_id") or raw.get("message-id"),
        "subject": subject,
        "from": str(raw.get("from") or ""),
        "date": raw.get("date"),
        "kind": "guest_message" if "sent you a message" in subject.lower() else "mail",
        "trip_id": trip,
        "vehicle": vehicle,
        "attachments": attachments,
        "claims_photos": claims,
    }
    if claims and not attachments:
        rec["photos_missing"] = True
    excerpt = body or snippet
    if excerpt:
        rec["excerpt"] = excerpt[:240]
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


def _message_from_email(
    msg: Message,
    fallback_id: str,
    media_dir: Path | None = None,
) -> dict[str, Any]:
    mid = str(msg.get("Message-ID") or fallback_id)
    rec: dict[str, Any] = {
        "id": mid,
        "subject": _header_str(msg, "Subject"),
        "from": _header_str(msg, "From"),
        "date": _header_str(msg, "Date"),
        "body": _body_text(msg),
    }
    attachments = turo_media.attachments_from_email(msg, media_dir, mid)
    if attachments:
        rec["attachments"] = attachments
    return rec


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
            "not the Auto Fleet dashboard process."
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
            "media_dir": data.get("media_dir"),
            "error": data.get("error"),
            "auth_dead": data.get("auth_dead"),
            "auth_dead_reason": data.get("auth_dead_reason"),
        }
        msgs = data.get("messages")
        if msgs is None:
            return [], None, meta
        if not isinstance(msgs, list):
            return [], "parse error: messages is not a list", meta
        return [m for m in msgs if isinstance(m, dict)], None, meta
    return [], "parse error: expected object or list", None


def load_maildir_messages(
    path: Path, media_dir: Path | None = None
) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        box = mailbox.Maildir(str(path), create=False)
    except Exception as exc:  # noqa: BLE001
        return [], f"parse error: {exc}"
    out: list[dict[str, Any]] = []
    dest = media_dir if media_dir is not None else turo_media.media_dir_for(path)
    try:
        for key, msg in box.iteritems():
            out.append(_message_from_email(msg, key, dest))
    except Exception as exc:  # noqa: BLE001
        return out, f"parse error: {exc}"
    finally:
        try:
            box.close()
        except Exception:  # noqa: BLE001
            pass
    return out, None


def load_eml_message(
    path: Path, media_dir: Path | None = None
) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        raw = path.read_bytes()
        msg = message_from_bytes(raw, policy=policy.default)
    except Exception as exc:  # noqa: BLE001
        return [], f"parse error: {exc}"
    dest = media_dir if media_dir is not None else turo_media.media_dir_for(path)
    return [_message_from_email(msg, path.name, dest)], None


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
    media = turo_media.media_dir_for(p)
    if p.is_dir():
        raw, err = load_maildir_messages(p, media)
        kind = "maildir"
    elif p.suffix.lower() == ".eml":
        raw, err = load_eml_message(p, media)
        kind = "eml"
    else:
        raw, err, meta = load_json_messages(p)
        kind = "json"
        if err and "parse error" in err:
            head = p.read_text(encoding="utf-8", errors="replace")[:1]
            if head not in ("{", "["):
                alt, alt_err = load_eml_message(p, media)
                if not alt_err and alt:
                    raw, err, kind = alt, None, "eml"

    if err:
        result = _result(
            bookings=[],
            status="error",
            detail=err,
            message_count=0,
            kind=kind,
            error=err,
        )
        result["raw_messages"] = []
        return result
    kept = [m for m in raw if keep_forward_message(m, cutoff)]
    dropped = len(raw) - len(kept)
    bookings = parse_records(kept)
    inbox_name = ""
    query = ""
    source = ""
    dump_error = ""
    auth_dead = False
    auth_dead_reason = ""
    if isinstance(meta, dict):
        inbox_name = str(meta.get("inbox") or "")
        query = str(meta.get("query") or "")
        source = str(meta.get("source") or "")
        dump_error = str(meta.get("error") or "")
        auth_dead = bool(meta.get("auth_dead"))
        auth_dead_reason = str(meta.get("auth_dead_reason") or "")
        if not auth_dead and source == "gmail_unconfigured":
            auth_dead = True
            auth_dead_reason = auth_dead_reason or "missing_token"
        if not auth_dead and "invalid_grant" in dump_error.lower():
            auth_dead = True
            auth_dead_reason = "invalid_grant"
    watching = inbox_name or (
        GMAIL_INBOX_ADDR if source.startswith("gmail") else ""
    )
    since_bit = (
        f" every 15m since {cutoff.date().isoformat()}" if cutoff is not None else ""
    )
    source_bit = ""
    if source in ("gmail_error", "gmail_unconfigured") or auth_dead:
        # Do not copy dump `note` here — it names GMAIL_* env keys and
        # would trip agent snapshot secret_leaks.
        err_head = dump_error.splitlines()[0].strip()[:120] if dump_error else ""
        if "invalid_grant" in err_head.lower() or auth_dead_reason == "invalid_grant":
            err_head = "invalid_grant"
        elif auth_dead_reason:
            err_head = auth_dead_reason
        elif err_head:
            err_head = err_head[:80]
        dead_bit = " AUTH_DEAD" if auth_dead else ""
        source_bit = (
            f" [{source or 'gmail'}"
            + (f": {err_head}" if err_head else "")
            + f"{dead_bit}]"
        )
    auth_status = "error" if (source in ("gmail_error", "gmail_unconfigured") or auth_dead) else None
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
        detail += source_bit
        empty_status = auth_status or "empty"
        empty = _result(
            bookings=[],
            status=empty_status,
            detail=detail,
            message_count=0,
            kind=kind,
            error=dump_error or None if empty_status == "error" else None,
        )
        empty["raw_messages"] = kept
        return empty
    if not bookings:
        prefix = f"watching {watching}{since_bit}; " if watching else ""
        empty = _result(
            bookings=[],
            status=auth_status or "empty",
            detail=(
                f"{prefix}{kind} parsed ({len(kept)} message(s)); "
                "none were trip booked/modified/canceled/payout"
                f"{source_bit}"
            ),
            message_count=len(kept),
            kind=kind,
            error=dump_error or None if auth_status == "error" else None,
        )
        empty["raw_messages"] = kept
        return empty
    parsed = _result(
        bookings=bookings,
        status=auth_status or "parsed",
        detail=f"{kind} parsed; {len(bookings)} trip event(s){since_bit}{source_bit}",
        message_count=len(kept),
        kind=kind,
        error=dump_error or None if auth_status == "error" else None,
    )
    parsed["raw_messages"] = kept
    return parsed


def _booking_match_blob(booking: Mapping[str, Any]) -> str:
    return _norm_text(
        " ".join(
            str(booking.get(k) or "")
            for k in ("vehicle", "subject", "body", "snippet", "excerpt")
        )
    )


def _mike_host_candidates(
    booking: Mapping[str, Any], units: list[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """(Mike's vehicle) mail only attaches to Turo-role units, never personal."""
    if not is_current_host_subject(str(booking.get("subject") or "")):
        return list(units)
    turo = [u for u in units if (u.get("role") or "").lower() == "turo"]
    if turo:
        return turo
    if any((u.get("role") or "").strip() for u in units):
        return []
    return list(units)


def match_unit(booking: Mapping[str, Any], units: list[Mapping[str, Any]]) -> Optional[str]:
    """Map a parsed trip event to a roster unit. None = unmatched, never invented.

    Primary: year next to make/model in the mail body (`Toyota Corolla 2024`
    or `2024 Toyota Corolla`). VIN is an exact override. Yearless Corolla
    text stays unmatched when both 2022 and 2024 exist.
    """
    candidates = _mike_host_candidates(booking, units)
    if not candidates:
        return None
    vin = (booking.get("vin") or "").strip().upper()
    if vin:
        for u in candidates:
            if (u.get("vin") or "").upper() == vin:
                return str(u["id"])
    blob = _booking_match_blob(booking)
    vehicle = (booking.get("vehicle") or "").lower()
    if not blob and not vehicle:
        return None
    hits: list[str] = []
    for u in candidates:
        year = str(u.get("year") or "")
        make = str(u.get("make") or "").lower()
        model = str(u.get("model") or "").lower()
        if not make or not model:
            continue
        name = f"{make} {model}"
        label = f"{year} {name}".strip()
        hay = blob or vehicle
        if year and (label in hay or f"{name} {year}" in hay):
            hits.append(str(u["id"]))
            continue
        if vehicle and label and label in vehicle:
            hits.append(str(u["id"]))
            continue
        if vehicle and name in vehicle and year and year in vehicle:
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


def _detect_trip_changes(
    raw_messages: Sequence[Mapping[str, Any]],
    bookings: Sequence[Mapping[str, Any]],
    units: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Late import — turo_changes depends on this module."""
    try:
        from . import turo_changes
    except ImportError:  # script / unittest path
        import turo_changes  # type: ignore
    return turo_changes.detect_changes(raw_messages, bookings, units)


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
        rec.pop("body", None)
        rec["unit_id"] = uid
        if uid:
            bookings.append(rec)
        else:
            unmatched.append(rec)

    by_trip: dict[str, list[dict[str, Any]]] = {}
    for rec in bookings + unmatched:
        tid = str(rec.get("trip_id") or "")
        if tid:
            by_trip.setdefault(tid, []).append(rec)

    photo_messages: list[dict[str, Any]] = []
    for raw in loaded.get("raw_messages") or []:
        if not isinstance(raw, dict):
            continue
        photo = parse_photo_message(raw)
        if not photo:
            continue
        uid = match_unit(photo, units) if (photo.get("vehicle") or photo.get("vin")) else None
        photo["unit_id"] = uid
        photo_messages.append(photo)
        tid = str(photo.get("trip_id") or "")
        atts = photo.get("attachments") or []
        if tid and atts:
            for rec in by_trip.get(tid, []):
                rec["attachments"] = turo_media.merge_attachments(
                    rec.get("attachments"), atts
                )

    status = loaded.get("inbox_status") or "empty"
    detail = loaded.get("inbox_detail") or ""
    by_unit: dict[str, list[dict[str, Any]]] = {str(u["id"]): [] for u in units}
    photos_by_unit: dict[str, list[dict[str, Any]]] = {str(u["id"]): [] for u in units}
    for b in bookings:
        by_unit[str(b["unit_id"])].append(b)
    unmatched_photos: list[dict[str, Any]] = []
    for photo in photo_messages:
        uid = photo.get("unit_id")
        if uid and uid in photos_by_unit:
            photos_by_unit[str(uid)].append(photo)
        else:
            unmatched_photos.append(photo)
    return {
        "inbox_status": _annotate_payout_dest(detail or status),
        "inbox_state": status,
        "inbox_detail": _annotate_payout_dest(detail),
        "inbox_kind": loaded.get("inbox_kind"),
        "inbox_path": str(inbox_path) if inbox_path else None,
        "media_dir": str(turo_media.media_dir_for(inbox_path)) if inbox_path else None,
        "refreshed_at": _now(),
        "by_unit": by_unit,
        "photos_by_unit": photos_by_unit,
        "unmatched": unmatched,
        "unmatched_photos": unmatched_photos,
        "photo_messages": photo_messages,
        "bookings": bookings + unmatched,
        "changes": _detect_trip_changes(
            loaded.get("raw_messages") or [],
            bookings + unmatched,
            units,
        ),
        "message_count": loaded.get("message_count", 0),
        "payout_destination": PAYOUT_DESTINATION,
        "forward_since": FORWARD_SINCE_ISO if resolve_since(since, env) else None,
        "poll_interval_s": POLL_INTERVAL_S,
    }


def turo_for_unit(unit_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bookings": list((payload.get("by_unit") or {}).get(unit_id) or []),
        "photos": list((payload.get("photos_by_unit") or {}).get(unit_id) or []),
        "inbox_status": payload.get("inbox_status"),
        "payout_destination": payload.get("payout_destination") or PAYOUT_DESTINATION,
    }
