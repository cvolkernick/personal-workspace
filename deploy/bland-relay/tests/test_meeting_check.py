"""Meeting-check endpoint (#1230). Fake calendar, no network, no Bland API."""
import email.message
import inspect
import io
import json
import logging
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import bland_relay
import meeting_check

FIX = Path(__file__).resolve().parent / "fixtures" / "meeting_check"
TOKEN = "meeting-token-secret"
OTHER = "caller-context-test-token"
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc).timestamp()
WHEN = {
    "meeting_date": "2026-10-19",
    "meeting_time": "3:00 PM",
    "meeting_tz": "ET",
}
ET_TEXT = "Monday, October 19 at 3:00 PM ET"
SECRET_KEYS = (
    "MEETING_CHECK_TOKEN",
    "MEETING_GCAL_CLIENT_ID",
    "MEETING_GCAL_CLIENT_SECRET",
    "MEETING_GCAL_REFRESH_TOKEN",
    "MEETING_GCAL_CALENDAR_ID",
    "MEETING_SELF_EMAILS",
)
SETKEY_KEYS = SECRET_KEYS + ("MEETING_CHECK_DISABLED", "MEETING_SYNC_S")


class Fake(object):
    def __init__(self, items=None, error=None):
        self.calls = []
        self.items = list(items or [])
        self.error = error

    def list_events(self, time_min, time_max, timeout):
        self.calls.append((time_min, time_max, timeout))
        if self.error is not None:
            raise self.error
        return self.items


def load(name):
    return json.loads((FIX / name).read_text())


def assert_shape(payload):
    keys = set(payload)
    assert keys == {"match"} or keys == {"match", "meeting_time"}
    assert isinstance(payload["match"], bool)
    if payload["match"] is False:
        assert "meeting_time" not in payload
    else:
        assert isinstance(payload["meeting_time"], str)
        assert payload["meeting_time"].endswith(" ET")


_seq = {"n": 0}


def body_for(**overrides):
    """Each call gets its own phone and call id unless the test overrides them."""
    _seq["n"] += 1
    n = _seq["n"]
    data = {
        "call_id": "call-%d" % n,
        "phone_number": "+1904555%04d" % (n % 10000),
        "caller_name": "Jordan Marsh",
    }
    data.update(WHEN)
    data.update(overrides)
    return data


class _Resp(object):
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _filler():
    return {
        "id": "filler",
        "status": "confirmed",
        "summary": "Hold",
        "start": {"dateTime": "2026-11-02T11:00:00-05:00", "timeZone": "America/New_York"},
        "end": {"dateTime": "2026-11-02T11:30:00-05:00", "timeZone": "America/New_York"},
        "attendees": [
            {"email": "owner@example.test", "self": True, "responseStatus": "accepted"},
            {"email": "someone.else@example.test", "responseStatus": "accepted"},
        ],
    }


def ask(data=None, now=NOW, token=TOKEN, ip="203.0.113.10", raw=None, headers=None):
    if raw is None:
        raw = json.dumps(data if data is not None else body_for()).encode()
    if headers is None:
        headers = {"Authorization": "Bearer " + token} if token else {}
    status, payload = meeting_check.http_response(headers, raw, ip, now)
    if status == 200:
        assert_shape(payload)
    return status, payload


def arm(payload, fetched_at=NOW):
    meeting_check.set_snapshot(payload, fetched_at)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "MEETING_CHECK_TOKEN", "MEETING_CHECK_DISABLED", "MEETING_SYNC_S",
        "MEETING_SELF_EMAILS", "MEETING_GCAL_CLIENT_ID", "MEETING_GCAL_CLIENT_SECRET",
        "MEETING_GCAL_REFRESH_TOKEN", "MEETING_GCAL_CALENDAR_ID", "BLAND_RELAY_STATE_DIR",
        "CALLER_CONTEXT_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MEETING_CHECK_TOKEN", TOKEN)
    meeting_check.reset_state()
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call in test")
    )


def test_shapes_and_jordan_matches_jordyn():
    arm(load("jordyn_marsh.json"))
    status, payload = ask()
    assert status == 200
    assert payload == {"match": True, "meeting_time": ET_TEXT}
    assert "jordyn.marsh@example.test" not in json.dumps(payload)
    assert "Wheelbase" not in json.dumps(payload)
    for clock in ("3:00 PM", "03:00 PM", "3 PM", "15:00"):
        status, payload = ask(body_for(meeting_time=clock, call_id="clock-" + clock))
        assert payload["meeting_time"] == ET_TEXT


