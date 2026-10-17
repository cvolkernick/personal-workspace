"""Mid-call meeting check for the Bland inbound line (#1230).

POST /bland/meeting-check answers one question for Alexandra: does this
caller have a meeting with Chris at the time they state. The response is
{"match": false} or {"match": true, "meeting_time": "Monday, October 19 at
3:00 PM ET"}. Nothing else from the calendar leaves this module.

The calendar read is optional. With the MEETING_GCAL_* env keys unset there is
no client, no snapshot thread and no network call, and every check answers
{"match": false}. A fake client is injected in tests; no test touches Google.

Stdlib only. Never log a name, a number, a stated time, an email or a token.
Errors log the exception class only.
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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("bland-relay")

ET = ZoneInfo("America/New_York")
ROUTE = "/bland/meeting-check"
MAX_BODY = 2 * 1024
MATCH_WINDOW_S = 15 * 60
STALE_S = 600.0
LIVE_TIMEOUT_S = 0.5
SYNC_BACK_S = 3600
SYNC_AHEAD_S = 45 * 86400
MAX_PAGES = 10
IP_LIMIT = 60
IP_WINDOW_S = 60.0
PER_CALL_MAX = 3
PER_NUMBER_MAX = 6
OVERALL_MAX = 40
LIMIT_WINDOW_S = 3600.0
ATTEMPT_KEEP_S = 48 * 3600
NAME_CLIP = 60
ALERT_EVERY_S = 86400.0

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/calendar/v3/calendars/"
# Fixed field mask. No description, location, conference data or links.
FIELDS = (
    "nextPageToken,items(id,status,summary,start,end,creator/email,organizer/email,"
    "attendees(email,displayName,self,responseStatus))"
)

NO_MATCH = {"match": False}

ZONES = {
    "eastern": ZoneInfo("America/New_York"),
    "central": ZoneInfo("America/Chicago"),
    "mountain": ZoneInfo("America/Denver"),
    "pacific": ZoneInfo("America/Los_Angeles"),
}
ZONE_ALIASES = {
    "et": "eastern", "est": "eastern", "edt": "eastern", "eastern": "eastern",
    "ct": "central", "cst": "central", "cdt": "central", "central": "central",
    "mt": "mountain", "mst": "mountain", "mdt": "mountain", "mountain": "mountain",
    "pt": "pacific", "pst": "pacific", "pdt": "pacific", "pacific": "pacific",
}
ZONE_NOISE = frozenset({"standard", "daylight", "time", "us", "usa", "the"})
BLANK_TZ = frozenset({"", "none", "null", "na", "n/a", "unknown", "undefined"})
HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "prof"})
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
SUMMARY_STOP = frozenset({
    "meeting", "meet", "call", "with", "and", "the", "for", "sync", "intro", "chat",
    "visit", "lunch", "coffee", "demo", "review", "check", "booking", "booked",
    "appointment", "discussion", "zoom", "video", "phone", "talk", "event",
})
GENERIC_LOCALS = frozenset({
    "info", "admin", "support", "contact", "hello", "hi", "noreply", "bookings",
    "booking", "team", "sales", "office", "calendar", "mail", "email", "reply",
})
NON_PERSON_DOMAINS = ("calendar.google.com",)

Event = namedtuple("Event", "start end pairs words")


def env(name):
    return (os.environ.get(name) or "").strip()


def disabled():
    return env("MEETING_CHECK_DISABLED") == "1"


# ---------------------------------------------------------------- text helpers

def _fold(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace("'", "").replace("\u2019", "")


def _words(text):
    return re.findall(r"[a-z]+(?:-[a-z]+)*", _fold(text))


def _person_words(text):
    words = _words(text)
    while words and words[0] in HONORIFICS:
        words = words[1:]
    while words and words[-1] in SUFFIXES:
        words = words[:-1]
    return words


def _lev(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _sound(word):
    """Rough consonant skeleton so Zefir and Zephyr, or Jordan and Jordyn, line up."""
    w = re.sub(r"[^a-z]", "", _fold(word))
    for old, new in (("ph", "f"), ("ck", "k"), ("gh", "g"), ("kn", "n"), ("wr", "r"), ("c", "k"),
                     ("q", "k"), ("z", "s"), ("x", "ks"), ("y", "i"), ("w", "u")):
        w = w.replace(old, new)
    if not w:
        return ""
    out = [w[0]]
    for ch in w[1:]:
        if ch in "aeiou" or ch == out[-1]:
            continue
        out.append(ch)
    return "".join(out)


def _last_ok(stated, known):
    if stated == known:
        return True
    return min(len(stated), len(known)) >= 6 and _lev(stated, known, 1) <= 1


def _first_ok(stated, known, known_is_initial):
    if known_is_initial:
        return stated[:1] == known[:1]
    if stated == known:
        return True
    if min(len(stated), len(known)) >= 4:
        return _lev(stated, known, 2) <= 2 or (len(_sound(stated)) >= 2 and _sound(stated) == _sound(known))
    return False


def _name_pairs(display_name, email):
    pairs = set()
    words = _person_words(display_name)
    if len(words) >= 2:
        pairs.add((words[0], words[-1], len(words[0]) == 1))
    local = _fold(str(email or "").split("@", 1)[0])
    if local and local not in GENERIC_LOCALS:
        parts = [p for p in re.split(r"[._+\-\d]+", local) if re.fullmatch(r"[a-z]+", p)]
        if len(parts) >= 2:
            pairs.add((parts[0], parts[-1], len(parts[0]) == 1))
        elif len(parts) == 1 and len(parts[0]) >= 4:
            pairs.add((parts[0][0], parts[0][1:], True))
    out = set()
    for first, last, initial in pairs:
        out.add((first, last, initial))
        if "-" in last:
            out.add((first, last.replace("-", ""), initial))
            for piece in last.split("-"):
                if piece:
                    out.add((first, piece, initial))
    return out


# ---------------------------------------------------------------- stated time

def _zone(raw):
    text = re.sub(r"[^a-z/ ]", "", str(raw or "").lower()).strip()
    if "{{" in str(raw or "") or text in BLANK_TZ:
        return ZONES["eastern"]
    words = [w for w in text.split() if w not in ZONE_NOISE]
    key = ZONE_ALIASES.get(" ".join(words)) or ZONE_ALIASES.get(words[0] if words else "")
    return ZONES.get(key) if key else None


def _clock(raw):
    text = str(raw or "").strip().lower().replace(".", "")
    if text == "noon":
        return 12, 0
    if text == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])m?", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not 1 <= hour <= 12 or minute > 59:
            return None
        return (hour % 12) + (12 if m.group(3) == "p" else 0), minute
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
        return int(m.group(1)), int(m.group(2))
    return None


def parse_stated(date_s, time_s, tz_s):
    """-> aware UTC datetime or None."""
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", str(date_s or ""))
    clock = _clock(time_s)
    zone = _zone(tz_s)
    if not m or clock is None or zone is None:
        return None
    try:
        local = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), clock[0], clock[1], tzinfo=zone)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def spoken_time(epoch):
    et = datetime.fromtimestamp(epoch, timezone.utc).astimezone(ET)
    hour = et.hour % 12 or 12
    return "%s, %s %d at %d:%02d %s ET" % (
        et.strftime("%A"), et.strftime("%B"), et.day, hour, et.minute, "PM" if et.hour >= 12 else "AM",
    )


def _epoch(value):
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
        return None
    return parsed.timestamp()


# ---------------------------------------------------------------- calendar

class AuthError(Exception):
    """invalid_grant, or a 401/403 from Google."""


class CalendarError(Exception):
    """Any other calendar failure. Carries no response text."""


def _rfc3339(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GcalClient:
    """Read-only Google Calendar client (scope calendar.events.readonly).

    The refresh token and client secret live only in the relay env. `opener`
    is a callable like urllib.request.urlopen so tests never touch the network.
    """

    def __init__(self, client_id, client_secret, refresh_token, calendar_id, opener=None, clock=time.time):
        self._id = client_id
        self._secret = client_secret
        self._refresh = refresh_token
        self._calendar = calendar_id
        self._opener = opener
        self._clock = clock
        self._access = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, opener=None):
        keys = ("MEETING_GCAL_CLIENT_ID", "MEETING_GCAL_CLIENT_SECRET",
                "MEETING_GCAL_REFRESH_TOKEN", "MEETING_GCAL_CALENDAR_ID")
        vals = [env(k) for k in keys]
        if not all(vals):
            return None
        return cls(*vals, opener=opener)

    def _open(self, req, timeout):
        opener = self._opener or urllib.request.urlopen
        return opener(req, timeout=timeout)

    def _access_token(self, timeout):
        with self._lock:
            now = self._clock()
            if self._access and now < self._expires - 60:
                return self._access
            body = urllib.parse.urlencode({
                "client_id": self._id, "client_secret": self._secret,
                "refresh_token": self._refresh, "grant_type": "refresh_token",
            }).encode()
            req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                with self._open(req, timeout) as res:
                    doc = json.loads(res.read(65536).decode("utf-8"))
            except urllib.error.HTTPError as exc:
                reason = ""
                try:
                    reason = str(json.loads(exc.read(2048).decode("utf-8")).get("error") or "")
                except Exception:  # noqa: BLE001
                    reason = ""
                if reason in ("invalid_grant", "invalid_client", "unauthorized_client") or exc.code in (401, 403):
                    raise AuthError("token refresh rejected") from None
                raise CalendarError("token http") from None
            except Exception as exc:  # noqa: BLE001
                raise CalendarError(type(exc).__name__) from None
            token = doc.get("access_token") if isinstance(doc, dict) else None
            if not isinstance(token, str) or not token:
                raise CalendarError("token shape")
            try:
                ttl = float(doc.get("expires_in") or 0)
            except (TypeError, ValueError):
                ttl = 0.0
            self._access, self._expires = token, now + ttl
            return token

    def events_list(self, time_min, time_max, timeout=5.0, page_token=None, max_results=250):
        token = self._access_token(timeout)
        query = {
            "timeMin": _rfc3339(time_min), "timeMax": _rfc3339(time_max),
            "singleEvents": "true", "orderBy": "startTime",
            "maxResults": str(int(max_results)), "fields": FIELDS,
        }
        if page_token:
            query["pageToken"] = page_token
        url = API_BASE + urllib.parse.quote(self._calendar, safe="@") + "/events?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        try:
            with self._open(req, timeout) as res:
                doc = json.loads(res.read(4 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                with self._lock:
                    self._access, self._expires = "", 0.0
                raise AuthError("calendar rejected") from None
            raise CalendarError("calendar http") from None
        except Exception as exc:  # noqa: BLE001
            raise CalendarError(type(exc).__name__) from None
        if not isinstance(doc, dict):
            raise CalendarError("calendar shape")
        return doc


def _self_emails():
    out = {e.strip().lower() for e in re.split(r"[,;\s]+", env("MEETING_SELF_EMAILS")) if "@" in e}
    cal = env("MEETING_GCAL_CALENDAR_ID").lower()
    if "@" in cal:
        out.add(cal)
    return out


def derive_event(item, self_emails):
    """events.list item -> Event of in-memory tuples, or None when it cannot be a candidate."""
    if not isinstance(item, dict) or item.get("status") == "cancelled":
        return None
    start_raw = item.get("start") if isinstance(item.get("start"), dict) else {}
    end_raw = item.get("end") if isinstance(item.get("end"), dict) else {}
    if "dateTime" not in start_raw:  # all-day events carry "date"
        return None
    start = _epoch(start_raw.get("dateTime"))
    end = _epoch(end_raw.get("dateTime"))
    if start is None:
        return None
    attendees = item.get("attendees")
    if not isinstance(attendees, list) or not attendees:
        return None  # no guests: nobody but the owner
    mine = set(self_emails)
    for a in attendees:
        if isinstance(a, dict) and a.get("self") and a.get("email"):
            mine.add(str(a["email"]).lower())
    others, owner_words = [], set()
    for a in attendees:
        if not isinstance(a, dict):
            continue
        email = str(a.get("email") or "").lower()
        is_self = bool(a.get("self")) or email in mine
        if is_self:
            if a.get("responseStatus") == "declined":
                return None
            owner_words.update(_words(a.get("displayName")))
            continue
        if email.endswith(NON_PERSON_DOMAINS):
            continue
        others.append((a.get("displayName"), email))
    for key in ("organizer", "creator"):
        person = item.get(key) if isinstance(item.get(key), dict) else {}
        email = str(person.get("email") or "").lower()
        if email and email not in mine and not email.endswith(NON_PERSON_DOMAINS):
            others.append((None, email))
    if not others:
        return None
    pairs = set()
    for name, email in others:
        pairs |= _name_pairs(name, email)
    words = frozenset(
        w for w in _words(item.get("summary")) if len(w) >= 3 and w not in SUMMARY_STOP and w not in owner_words
    )
    return Event(start, end if end is not None else start, frozenset(pairs), words)


class Snapshot:
    def __init__(self, events, at):
        self.events = sorted(events, key=lambda e: e.start)
        self.starts = [e.start for e in self.events]
        self.at = at

    def near(self, instant):
        lo = bisect.bisect_left(self.starts, instant - MATCH_WINDOW_S)
        hi = bisect.bisect_right(self.starts, instant + MATCH_WINDOW_S)
        return self.events[lo:hi]


# ---------------------------------------------------------------- limits / record

class Limits:
    """Anti-guess caps. A denied check is not counted and never reaches the calendar."""

    def __init__(self, per_call=PER_CALL_MAX, per_number=PER_NUMBER_MAX, overall=OVERALL_MAX, window=LIMIT_WINDOW_S):
        self.per_call, self.per_number, self.overall, self.window = per_call, per_number, overall, window
        self._lock = threading.Lock()
        self._calls, self._numbers, self._all = {}, {}, []

    def allow(self, call_id, number, now):
        with self._lock:
            floor = now - self.window
            self._all = [t for t in self._all if t > floor]
            if len(self._all) >= self.overall:
                return False
            calls = [t for t in self._calls.get(call_id, ()) if t > floor]
            if len(calls) >= self.per_call:
                self._calls[call_id] = calls
                return False
            nums = [t for t in self._numbers.get(number, ()) if t > floor] if number else []
            if number and len(nums) >= self.per_number:
                self._numbers[number] = nums
                return False
            self._all.append(now)
            self._calls[call_id] = calls + [now]
            if number:
                self._numbers[number] = nums + [now]
            for table in (self._calls, self._numbers):
                if len(table) > 5000:
                    table.clear()
            return True


def _state_dir():
    override = env("BLAND_RELAY_STATE_DIR")
    return Path(override) if override else Path.home() / ".local" / "state" / "bland-relay"


class AttemptStore:
    """meeting_attempts.sqlite (0600). Written here, read by #1231, never by the logger."""

    def __init__(self, path=None, keep_s=ATTEMPT_KEEP_S):
        self.path = Path(path) if path else _state_dir() / "meeting_attempts.sqlite"
        self.keep_s = keep_s
        self._lock = threading.Lock()

    def record(self, call_id, now, name, start_iso, result):
        try:
            with self._lock:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.close(os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600))
                os.chmod(self.path, 0o600)
                db = sqlite3.connect(str(self.path), timeout=2)
                try:
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS attempts (call_id TEXT, ts REAL, stated_name TEXT, "
                        "stated_start TEXT, result INTEGER)"
                    )
                    db.execute("INSERT INTO attempts VALUES (?,?,?,?,?)",
                               (call_id, float(now), str(name or "")[:NAME_CLIP], start_iso or "", 1 if result else 0))
                    db.execute("DELETE FROM attempts WHERE ts < ?", (float(now) - self.keep_s,))
                    db.commit()
                finally:
                    db.close()
        except Exception as exc:  # noqa: BLE001 - class only, never the row
            log.error("meeting-check: attempt record failed: %s", type(exc).__name__)


