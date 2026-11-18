#!/usr/bin/env python3
"""Flag Turo booking mail and wake Helm (kind=booking_change).

The real-time Gmail path (turo_email.py) keeps guest messages only and returns
not_guest_message for bookings, trip changes, and cancellations. The 15-minute
auto-fleet dump classifies those subjects and does not wake Helm. This module
is the classify / build_payload / webhook_wake path for that mail.

Unverified change patterns are ignored. Shipped Turo booking patterns are
verified. Alex's co-host mail ("Alex's vehicle") and SafeWheels stay excluded.
A guest named Alex on our fleet is still a booking. Same message id, or the
same reservation and change_type, wakes Helm once.

webhook_wake posts to HELM_TURO_WEBHOOK_URL (Helm's Turo inbound routine).
The key, when set, is sent as Authorization via booking_authorization().
Neither value is logged. An unset URL does not post.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path

log = logging.getLogger("booking-alert")

SCHEMA = "panamerica.booking.event.v1"
KIND = "booking_change"
CHANGE_TYPES = ("new", "modified", "cancelled", "unknown")
TURO_DOMAINS = ("turo.com", "mail.turo.com", "transactional.turo.com")
DEFAULT_MAX_AGE_S = 12 * 60 * 60
DEDUPE_WINDOW_S = 24 * 60 * 60
WAKE_TIMEOUT_S = 5.0
_RES_RE = re.compile(
    r"(?:Reservation|Trip)\s*(?:ID|#)?\s*#?\s*(\d{7,9})(?!\d)",
    re.IGNORECASE,
)
_lock = threading.Lock()


def booking_authorization(key):
    """Authorization header value. A key that already has a scheme is sent as is."""
    key = str(key or "")
    if " " in key:
        return key
    return "Bearer " + key


def _norm(text):
    return (
        str(text or "")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u02bc", "'")
    )


def _env(name):
    return (os.environ.get(name) or "").strip()


def patterns_path():
    return Path(__file__).resolve().with_name("patterns.json")


def load_patterns(path=None):
    raw = Path(path or patterns_path()).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        return {"ignore": [], "exclude": [], "changes": []}
    return data


def _compile(rows):
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            rx = re.compile(row.get("re") or "")
        except re.error:
            continue
        out.append((row, rx))
    return out


def _from_domain(mail):
    raw = str((mail or {}).get("from") or "")
    _name, addr = parseaddr(raw)
    if "@" not in addr and "@" in raw:
        addr = raw
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].strip().lower().strip(".")


def _turo_from(mail):
    """True only for a Turo domain. A missing, empty, or domainless From is not Turo."""
    domain = _from_domain(mail)
    if not domain:
        return False
    return any(domain == d or domain.endswith("." + d) for d in TURO_DOMAINS)


def _hay(mail, *, body=False):
    subject = _norm((mail or {}).get("subject"))
    if not body:
        return subject
    return subject + "\n" + _norm((mail or {}).get("body"))


def classify(mail, patterns=None):
    """-> change_type or None. Subject decides. Guest mail, reviews, and payouts are not bookings."""
    if not isinstance(mail, dict) or not _turo_from(mail):
        return None
    pats = patterns if patterns is not None else load_patterns()
    subject = _hay(mail, body=False)
    if not subject.strip():
        return None
    for _row, rx in _compile(pats.get("ignore")):
        if rx.search(subject):
            return None
    blob = _hay(mail, body=True)
    for row, rx in _compile(pats.get("exclude")):
        target = subject if row.get("id") == "alex_host" else blob
        if rx.search(target):
            return None
    for row, rx in _compile(pats.get("changes")):
        if row.get("verified") is not True:
            continue
        change = row.get("change_type")
        if change not in CHANGE_TYPES:
            continue
        if rx.search(subject):
            return change
    return None


def _message_id(mail):
    mid = mail.get("message_id") if mail.get("message_id") not in (None, "") else mail.get("id")
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(mid or ""))[:128]


def _reservation(mail):
    blob = _hay(mail, body=True)
    m = _RES_RE.search(blob)
    return m.group(1) if m else ""


def _iso(mail, now=None):
    raw = mail.get("received_at") or mail.get("date")
    if isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ms = mail.get("internal_ms") or mail.get("internalDate")
    try:
        ms_i = int(ms or 0)
    except (TypeError, ValueError):
        ms_i = 0
    if ms_i > 0:
        if ms_i > 10_000_000_000:
            ms_i = ms_i / 1000.0
        return datetime.fromtimestamp(ms_i, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = time.time() if now is None else now
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_payload(mail, change_type, now=None):
    """Helm body. kind=booking_change, schema panamerica.booking.event.v1."""
    if change_type not in CHANGE_TYPES:
        raise ValueError("change_type")
    subject = " ".join(_norm((mail or {}).get("subject")).split())[:300]
    return {
        "kind": KIND,
        "schema": SCHEMA,
        "change_type": change_type,
        "message_id": _message_id(mail or {}),
        "subject": subject,
        "received_at": _iso(mail or {}, now),
    }


def _state_file(state_dir):
    directory = Path(state_dir) if state_dir else Path.home() / ".local" / "state" / "booking-alert"
    return directory / "seen.json"


def _keys(mail, change_type):
    keys = []
    mid = _message_id(mail)
    if mid:
        keys.append("id:" + mid)
    res = _reservation(mail)
    if res:
        keys.append("res:%s:%s" % (res, change_type))
    subject = " ".join(_norm(mail.get("subject")).split()).casefold()
    # Generic unknown notices share a subject. Message id still dedupes those mails.
    if subject and change_type != "unknown":
        digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]
        keys.append("sub:%s:%s" % (digest, change_type))
    return keys


def _load_seen(path, now):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        k: v for k, v in data.items()
        if isinstance(v, (int, float)) and now - float(v) <= DEDUPE_WINDOW_S
    }


def _save_seen(path, seen):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def claim(mail, change_type, state_dir=None, now=None):
    """True when this mail is new. A repeat message id or reservation is False."""
    now = time.time() if now is None else now
    keys = _keys(mail, change_type)
    if not keys:
        return True
    path = _state_file(state_dir)
    with _lock:
        seen = _load_seen(path, now)
        if any(k in seen for k in keys):
            return False
        for k in keys:
            seen[k] = now
        try:
            _save_seen(path, seen)
        except OSError:
            return True
    return True


def release(mail, change_type, state_dir=None, now=None):
    now = time.time() if now is None else now
    keys = set(_keys(mail, change_type))
    if not keys:
        return
    path = _state_file(state_dir)
    with _lock:
        seen = _load_seen(path, now)
        for k in keys:
            seen.pop(k, None)
        try:
            _save_seen(path, seen)
        except OSError:
            return


def _too_old(mail, now):
    ms = mail.get("internal_ms") if mail.get("internal_ms") not in (None, "") else mail.get("internalDate")
    try:
        ms_i = int(ms or 0)
    except (TypeError, ValueError):
        return False
    if ms_i <= 0:
        return False
    if ms_i > 10_000_000_000:
        ms_i = ms_i / 1000.0
    try:
        limit = int(_env("BOOKING_ALERT_MAX_AGE_S") or DEFAULT_MAX_AGE_S)
    except ValueError:
        limit = DEFAULT_MAX_AGE_S
    return now - ms_i > max(60, limit)


def prepare(mail, state_dir=None, now=None, patterns=None):
    """Payload when Helm should be woken, else None. Claims the dedupe keys."""
    now = time.time() if now is None else now
    if not isinstance(mail, dict) or _too_old(mail, now):
        return None
    change = classify(mail, patterns)
    if change is None:
        return None
    if not claim(mail, change, state_dir, now):
        return None
    return build_payload(mail, change, now)


def _urllib_opener(url, payload, headers, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status)


def webhook_wake(payload, opener=None, timeout=WAKE_TIMEOUT_S):
    """POST payload to Helm. -> ok | unset | failed. Never logs the URL, key, or header."""
    url = _env("HELM_TURO_WEBHOOK_URL")
    if not url:
        return "unset"
    headers = {"Content-Type": "application/json"}
    key = _env("HELM_TURO_WEBHOOK_KEY")
    if key:
        headers["Authorization"] = booking_authorization(key)
    post = opener or _urllib_opener
    try:
        status = post(url, payload, headers, timeout)
    except urllib.error.HTTPError as exc:
        log.info("booking wake failed status=%s", exc.code)
        return "failed"
    except Exception:
        log.info("booking wake failed status=err")
        return "failed"
    try:
        code = int(status)
    except (TypeError, ValueError):
        log.info("booking wake failed status=err")
        return "failed"
    if 200 <= code < 300:
        log.info("booking wake sent status=%s", code)
        return "ok"
    log.info("booking wake failed status=%s", code)
    return "failed"


def _b64(data):
    s = str(data or "")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _header(payload, name):
    for h in (payload or {}).get("headers") or []:
        if isinstance(h, dict) and str(h.get("name") or "").lower() == name:
            return str(h.get("value") or "")
    return ""


def _plain(payload):
    stack = [payload or {}]
    while stack:
        part = stack.pop(0)
        if not isinstance(part, dict):
            continue
        if str(part.get("mimeType") or "").lower() == "text/plain":
            data = (part.get("body") or {}).get("data")
            if data:
                return _b64(data)
        stack[0:0] = list(part.get("parts") or [])
    return ""


def mail_from_gmail(msg):
    """Gmail API message resource -> the dict classify() reads."""
    if not isinstance(msg, dict):
        return {}
    payload = msg.get("payload") or {}
    try:
        internal = int(msg.get("internalDate") or 0)
    except (TypeError, ValueError):
        internal = 0
    return {
        "message_id": str(msg.get("id") or ""),
        "from": _header(payload, "from"),
        "subject": _header(payload, "subject"),
        "body": _plain(payload),
        "internal_ms": internal,
    }


def prepare_gmail(msg, state_dir=None, now=None, patterns=None):
    return prepare(mail_from_gmail(msg), state_dir=state_dir, now=now, patterns=patterns)