def test_initial_local_part_and_summary_last_name():
    arm(load("jmarsh.json"))
    status, payload = ask()
    assert payload == {"match": True, "meeting_time": ET_TEXT}
    arm(load("summary_marsh.json"))
    status, payload = ask(body_for(call_id="summary"))
    assert payload == {"match": True, "meeting_time": ET_TEXT}


def test_central_and_eastern_standard_use_the_offset():
    arm(load("jordyn_marsh.json"))
    status, payload = ask(body_for(meeting_time="2:00 PM", meeting_tz="CT", call_id="ct"))
    assert payload["meeting_time"] == ET_TEXT
    status, payload = ask(body_for(meeting_time="3:00 PM", meeting_tz="Eastern Standard", call_id="est"))
    assert payload["meeting_time"] == ET_TEXT
    status, payload = ask(body_for(meeting_time="3:00 PM", meeting_tz="Eastern", call_id="eastern"))
    assert payload["meeting_time"] == ET_TEXT
    status, payload = ask(body_for(meeting_time="2:00 PM", meeting_tz="ET", call_id="et-early"))
    assert payload == {"match": False}


def test_window_edges_and_closest_start():
    arm(load("jordyn_marsh.json"))
    assert ask(body_for(meeting_time="3:15 PM", call_id="plus15"))[1]["match"] is True
    assert ask(body_for(meeting_time="2:45 PM", call_id="minus15"))[1]["match"] is True
    assert ask(body_for(meeting_time="3:16 PM", call_id="plus16"))[1] == {"match": False}
    assert ask(body_for(meeting_time="2:44 PM", call_id="minus16"))[1] == {"match": False}
    assert ask(body_for(meeting_time="4:00 PM", call_id="plus1h"))[1] == {"match": False}
    assert ask(body_for(meeting_time="2:00 PM", call_id="minus1h"))[1] == {"match": False}
    assert ask(body_for(meeting_date="2026-10-20", call_id="nextday"))[1] == {"match": False}
    assert ask(body_for(meeting_date="2026-10-18", call_id="prevday"))[1] == {"match": False}
    later = load("jordyn_marsh.json")["items"][0]
    earlier = json.loads(json.dumps(later))
    earlier["id"] = "evt-early"
    earlier["start"] = {"dateTime": "2026-10-19T15:00:00-04:00", "timeZone": "America/Chicago"}
    later = json.loads(json.dumps(later))
    later["id"] = "evt-late"
    later["start"] = {"dateTime": "2026-10-19T15:10:00-04:00", "timeZone": "America/Chicago"}
    arm({"items": [earlier, later]})
    status, payload = ask(body_for(meeting_time="3:08 PM", call_id="closest"))
    assert payload == {"match": True, "meeting_time": "Monday, October 19 at 3:10 PM ET"}


def test_no_match_cases():
    arm(load("jordyn_marsh.json"))
    assert ask(body_for(caller_name="Jordan"))[1] == {"match": False}
    assert ask(body_for(caller_name="Marsh"))[1] == {"match": False}
    assert ask(body_for(caller_name=""))[1] == {"match": False}
    assert ask(body_for(caller_name="Alex Rivera", call_id="wrong-last"))[1] == {"match": False}
    bare = dict(WHEN)
    bare["phone_number"] = "+19045550100"
    assert ask(bare)[1] == {"match": False}
    assert ask({"phone_number": "+19045550100", "call_id": "num"})[1] == {"match": False}
    other = {
        "items": [{
            "id": "alex",
            "status": "confirmed",
            "summary": "Wheelbase weekly",
            "start": {"dateTime": "2026-10-19T15:00:00-04:00", "timeZone": "America/Chicago"},
            "end": {"dateTime": "2026-10-19T15:30:00-04:00", "timeZone": "America/Chicago"},
            "attendees": [
                {"email": "owner@example.test", "self": True, "responseStatus": "accepted"},
                {"email": "alex.rivera@example.test", "responseStatus": "accepted"},
            ],
        }]
    }
    arm(other)
    assert ask(body_for(call_id="other-person"))[1] == {"match": False}
    cancelled = load("jordyn_marsh.json")
    cancelled["items"][0]["status"] = "cancelled"
    assert meeting_check.derive(cancelled["items"][0]) is None
    arm({"items": [cancelled["items"][0], _filler()]})
    assert ask(body_for(call_id="cancelled"))[1] == {"match": False}
    allday = load("jordyn_marsh.json")
    allday["items"][0]["start"] = {"date": "2026-10-19"}
    allday["items"][0]["end"] = {"date": "2026-10-20"}
    assert meeting_check.derive(allday["items"][0]) is None
    arm({"items": [allday["items"][0], _filler()]})
    assert ask(body_for(call_id="allday"))[1] == {"match": False}


