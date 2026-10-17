"""POST /bland/meeting-check (#1230).

Imported from relay_config.py next to caller_context. Not a relay_*.py part.
Stdlib only. The calendar client is read-only and replaceable in tests.
No names, numbers, stated times, emails, or tokens are logged.
"""

from __future__ import annotations

import bisect
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("bland-relay")

ET = ZoneInfo("America/New_York")
WINDOW_S = 15 * 60
STALE_S = 600
PAST_S = 3600
FUTURE_S = 45 * 86400
LIVE_TIMEOUT_S = 0.5
SYNC_TIMEOUT_S = 10.0
BODY_MAX = 2048
CALL_CAP = 3
PHONE_CAP = 6
GLOBAL_CAP = 40
HOUR_S = 3600
PURGE_S = 48 * 3600
IP_LIMIT = 60
IP_WINDOW_S = 60.0
NAME_CLIP = 60
CALL_ID_CLIP = 128
SYNC_DEFAULT_S = 120
TOKEN_URL = "https://oauth2.googleapis.com/token"
FIELDS = (
    "nextPageToken,items(id,status,summary,start,end,creator/email,"
    "organizer/email,attendees(email,displayName,self,responseStatus))"
)
_FORBIDDEN_FIELDS = ("description", "location", "conferenceData", "htmlLink", "hangoutLink")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_TZS = {
    "et": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
    "eastern": "America/New_York",
    "eastern standard": "America/New_York",
    "eastern standard time": "America/New_York",
    "eastern time": "America/New_York",
    "ct": "America/Chicago",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "central": "America/Chicago",
    "central standard": "America/Chicago",
    "central standard time": "America/Chicago",
    "central time": "America/Chicago",
    "mt": "America/Denver",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "mountain": "America/Denver",
    "mountain standard": "America/Denver",
    "mountain time": "America/Denver",
    "pt": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "pacific standard": "America/Los_Angeles",
    "pacific time": "America/Los_Angeles",
}
_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap]m)?$", re.IGNORECASE)
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_CALL_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]")

_ip_lock = threading.Lock()
_ip_hits = {}
_guard = threading.Lock()
_checker = None


class CalendarAuthError(Exception):
    """Token or scope failure. The message is a short code, never a response body."""

    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


class View(object):
    __slots__ = ("start", "epoch", "end", "pairs", "words", "owner_declined", "has_other")

    def __init__(self, start, epoch, end, pairs, words, owner_declined, has_other):
        self.start = start
        self.epoch = epoch
        self.end = end
        self.pairs = pairs
        self.words = words
        self.owner_declined = owner_declined
        self.has_other = has_other


def env(name):
    return (os.environ.get(name) or "").strip()