# ---------------------------------------------------------------- checker

ALERT_SINK = None  # callable(title, detail); the relay wires it to alert_failure in main()


def _clean_id(value):
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(value or ""))[:64] or "-"


def _clip_name(value):
    return " ".join(str(value or "").split())[:NAME_CLIP]


class MeetingChecker:
    def __init__(self, client=None, limits=None, store=None, self_emails=(), sync_s=120.0, clock=time.time,
                 stale_s=STALE_S, live_timeout=LIVE_TIMEOUT_S):
        self.client = client
        self.limits = limits if limits is not None else Limits()
        self.store = store
        self.self_emails = {e.lower() for e in self_emails}
        self.sync_s = sync_s
        self.clock = clock
        self.stale_s = stale_s
        self.live_timeout = live_timeout
        self.snapshot = None
        self._last_alert = 0.0

    # calendar side
    def _auth_failing(self, now):
        log.error("meeting-check: calendar auth failing")
        if now - self._last_alert >= ALERT_EVERY_S:
            self._last_alert = now
            sink = ALERT_SINK
            if sink is not None:
                try:
                    sink("Meeting check: calendar auth failing", "calendar auth failing")
                except Exception as exc:  # noqa: BLE001
                    log.error("meeting-check: alert failed: %s", type(exc).__name__)

    def _events(self, doc):
        out = []
        for item in doc.get("items") or []:
            ev = derive_event(item, self.self_emails)
            if ev is not None:
                out.append(ev)
        return out

    def refresh(self, now=None):
        if self.client is None:
            return False
        now = self.clock() if now is None else now
        events, token = [], None
        try:
            for _ in range(MAX_PAGES):
                doc = self.client.events_list(now - SYNC_BACK_S, now + SYNC_AHEAD_S, timeout=10.0, page_token=token)
                events.extend(self._events(doc))
                token = doc.get("nextPageToken")
                if not token:
                    break
        except AuthError:
            self._auth_failing(now)
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("meeting-check: calendar sync failed: %s", type(exc).__name__)
            return False
        self.snapshot = Snapshot(events, now)
        return True

    def _candidates(self, instant, now):
        snap = self.snapshot
        if snap is not None and snap.events and now - snap.at <= self.stale_s:
            return snap.near(instant)
        if self.client is None:
            return []
        zone_day = datetime.fromtimestamp(instant, timezone.utc).astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0)
        lo = (zone_day - timedelta(hours=14)).timestamp()
        hi = (zone_day + timedelta(hours=38)).timestamp()
        try:
            doc = self.client.events_list(lo, hi, timeout=self.live_timeout, max_results=250)
        except AuthError:
            self._auth_failing(now)
            return []
        except Exception as exc:  # noqa: BLE001
            log.warning("meeting-check: live fetch failed: %s", type(exc).__name__)
            return []
        return Snapshot(self._events(doc), now).near(instant)

    # request side
    def _find(self, instant, name, now):
        words = _person_words(name)
        if len(words) < 2:
            return None
        first, last = words[0], words[-1]
        best = None
        for ev in self._candidates(instant, now):
            hit = last in ev.words or any(
                _last_ok(last, pl) and _first_ok(first, pf, init) for pf, pl, init in ev.pairs
            )
            if hit and (best is None or abs(ev.start - instant) < abs(best.start - instant)):
                best = ev
        return best

    def check(self, data, now=None):
        """data is the parsed request body. -> (response dict, call_id)."""
        now = self.clock() if now is None else now
        if not isinstance(data, dict):
            self.limits.allow("-", "", now)
            return dict(NO_MATCH), "-"
        call_id = _clean_id(data.get("call_id"))
        number = ""
        raw_number = data.get("phone_number")
        if isinstance(raw_number, str) and raw_number.strip() and "{{" not in raw_number:
            digits = re.sub(r"\D", "", raw_number)
            number = digits[-10:] if len(digits) >= 10 else digits
        name = data.get("caller_name") if isinstance(data.get("caller_name"), str) else ""
        stated = None
        try:
            if self.limits.allow(call_id, number, now):
                stated = parse_stated(
                    data.get("meeting_date") if isinstance(data.get("meeting_date"), str) else "",
                    data.get("meeting_time") if isinstance(data.get("meeting_time"), str) else "",
                    data.get("meeting_tz") if isinstance(data.get("meeting_tz"), str) else "",
                )
                event = self._find(stated.timestamp(), name, now) if stated is not None else None
                result = {"match": True, "meeting_time": spoken_time(event.start)} if event is not None else dict(NO_MATCH)
            else:
                result = dict(NO_MATCH)
        except Exception as exc:  # noqa: BLE001
            log.error("meeting-check: check failed: %s", type(exc).__name__)
            result = dict(NO_MATCH)
        if self.store is not None:
            start_iso = stated.strftime("%Y-%m-%dT%H:%M:%SZ") if stated is not None else ""
            self.store.record(call_id, now, _clip_name(name), start_iso, result.get("match") is True)
        return result, call_id