def test_owner_declined_stays_in_view_and_does_not_match(monkeypatch):
    item = load("jordyn_marsh.json")["items"][0]
    item["attendees"][0]["responseStatus"] = "declined"
    view = meeting_check.derive(item)
    assert view is not None and view.owner_declined is True
    arm({"items": [item]})
    assert ask(body_for(call_id="declined"))[1] == {"match": False}
    item["attendees"][0]["responseStatus"] = "tentative"
    arm({"items": [item]})
    assert ask(body_for(call_id="tentative"))[1]["match"] is True
    item["attendees"][1]["responseStatus"] = "declined"
    item["attendees"][0]["responseStatus"] = "accepted"
    arm({"items": [item]})
    assert ask(body_for(call_id="guest-declined"))[1]["match"] is True
    owner_only = {
        "id": "owner",
        "status": "confirmed",
        "summary": "Hold",
        "start": {"dateTime": "2026-10-19T15:00:00-04:00", "timeZone": "America/New_York"},
        "end": {"dateTime": "2026-10-19T15:30:00-04:00", "timeZone": "America/New_York"},
        "attendees": [{
            "email": "owner@example.test",
            "displayName": "Jordan Marsh",
            "self": True,
            "responseStatus": "accepted",
        }],
    }
    assert meeting_check.derive(owner_only).has_other is False
    arm({"items": [owner_only]})
    assert ask(body_for(call_id="owner-only"))[1] == {"match": False}
    sweet = dict(owner_only)
    sweet["summary"] = "Intro Marshmallow"
    arm({"items": [sweet]})
    assert ask(body_for(call_id="marshmallow"))[1] == {"match": False}
    booked = {
        "id": "booked",
        "status": "confirmed",
        "summary": "Wheelbase weekly",
        "start": {"dateTime": "2026-10-19T15:00:00-04:00", "timeZone": "America/New_York"},
        "end": {"dateTime": "2026-10-19T15:30:00-04:00", "timeZone": "America/New_York"},
        "creator": {"email": "owner@example.test"},
        "organizer": {"email": "jordyn.marsh@example.test"},
        "attendees": [{"email": "owner@example.test", "self": True, "responseStatus": "accepted"}],
    }
    arm({"items": [booked]})
    assert ask(body_for(call_id="organizer-email"))[1]["match"] is True
    monkeypatch.setenv("MEETING_SELF_EMAILS", "owner@example.test")
    masked = load("jordyn_marsh.json")["items"][0]
    masked["attendees"][0] = {
        "email": "owner@example.test",
        "responseStatus": "declined",
    }
    arm({"items": [masked]})
    assert ask(body_for(call_id="self-email"))[1] == {"match": False}


def test_phone_is_not_a_match_factor():
    arm(load("jordyn_marsh.json"))
    status, payload = ask(body_for(phone_number="+12125550199", call_id="spoof"))
    assert payload["match"] is True
    status, payload = ask(body_for(
        phone_number="+19045550100", caller_name="Alex Rivera", call_id="known-wrong",
    ))
    assert payload == {"match": False}


def test_three_edit_first_name_does_not_match():
    """Zefir vs Zephyr is 3 edits. The unit bar stays at 2. Kris vs Chris is 2 and matches."""
    item = load("jordyn_marsh.json")["items"][0]
    item["summary"] = "QA hold"
    item["attendees"][1] = {
        "email": "zephyr.quillfeather@example.test",
        "displayName": "Zephyr Quillfeather",
        "responseStatus": "accepted",
    }
    arm({"items": [item]})
    assert ask(body_for(caller_name="Zefir Quillfeather", call_id="zefir"))[1] == {"match": False}
    assert meeting_check.levenshtein("zefir", "zephyr", 3) == 3
    item["attendees"][1] = {
        "email": "kris.example@example.test",
        "displayName": "Kris Example",
        "responseStatus": "accepted",
    }
    item["summary"] = "QA hold"
    arm({"items": [item]})
    assert ask(body_for(caller_name="Chris Example", call_id="kris"))[1]["match"] is True