def _state_dir():
    override = env("BLAND_RELAY_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "bland-relay"


def startup_line():
    """One startup line. No calendar id and no token value."""
    return "meeting-check: token=%s" % ("set" if env("MEETING_CHECK_TOKEN") else "unset")


def sync_interval():
    raw = env("MEETING_SYNC_S")
    if not raw:
        return SYNC_DEFAULT_S
    try:
        value = int(raw)
    except ValueError:
        return SYNC_DEFAULT_S
    if value <= 0:
        return SYNC_DEFAULT_S
    return value


def log_result(call_id, matched, elapsed_ms):
    safe = _CALL_ID_RE.sub("", call_id or "")[:64]
    result = "true" if matched else "false"
    log.info("meeting-check call_id=%s result=%s ms=%d", safe, result, elapsed_ms)


def events_list_url(calendar_id, time_min, time_max, page_token=None):
    """events.list URL. The field mask never asks for body, place, or links."""
    for word in _FORBIDDEN_FIELDS:
        if word in FIELDS:
            raise RuntimeError("meeting-check field mask")
    params = [
        ("singleEvents", "true"),
        ("orderBy", "startTime"),
        ("maxResults", "250"),
        ("timeMin", time_min),
        ("timeMax", time_max),
        ("fields", FIELDS),
    ]
    if page_token:
        params.append(("pageToken", page_token))
    query = urllib.parse.urlencode(params, safe="(),/:", quote_via=urllib.parse.quote)
    quoted = urllib.parse.quote(calendar_id, safe="")
    return "https://www.googleapis.com/calendar/v3/calendars/%s/events?%s" % (quoted, query)


def _rfc3339(value):
    if isinstance(value, str):
        return value
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleCalendar(object):
    def __init__(self, opener=None):
        self._opener = opener
        self._access = ""
        self._deadline = 0.0
        self._token_lock = threading.Lock()

    def _open(self, req, timeout):
        fn = self._opener or urllib.request.urlopen
        return fn(req, timeout=timeout)

    def _raise_http(self, exc):
        code = getattr(exc, "code", None)
        if code == 403:
            raise CalendarAuthError("403")
        if code == 400:
            raw = b""
            try:
                raw = exc.read() or b""
            except Exception:
                raw = b""
            err = None
            try:
                err = json.loads(raw.decode("utf-8", "replace")).get("error")
            except Exception:
                err = None
            if err == "invalid_grant":
                raise CalendarAuthError("invalid_grant")
        raise OSError("calendar_http")

    def _refresh(self, timeout):
        body = urllib.parse.urlencode({
            "client_id": env("MEETING_GCAL_CLIENT_ID"),
            "client_secret": env("MEETING_GCAL_CLIENT_SECRET"),
            "refresh_token": env("MEETING_GCAL_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }).encode("utf-8")
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        try:
            with self._open(req, timeout) as res:
                payload = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._raise_http(exc)
        token = payload.get("access_token") or ""
        if not token:
            raise OSError("calendar_token")
        try:
            expires = int(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires = 3600
        return token, expires

    def _access_token(self, timeout):
        now = time.time()
        with self._token_lock:
            if self._access and now < self._deadline:
                return self._access
            token, expires = self._refresh(timeout)
            self._access = token
            self._deadline = time.time() + expires - 60
            return self._access

    def list_events(self, time_min, time_max, timeout):
        calendar_id = env("MEETING_GCAL_CALENDAR_ID")
        if not env("MEETING_GCAL_CLIENT_ID") or not calendar_id:
            return []
        url = events_list_url(calendar_id, _rfc3339(time_min), _rfc3339(time_max))
        token = self._access_token(timeout)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with self._open(req, timeout) as res:
                payload = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._raise_http(exc)
        items = payload.get("items") if isinstance(payload, dict) else None
        return items if isinstance(items, list) else []


class _Boom(object):
    def list_events(self, time_min, time_max, timeout):
        raise AssertionError("meeting-check consulted the calendar")


def _self_emails():
    raw = env("MEETING_SELF_EMAILS").lower()
    return {part for part in re.split(r"[,\s]+", raw) if part}


def _display_pairs(name):
    parts = re.findall(r"[a-z]+", (name or "").lower())
    if len(parts) < 2:
        return []
    return [(parts[0], parts[-1], False)]


def _email_pairs(email):
    text = (email or "").strip().lower()
    if not text:
        return []
    local = text.split("@", 1)[0]
    parts = [part for part in re.split(r"[._+\-\d]+", local) if part]
    if len(parts) >= 2:
        return [(parts[0], parts[1], False)]
    if len(parts) == 1 and len(parts[0]) >= 2:
        token = parts[0]
        return [(token[0], token[1:], True)]
    return []


def _summary_words(text):
    return frozenset(re.findall(r"[a-z]+", (text or "").lower()))


def _parse_rfc3339(text):
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def derive(item):
    """One in-memory view. Cancelled and all-day events are dropped.

    Owner-declined events stay, with owner_declined set, so a later match
    can reject them on purpose. The view keeps tokens and summary words only.
    """
    if not isinstance(item, dict):
        return None
    if str(item.get("status") or "").lower() == "cancelled":
        return None
    start_raw = item.get("start") if isinstance(item.get("start"), dict) else {}
    start = _parse_rfc3339(start_raw.get("dateTime"))
    if start is None:
        return None
    end_raw = item.get("end") if isinstance(item.get("end"), dict) else {}
    end = _parse_rfc3339(end_raw.get("dateTime"))
    attendees = item.get("attendees") if isinstance(item.get("attendees"), list) else []
    selves = _self_emails()
    for att in attendees:
        if not isinstance(att, dict):
            continue
        email = (att.get("email") or "").strip().lower()
        if att.get("self") is True and email:
            selves.add(email)
    owner_declined = False
    has_other = False
    pairs = []
    for att in attendees:
        if not isinstance(att, dict):
            continue
        email = (att.get("email") or "").strip().lower()
        if att.get("self") is True or (email and email in selves):
            if str(att.get("responseStatus") or "").lower() == "declined":
                owner_declined = True
            continue
        has_other = True
        pairs.extend(_display_pairs(att.get("displayName")))
        pairs.extend(_email_pairs(email))
    for key in ("creator", "organizer"):
        person = item.get(key) if isinstance(item.get(key), dict) else {}
        email = (person.get("email") or "").strip().lower()
        if email and email not in selves:
            has_other = True
            pairs.extend(_email_pairs(email))
    return View(
        start,
        int(start.timestamp()),
        end,
        tuple(pairs),
        _summary_words(item.get("summary")),
        owner_declined,
        has_other,
    )


def _build(items):
    views = []
    for item in items or []:
        view = derive(item)
        if view is not None:
            views.append(view)
    views.sort(key=lambda view: (view.epoch, view.start.isoformat()))
    return views, [view.epoch for view in views]


def _as_items(payload):
    if isinstance(payload, dict):
        items = payload.get("items")
        return items if isinstance(items, list) else []
    if isinstance(payload, list):
        return payload
    return []


def levenshtein(left, right, limit):
    """Edit distance, or limit+1 once the distance is known to exceed limit."""
    if left == right:
        return 0
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    if len(left) > len(right):
        left, right = right, left
    prev = list(range(len(left) + 1))
    for col, rb in enumerate(right, 1):
        cur = [col]
        row_min = col
        for row, la in enumerate(left, 1):
            ins = cur[row - 1] + 1
            delete = prev[row] + 1
            sub = prev[row - 1] + (la != rb)
            best = ins if ins < delete else delete
            if sub < best:
                best = sub
            cur.append(best)
            if best < row_min:
                row_min = best
        if row_min > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _last_close(stated, token):
    if stated == token:
        return True
    if len(stated) >= 6 or len(token) >= 6:
        return levenshtein(stated, token, 1) <= 1
    return False


def _first_close(stated, token, initial_form):
    if stated == token:
        return True
    if initial_form and stated[:1] and token[:1] and stated[:1] == token[:1]:
        return True
    if len(stated) >= 4 or len(token) >= 4:
        return levenshtein(stated, token, 2) <= 2
    return False


def _name_match(view, first, last):
    if view.owner_declined:
        return False
    if view.has_other:
        for token_first, token_last, initial_form in view.pairs:
            if _last_close(last, token_last) and _first_close(first, token_first, initial_form):
                return True
    return last in view.words


def _stated_pair(name):
    parts = re.findall(r"[a-z]+", (name or "").lower())
    if len(parts) < 2:
        return None
    return parts[0], parts[-1]


def _zone(raw):
    text = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if not text:
        return ET
    name = _TZS.get(text)
    if not name:
        return None
    return ZoneInfo(name)


def _parse_clock(text):
    if not isinstance(text, str):
        return None
    match = _TIME_RE.match(text.strip())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or "").lower()
    if minute > 59:
        return None
    if ampm:
        if hour < 1 or hour > 12:
            return None
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    else:
        if match.group(2) is None or hour > 23:
            return None
    return hour, minute


def _parse_when(data):
    date = data.get("meeting_date")
    clock = data.get("meeting_time")
    if not isinstance(date, str):
        return None
    date_match = _DATE_RE.match(date.strip())
    parsed_clock = _parse_clock(clock)
    zone = _zone(data.get("meeting_tz") if isinstance(data.get("meeting_tz"), str) else "")
    if date_match is None or parsed_clock is None or zone is None:
        return None
    year, month, day = (int(part) for part in date_match.groups())
    hour, minute = parsed_clock
    try:
        return datetime(year, month, day, hour, minute, tzinfo=zone)
    except ValueError:
        return None


def format_et(when):
    local = when.astimezone(ET)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return "%s, %s %d at %d:%02d %s ET" % (
        _WEEKDAYS[local.weekday()],
        _MONTHS[local.month - 1],
        local.day,
        hour,
        local.minute,
        suffix,
    )


def _phone_key(raw):
    text = str(raw or "")
    digits = re.sub(r"\D", "", text)
    core = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    if len(core) == 10 and core[0] not in "01" and core[3] not in "01":
        return "+1" + core
    if digits:
        return digits
    return re.sub(r"\s+", "", text).lower()


def _header(headers, name):
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    return (getter(name) or "").strip()


def _token_from_headers(headers):
    specific = _header(headers, "X-Meeting-Check-Token")
    if specific:
        return specific
    auth = _header(headers, "Authorization")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _authorized(headers):
    got = _token_from_headers(headers)
    expected = env("MEETING_CHECK_TOKEN")
    if not got or not expected:
        return False
    try:
        left = got.encode("utf-8")
        right = expected.encode("utf-8")
    except Exception:
        return False
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def allow_ip(ip, now):
    """Own 60/minute bucket. Does not touch the caller-context limiter."""
    window = IP_WINDOW_S
    with _ip_lock:
        buf = [stamp for stamp in _ip_hits.get(ip, ()) if now - stamp < window]
        if len(buf) >= IP_LIMIT:
            _ip_hits[ip] = buf
            return False
        buf.append(now)
        _ip_hits[ip] = buf
        if len(_ip_hits) > 2000:
            _ip_hits.clear()
            _ip_hits[ip] = buf
        return True


def _reset_ip():
    with _ip_lock:
        _ip_hits.clear()


class Checker(object):
    def __init__(self, client=None):
        self.client = client if client is not None else GoogleCalendar()
        self._lock = threading.Lock()
        self._views = []
        self._epochs = []
        self._fetched_at = None
        self._calls = {}
        self._phones = {}
        self._global = []
        self._alert_day = ""
        self._alert_hook = None
        self._stop = threading.Event()
        self._thread = None
        self._db_lock = threading.Lock()

    def set_alert(self, hook):
        self._alert_hook = hook

    def set_snapshot(self, payload, fetched_at):
        views, epochs = _build(_as_items(payload))
        with self._lock:
            self._views = views
            self._epochs = epochs
            self._fetched_at = fetched_at

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="meeting-check-sync", daemon=True,
            )
            self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._sync(time.time(), SYNC_TIMEOUT_S)
            except Exception as exc:
                log.error("meeting-check error: %s", type(exc).__name__)
            self._stop.wait(sync_interval())

    def _sync(self, now, timeout):
        start = datetime.fromtimestamp(now - PAST_S, timezone.utc)
        end = datetime.fromtimestamp(now + FUTURE_S, timezone.utc)
        items = self.client.list_events(start, end, timeout)
        views, epochs = _build(items)
        with self._lock:
            self._views = views
            self._epochs = epochs
            self._fetched_at = now

    def _over_limit(self, call_id, phone, now):
        key = _phone_key(phone)
        with self._lock:
            self._prune(now)
            call_n = self._calls.get(call_id, 0) if call_id else 0
            phone_n = len(self._phones.get(key, ()))
            global_n = len(self._global)
            if (call_id and call_n >= CALL_CAP) or phone_n >= PHONE_CAP or global_n >= GLOBAL_CAP:
                return True
            if call_id:
                self._calls[call_id] = call_n + 1
            self._phones.setdefault(key, []).append(now)
            self._global.append(now)
            return False

    def _prune(self, now):
        self._global = [stamp for stamp in self._global if now - stamp < HOUR_S]
        empty = []
        for key, buf in self._phones.items():
            kept = [stamp for stamp in buf if now - stamp < HOUR_S]
            if kept:
                self._phones[key] = kept
            else:
                empty.append(key)
        for key in empty:
            del self._phones[key]

    def _note_auth(self, now):
        log.error("meeting-check: calendar auth failing")
        day = datetime.fromtimestamp(now, ET).date().isoformat()
        with self._lock:
            if self._alert_day == day:
                return
            self._alert_day = day
            hook = self._alert_hook
        if hook is None:
            return
        try:
            hook()
        except Exception as exc:
            log.error("meeting-check error: %s", type(exc).__name__)

    def _views_for(self, stated, now):
        with self._lock:
            fetched = self._fetched_at
            views = self._views
            epochs = self._epochs
        fresh = fetched is not None and (now - fetched) <= STALE_S and bool(views)
        if fresh:
            return views, epochs
        start = stated.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        items = self.client.list_events(start, end, LIVE_TIMEOUT_S)
        return _build(items)

    def _best(self, views, epochs, stated):
        stated_epoch = int(stated.when.timestamp())
        lo = stated_epoch - WINDOW_S
        hi = stated_epoch + WINDOW_S
        left = bisect.bisect_left(epochs, lo)
        right = bisect.bisect_right(epochs, hi)
        best = None
        best_delta = None
        for view in views[left:right]:
            if not _name_match(view, stated.first, stated.last):
                continue
            delta = abs(view.epoch - stated_epoch)
            if best is None or delta < best_delta or (delta == best_delta and view.epoch < best.epoch):
                best = view
                best_delta = delta
        return best

    def evaluate(self, data, now):
        """Return the 200 payload. `data` is the parsed JSON object."""
        matched, text, _call_id, _name, _start = self._decide(data, now)
        if matched:
            return {"match": True, "meeting_time": text}
        return {"match": False}

    def _decide(self, data, now):
        call_id = ""
        full_name = ""
        stated_start = ""
        if isinstance(data, dict):
            raw_id = data.get("call_id")
            if isinstance(raw_id, str):
                call_id = raw_id[:CALL_ID_CLIP]
            raw_name = data.get("caller_name")
            if isinstance(raw_name, str):
                full_name = raw_name
        stated_name = full_name[:NAME_CLIP]
        disabled = env("MEETING_CHECK_DISABLED") == "1"
        stated = _parse_when(data) if isinstance(data, dict) else None
        pair = _stated_pair(full_name) if full_name else None
        if stated is not None:
            stated_start = stated.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        phone = ""
        if isinstance(data, dict) and isinstance(data.get("phone_number"), str):
            phone = data.get("phone_number")
        if disabled or stated is None or pair is None:
            return False, "", call_id, stated_name, stated_start
        if self._over_limit(call_id.strip(), phone, now):
            return False, "", call_id, stated_name, stated_start
        holder = _Parsed(stated, pair[0], pair[1])
        try:
            views, epochs = self._views_for(stated, now)
            best = self._best(views, epochs, holder)
        except CalendarAuthError:
            self._note_auth(now)
            return False, "", call_id, stated_name, stated_start
        except AssertionError:
            raise
        except Exception as exc:
            log.error("meeting-check error: %s", type(exc).__name__)
            return False, "", call_id, stated_name, stated_start
        if best is None:
            return False, "", call_id, stated_name, stated_start
        return True, format_et(best.start), call_id, stated_name, stated_start

    def _record(self, call_id, now, stated_name, stated_start, matched):
        path = _state_dir() / "meeting_attempts.sqlite"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._db_lock:
                conn = sqlite3.connect(str(path), timeout=1.0)
                try:
                    os.chmod(path, 0o600)
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS attempts ("
                        "call_id TEXT, ts REAL, stated_name TEXT, stated_start TEXT, result INTEGER)"
                    )
                    conn.execute("DELETE FROM attempts WHERE ts < ?", (now - PURGE_S,))
                    conn.execute(
                        "INSERT INTO attempts (call_id, ts, stated_name, stated_start, result) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            (call_id or "")[:CALL_ID_CLIP],
                            float(now),
                            (stated_name or "")[:NAME_CLIP],
                            stated_start or "",
                            1 if matched else 0,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
                os.chmod(path, 0o600)
        except (OSError, sqlite3.Error) as exc:
            log.error("meeting-check error: %s", type(exc).__name__)

    def http_response(self, headers, body, ip, now):
        if not allow_ip(ip or "", now):
            return 429, {"ok": False, "error": "rate_limited"}
        if not _authorized(headers):
            return 401, {"ok": False, "error": "unauthorized"}
        started = time.perf_counter()
        data = _parse_body(body)
        matched, text, call_id, stated_name, stated_start = self._decide(data, now)
        self._record(call_id, now, stated_name, stated_start, matched)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_result(call_id, matched, elapsed_ms)
        if matched:
            return 200, {"match": True, "meeting_time": text}
        return 200, {"match": False}


class _Parsed(object):
    __slots__ = ("when", "first", "last")

    def __init__(self, when, first, last):
        self.when = when
        self.first = first
        self.last = last


def _parse_body(body):
    if body is None:
        return None
    if isinstance(body, str):
        raw = body.encode("utf-8")
    else:
        raw = body
    if not isinstance(raw, (bytes, bytearray)):
        return None
    if len(raw) > BODY_MAX:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def get_checker():
    global _checker
    with _guard:
        if _checker is None:
            _checker = Checker()
        return _checker


def reset_state(client=None):
    """Test hook. Stops a prior sync thread and installs a fresh checker."""
    global _checker
    with _guard:
        old = _checker
        if client is None:
            client = _Boom()
        _checker = Checker(client=client)
    if old is not None:
        old.stop()
    _reset_ip()
    return _checker


def set_alert(hook):
    get_checker().set_alert(hook)


def set_snapshot(payload, fetched_at):
    get_checker().set_snapshot(payload, fetched_at)


def start_background():
    get_checker().start()


def evaluate(data, now):
    return get_checker().evaluate(data, now)


def http_response(headers, body, ip, now):
    return get_checker().http_response(headers, body, ip, now)