# ---------------------------------------------------------------- module state / http

_checker = None
_checker_lock = threading.Lock()
_hits = {}
_hits_lock = threading.Lock()


def get_checker():
    global _checker
    with _checker_lock:
        if _checker is None:
            try:
                sync_s = float(env("MEETING_SYNC_S") or 120)
            except ValueError:
                sync_s = 120.0
            _checker = MeetingChecker(
                client=GcalClient.from_env(), store=AttemptStore(), self_emails=_self_emails(),
                sync_s=max(30.0, min(sync_s, 3600.0)),
            )
        return _checker


def reset():
    global _checker
    with _checker_lock:
        _checker = None
    with _hits_lock:
        _hits.clear()


def allow_ip(ip, now):
    with _hits_lock:
        buf = [t for t in _hits.get(ip, ()) if now - t < IP_WINDOW_S]
        if len(buf) >= IP_LIMIT:
            _hits[ip] = buf
            return False
        buf.append(now)
        _hits[ip] = buf
        if len(_hits) > 2000:
            _hits.clear()
            _hits[ip] = buf
        return True


def _token_ok(got, expected):
    if not got or not expected:
        return False
    a, b = got.encode(), expected.encode()
    return len(a) == len(b) and hmac.compare_digest(a, b)


def _final(result):
    """Last guard: the body is exactly {match} or {match, meeting_time}."""
    if isinstance(result, dict) and result.get("match") is True and isinstance(result.get("meeting_time"), str):
        return {"match": True, "meeting_time": result["meeting_time"]}
    return dict(NO_MATCH)