def test_auth_and_body_limit():
    arm(load("jordyn_marsh.json"))
    raw = json.dumps(body_for()).encode()
    assert ask(token="")[0] == 401
    assert ask(token="wrong-token")[0] == 401
    status, payload = meeting_check.http_response(
        {"Authorization": TOKEN}, raw, "203.0.113.11", NOW,
    )
    assert status == 401 and payload == {"ok": False, "error": "unauthorized"}
    status, payload = meeting_check.http_response(
        {"Authorization": "Bearer " + OTHER}, raw, "203.0.113.12", NOW,
    )
    assert status == 401
    status, payload = meeting_check.http_response(
        {"X-Meeting-Check-Token": TOKEN}, raw, "203.0.113.13", NOW,
    )
    assert status == 200 and payload["match"] is True
    assert ask(raw=b"{")[1] == {"match": False}
    assert ask(raw=b"[]")[1] == {"match": False}
    assert ask(raw=b"")[1] == {"match": False}
    assert ask(data={"caller_name": "Jordan Marsh"})[1] == {"match": False}
    padded = body_for(caller_name="Jordan Marsh " + ("A" * 2100))
    raw_over = json.dumps(padded).encode()
    assert len(raw_over) > 2048
    status, payload = ask(raw=raw_over, ip="203.0.113.14")
    assert payload == {"match": False}
    limited = "198.51.100.9"
    for _ in range(60):
        assert meeting_check.allow_ip(limited, NOW)
    assert meeting_check.allow_ip(limited, NOW) is False
    status, payload = ask(ip=limited, now=NOW)
    assert status == 429 and payload == {"ok": False, "error": "rate_limited"}
    reopened = "198.51.100.8"
    for _ in range(60):
        assert meeting_check.allow_ip(reopened, NOW)
    assert meeting_check.allow_ip(reopened, NOW + 60) is True


def test_kill_switch_skips_the_client_and_the_cap(monkeypatch):
    fake = Fake(items=load("jordyn_marsh.json")["items"])
    meeting_check.reset_state(client=fake)
    monkeypatch.setenv("MEETING_CHECK_DISABLED", "1")
    status, payload = ask(body_for(call_id="killed"))
    assert payload == {"match": False}
    assert fake.calls == []
    monkeypatch.setenv("MEETING_CHECK_DISABLED", "0")
    status, payload = ask(body_for(call_id="killed"))
    assert len(fake.calls) == 1


def test_missing_fields_do_not_burn_the_cap():
    fake = Fake(items=[])
    meeting_check.reset_state(client=fake)
    for _ in range(3):
        assert ask(body_for(caller_name="Jordan", call_id="same"))[1] == {"match": False}
    assert fake.calls == []
    assert ask(body_for(call_id="same"))[1] == {"match": False}
    assert len(fake.calls) == 1


def test_anti_guess_caps():
    fake = Fake(items=[])
    meeting_check.reset_state(client=fake)
    for n in range(3):
        assert ask(body_for(call_id="same", phone_number="+19045550123"))[1] == {"match": False}
    assert len(fake.calls) == 3
    assert ask(body_for(call_id="same", phone_number="+19045550123"))[1] == {"match": False}
    assert ask(body_for(call_id="same", phone_number="+19045550123"))[1] == {"match": False}
    assert len(fake.calls) == 3
    fake = Fake(items=[])
    meeting_check.reset_state(client=fake)
    phone = "+19045550123"
    for n in range(6):
        ask(body_for(call_id="p-%d" % n, phone_number="(%s) %s-%s" % (
            phone[2:5], phone[5:8], phone[8:],
        )))
    assert len(fake.calls) == 6
    ask(body_for(call_id="p-6", phone_number="+1 904 555 0123"))
    assert len(fake.calls) == 6
    fake = Fake(items=[])
    meeting_check.reset_state(client=fake)
    for n in range(4):
        ask(body_for(call_id="", phone_number="+1904555%04d" % n))
    assert len(fake.calls) == 4
    fake = Fake(items=[])
    meeting_check.reset_state(client=fake)
    for n in range(40):
        ask(body_for(call_id="g-%d" % n, phone_number="+1904555%04d" % n), ip="203.0.113.40")
    assert len(fake.calls) == 40
    ask(body_for(call_id="g-40", phone_number="+19045559999"), ip="203.0.113.40")
    assert len(fake.calls) == 40


def test_rejected_phone_check_does_not_consume_the_next_hour():
    fake = Fake(items=[])
    meeting_check.reset_state(client=fake)
    phone = "+19045550123"
    t0 = 1_800_000_000.0
    for n in range(6):
        ask(body_for(call_id="a-%d" % n, phone_number=phone), now=t0, ip="203.0.113.70")
    ask(body_for(call_id="a-block", phone_number=phone), now=t0 + 100, ip="203.0.113.70")
    assert len(fake.calls) == 6
    for n in range(6):
        ask(body_for(call_id="b-%d" % n, phone_number=phone), now=t0 + 3601, ip="203.0.113.70")
    assert len(fake.calls) == 12


