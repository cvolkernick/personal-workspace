#!/usr/bin/env python3
"""Turo guest-message email parser for the Gmail push path (#1142). Stdlib only.

Input is a Gmail API message resource (users.messages.get, format=full).
Output is the small field set the relay's loopback listener takes
(bland_relay.turo_email_handle). The relay normalizes it with the same
turo_event()/turo_ingest() code as the 904 phone notifications. This module
only decides "is this a Turo guest message" and pulls out guest, vehicle,
reservation and the message body. No network, no logging of bodies.

Kept: From is a turo.com address, Gmail's Authentication-Results says
dkim=pass for a turo.com domain, the subject reads "<Guest> has sent you a
message about your <Vehicle>" (optionally prefixed "(<owner>'s vehicle) - "),
and the message is younger than TURO_EMAIL_MAX_AGE_S. Everything else is
ignored with a reason.
"""
import base64
import html
import os
import re
import time
from email.utils import parseaddr

DEFAULT_FROM_DOMAINS = ("turo.com",)
DEFAULT_MAX_AGE_S = 12 * 60 * 60
SKIP_LABELS = frozenset({"SPAM", "TRASH", "SENT", "DRAFT"})
TEXT_MAX = 4000

SUBJECT_RE = re.compile(
    r"\A(?:\((?P<owner>[^()\n]{1,80})\)\s*-\s*)?"
    r"(?P<guest>[^\n]{1,64}?) has sent you (?:a |an )?(?:new )?messages? about your (?P<vehicle>[^\n]{1,80}?)\.?\s*\Z",
    re.IGNORECASE,
)
DKIM_PASS_RE = re.compile(r"\bdkim=pass\b[^;]*?\bheader\.(?:i|d)=@?([A-Za-z0-9.-]+)", re.IGNORECASE)
RES_RES = (
    re.compile(r"/reservation/(\d{7,9})(?!\d)"),
    re.compile(r"Reservation ID\s*#?\s*(\d{7,9})(?!\d)", re.IGNORECASE),
)
END_MARKERS_RE = re.compile(r"^\s*(?:Reply\s+https?://|Reply\s*$|View .{0,40}profile|Send .{0,64} a message)", re.IGNORECASE)


def _env(name):
    return (os.environ.get(name) or "").strip()


def from_domains():
    raw = _env("TURO_EMAIL_FROM_DOMAINS")
    doms = tuple(d.strip().lower().lstrip("@.") for d in raw.split(",") if d.strip()) if raw else ()
    return doms or DEFAULT_FROM_DOMAINS


def max_age_s():
    try:
        v = int(_env("TURO_EMAIL_MAX_AGE_S") or DEFAULT_MAX_AGE_S)
    except ValueError:
        v = DEFAULT_MAX_AGE_S
    return max(60, min(v, 7 * 24 * 3600))


def require_dkim():
    return _env("TURO_EMAIL_REQUIRE_DKIM") != "0"


def _domain_ok(domain, doms):
    d = (domain or "").lower().strip(".")
    return any(d == x or d.endswith("." + x) for x in doms)


def headers_map(payload):
    """-> {lower name: [values]} from a Gmail payload."""
    out = {}
    for h in (payload or {}).get("headers") or []:
        if isinstance(h, dict) and h.get("name"):
            out.setdefault(str(h["name"]).lower(), []).append(str(h.get("value") or ""))
    return out


def _b64(data):
    s = str(data or "")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _walk(payload):
    stack = [payload or {}]
    while stack:
        part = stack.pop(0)
        if not isinstance(part, dict):
            continue
        yield part
        stack[0:0] = list(part.get("parts") or [])


def _html_to_text(s):
    s = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|td|h\d|li|table)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def body_text(payload):
    """text/plain part, else a crude text rendering of text/html."""
    plain = htm = ""
    for part in _walk(payload):
        mt = str(part.get("mimeType") or "").lower()
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        if mt == "text/plain" and not plain:
            plain = _b64(data)
        elif mt == "text/html" and not htm:
            htm = _b64(data)
    return plain or (_html_to_text(htm) if htm else "")


def dkim_ok(hdrs, doms):
    for value in hdrs.get("authentication-results") or []:
        if "mx.google.com" not in value.lower():
            continue
        for m in DKIM_PASS_RE.finditer(value):
            if _domain_ok(m.group(1), doms):
                return True
    return False


def extract_message(text, guest, vehicle):
    """Message body between the "<guest> has sent you a message ..." line and "Reply <url>"."""
    lines = [ln.strip() for ln in str(text or "").replace("\r\n", "\n").split("\n")]
    head_re = re.compile(r"has sent you (?:a |an )?(?:new )?messages? about your", re.IGNORECASE)
    start = None
    for i, ln in enumerate(lines):
        if head_re.search(ln):
            start = i + 1
            break
    if start is None:
        return ""
    body = []
    for ln in lines[start:]:
        if END_MARKERS_RE.match(ln):
            break
        body.append(ln)
    out = "\n".join(body).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out[:TEXT_MAX]


def reservation_id(*fields):
    for rx in RES_RES:
        for f in fields:
            m = rx.search(str(f or ""))
            if m:
                return m.group(1)
    return None


def parse_gmail_message(msg, now=None):
    """Gmail message (format=full) -> (fields dict, None) or (None, reason)."""
    now = time.time() if now is None else now
    if not isinstance(msg, dict):
        return None, "bad_message"
    labels = set(msg.get("labelIds") or [])
    if labels & SKIP_LABELS:
        return None, "label"
    payload = msg.get("payload") or {}
    hdrs = headers_map(payload)
    doms = from_domains()
    _name, addr = parseaddr((hdrs.get("from") or [""])[0])
    if "@" not in addr or not _domain_ok(addr.rsplit("@", 1)[1], doms):
        return None, "not_turo"
    subject = " ".join((hdrs.get("subject") or [""])[0].split())
    m = SUBJECT_RE.match(subject)
    if not m:
        return None, "not_guest_message"
    if require_dkim() and not dkim_ok(hdrs, doms):
        return None, "dkim"
    try:
        internal_ms = int(msg.get("internalDate") or 0)
    except (TypeError, ValueError):
        internal_ms = 0
    if internal_ms and now - internal_ms / 1000.0 > max_age_s():
        return None, "too_old"
    guest = m.group("guest").strip()
    vehicle = m.group("vehicle").strip()
    text = body_text(payload)
    message = extract_message(text, guest, vehicle)
    if not message:
        return None, "no_body"
    return {
        "message_id": str(msg.get("id") or "")[:32],
        "guest": guest[:64],
        "vehicle": vehicle[:80],
        "reservation_id": reservation_id(text),
        "text": message,
        "subject": subject[:300],
        "internal_ms": internal_ms or None,
    }, None