def handle(got_token, read_body, ip, now=None, checker=None):
    """-> (status, body). 429 and 401 come before anything else; after auth it is always 200."""
    now = time.time() if now is None else now
    if not allow_ip(ip, now):
        return 429, {"ok": False, "error": "rate_limited"}
    if not _token_ok(got_token, env("MEETING_CHECK_TOKEN")):
        log.warning("meeting-check: bad/missing token (len %d)", len(got_token or ""))
        return 401, {"ok": False, "error": "unauthorized"}
    started = time.perf_counter()
    result, call_id = dict(NO_MATCH), "-"
    try:
        if not disabled():
            raw = read_body()
            data = None
            if raw is not None and len(raw) <= MAX_BODY:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    data = None
            result, call_id = (checker or get_checker()).check(data, now)
    except Exception as exc:  # noqa: BLE001
        log.error("meeting-check failed: %s", type(exc).__name__)
    result = _final(result)
    log.info("meeting-check: call_id=%s result=%s ms=%d", call_id, "true" if result["match"] else "false",
             int((time.perf_counter() - started) * 1000))
    return 200, result


def startup_line():
    """One startup line. Names of states only: no token, email or id."""
    return "meeting-check: token=%s calendar=%s disabled=%d" % (
        "set" if env("MEETING_CHECK_TOKEN") else "unset",
        "set" if GcalClient.from_env() is not None else "unset",
        1 if disabled() else 0,
    )


def start_sync():
    """Start the snapshot thread. -> thread, or None when disabled or no calendar credentials."""
    if disabled():
        return None
    checker = get_checker()
    if checker.client is None:
        return None

    def loop():
        checker.refresh()
        while True:
            time.sleep(checker.sync_s)
            checker.refresh()

    thread = threading.Thread(target=loop, name="meeting-check-sync", daemon=True)
    thread.start()
    return thread