def test_fresh_snapshot_makes_no_call_and_stale_makes_one():
    arm(load("jordyn_marsh.json"), NOW)
    assert ask()[1]["match"] is True
    arm(load("jordyn_marsh.json"), NOW - 600)
    assert ask(body_for(call_id="edge"))[1]["match"] is True
    fake = Fake(items=load("jordyn_marsh.json")["items"])
    meeting_check.reset_state(client=fake)
    meeting_check.set_snapshot(load("jordyn_marsh.json"), NOW - 601)
    status, payload = ask(body_for(call_id="stale"))
    assert payload["meeting_time"] == ET_TEXT
    assert len(fake.calls) == 1
    time_min, time_max, timeout = fake.calls[0]
    assert timeout == 0.5
    assert time_min.hour == 0 and time_min.minute == 0
    assert time_max - time_min == timedelta(days=1)
    assert time_min.tzinfo.key == "America/New_York"
    assert meeting_check.get_checker()._fetched_at == NOW - 601
    fake = Fake(items=load("jordyn_marsh.json")["items"])
    meeting_check.reset_state(client=fake)
    meeting_check.set_snapshot({"items": []}, NOW)
    ask(body_for(meeting_tz="CT", meeting_time="2:00 PM", call_id="empty-ct"))
    assert len(fake.calls) == 1
    time_min, time_max, timeout = fake.calls[0]
    assert timeout == 0.5
    assert time_min.tzinfo.key == "America/Chicago"
    assert time_min.hour == 0
    assert (time_max - time_min) == timedelta(days=1)


def test_calendar_errors_log_the_class_and_alert_once_per_et_day(caplog):
    caplog.set_level(logging.INFO, logger="bland-relay")
    hooks = []
    fake = Fake(error=meeting_check.CalendarAuthError("invalid_grant"))
    meeting_check.reset_state(client=fake)
    meeting_check.set_alert(lambda: hooks.append("alert"))
    day1 = datetime(2026, 10, 17, 23, 0, tzinfo=ET).timestamp()
    day2 = datetime(2026, 10, 18, 0, 30, tzinfo=ET).timestamp()
    assert ask(body_for(call_id="a1", phone_number="+19045551001"), now=day1)[1] == {"match": False}
    assert ask(body_for(call_id="a2", phone_number="+19045551002"), now=day1 + 10)[1] == {"match": False}
    assert hooks == ["alert"]
    assert ask(body_for(call_id="a3", phone_number="+19045551003"), now=day2)[1] == {"match": False}
    assert hooks == ["alert", "alert"]
    assert "meeting-check: calendar auth failing" in caplog.text
    assert "invalid_grant" not in caplog.text
    fake = Fake(error=RuntimeError("SECRET-NAME"))
    meeting_check.reset_state(client=fake)
    hooks[:] = []
    meeting_check.set_alert(lambda: hooks.append("alert"))
    caplog.clear()
    assert ask(body_for(call_id="boom", phone_number="+19045551004"))[1] == {"match": False}
    assert hooks == []
    assert "RuntimeError" in caplog.text
    assert "SECRET-NAME" not in caplog.text
    fake = Fake(error=socket.timeout("slow-secret"))
    meeting_check.reset_state(client=fake)
    caplog.clear()
    assert ask(body_for(call_id="slow", phone_number="+19045551005"))[1] == {"match": False}
    assert "slow-secret" not in caplog.text


def test_privacy_log_and_response(caplog):
    caplog.set_level(logging.INFO, logger="bland-relay")
    poisons = [
        "QX-TITLE-07",
        "QX-LOC-07",
        "https://meet.example.test/qx-07",
        "qx-guest-07@example.test",
        "+19995550007",
        "QX-DESC-07",
        "Nora Pembleton",
        "04:15",
        "2026-10-21",
        TOKEN,
    ]
    items = []
    for n in range(25):
        items.append({
            "id": "qx-%02d" % n,
            "status": "confirmed",
            "summary": "QX-TITLE-%02d" % n,
            "location": "QX-LOC-%02d" % n,
            "description": "QX-DESC-%02d call +1999555%04d" % (n, n),
            "hangoutLink": "https://meet.example.test/qx-%02d" % n,
            "start": {"dateTime": "2026-10-21T09:%02d:00-04:00" % (n % 60), "timeZone": "America/Chicago"},
            "end": {"dateTime": "2026-10-21T09:%02d:00-04:00" % (n % 60), "timeZone": "America/Chicago"},
            "attendees": [
                {"email": "owner@example.test", "self": True, "responseStatus": "accepted"},
                {
                    "email": "qx-guest-%02d@example.test" % n,
                    "displayName": "QX-TITLE-%02d Person" % n,
                    "responseStatus": "accepted",
                },
            ],
        })
    arm({"items": items})
    status, payload = ask(body_for(
        caller_name="Nora Pembleton",
        meeting_date="2026-10-21",
        meeting_time="04:15 PM",
        call_id="call-privacy",
        phone_number="+19995550099",
    ))
    assert status == 200 and payload == {"match": False}
    rendered = json.dumps(payload) + "\n" + caplog.text
    for poison in poisons:
        assert poison not in rendered
    assert "meeting-check call_id=call-privacy result=false" in caplog.text


