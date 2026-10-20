"""Post-call write-back for Bland webhooks (#1231).

The relay already receives every post-call webhook. After the HMAC check,
``handle`` runs in a background thread and returns before the raw body is
forwarded. It classifies the call, appends one CRM outbox row, updates the
local caller index, and sends one ``call_followup`` event for the three
follow-up classes. A second delivery of the same ``call_id`` does not write
again.

Stdlib only. No Bland API call, no calendar call, no mail. The transcript is
read in memory for classification and then dropped. Logs are ``call_id``,
class, and milliseconds, or an exception class.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import caller_context

log = logging.getLogger("bland-relay")

ET = caller_context.ET
CRM_SCHEMA = "panamerica.crm.interaction.v1"
EVENT_SCHEMA = "panamerica.call.followup.v1"
FOLLOWUP_CLASSES = (
    "meeting-confirmation-requested",
    "meeting-reschedule-requested",
    "callback-requested",
)
# Callback follow-up, but not an Alexandra push (#1301 D3).
CONCURRENCY_CLASS = "concurrency-rejected"
# Callback follow-up and one Alexandra event (#1301 D4).
PUSH_ALSO = ("agent-error", "transferred")
# Bland's call_length is minutes. Hang-up before greeting is under 5 seconds.
SHORT_S = 5.0
# Hook-thread retries after an outbox or push failure, then one dead-letter line.
RETRY_DELAYS = (30, 120, 600)
_SLEEP = time.sleep
_CLOCK = time.monotonic
_NEAR = 8
_CONFIRM_ACTIONS = frozenset({
    "confirm", "confirms", "confirmed", "confirming", "confirmation",
    "check", "checks", "checked", "checking",
})
_CONFIRM_OBJECTS = frozenset({"meeting", "meetings"})
_MOVE_ACTIONS = frozenset({
    "move", "moves", "moved", "moving",
    "reschedule", "reschedules", "rescheduled", "rescheduling",
    "change", "changes", "changed", "changing",
    "push", "pushes", "pushed", "pushing",
})
_MOVE_OBJECTS = frozenset({
    "meeting", "meetings", "call", "calls", "appointment", "appointments",
})
_CALLBACK_RE = re.compile(
    r"\bcall\s*backs?\b|\bcallbacks?\b|\bcall me back\b"
    r"|\b(?:speak|talk|asked|asking|ask|reach)\b(?:\s+\w+){0,5}\s+(?:to\s+|with\s+|for\s+)?chris\b"
    r"|\basked for chris\b|\bfor chris\b",
    re.I,
)
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_lock = threading.Lock()


def _env(name):
    return (os.environ.get(name) or "").strip()


def _state_dir():
    override = _env("BLAND_RELAY_STATE_DIR")
    return Path(override) if override else Path.home() / ".local" / "state" / "bland-relay"


def _seen_path():
    return _state_dir() / "post_call_seen.sqlite"


def _clean_id(value):
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(value or ""))[:64]


def _utc_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _et_iso(dt):
    return dt.astimezone(ET).isoformat(timespec="seconds")


def _phrase(dt):
    if dt is None:
        return None
    local = dt.astimezone(ET)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{_DOW[local.weekday()]} {_MON[local.month - 1]} {local.day} {hour}:{local.minute:02d} {suffix}"


def _short_name(stated):
    parts = str(stated or "").split()
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."


def _completed(payload):
    if "completed" in payload and payload["completed"] is not None:
        val = payload["completed"]
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str) and val.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
            return val.strip().lower() in ("true", "yes", "1")
        return None
    status = str(payload.get("status") or "").strip().lower()
    if status in ("completed", "complete"):
        return True
    if status:
        return False
    return None


def _error_set(payload):
    err = payload.get("error_message")
    if err is None or err is False:
        return False
    return bool(str(err).strip())


def _concurrency(payload):
    blob = " ".join(
        str(payload.get(key) or "") for key in ("error_message", "status", "queue_status")
    ).lower()
    return "concurren" in blob or "busy" in blob


def _transferred(payload):
    flag = payload.get("transferred")
    if flag is True or (isinstance(flag, str) and flag.strip().lower() in ("true", "yes", "1")):
        return True
    dest = payload.get("transferred_to")
    if isinstance(dest, str) and dest.strip():
        return True
    for key in ("status", "answered_by", "call_ended_by", "disposition_tag"):
        if "transfer" in str(payload.get(key) or "").lower():
            return True
    return False


def _call_seconds(payload):
    """Bland ``call_length`` is minutes. None when the field is absent."""
    raw = payload.get("call_length")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        minutes = float(raw)
    except (TypeError, ValueError):
        return None
    if minutes < 0:
        return None
    return minutes * 60.0


def _caller_turns(payload):
    """Caller speech only. Summary and assistant lines are not turns."""
    parts = []
    for key in ("transcripts", "transcript"):
        val = payload.get(key)
        if not isinstance(val, list):
            continue
        for turn in val[:20]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("user") or turn.get("role") or turn.get("speaker") or "").lower()
            if role not in ("user", "human", "caller", "customer"):
                continue
            text = str(turn.get("text") or turn.get("content") or "").strip()
            if text:
                parts.append(text)
    raw = payload.get("concatenated_transcript")
    if isinstance(raw, str):
        for line in raw.splitlines()[:40]:
            if re.match(r"\s*(user|caller|human)\s*:", line, re.I):
                spoken = line.split(":", 1)[1].strip()
                if spoken:
                    parts.append(spoken)
    return parts


def _short_call(payload):
    seconds = _call_seconds(payload)
    return seconds is not None and seconds < SHORT_S and not _caller_turns(payload)


def _caller_blob(payload):
    """Caller turns, summary, and string variables. Not retained."""
    parts = []
    for key in ("transcripts", "transcript"):
        val = payload.get(key)
        if not isinstance(val, list):
            continue
        for turn in val[:20]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("user") or turn.get("role") or turn.get("speaker") or "").lower()
            if role not in ("user", "human", "caller", "customer"):
                continue
            parts.append(str(turn.get("text") or turn.get("content") or "")[:500])
    raw = payload.get("concatenated_transcript")
    if isinstance(raw, str):
        for line in raw.splitlines()[:40]:
            if re.match(r"\s*(user|caller|human)\s*:", line, re.I):
                parts.append(line.split(":", 1)[1][:500])
    summary = payload.get("summary")
    if isinstance(summary, str):
        parts.append(summary[:2000])
    variables = payload.get("variables")
    if isinstance(variables, dict):
        for val in list(variables.values())[:20]:
            if isinstance(val, str):
                parts.append(val[:500])
    return " ".join(parts)[:8000]


def _near(text, actions, objects):
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    act = [i for i, tok in enumerate(tokens) if tok in actions]
    obj = [i for i, tok in enumerate(tokens) if tok in objects]
    return any(abs(a - b) <= _NEAR for a in act for b in obj)


def classify(payload, has_attempt):
    """First match wins. ``has_attempt`` is any meeting_attempts row for this call."""
    if not isinstance(payload, dict):
        return "unknown"
    answered = str(payload.get("answered_by") or "").lower()
    if "voicemail" in answered or "voice_mail" in answered:
        return "voicemail"
    if _concurrency(payload):
        return CONCURRENCY_CLASS
    if _transferred(payload):
        return "transferred"
    if _error_set(payload):
        return "agent-error"
    if _short_call(payload):
        return "short"
    done = _completed(payload)
    if done is False:
        return "no-answer"
    if done is None:
        return "unknown"
    blob = _caller_blob(payload)
    if has_attempt or _near(blob, _CONFIRM_ACTIONS, _CONFIRM_OBJECTS):
        return "meeting-confirmation-requested"
    if _near(blob, _MOVE_ACTIONS, _MOVE_OBJECTS):
        return "meeting-reschedule-requested"
    if _CALLBACK_RE.search(blob):
        return "callback-requested"
    return "info-only"


def followup_due(now, stated_start):
    """Next 9:00 AM ET, or one hour before the stated meeting when that is sooner and still ahead."""
    local_now = now.astimezone(ET)
    nine = local_now.replace(hour=9, minute=0, second=0, microsecond=0)
    if local_now >= nine:
        nine = nine + timedelta(days=1)
    if stated_start is not None:
        hour_before = stated_start.astimezone(ET) - timedelta(hours=1)
        if local_now < hour_before < nine:
            return hour_before
    return nine


def _title(kind, stated_name, when):
    who = _short_name(stated_name)
    if kind == "callback-requested":
        if who and when:
            return f"Call {who} back about {when} meeting."
        if who:
            return f"Call {who} back."
        return "Call back — see call summary."
    verb = "Confirm" if kind == "meeting-confirmation-requested" else "Reschedule"
    if when and who:
        # The last initial already ends with a period ("Alex R.").
        text = f"{verb} {when} meeting with {who}"
        return text if text.endswith(".") else text + "."
    if when:
        return f"{verb} {when} meeting."
    if who:
        return f"{verb} meeting with {who} — see call summary."
    return f"{verb} meeting — see call summary."


def _sentence(kind, when):
    if kind == "callback-requested":
        if when:
            return f"Chris's team to call back about {when} meeting"
        return "Chris's team to call back — see call summary"
    verb = "confirm" if kind == "meeting-confirmation-requested" else "reschedule"
    if when:
        return f"Chris's team to {verb} {when} meeting"
    return f"Chris's team to {verb} — see call summary"


def _latest_attempt(call_id):
    path = _state_dir() / "meeting_attempts.sqlite"
    if not call_id or not path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = db.execute(
            "SELECT stated_name, stated_start, result FROM attempts WHERE call_id = ? "
            "ORDER BY ts DESC LIMIT 1",
            (call_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    if row is None:
        return None
    name = " ".join(str(row[0] or "").split())[:60]
    return {
        "name": name,
        "start": caller_context._parse_time(row[1]),
        "match": bool(row[2]),
    }


def _parties(payload):
    direction = caller_context._direction(
        payload.get("direction"), payload.get("inbound") if "inbound" in payload else None
    )
    phone = caller_context._party_phone(payload, direction)
    ours = []
    for key in ("from", "to"):
        number = caller_context.normalize_e164(payload.get(key))
        if number in caller_context.OUR_LINES and number not in ours:
            ours.append(number)
    if not ours:
        return None
    our_line = ours[0]
    other = phone or ""
    if direction == "in":
        return direction, phone, other, our_line
    return direction, phone, our_line, other


def _occurred(payload, now):
    parsed = caller_context._occurred(payload)
    return parsed or now.astimezone(timezone.utc)


def _qa(payload):
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return meta.get("qa") is True


def _wants_followup(kind, phone, qa):
    """Callback follow-up. Concurrency only when the other party is valid E.164."""
    if qa:
        return False
    if kind in FOLLOWUP_CLASSES or kind in PUSH_ALSO:
        return True
    if kind == CONCURRENCY_CLASS:
        return bool(phone)
    return False


def _wants_push(record):
    if record.get("_qa") or record["outcome"] == CONCURRENCY_CLASS:
        return False
    if record["outcome"] not in FOLLOWUP_CLASSES and record["outcome"] not in PUSH_ALSO:
        return False
    if _env("POST_CALL_PUSH_DISABLED") == "1" or not _env("ALEXANDRA_ALERT_URL"):
        return False
    return True


def _meta_text(payload, key):
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return caller_context.scrub_text(meta.get(key))[:200]


def build_record(payload, now=None, attempt=None):
    """One ``panamerica.crm.interaction.v1`` row. None when this call is not ours.

    #1097 drains the same row. The transcript is not an input to the row.
    """
    if not isinstance(payload, dict):
        return None
    call_id = _clean_id(payload.get("call_id") or payload.get("c_id"))
    parties = _parties(payload)
    if not call_id or parties is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    direction, phone, from_addr, to_addr = parties
    attempt = _latest_attempt(call_id) if attempt is None else attempt
    kind = classify(payload, attempt is not None)
    stated_name = (attempt or {}).get("name") or ""
    stated_start = (attempt or {}).get("start") if attempt else None
    when = _phrase(stated_start)
    qa = _qa(payload)
    summary = caller_context.scrub_text(payload.get("summary"))[:200]
    purpose = _meta_text(payload, "purpose")
    placed_by = _meta_text(payload, "placed_by")
    meta_type = _meta_text(payload, "caller_type").lower()
    party_type = "vendor" if meta_type == "vendor" else "unknown"
    occurred = _occurred(payload, now)
    record = {
        "schema": CRM_SCHEMA,
        "channel": "call",
        "direction": direction,
        "provider": "bland",
        "provider_msg_id": call_id,
        "from_addr": from_addr,
        "to_addr": to_addr,
        "summary": summary,
        "outcome": kind,
        "error_code": "",
        "occurred_at": _utc_z(occurred),
        "logged_by": "bland-relay",
        "metadata": {"purpose": purpose or None, "placed_by": placed_by or None},
        "party_hint": {
            "phone": phone,
            "display_name": stated_name or None,
            "type": party_type,
        },
    }
    if qa:
        record["metadata"]["qa"] = True
    title_kind = kind if kind in ("meeting-confirmation-requested", "meeting-reschedule-requested") else "callback-requested"
    wants = _wants_followup(kind, phone, qa)
    if wants:
        check = "not-run"
        if attempt is not None:
            check = "match" if attempt.get("match") else "no-match"
        record["followup"] = {
            "title": _title(title_kind, stated_name, when),
            "due_at": _et_iso(followup_due(now, stated_start)),
            "assignee_seat": "grok",
            "kind": kind,
            "calendar_check": check,
        }
    # QA skips the outbox follow-up, but the index still stores the sentence
    # for the three original classes (same as before #1301).
    record["_index_followup"] = (
        _sentence(title_kind, when) if kind in FOLLOWUP_CLASSES or wants else ""
    )
    record["_stated_meeting"] = _et_iso(stated_start) if stated_start is not None else None
    record["_calendar_check"] = (
        record["followup"]["calendar_check"] if "followup" in record else (
            "match" if attempt and attempt.get("match") else "no-match" if attempt else "not-run"
        )
    )
    record["_qa"] = qa
    record["_occurred"] = occurred
    return record


def _open_seen():
    path = _seen_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    db = sqlite3.connect(str(path), timeout=2)
    db.execute(
        "CREATE TABLE IF NOT EXISTS seen ("
        "call_id TEXT PRIMARY KEY, outbox INTEGER NOT NULL DEFAULT 0, push INTEGER NOT NULL DEFAULT 0)"
    )
    return db


def _marked(db, call_id, column):
    row = db.execute(f"SELECT {column} FROM seen WHERE call_id = ?", (call_id,)).fetchone()
    return bool(row and row[0])


def _public_record(record):
    skip = {"body", "transcript", "transcripts", "concatenated_transcript"}
    return {k: v for k, v in record.items() if not str(k).startswith("_") and k not in skip}


def _write_outbox(call_id, record):
    """True when the line is written, already stored, or the sink is off. False on failure."""
    import bland_relay

    sink = bland_relay.crm_sink()
    if sink is None:
        return True
    public = _public_record(record)
    try:
        with _lock:
            db = _open_seen()
            try:
                db.execute("INSERT OR IGNORE INTO seen(call_id) VALUES (?)", (call_id,))
                if _marked(db, call_id, "outbox"):
                    db.commit()
                    return True
                sink.emit(public)
                db.execute("UPDATE seen SET outbox = 1 WHERE call_id = ?", (call_id,))
                db.commit()
                return True
            finally:
                db.close()
    except Exception as exc:  # noqa: BLE001 — class only, never the row
        log.error("post-call: failed: %s", type(exc).__name__)
        return False


def _claim_push(call_id):
    with _lock:
        db = _open_seen()
        try:
            db.execute("INSERT OR IGNORE INTO seen(call_id) VALUES (?)", (call_id,))
            if _marked(db, call_id, "push"):
                db.commit()
                return False
            db.execute("UPDATE seen SET push = 1 WHERE call_id = ?", (call_id,))
            db.commit()
            return True
        finally:
            db.close()


def _index_payload(payload, record):
    direction = record["direction"]
    kind = record["outcome"]
    body = {
        "call_id": record["provider_msg_id"],
        "from": record["from_addr"],
        "to": record["to_addr"],
        "inbound": direction == "in",
        "created_at": record["occurred_at"],
        "metadata": {
            "purpose": record["metadata"].get("purpose") or "",
            "follow_up": record.get("_index_followup") or "",
            "caller_type": record["party_hint"]["type"],
        },
    }
    if kind == "voicemail":
        body["answered_by"] = "voicemail"
        body["status"] = "completed"
    elif kind == "no-answer":
        body["status"] = "error"
        err = payload.get("error_message")
        if isinstance(err, str) and err.strip() and "concurren" not in err.lower() and "busy" not in err.lower():
            body["error_message"] = err.strip()[:120]
    elif kind in ("short", CONCURRENCY_CLASS, "agent-error", "transferred"):
        body["status"] = kind
    else:
        body["status"] = "completed"
    return body


def _index_tuple(record):
    party = record["party_hint"]["type"]
    if party not in caller_context.CALLER_TYPES:
        party = "unknown"
    purpose = (record.get("metadata") or {}).get("purpose") or ""
    return (
        "bland",
        "call:" + record["provider_msg_id"],
        record["party_hint"].get("phone") or "",
        "call",
        record["direction"],
        record["outcome"],
        caller_context.scrub_text(purpose)[:160],
        caller_context.scrub_text(record.get("_index_followup") or "")[:160],
        party,
        0,
        record["occurred_at"],
    )


def _tag_qa(index, call_id):
    with index.lock:
        cols = [row[1] for row in index.conn.execute("PRAGMA table_info(interaction)")]
        if "qa" not in cols:
            index.conn.execute("ALTER TABLE interaction ADD COLUMN qa INTEGER NOT NULL DEFAULT 0")
        index.conn.execute(
            "UPDATE interaction SET qa = 1 WHERE provider = 'bland' AND provider_msg_id = ?",
            (f"call:{call_id}",),
        )
        index.conn.commit()


def _write_index(payload, record):
    index = caller_context.CallerIndex()
    try:
        wrote = False
        if record["party_hint"].get("phone"):
            wrote = caller_context.ingest_call_webhook(index, _index_payload(payload, record))
        if not wrote:
            index.upsert(_index_tuple(record))
        if record.get("_qa"):
            _tag_qa(index, record["provider_msg_id"])
    finally:
        index.close()


def _event(record):
    follow = record.get("followup") or {}
    return {
        "kind": "call_followup",
        "schema": EVENT_SCHEMA,
        "call_id": record["provider_msg_id"],
        "class": record["outcome"],
        "phone": record["party_hint"]["phone"],
        "stated_name": record["party_hint"]["display_name"],
        "stated_meeting": record.get("_stated_meeting"),
        "calendar_check": follow.get("calendar_check") or record.get("_calendar_check") or "not-run",
        "due_at": follow.get("due_at"),
        "summary": record["summary"],
        "received_at": record["occurred_at"],
    }


def _release_push(call_id):
    with _lock:
        db = _open_seen()
        try:
            db.execute("UPDATE seen SET push = 0 WHERE call_id = ?", (call_id,))
            db.commit()
        finally:
            db.close()


def _push(record):
    """True when there is nothing to send, it was already sent, or this attempt succeeded."""
    if not _wants_push(record):
        return True
    call_id = record["provider_msg_id"]
    if not _claim_push(call_id):
        return True
    try:
        import bland_relay

        status, _err, _attempts = bland_relay.forward(
            json.dumps(_event(record), ensure_ascii=False).encode("utf-8")
        )
    except Exception as exc:  # noqa: BLE001 — class only
        _release_push(call_id)
        log.error("post-call: failed: %s", type(exc).__name__)
        return False
    if isinstance(status, int) and 200 <= status < 300:
        return True
    _release_push(call_id)
    log.error("post-call: failed: push")
    return False


def _deliver(record):
    if not _write_outbox(record["provider_msg_id"], record):
        return False
    return _push(record)


def _deadletter_path():
    return _state_dir() / "post_call_deadletter.jsonl"


def _write_bytes(path, blob, append):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, blob)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def _dead_letter(record):
    # stated_meeting stays beside the CRM row. _public_record drops every _field.
    line = {
        "call_id": record["provider_msg_id"],
        "done": False,
        "record": _public_record(record),
        "stated_meeting": record.get("_stated_meeting"),
    }
    blob = (json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    _write_bytes(_deadletter_path(), blob, append=True)
    log.info("post-call: dead-letter call_id=%s", record["provider_msg_id"])


def _deliver_with_retry(record):
    if _deliver(record):
        return
    for delay in RETRY_DELAYS:
        _CLOCK()
        _SLEEP(delay)
        if _deliver(record):
            return
    _dead_letter(record)


def replay_dead_letters(path=None):
    """Re-drive each not-done line once. Lines stay in the file and gain ``done``."""
    path = Path(path) if path else _deadletter_path()
    if not path.is_file():
        return 0
    items = []
    for line in path.read_text().splitlines():
        if line.strip():
            items.append(json.loads(line))
    done = 0
    for item in items:
        if item.get("done") is True:
            continue
        record = item.get("record")
        if not isinstance(record, dict) or not record.get("provider_msg_id"):
            continue
        record = dict(record)
        meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        record["_qa"] = meta.get("qa") is True
        record.setdefault("_index_followup", "")
        if "stated_meeting" in item:
            record["_stated_meeting"] = item.get("stated_meeting")
        try:
            _write_index({}, record)
        except Exception as exc:  # noqa: BLE001 — class only
            log.error("post-call: failed: %s", type(exc).__name__)
            continue
        if _deliver(record):
            item["done"] = True
            done += 1
    blob = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items)
    _write_bytes(path, blob.encode("utf-8"), append=False)
    return done


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--replay-dead-letters"]:
        replay_dead_letters()
        return 0
    return 2


def _run(body, now):
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    payload = json.loads(body)
    call_id = _clean_id(payload.get("call_id") or payload.get("c_id")) if isinstance(payload, dict) else ""
    attempt = _latest_attempt(call_id) if call_id else None
    kind = classify(payload, attempt is not None)
    record = build_record(payload, now=now, attempt=attempt)
    if record is not None:
        _write_index(payload if isinstance(payload, dict) else {}, record)
        _deliver_with_retry(record)
    return call_id or "-", kind


def handle(body, now=None):
    """Background hook. Swallows every exception. ``POST_CALL_INGEST=off`` returns immediately."""
    if _env("POST_CALL_INGEST").lower() == "off":
        return
    started = time.perf_counter()
    try:
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        call_id, kind = _run(body, now)
        log.info("post-call: call_id=%s class=%s ms=%d", call_id, kind, int((time.perf_counter() - started) * 1000))
    except Exception as exc:  # noqa: BLE001 — class only, never the payload
        log.error("post-call: failed: %s", type(exc).__name__)


if __name__ == "__main__":
    raise SystemExit(main())
