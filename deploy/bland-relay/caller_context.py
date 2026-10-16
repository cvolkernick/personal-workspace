"""Read-only caller context for the Bland inbound line (#1096).

The CRM is the system of record once #1097 exposes
GET /v1/contacts/by-phone. This module reads that route only when CRM_API_BASE
is http://127.0.0.1:<port> or http://localhost:<port>. An unset base, a
rejected base, a timeout, or a 5xx answers from a local SQLite index.

Production ingest (webhooks, the Bland poller, SMSGate, Gmail) belongs to
#1097. The ingest and poll helpers here exist so the fallback index can load
the same fixtures. Nothing in this module starts a poller or calls Bland.

Stdlib only. Never log the read token, the forget token, or the CRM token.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
RETENTION_DAYS = 180
CRM_TIMEOUT_S = 0.25
CONTEXT_MAX = 600
OUR_LINES = frozenset({"+19062406221", "+19043343975"})
CALLER_TYPES = frozenset({"renter", "owner_prospect", "vendor", "unknown"})

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
CARD_RE = re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b|\b\d{13,19}\b")
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.'\-]+(?:\s+[A-Za-z0-9.'\-]+){0,4}\s"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct)\b",
    re.I,
)
PLATE_RE = re.compile(r"\b[A-Z]{3}\d{4}\b")

_hits = {}
_hits_lock = threading.Lock()


def env(name):
    return (os.environ.get(name) or "").strip()


def normalize_e164(raw):
    """(239) 555-0101 and +1 239 555 0101 both become +12395550101."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    if digits[0] in "01" or digits[3] in "01":
        return None
    return "+1" + digits


def scrub_text(text):
    """Drop email, card, street-address, and plate shapes from prose."""
    out = str(text or "")
    for pattern in (EMAIL_RE, CARD_RE, ADDRESS_RE, PLATE_RE):
        out = pattern.sub(" ", out)
    return " ".join(out.split())


def empty_context(backend="empty"):
    return {
        "known": False,
        "caller_context": "No previous contact on file.",
        "last_contact_at": "",
        "open_followups": [],
        "caller_type": "unknown",
        "backend": backend,
    }


def reset_limits():
    with _hits_lock:
        _hits.clear()


def allow_ip(ip, now):
    try:
        limit = int(env("CALLER_CONTEXT_RPM") or "60")
    except ValueError:
        limit = 60
    limit = max(1, min(limit, 600))
    window = 60.0
    with _hits_lock:
        buf = [t for t in _hits.get(ip, ()) if now - t < window]
        if len(buf) >= limit:
            _hits[ip] = buf
            return False
        buf.append(now)
        _hits[ip] = buf
        if len(_hits) > 2000:
            _hits.clear()
            _hits[ip] = buf
        return True


def default_index_path():
    override = env("CALLER_INDEX_PATH")
    if override:
        return Path(override)
    state = env("BLAND_RELAY_STATE_DIR")
    if state:
        return Path(state) / "caller_index.sqlite"
    return Path.home() / ".local/state/bland-relay/caller_index.sqlite"


def _parse_time(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clock(dt):
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d} {suffix}"


def _when(occurred, now):
    local = occurred.astimezone(ET)
    if local.date() == now.astimezone(ET).date():
        return f"Today {_clock(local)}"
    return f"{local.strftime('%Y-%m-%d')} {_clock(local)}"


def _direction(value, inbound_flag):
    if inbound_flag is True:
        return "in"
    if inbound_flag is False:
        return "out"
    text = str(value or "").lower()
    if text in ("in", "inbound"):
        return "in"
    return "out"


def _channel(value):
    text = str(value or "call").lower()
    if "sms" in text or "text" in text or "rcs" in text:
        return "sms"
    return "call"


def _purpose(payload):
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return str(meta.get("purpose") or payload.get("purpose") or "").strip()


def _followup(payload):
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return str(meta.get("follow_up") or meta.get("followup") or payload.get("follow_up") or "").strip()


def _party_phone(payload, direction):
    if direction == "out":
        keys = ("to", "recipient", "phone", "phone_number", "from", "sender")
    else:
        keys = ("from", "sender", "phone", "phone_number", "to", "recipient")
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    for source in (payload, nested):
        for key in keys:
            number = normalize_e164(source.get(key))
            if number and number not in OUR_LINES:
                return number
    return None