def test_attempts_are_private_and_purged(caplog):
    caplog.set_level(logging.INFO, logger="bland-relay")
    src = inspect.getsource(meeting_check.log_result)
    assert "sqlite" not in src.lower()
    assert "stated" not in src.lower()
    arm(load("jordyn_marsh.json"))
    long_name = "Jonathan " + ("Middle " * 8) + "Quillfeather"
    assert len(long_name) > 60
    ask(body_for(caller_name=long_name, call_id="clip-me", phone_number="+19045550100"), now=1_000.0)
    path = Path(meeting_check.env("BLAND_RELAY_STATE_DIR")) / "meeting_attempts.sqlite"
    assert path.stat().st_mode & 0o777 == 0o600
    conn = sqlite3.connect(str(path))
    try:
        cols = [row[1] for row in conn.execute("pragma table_info(attempts)")]
        assert cols == ["call_id", "ts", "stated_name", "stated_start", "result"]
        rows = conn.execute(
            "select call_id, stated_name, stated_start, result from attempts"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("clip-me", long_name[:60], "2026-10-19T19:00:00Z", 0)]
    assert "+19045550100" not in rows[0][1]
    ask(body_for(call_id="later"), now=1_000.0 + 49 * 3600)
    conn = sqlite3.connect(str(path))
    try:
        left = conn.execute("select call_id from attempts").fetchall()
    finally:
        conn.close()
    assert left == [("later",)]
    assert "Jonathan" not in caplog.text
    assert "sqlite" not in caplog.text

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkey_connect = meeting_check.sqlite3.connect
    meeting_check.sqlite3.connect = boom
    try:
        caplog.clear()
        status, payload = ask(body_for(call_id="io-err"))
        assert payload["match"] is True
        assert "OSError" in caplog.text
        assert "disk full" not in caplog.text
    finally:
        meeting_check.sqlite3.connect = monkey_connect