def _occurred(payload):
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    for source in (payload, nested):
        for key in ("created_at", "start_time", "occurred_at", "received_at", "receivedAt", "timestamp"):
            parsed = _parse_time(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _call_outcome(payload):
    err = str(payload.get("error_message") or "")
    if "concurren" in err.lower() or "busy" in err.lower():
        return "concurrency_rejected"
    answered = str(payload.get("answered_by") or "").lower()
    if "voicemail" in answered or "voice_mail" in answered:
        return "voicemail"
    status = str(payload.get("status") or "").strip().lower()
    return status.replace(" ", "_")[:64] if status else (answered or "")


def _caller_type(raw, tags):
    if raw in CALLER_TYPES:
        return raw
    tag_set = {str(t).lower() for t in (tags or [])}
    party = str(raw or "").lower()
    if "vendor" in tag_set or party in ("vendor", "supplier"):
        return "vendor"
    if "renter" in tag_set or "turo" in tag_set or party == "customer":
        return "renter"
    if "owner" in tag_set or "storage" in tag_set or party == "lead":
        return "owner_prospect"
    return "unknown"


def _clip(text):
    text = " ".join(str(text or "").split())
    if len(text) <= CONTEXT_MAX:
        return text
    cut = text[:CONTEXT_MAX].rsplit(" ", 1)[0]
    return cut if len(cut) > CONTEXT_MAX // 2 else text[:CONTEXT_MAX]


class CallerIndex:
    """Fallback store. Idempotent on (provider, provider_msg_id). Mode 0600."""

    def __init__(self, path=None):
        self.path = Path(path) if path else default_index_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=0.25, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS interaction (
                  provider TEXT NOT NULL,
                  provider_msg_id TEXT NOT NULL,
                  phone TEXT NOT NULL,
                  channel TEXT NOT NULL,
                  direction TEXT NOT NULL,
                  outcome TEXT NOT NULL DEFAULT '',
                  purpose TEXT NOT NULL DEFAULT '',
                  followup TEXT NOT NULL DEFAULT '',
                  caller_type TEXT NOT NULL DEFAULT 'unknown',
                  turo_booking INTEGER NOT NULL DEFAULT 0,
                  occurred_at TEXT NOT NULL,
                  PRIMARY KEY (provider, provider_msg_id)
                );
                CREATE INDEX IF NOT EXISTS idx_interaction_phone
                  ON interaction(phone, occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_interaction_occurred
                  ON interaction(occurred_at);
                CREATE TABLE IF NOT EXISTS meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                """
            )
            self.conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self):
        self.conn.close()

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM interaction").fetchone()[0]

    def upsert(self, row):
        """Insert one interaction. Return True when the provider id is new."""
        with self.lock:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO interaction(
                  provider, provider_msg_id, phone, channel, direction, outcome,
                  purpose, followup, caller_type, turo_booking, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            self.conn.commit()
            return cur.rowcount == 1

    def add_many(self, rows):
        with self.lock:
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO interaction(
                  provider, provider_msg_id, phone, channel, direction, outcome,
                  purpose, followup, caller_type, turo_booking, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self.conn.commit()

    def purge(self, now):
        cutoff = _iso(now - timedelta(days=RETENTION_DAYS))
        with self.lock:
            self.conn.execute("DELETE FROM interaction WHERE occurred_at < ?", (cutoff,))
            self.conn.commit()

    def forget(self, phone):
        number = normalize_e164(phone)
        if not number:
            return 0
        with self.lock:
            cur = self.conn.execute("DELETE FROM interaction WHERE phone = ?", (number,))
            self.conn.commit()
            return cur.rowcount

    def poll_start(self, now):
        with self.lock:
            row = self.conn.execute("SELECT MAX(occurred_at) FROM interaction").fetchone()
        latest = _parse_time(row[0]) if row and row[0] else None
        anchor = latest or (now - timedelta(days=1))
        return anchor.astimezone(timezone.utc).strftime("%Y-%m-%d")

    def lookup(self, phone, now):
        number = normalize_e164(phone)
        if not number:
            return None
        self.purge(now)
        cutoff = _iso(now - timedelta(days=RETENTION_DAYS))
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT * FROM interaction
                WHERE phone = ? AND occurred_at >= ?
                ORDER BY occurred_at DESC, provider_msg_id DESC
                LIMIT 20
                """,
                (number, cutoff),
            ).fetchall()
        if not rows:
            return None
        followups = []
        interactions = []
        turo = False
        caller_type = "unknown"
        for row in rows:
            if row["caller_type"] in CALLER_TYPES and caller_type == "unknown":
                caller_type = row["caller_type"]
            turo = turo or bool(row["turo_booking"])
            if row["followup"]:
                followups.append(row["followup"])
            interactions.append(
                {
                    "channel": row["channel"],
                    "direction": row["direction"],
                    "outcome": row["outcome"],
                    "purpose": row["purpose"],
                    "occurred_at": row["occurred_at"],
                }
            )
        return {
            "known": True,
            "caller_type": caller_type,
            "turo_booking": turo,
            "interactions": interactions,
            "followups": followups,
        }


def _row_from_call(payload, provider_prefix):
    call_id = str(payload.get("call_id") or payload.get("c_id") or payload.get("id") or "").strip()
    if not call_id:
        return None
    direction = _direction(payload.get("direction"), payload.get("inbound") if "inbound" in payload else None)
    phone = _party_phone(payload, direction)
    occurred = _occurred(payload)
    if not phone or occurred is None:
        return None
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return (
        "bland",
        f"{provider_prefix}:{call_id}",
        phone,
        "call",
        direction,
        _call_outcome(payload),
        scrub_text(_purpose(payload))[:160],
        scrub_text(_followup(payload))[:160],
        _caller_type(meta.get("caller_type") or payload.get("caller_type"), meta.get("tags")),
        1 if meta.get("turo_booking") or payload.get("turo_booking") else 0,
        _iso(occurred),
    )


def ingest_call_webhook(index, payload):
    """Inbound Bland post-call webhook fixture -> fallback index."""
    row = _row_from_call(payload if isinstance(payload, dict) else {}, "call")
    if row is None:
        return False
    return index.upsert(row)


def ingest_polled_call(index, payload):
    """One call object from GET /v1/calls. Same row shape as the webhook."""
    row = _row_from_call(payload if isinstance(payload, dict) else {}, "call")
    if row is None:
        return False
    return index.upsert(row)


def ingest_bland_sms(index, payload):
    if not isinstance(payload, dict):
        return False
    msg_id = str(payload.get("id") or payload.get("message_id") or "").strip()
    direction = _direction(payload.get("direction"), payload.get("inbound") if "inbound" in payload else None)
    phone = _party_phone(payload, direction)
    occurred = _occurred(payload)
    if not msg_id or not phone or occurred is None:
        return False
    return index.upsert(
        (
            "bland",
            f"sms:{msg_id}",
            phone,
            "sms",
            direction,
            "sent",
            scrub_text(_purpose(payload))[:160],
            scrub_text(_followup(payload))[:160],
            "unknown",
            0,
            _iso(occurred),
        )
    )


def ingest_smsgate(index, payload):
    if not isinstance(payload, dict):
        return False
    nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    msg_id = str(nested.get("messageId") or payload.get("id") or "").strip()
    sender = nested.get("sender") or nested.get("phoneNumber") or payload.get("from")
    phone = normalize_e164(sender)
    occurred = _occurred(payload)
    if not msg_id or not phone or phone in OUR_LINES or occurred is None:
        return False
    return index.upsert(
        (
            "smsgate",
            f"smsgate:{msg_id}",
            phone,
            "sms",
            "in",
            "received",
            scrub_text(_purpose(payload))[:160],
            "",
            "unknown",
            0,
            _iso(occurred),
        )
    )


def _items(body, preferred):
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    for key in (preferred, "data", "calls", "messages", "conversations"):
        if isinstance(body.get(key), list):
            return body[key]
    return []


def _safe_get(client, path, params):
    try:
        got = client.get(path, params)
    except Exception as exc:  # noqa: BLE001 — a Bland failure must not escape the tick
        return "error", type(exc).__name__
    if not isinstance(got, tuple) or len(got) != 2:
        return "error", "bad-client"
    status, body = got
    try:
        status = int(status)
    except (TypeError, ValueError):
        return "error", "bad-status"
    if status == 429:
        return "rate", body
    if status != 200:
        return "error", f"{path}:{status}"
    return "ok", body


def poll_once(client, index, now=None):
    """One read-only tick. GET /v1/calls and GET /v1/sms/conversations only.

    A 429 stops the tick. A 5xx is recorded and the other GET still runs.
    There is no POST path on this function.
    """
    now = now or datetime.now(timezone.utc)
    start = index.poll_start(now)
    ingested = 0
    errors = []
    status, body = _safe_get(client, "/v1/calls", {"start_date": start})
    if status == "rate":
        return {"ingested": 0, "errors": errors, "rate_limited": True}
    if status == "error":
        errors.append(str(body))
    else:
        for call in _items(body, "calls"):
            if ingest_polled_call(index, call):
                ingested += 1
    status, body = _safe_get(client, "/v1/sms/conversations", {"start_date": start})
    if status == "rate":
        return {"ingested": ingested, "errors": errors, "rate_limited": True}
    if status == "error":
        errors.append(str(body))
        return {"ingested": ingested, "errors": errors, "rate_limited": False}
    for convo in _items(body, "conversations"):
        if not isinstance(convo, dict):
            continue
        messages = convo.get("messages") if isinstance(convo.get("messages"), list) else None
        convo_id = str(convo.get("id") or "").strip()
        if messages is None and convo_id:
            detail_status, detail = _safe_get(client, f"/v1/sms/conversations/{convo_id}", None)
            if detail_status == "rate":
                return {"ingested": ingested, "errors": errors, "rate_limited": True}
            if detail_status == "error":
                errors.append(str(detail))
                continue
            messages = _items(detail, "messages")
        for message in messages or []:
            if ingest_bland_sms(index, message):
                ingested += 1
    return {"ingested": ingested, "errors": errors, "rate_limited": False}


class BlandReadClient:
    """Explicit-base GET client. No post method, no default Bland host."""

    def __init__(self, base, api_key, opener=None, timeout=10):
        base = str(base or "").strip().rstrip("/")
        if not base:
            raise ValueError("Bland base URL is required")
        self.base = base
        self.api_key = api_key
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.timeout = timeout

    def get(self, path, params=None):
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.base + path + (("?" + query) if query else "")
        req = urllib.request.Request(
            url,
            headers={"authorization": self.api_key, "accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as res:
                raw = res.read()
                status = getattr(res, "status", 200)
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            status = exc.code
        if not raw:
            return status, {}
        try:
            body = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        return status, body


def view_from_crm(doc):
    """Narrow a CRM record down to fields Alexandra may hear. No vehicles."""
    tags = doc.get("tags") if isinstance(doc.get("tags"), list) else []
    caller_type = _caller_type(doc.get("caller_type") or doc.get("type"), tags)
    turo = bool(doc.get("turo_booking") or doc.get("booking_linked"))
    if not turo:
        for ident in doc.get("identities") or []:
            if isinstance(ident, dict) and ident.get("kind") in ("turo_guest", "turo_guest_id"):
                turo = True
    interactions = []
    raw_items = doc.get("interactions") or doc.get("timeline") or []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        occurred = _parse_time(raw.get("occurred_at"))
        interactions.append(
            {
                "channel": _channel(raw.get("channel")),
                "direction": _direction(raw.get("direction"), None),
                "outcome": str(raw.get("outcome") or raw.get("answered_by") or ""),
                "purpose": str(meta.get("purpose") or raw.get("purpose") or ""),
                "occurred_at": _iso(occurred) if occurred else "",
            }
        )
    followups = []
    for item in doc.get("open_followups") or []:
        if str(item).strip():
            followups.append(str(item))
    for task in doc.get("tasks") or []:
        if isinstance(task, dict) and str(task.get("status") or "open") == "open" and task.get("title"):
            followups.append(str(task["title"]))
    return {
        "known": True,
        "caller_type": caller_type,
        "turo_booking": turo,
        "interactions": interactions,
        "followups": followups,
    }


def loopback_http_base(raw):
    """http://127.0.0.1:<port> or http://localhost:<port>, else None.

    Same allowlist as SEND904_BASE. A rejected value must not be logged: the
    userinfo can carry CRM_API_TOKEN.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "http"
        or host not in ("127.0.0.1", "localhost")
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        return None
    return "http://%s:%d" % (host, port)


def crm_lookup(base, token, phone, timeout=CRM_TIMEOUT_S, opener=None):
    """Return ('hit', view), ('miss', None), or ('down', None). Never raises.

    A base outside the loopback allowlist is 'down' and opens no request.
    """
    base = loopback_http_base(base)
    if not base or not str(token or "").strip():
        return "down", None
    url = base + "/v1/contacts/by-phone?" + urllib.parse.urlencode({"pn": phone})
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-CRM-Scope": "caller-context",
        },
        method="GET",
    )
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    open_fn = opener.open
    try:
        with open_fn(req, timeout=timeout) as res:
            status = getattr(res, "status", 200)
            raw = res.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "miss", None
        return "down", None
    except Exception:  # noqa: BLE001 — timeout and connect errors are "down"
        return "down", None
    if status == 404:
        return "miss", None
    if status != 200:
        return "down", None
    try:
        doc = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return "down", None
    if not isinstance(doc, dict):
        return "down", None
    return "hit", view_from_crm(doc)


def _line(item, now):
    occurred = _parse_time(item.get("occurred_at"))
    if occurred is None:
        return None
    outcome = str(item.get("outcome") or "").lower()
    channel = item.get("channel")
    direction = item.get("direction")
    if channel == "call" and direction == "out" and "voicemail" in outcome:
        what = "we called you from this line; it went to voicemail"
    elif channel == "call" and direction == "out" and (
        "concurren" in outcome or "busy" in outcome
    ):
        what = "we called you from this line; the call did not connect because the line was busy"
    elif channel == "call" and direction == "out":
        what = "we called you from this line"
    elif channel == "call" and direction == "in":
        what = "you called this line"
    elif channel == "sms" and direction == "out":
        what = "we texted you"
    elif channel == "sms":
        what = "you texted this line"
    else:
        what = "we have a previous contact on file"
    purpose = scrub_text(item.get("purpose")) or "not recorded"
    return f"{_when(occurred, now)}: {what} (purpose: {purpose})"


def render(view, now, backend):
    if not view or not view.get("known"):
        return empty_context(backend)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    kept = []
    for item in view.get("interactions") or []:
        occurred = _parse_time(item.get("occurred_at"))
        if occurred is not None and occurred >= cutoff:
            kept.append(item)
    kept.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)
    lines = []
    for item in kept[:3]:
        line = _line(item, now)
        if line:
            lines.append(line)
    followups = []
    for text in view.get("followups") or []:
        clean = scrub_text(text)
        if clean and clean not in followups:
            followups.append(clean)
    followups = followups[:5]
    caller_type = view.get("caller_type") if view.get("caller_type") in CALLER_TYPES else "unknown"
    if not lines and not followups and not view.get("turo_booking"):
        return empty_context(backend)
    parts = []
    if lines:
        parts.append(" ".join(lines))
    if followups:
        parts.append("Open follow-up: " + "; ".join(followups) + ".")
    if view.get("turo_booking"):
        parts.append(
            "Has a Turo booking on file; confirm name and trip number before discussing it."
        )
    prose = _clip(scrub_text(" ".join(parts)))
    if not prose:
        return empty_context(backend)
    latest = _parse_time(kept[0].get("occurred_at")) if kept else None
    last_at = ""
    if latest is not None:
        local = latest.astimezone(ET)
        last_at = f"{local.strftime('%Y-%m-%d')} {_clock(local)} ET"
    return {
        "known": True,
        "caller_context": prose,
        "last_contact_at": last_at,
        "open_followups": followups,
        "caller_type": caller_type,
        "backend": backend,
    }


def lookup_caller(phone, now=None, index=None, opener=None):
    """CRM first (250 ms) on a loopback base. Otherwise the local index. Never raises."""
    now = now or datetime.now(timezone.utc)
    number = normalize_e164(phone)
    if not number:
        return empty_context("empty")
    base = loopback_http_base(env("CRM_API_BASE"))
    token = env("CRM_API_TOKEN")
    if base and token:
        status, view = crm_lookup(base, token, number, opener=opener)
        if status == "hit":
            return render(view, now, "crm")
        if status == "miss":
            return empty_context("crm")
    try:
        store = index if index is not None else CallerIndex()
        view = store.lookup(number, now)
    except Exception:  # noqa: BLE001 — the greeting must still get a 200
        return empty_context("empty")
    if view is None:
        return empty_context("index")
    try:
        return render(view, now, "index")
    except Exception:  # noqa: BLE001
        return empty_context("empty")