def test_sync_bounds_background_and_startup(monkeypatch):
    assert "meeting-check-sync" not in [thread.name for thread in threading.enumerate()]
    fake = Fake(items=[])
    meeting_check.reset_state(client=fake)
    meeting_check.get_checker()._sync(NOW, 10)
    time_min, time_max, timeout = fake.calls[0]
    assert timeout == 10
    assert abs(time_min.timestamp() - (NOW - 3600)) < 1
    assert abs(time_max.timestamp() - (NOW + 45 * 86400)) < 1
    assert meeting_check.sync_interval() == 120
    monkeypatch.setenv("MEETING_SYNC_S", "0")
    assert meeting_check.sync_interval() == 120
    monkeypatch.setenv("MEETING_SYNC_S", "nope")
    assert meeting_check.sync_interval() == 120
    monkeypatch.setenv("MEETING_SYNC_S", "30")
    assert meeting_check.sync_interval() == 30
    meeting_check.start_background()
    meeting_check.start_background()
    deadline = time.time() + 2
    while len(fake.calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    alive = [
        thread for thread in threading.enumerate()
        if thread.name == "meeting-check-sync" and thread.is_alive()
    ]
    assert len(alive) == 1
    meeting_check.reset_state()
    alive = [
        thread for thread in threading.enumerate()
        if thread.name == "meeting-check-sync" and thread.is_alive()
    ]
    assert alive == []
    assert meeting_check.startup_line() == "meeting-check: token=set"
    monkeypatch.setenv("MEETING_GCAL_CALENDAR_ID", "secret-calendar-id")
    line = meeting_check.startup_line()
    assert line == "meeting-check: token=set"
    assert "secret-calendar-id" not in line
    assert TOKEN not in line
    monkeypatch.delenv("MEETING_CHECK_TOKEN", raising=False)
    assert meeting_check.startup_line() == "meeting-check: token=unset"
    src = inspect.getsource(bland_relay.main)
    assert src.index("start_background") < src.index("serve_forever")
    assert src.index("meeting_check.startup_line") < src.index("serve_forever")
    assert src.index("caller_context_startup_line") < src.index("serve_forever")
    assert "api.bland.ai" not in src
    alert = inspect.getsource(bland_relay._meeting_alert)
    assert "meeting-check" in alert
    assert "calendar auth failing" in alert
    assert "api.bland.ai" not in Path(meeting_check.__file__).read_text()
    readme = Path(bland_relay.__file__).resolve().parent.joinpath("README.md").read_text()
    assert "Check Meeting" not in readme
    assert "api.bland.ai" not in readme


def test_google_client_masks_fields_and_caches_the_token(monkeypatch):
    monkeypatch.setenv("MEETING_GCAL_CLIENT_ID", "client-id")
    monkeypatch.setenv("MEETING_GCAL_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("MEETING_GCAL_REFRESH_TOKEN", "refresh-token-value")
    monkeypatch.setenv("MEETING_GCAL_CALENDAR_ID", "cal id/x")
    seen = []

    def opener(req, timeout=None):
        seen.append(req.full_url)
        if req.full_url.startswith(meeting_check.TOKEN_URL):
            return _Resp({"access_token": "ya29.test", "expires_in": 3600})
        return _Resp({"items": [{"id": "a"}], "nextPageToken": "next-secret"})

    cal = meeting_check.GoogleCalendar(opener=opener)
    start = datetime(2026, 10, 19, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    assert cal.list_events(start, end, 0.5) == [{"id": "a"}]
    assert cal.list_events(start, end, 0.5) == [{"id": "a"}]
    assert sum(1 for url in seen if url.startswith(meeting_check.TOKEN_URL)) == 1
    event_urls = [url for url in seen if "calendar/v3" in url]
    assert len(event_urls) == 2
    for word in ("description", "location", "conferenceData", "htmlLink", "hangoutLink", "next-secret"):
        assert word not in event_urls[0]
    assert meeting_check.FIELDS in event_urls[0]
    assert "singleEvents=true" in event_urls[0]
    assert "orderBy=startTime" in event_urls[0]
    assert "maxResults=250" in event_urls[0]
    assert "cal%20id%2Fx" in event_urls[0]
    assert "client-secret-value" not in event_urls[0]
    assert "events_list_url" in inspect.getsource(meeting_check.GoogleCalendar.list_events)
    short = []

    def short_opener(req, timeout=None):
        short.append(req.full_url)
        if req.full_url.startswith(meeting_check.TOKEN_URL):
            return _Resp({"access_token": "ya29.test", "expires_in": 30})
        return _Resp({"items": []})

    cal = meeting_check.GoogleCalendar(opener=short_opener)
    cal.list_events(start, end, 1)
    cal.list_events(start, end, 1)
    assert sum(1 for url in short if url.startswith(meeting_check.TOKEN_URL)) == 2
    monkeypatch.delenv("MEETING_GCAL_CLIENT_ID")
    assert meeting_check.GoogleCalendar(opener=lambda *a, **k: pytest.fail("called")) \
        .list_events(start, end, 1) == []


def test_invalid_grant_and_403_carry_no_body(monkeypatch):
    for key, value in (
        ("MEETING_GCAL_CLIENT_ID", "client-id"),
        ("MEETING_GCAL_CLIENT_SECRET", "client-secret"),
        ("MEETING_GCAL_REFRESH_TOKEN", "refresh"),
        ("MEETING_GCAL_CALENDAR_ID", "primary"),
    ):
        monkeypatch.setenv(key, value)

    def opener(req, timeout=None):
        raw = b'{"error":"invalid_grant","error_description":"SECRET-BODY"}'
        raise urllib.error.HTTPError(
            req.full_url, 400, "bad", email.message.Message(), io.BytesIO(raw),
        )

    cal = meeting_check.GoogleCalendar(opener=opener)
    with pytest.raises(meeting_check.CalendarAuthError) as caught:
        cal.list_events(datetime(2026, 10, 19, tzinfo=timezone.utc), datetime(2026, 10, 20, tzinfo=timezone.utc), 1)
    assert caught.value.code == "invalid_grant"
    assert "SECRET-BODY" not in str(caught.value)

    def denied(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 403, "no", email.message.Message(), io.BytesIO(b"SECRET-BODY"),
        )

    cal = meeting_check.GoogleCalendar(opener=denied)
    with pytest.raises(meeting_check.CalendarAuthError) as caught:
        cal.list_events(datetime(2026, 10, 19, tzinfo=timezone.utc), datetime(2026, 10, 20, tzinfo=timezone.utc), 1)
    assert caught.value.code == "403"
    assert "SECRET-BODY" not in str(caught.value)


def test_scrub_and_setkey(monkeypatch):
    values = {}
    for key in SECRET_KEYS:
        values[key] = "UNIQUE-" + key + "-" + ("x" * 40)
        monkeypatch.setenv(key, values[key])
    scrubbed = bland_relay._scrub(" ".join(values.values()))
    for value in values.values():
        assert value not in scrubbed
    assert "<redacted>" in scrubbed
    assert "MEETING_CHECK_DISABLED" not in bland_relay.SECRET_ENV_KEYS
    assert "MEETING_SYNC_S" not in bland_relay.SECRET_ENV_KEYS
    for key in SECRET_KEYS:
        assert key in bland_relay.SECRET_ENV_KEYS
    script = (Path(bland_relay.__file__).resolve().parent / "bland-relay-setkey").read_text()
    for key in SETKEY_KEYS:
        assert key in script


def test_http_route_auth_match_and_rate_limit():
    arm(load("jordyn_marsh.json"), time.time())
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address
    base = "http://%s:%d" % (host, port)
    try:
        status, payload, raw = post(base, body_for(), token=None)
        assert status == 401 and payload == {"ok": False, "error": "unauthorized"}
        status, payload, raw = post(base, body_for(), token="nope")
        assert status == 401
        status, payload, raw = post(base, body_for(), token=OTHER)
        assert status == 401
        status, payload, raw = post(base, body_for(), token=TOKEN, bare=True)
        assert status == 401
        status, payload, raw = post(base, body_for(), token=TOKEN)
        assert status == 200 and payload["meeting_time"] == ET_TEXT
        assert b"True" not in raw and b"False" not in raw
        assert b"example.test" not in raw
        status, payload, raw = post(base, body_for(call_id="hdr"), token=TOKEN, header="X-Meeting-Check-Token")
        assert status == 200 and payload["match"] is True
        over = json.dumps(body_for(caller_name="Jordan Marsh " + ("B" * 2100))).encode()
        status, payload, raw = post(base, None, token=TOKEN, raw=over)
        assert status == 200 and payload == {"match": False}
        meeting_check._reset_ip()
        for _ in range(60):
            status, payload, raw = post(base, body_for(), token=None)
            assert status == 401
        status, payload, raw = post(base, body_for(), token=TOKEN)
        assert status == 429 and payload == {"ok": False, "error": "rate_limited"}
    finally:
        srv.shutdown()
        srv.server_close()


def post(base, data, token, header="Authorization", bare=False, raw=None):
    body = raw if raw is not None else json.dumps(data).encode()
    headers = {}
    if token and bare:
        headers["Authorization"] = token
    elif token and header == "Authorization":
        headers["Authorization"] = "Bearer " + token
    elif token:
        headers[header] = token
    req = urllib.request.Request(
        base + "/bland/meeting-check?x=1", data=body, headers=headers, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as res:
            raw_body = res.read()
            return res.status, json.loads(raw_body.decode()), raw_body
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        return exc.code, json.loads(raw_body.decode()) if raw_body else {}, raw_body


def test_p95_under_50ms_for_1000_against_2000_events():
    base = datetime(2026, 10, 17, 8, 0, tzinfo=ET)
    step = (45 * 86400) / 2000
    items = []
    for n in range(1999):
        when = base + timedelta(seconds=step * n)
        items.append({
            "id": "slot-%d" % n,
            "status": "confirmed",
            "summary": "Slot %d" % n,
            "start": {"dateTime": when.isoformat(timespec="seconds"), "timeZone": "America/New_York"},
            "end": {"dateTime": when.isoformat(timespec="seconds"), "timeZone": "America/New_York"},
            "attendees": [
                {"email": "owner@example.test", "self": True, "responseStatus": "accepted"},
                {"email": "guest%d@example.test" % n, "responseStatus": "needsAction"},
            ],
        })
    items.append(load("jordyn_marsh.json")["items"][0])
    arm({"items": items}, NOW)
    assert len(meeting_check.get_checker()._views) == 2000
    assert ask(body_for(call_id="control"))[1]["meeting_time"] == ET_TEXT
    checker = meeting_check.get_checker()
    raw = json.dumps(body_for(
        caller_name="Zelda Quillfeather",
        meeting_time="11:11 AM",
        call_id="perf",
    )).encode()
    headers = {"Authorization": "Bearer " + TOKEN}
    samples = []
    for n in range(1000):
        checker._calls.clear()
        checker._phones.clear()
        checker._global[:] = []
        ip = "10.%d.%d.%d" % (1, (n // 256) % 256, n % 256)
        started = time.perf_counter()
        status, payload = meeting_check.http_response(headers, raw, ip, NOW)
        samples.append(time.perf_counter() - started)
        assert status == 200 and payload == {"match": False}
    samples.sort()
    p95 = samples[int(0.95 * (len(samples) - 1))]
    assert p95 < 0.05, p95
