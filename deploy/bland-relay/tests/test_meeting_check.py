"""POST /bland/meeting-check (#1230). Fake calendar client only: no Google, no Bland, no network."""
import copy
import inspect
import io
import json
import logging
import os
import socket
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

import bland_relay
import meeting_check as mc

FIX = Path(__file__).resolve().parent / "fixtures" / "meeting_check"
TOKEN = "meeting-check-test-token"
OTHER_TOKEN = "caller-context-test-token"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone(__import__("datetime").timedelta(hours=-4))).timestamp()
KEYS_OK = ({"match"}, {"match", "meeting_time"})
ALL_KEYS = (
    "MEETING_CHECK_TOKEN", "MEETING_CHECK_DISABLED", "MEETING_SELF_EMAILS", "MEETING_SYNC_S",
    "MEETING_GCAL_CLIENT_ID", "MEETING_GCAL_CLIENT_SECRET", "MEETING_GCAL_REFRESH_TOKEN",
    "MEETING_GCAL_CALENDAR_ID", "CALLER_CONTEXT_TOKEN", "BLAND_RELAY_STATE_DIR",
)


def load(name):
    return json.loads((FIX / name).read_text())


class FakeClient:
    """Stands in for GcalClient. Counts every call."""

    def __init__(self, *docs, error=None):
        self.docs = list(docs)
        self.error = error
        self.calls = []
        self.total = 0

    def events_list(self, time_min, time_max, timeout=5.0, page_token=None, max_results=250):
        self.calls.append({"min": time_min, "max": time_max, "timeout": timeout, "page": page_token})
        if self.error is not None:
            raise self.error
        self.total += 1
        return copy.deepcopy(self.docs[min(self.total - 1, len(self.docs) - 1)])


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ALL_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MEETING_CHECK_TOKEN", TOKEN)
    monkeypatch.setattr(mc, "ALERT_SINK", None)
    mc.reset()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call in test"))
    yield
    mc.reset()


def make(*docs, error=None, limits=None, store=True, tmp=None, sync=True, **kw):
    client = FakeClient(*docs, error=error)
    st = mc.AttemptStore(path=(tmp or Path(os.environ["BLAND_RELAY_STATE_DIR"])) / "meeting_attempts.sqlite") if store else None
    checker = mc.MeetingChecker(
        client=client, limits=limits or mc.Limits(per_call=99, per_number=99, overall=999), store=st,
        clock=lambda: NOW, **kw,
    )
    if sync and docs and error is None:
        assert checker.refresh(NOW) is True
        client.calls.clear()
    return checker, client


def body(name="Jordan Marsh", date="2026-10-19", time_="03:00 PM", tz="ET", call_id="call-1", phone="+12075550100"):
    return {"call_id": call_id, "phone_number": phone, "caller_name": name,
            "meeting_date": date, "meeting_time": time_, "meeting_tz": tz}


def ask(checker, **kw):
    result, _cid = checker.check(body(**kw), NOW)
    assert set(result) in KEYS_OK
    return result


def counter():
    n = {"i": 0}

    def nxt(**kw):
        n["i"] += 1
        return dict(call_id="c%d" % n["i"], phone="+1207555%04d" % n["i"], **kw)
    return nxt


# ------------------------------------------------------------------ matches

def test_matches_booking_page_email_local_part():
    checker, client = make(load("booking_page.json"))
    res = ask(checker)
    assert res == {"match": True, "meeting_time": "Monday, October 19 at 3:00 PM ET"}
    assert client.calls == []


@pytest.mark.parametrize("spoken", ["Jordyn Marsh", "Jordan Marsh", "jordin marsh", "Mr. Jordan Marsh"])
def test_speech_to_text_spellings_match(spoken):
    checker, _ = make(load("booking_page.json"))
    assert ask(checker, name=spoken)["match"] is True


def test_initial_plus_last_local_part_matches():
    checker, _ = make(load("booking_page.json"))
    res = ask(checker, name="Jordan Marsh", date="2026-10-20", time_="10:00 AM")
    assert res == {"match": True, "meeting_time": "Tuesday, October 20 at 10:00 AM ET"}


def test_summary_only_last_name_matches():
    checker, _ = make(load("booking_page.json"))
    res = ask(checker, name="Zefir Quillfeather", date="2026-10-21", time_="9:00 AM")
    assert res == {"match": True, "meeting_time": "Wednesday, October 21 at 9:00 AM ET"}


def test_scheduled_time_zone_is_ignored_offset_is_used():
    checker, _ = make(load("booking_page.json"))
    res = ask(checker, name="Riley Okafor", date="2026-10-22", time_="3:00 PM", tz="ET")
    assert res == {"match": True, "meeting_time": "Thursday, October 22 at 3:00 PM ET"}


@pytest.mark.parametrize("tz", ["CT", "Central", "central time", "CDT"])
def test_two_pm_central_matches_three_pm_et(tz):
    checker, _ = make(load("booking_page.json"))
    res = ask(checker, name="Riley Okafor", date="2026-10-22", time_="2 PM", tz=tz)
    assert res == {"match": True, "meeting_time": "Thursday, October 22 at 3:00 PM ET"}


@pytest.mark.parametrize("tz", ["Eastern", "Eastern Standard", "ET", "", None, "{{input.meeting_tz}}"])
def test_eastern_names_and_default(tz):
    checker, _ = make(load("booking_page.json"))
    assert ask(checker, tz=tz if tz is not None else "")["match"] is True


def test_time_formats_and_boundary_minutes():
    checker, _ = make(load("booking_page.json"))
    for t in ("15:00", "3:00PM", "3 pm", "3:15 PM", "2:45 PM"):
        assert ask(checker, time_=t)["match"] is True, t


# ---------------------------------------------------------------- no-matches

@pytest.mark.parametrize("kw", [
    {"time_": "3:16 PM"}, {"time_": "2:44 PM"}, {"time_": "4:00 PM", "name": "Riley Okafor"},
    {"time_": "2:00 PM"},
    {"date": "2026-10-18"}, {"date": "2026-10-20"},
    {"name": "Jordan Smith"}, {"name": "Jordan Marsh-Hall"},
    {"name": "Jordan"}, {"name": "Marsh"}, {"name": ""},
    {"name": "Sam Ortega"},  # right person, another person's time
    {"name": "Jordan Marsh", "time_": "4:00 PM"},  # right name, another person's meeting time
    {"name": "Pat Marsh"},  # right last name, wrong first name
])
def test_no_match_cases(kw):
    checker, _ = make(load("booking_page.json"))
    assert ask(checker, **kw) == {"match": False}


@pytest.mark.parametrize("kw", [
    {"name": "Dana Whitfield", "date": "2026-10-23", "time_": "11:00 AM"},      # cancelled
    {"name": "Avery Lindqvist", "date": "2026-10-23", "time_": "12:00 AM"},     # all-day
    {"name": "Avery Lindqvist", "date": "2026-10-23", "time_": "9:00 AM"},
    {"name": "Morgan Teller", "date": "2026-10-24", "time_": "1:00 PM"},        # declined by owner
    {"name": "Focus Time", "date": "2026-10-24", "time_": "4:00 PM"},           # owner-only, no guests
    {"name": "Casey Owner", "date": "2026-10-25", "time_": "10:00 AM"},         # only the owner invited
    {"name": "Owner Planning", "date": "2026-10-25", "time_": "10:00 AM"},
])
def test_excluded_events_never_match(kw):
    checker, _ = make(load("booking_page.json"))
    assert ask(checker, **kw) == {"match": False}


def test_number_alone_and_time_alone_never_match():
    checker, _ = make(load("booking_page.json"))
    assert ask(checker, name="", phone="+12075550100") == {"match": False}
    assert ask(checker, name="  ", call_id="c2") == {"match": False}
    assert ask(checker, name="Zed", call_id="c3") == {"match": False}


def test_spoofed_number_is_not_a_factor():
    checker, _ = make(load("booking_page.json"))
    assert ask(checker, phone="+19995550123", call_id="a")["match"] is True
    assert ask(checker, phone="", call_id="b")["match"] is True
    assert ask(checker, phone="+12075550100", name="Jordan Smith", call_id="c") == {"match": False}


def _guest(eid, start, email, name, summary):
    return {
        "id": eid,
        "status": "confirmed",
        "start": {"dateTime": start},
        "end": {"dateTime": start[:19] + start[19:]},
        "summary": summary,
        "attendees": [
            {"email": "owner@example.test", "displayName": "Casey Owner", "self": True, "responseStatus": "accepted"},
            {"email": email, "displayName": name, "responseStatus": "accepted"},
        ],
    }


def test_multiple_candidates_pick_nearest_then_earliest():
    doc = {"items": [
        _guest("near-early", "2026-10-19T14:50:00-04:00", "jordan.marsh@example.test", "Jordan Marsh", "Early"),
        _guest("near-late", "2026-10-19T15:10:00-04:00", "jordan.marsh@example.test", "Jordan Marsh", "Late"),
        _guest("closer", "2026-10-19T15:00:00-04:00", "jordan.marsh@example.test", "Jordan Marsh", "On time"),
        _guest("sam", "2026-10-19T15:00:00-04:00", "sam.ortega@example.test", "Sam Ortega", "Sam"),
        _guest("riley", "2026-10-19T15:00:00-04:00", "riley.okafor@example.test", "Riley Okafor", "Riley"),
    ]}
    tie, _ = make({"items": [doc["items"][0], doc["items"][1]]})
    assert ask(tie, name="Jordan Marsh", time_="3:00 PM") == {
        "match": True, "meeting_time": "Monday, October 19 at 2:50 PM ET",
    }
    nearest, _ = make({"items": [doc["items"][1], doc["items"][2]]})
    assert ask(nearest, name="Jordan Marsh", time_="3:08 PM") == {
        "match": True, "meeting_time": "Monday, October 19 at 3:10 PM ET",
    }
    none, _ = make({"items": [doc["items"][3], doc["items"][4]]})
    assert ask(none, name="Pat Nobody", time_="3:00 PM") == {"match": False}


# ------------------------------------------------------------------ http / auth

@pytest.fixture
def http():
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def post(base, payload, token=TOKEN, header="Authorization", raw=None, path="/bland/meeting-check", length=None):
    data = raw if raw is not None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if length is not None:
        headers["Content-Length"] = str(length)
    if token:
        headers[header] = f"Bearer {token}" if header == "Authorization" else token
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode()
        return exc.code, json.loads(raw_body) if raw_body else {}


def install(monkeypatch, checker):
    monkeypatch.setattr(mc, "_checker", checker)


def assert_wire(status, doc):
    assert status == 200
    assert set(doc) in KEYS_OK


def test_http_match_and_exact_key_set(http, monkeypatch):
    checker, _ = make(load("booking_page.json"))
    install(monkeypatch, checker)
    status, doc = post(http, body())
    assert_wire(status, doc)
    assert doc == {"match": True, "meeting_time": "Monday, October 19 at 3:00 PM ET"}
    status, doc = post(http, body(name="Nobody Here", call_id="x"))
    assert_wire(status, doc)
    assert doc == {"match": False}


def test_http_401_without_or_with_wrong_token(http, monkeypatch):
    checker, client = make(load("booking_page.json"))
    install(monkeypatch, checker)
    for token in (None, "wrong", OTHER_TOKEN, ""):
        status, doc = post(http, body(), token=token)
        assert status == 401
        assert doc == {"ok": False, "error": "unauthorized"}
    assert client.calls == []
    assert mc.AttemptStore(path=Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "meeting_attempts.sqlite").path.exists() is False


def test_http_alternate_header_and_unset_token_fails_closed(http, monkeypatch):
    checker, _ = make(load("booking_page.json"))
    install(monkeypatch, checker)
    status, doc = post(http, body(), header="X-Meeting-Check-Token")
    assert_wire(status, doc)
    assert doc["match"] is True
    monkeypatch.delenv("MEETING_CHECK_TOKEN")
    assert post(http, body())[0] == 401
    monkeypatch.setenv("MEETING_CHECK_TOKEN", "")
    assert post(http, body(), token="")[0] == 401


def test_client_disconnect_during_check_is_swallowed_and_row_kept(http, monkeypatch, caplog):
    checker, _ = make(load("booking_page.json"))
    install(monkeypatch, checker)
    orig = bland_relay.H._send
    state = {"n": 0}

    def wrapped(self, code, obj):
        state["n"] += 1
        if state["n"] == 1:
            def boom(_blob):
                raise ConnectionResetError()

            self.wfile.write = boom
        return orig(self, code, obj)

    monkeypatch.setattr(bland_relay.H, "_send", wrapped)
    with caplog.at_level(logging.DEBUG, logger="bland-relay"):
        try:
            post(http, body(call_id="drop-1"))
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
    assert not any(rec.levelno >= logging.ERROR for rec in caplog.records)
    assert "Traceback" not in caplog.text
    assert "client disconnect: ConnectionResetError" in caplog.text
    path = Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "meeting_attempts.sqlite"
    rows = sqlite3.connect(path).execute("SELECT call_id, result FROM attempts").fetchall()
    assert ("drop-1", 1) in rows
    status, doc = post(http, body(call_id="drop-2"))
    assert_wire(status, doc)
    assert doc["match"] is True


def test_http_other_path_and_get_are_404(http):
    assert post(http, {}, path="/bland/meeting-checkx")[0] == 404
    req = urllib.request.Request(http + "/bland/meeting-check", method="GET")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)
    assert err.value.code == 404


def test_http_per_ip_limit_is_429(http, monkeypatch):
    checker, _ = make(load("booking_page.json"))
    install(monkeypatch, checker)
    monkeypatch.setattr(mc, "IP_LIMIT", 3)
    codes = [post(http, body(call_id="k%d" % i))[0] for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    status, doc = post(http, body(), token=None)
    assert status == 429 and doc == {"ok": False, "error": "rate_limited"}


@pytest.mark.parametrize("raw,length", [
    (b"not json", None), (b"", None), (b"[1,2]", None), (b'"text"', None), (b"{\"call_id\": 5}", None),
    (b"x" * 4000, None), (b'{"caller_name": "' + b"a" * 3000 + b'"}', None),
])
def test_http_bad_bodies_are_200_no_match(http, monkeypatch, raw, length):
    checker, client = make(load("booking_page.json"))
    install(monkeypatch, checker)
    status, doc = post(http, None, raw=raw, length=length)
    assert_wire(status, doc)
    assert doc == {"match": False}
    assert client.calls == []


def test_http_missing_fields_are_200_no_match(http, monkeypatch):
    checker, _ = make(load("booking_page.json"))
    install(monkeypatch, checker)
    for i, drop in enumerate(("caller_name", "meeting_date", "meeting_time")):
        payload = body(call_id="m%d" % i)
        del payload[drop]
        status, doc = post(http, payload)
        assert_wire(status, doc)
        assert doc == {"match": False}
    assert post(http, {"call_id": "m9"})[1] == {"match": False}


def test_http_bad_content_length_is_200_no_match(http, monkeypatch):
    checker, _ = make(load("booking_page.json"))
    install(monkeypatch, checker)
    status, doc = post(http, None, raw=b"{}", length="abc")
    assert (status, doc) == (200, {"match": False})


def test_kill_switch_answers_no_match_and_skips_the_calendar(http, monkeypatch, tmp_path):
    checker, client = make(load("booking_page.json"))
    install(monkeypatch, checker)
    monkeypatch.setenv("MEETING_CHECK_DISABLED", "1")
    status, doc = post(http, body())
    assert (status, doc) == (200, {"match": False})
    assert client.calls == []
    assert not (tmp_path / "meeting_attempts.sqlite").exists()
    assert mc.start_sync() is None
    monkeypatch.setenv("MEETING_CHECK_DISABLED", "0")
    assert post(http, body(call_id="again"))[1]["match"] is True


# ------------------------------------------------- calendar failures all fail closed

@pytest.mark.parametrize("error", [
    mc.CalendarError("calendar http"), socket.timeout("timed out"), TimeoutError(), RuntimeError("boom"),
    mc.AuthError("calendar rejected"), urllib.error.URLError("down"),
])
def test_calendar_failures_are_no_match(error):
    checker, client = make(error=error)
    assert ask(checker) == {"match": False}
    assert len(client.calls) == 1  # empty snapshot: one live fetch, then no-match
    assert client.calls[0]["timeout"] == 0.5
    assert checker.refresh(NOW) is False


def test_exception_inside_matching_is_no_match(monkeypatch):
    checker, _ = make(load("booking_page.json"))
    monkeypatch.setattr(checker, "_find", lambda *a, **k: 1 / 0)
    assert ask(checker) == {"match": False}
    assert mc._final({"match": True}) == {"match": False}
    assert mc._final(["x"]) == {"match": False}
    assert mc._final({"match": True, "meeting_time": "x", "extra": 1}) == {"match": True, "meeting_time": "x"}


def test_handle_survives_a_checker_that_raises(monkeypatch):
    class Boom:
        def check(self, *a, **k):
            raise RuntimeError("boom")
    status, doc = mc.handle(TOKEN, lambda: b"{}", "10.0.0.1", now=NOW, checker=Boom())
    assert (status, doc) == (200, {"match": False})


def test_invalid_grant_alerts_once_a_day_and_keeps_answering(caplog):
    alerts = []
    mc.ALERT_SINK = lambda title, detail: alerts.append((title, detail))
    checker, _ = make(error=mc.AuthError("token refresh rejected"))
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        assert checker.refresh(NOW) is False
        assert checker.refresh(NOW + 120) is False
        assert ask(checker) == {"match": False}
        assert checker.refresh(NOW + 86400 + 1) is False
    assert alerts == [("Meeting check: calendar auth failing", "calendar auth failing")] * 2
    assert "meeting-check: calendar auth failing" in caplog.text
    assert "token refresh rejected" not in caplog.text


# ----------------------------------------------------------------- anti-guess

def test_per_call_limit_blocks_4th_check_without_a_calendar_read():
    checker, client = make(load("booking_page.json"), limits=mc.Limits())
    for _ in range(3):
        assert ask(checker, call_id="one", phone="")["match"] is True
    assert ask(checker, call_id="one", phone="") == {"match": False}
    assert ask(checker, call_id="two", phone="")["match"] is True


def test_per_call_limit_with_stale_snapshot_makes_zero_reads_when_denied():
    checker, client = make(load("booking_page.json"), limits=mc.Limits(per_call=3))
    checker.snapshot.at = NOW - 10_000  # stale: every allowed check would read live
    for _ in range(3):
        ask(checker, call_id="one", phone="")
    assert len(client.calls) == 3
    ask(checker, call_id="one", phone="")
    assert len(client.calls) == 3


def test_per_number_limit_blocks_7th_in_an_hour():
    checker, client = make(load("booking_page.json"), limits=mc.Limits())
    nxt = counter()
    for i in range(6):
        assert ask(checker, call_id="n%d" % i, phone="+12075550100")["match"] is True
    assert ask(checker, call_id="n7", phone="+12075550100") == {"match": False}
    assert ask(checker, call_id="n8", phone="(207) 555-0100") == {"match": False}  # same number, other format
    assert ask(checker, call_id="n9", phone="+12075550111")["match"] is True
    del nxt


def test_overall_limit_blocks_41st_without_a_calendar_read():
    checker, client = make(load("booking_page.json"), limits=mc.Limits())
    checker.snapshot.at = NOW - 10_000
    nxt = counter()
    for _ in range(40):
        ask(checker, **nxt())
    assert len(client.calls) == 40
    assert ask(checker, **nxt()) == {"match": False}
    assert len(client.calls) == 40


def test_limits_expire_after_the_window():
    lim = mc.Limits(per_call=1, per_number=1, overall=1, window=100)
    assert lim.allow("a", "n", 0) is True
    assert lim.allow("b", "m", 50) is False
    assert lim.allow("b", "m", 101) is True


# --------------------------------------------------------------- snapshot / live

def test_fresh_snapshot_makes_zero_network_calls():
    checker, client = make(load("booking_page.json"))
    for i in range(5):
        ask(checker, call_id="f%d" % i)
    assert client.calls == []


def test_stale_snapshot_makes_one_live_call_with_500ms_timeout():
    checker, client = make(load("booking_page.json"))
    checker.snapshot.at = NOW - 601
    assert ask(checker)["match"] is True
    assert len(client.calls) == 1
    assert client.calls[0]["timeout"] == 0.5
    assert client.calls[0]["page"] is None


def test_empty_snapshot_makes_one_live_call():
    checker, client = make({"items": []}, load("booking_page.json"))
    assert checker.snapshot.events == []
    assert ask(checker)["match"] is True
    assert len(client.calls) == 1 and client.calls[0]["timeout"] == 0.5


def test_sync_pages_and_window():
    page1 = {"items": load("booking_page.json")["items"][:2], "nextPageToken": "p2"}
    page2 = {"items": load("booking_page.json")["items"][2:]}
    client = FakeClient(page1, page2)
    checker = mc.MeetingChecker(client=client, clock=lambda: NOW)
    assert checker.refresh(NOW) is True
    assert [c["page"] for c in client.calls] == [None, "p2"]
    assert client.calls[0]["min"] == NOW - 3600 and client.calls[0]["max"] == NOW + 45 * 86400
    assert len(checker.snapshot.events) > 3


def test_no_credentials_means_no_client_no_thread_no_network(monkeypatch):
    mc.reset()
    checker = mc.get_checker()
    assert checker.client is None
    assert mc.start_sync() is None
    assert mc.GcalClient.from_env() is None
    status, doc = mc.handle(TOKEN, lambda: json.dumps(body()).encode(), "10.0.0.2", now=NOW)
    assert (status, doc) == (200, {"match": False})
    assert mc.startup_line() == "meeting-check: token=set calendar=unset disabled=0"
    for key in ("MEETING_GCAL_CLIENT_ID", "MEETING_GCAL_CLIENT_SECRET", "MEETING_GCAL_REFRESH_TOKEN"):
        monkeypatch.setenv(key, "x-" + key)
    assert mc.GcalClient.from_env() is None  # the fourth key is still missing


def test_startup_line_has_no_values(monkeypatch):
    secrets = {"MEETING_CHECK_TOKEN": "tok-secret-1", "MEETING_GCAL_CLIENT_ID": "cid-secret-2",
               "MEETING_GCAL_CLIENT_SECRET": "csec-secret-3", "MEETING_GCAL_REFRESH_TOKEN": "rt-secret-4",
               "MEETING_GCAL_CALENDAR_ID": "owner-secret@example.test"}
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    line = mc.startup_line()
    assert line == "meeting-check: token=set calendar=set disabled=0"
    assert not any(v in line for v in secrets.values())


# ---------------------------------------------------- google client (fake opener)

class Resp:
    def __init__(self, doc):
        self.raw = json.dumps(doc).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.raw


def http_error(code, doc=None):
    return urllib.error.HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(json.dumps(doc or {}).encode()))


def test_gcal_fields_mask_and_urls():
    seen = []

    def opener(req, timeout=None):
        seen.append((req.full_url, req.get_method(), timeout, dict(req.header_items())))
        if req.full_url == mc.TOKEN_URL:
            return Resp({"access_token": "at-1", "expires_in": 3600})
        return Resp({"items": []})

    clock = {"t": 1000.0}
    client = mc.GcalClient("cid", "csec", "rtok", "owner@example.test", opener=opener, clock=lambda: clock["t"])
    client.events_list(NOW, NOW + 100, timeout=0.5)
    client.events_list(NOW, NOW + 100, timeout=0.5, page_token="pg")
    token_calls = [s for s in seen if s[0] == mc.TOKEN_URL]
    api = [s for s in seen if s[0].startswith(mc.API_BASE)]
    assert len(token_calls) == 1  # cached
    assert len(api) == 2
    url = api[0][0]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["fields"] == [mc.FIELDS]
    assert query["singleEvents"] == ["true"] and query["orderBy"] == ["startTime"] and query["maxResults"] == ["250"]
    for word in ("description", "location", "conferenceData", "htmlLink", "hangoutLink", "attachments"):
        assert word not in url
        assert word not in mc.FIELDS
    assert "pageToken=pg" in api[1][0]
    assert api[0][3]["Authorization"] == "Bearer at-1"
    assert api[0][1] == "GET" and api[0][2] == 0.5
    assert "owner@example.test" in url
    clock["t"] = 1000.0 + 3600 - 59  # inside the 60 s refresh margin
    client.events_list(NOW, NOW + 100)
    assert len([s for s in seen if s[0] == mc.TOKEN_URL]) == 2
    assert all(s[0].startswith(("https://oauth2.googleapis.com/", "https://www.googleapis.com/calendar/v3/")) for s in seen)


def test_gcal_invalid_grant_and_http_errors_map_to_auth_or_calendar_errors():
    def invalid(req, timeout=None):
        raise http_error(400, {"error": "invalid_grant"})

    with pytest.raises(mc.AuthError):
        mc.GcalClient("a", "b", "c", "d", opener=invalid).events_list(1, 2)

    def forbidden(req, timeout=None):
        if req.full_url == mc.TOKEN_URL:
            return Resp({"access_token": "t", "expires_in": 3600})
        raise http_error(403)

    with pytest.raises(mc.AuthError):
        mc.GcalClient("a", "b", "c", "d", opener=forbidden).events_list(1, 2)

    def server_error(req, timeout=None):
        if req.full_url == mc.TOKEN_URL:
            return Resp({"access_token": "t", "expires_in": 3600})
        raise http_error(500)

    with pytest.raises(mc.CalendarError):
        mc.GcalClient("a", "b", "c", "d", opener=server_error).events_list(1, 2)

    def slow(req, timeout=None):
        raise socket.timeout("timed out")

    with pytest.raises(mc.CalendarError):
        mc.GcalClient("a", "b", "c", "d", opener=slow).events_list(1, 2)


def test_gcal_client_is_wired_to_the_checker_through_events_list():
    def opener(req, timeout=None):
        if req.full_url == mc.TOKEN_URL:
            return Resp({"access_token": "t", "expires_in": 3600})
        return Resp(load("booking_page.json"))

    gcal = mc.GcalClient("a", "b", "c", "owner@example.test", opener=opener)
    checker = mc.MeetingChecker(client=gcal, clock=lambda: NOW, limits=mc.Limits(99, 99, 999))
    assert checker.refresh(NOW) is True
    assert ask(checker)["match"] is True


# ------------------------------------------------------------------- privacy

def sentinels(doc):
    out = set()
    for item in doc["items"]:
        for key in ("summary", "location", "description", "hangoutLink", "htmlLink"):
            if item.get(key):
                out.add(item[key])
        for entry in (item.get("conferenceData") or {}).get("entryPoints", []):
            out.add(entry["uri"])
        for a in item.get("attendees", []):
            out.add(a["email"])
            if a.get("displayName"):
                out.add(a["displayName"])
    out.update({"+1 555 010 0100", "555 010 01", "12 Hidden Lane", "owner@example.test", "Casey Owner"})
    return out


def test_privacy_response_and_logs_carry_nothing_from_the_calendar(caplog, tmp_path):
    doc = load("busy_calendar.json")
    assert len(doc["items"]) == 25
    checker, _ = make(doc, tmp=tmp_path)
    stated_name, phone = "Priya Natarajan", "+12075550177"
    leaks = sentinels(doc)
    with caplog.at_level(logging.DEBUG):
        status, wire = mc.handle(TOKEN, lambda: json.dumps(body(
            name=stated_name, date="2026-10-26", time_="8:00 AM", phone=phone, call_id="priv-1")).encode(),
            "10.1.1.1", now=NOW, checker=checker)
        miss = mc.handle(TOKEN, lambda: json.dumps(body(
            name="Wrong Person", date="2026-10-26", time_="8:00 AM", phone=phone, call_id="priv-2")).encode(),
            "10.1.1.1", now=NOW, checker=checker)[1]
        bad = mc.handle("bad-token-value", lambda: b"{}", "10.1.1.2", now=NOW, checker=checker)[1]
    assert status == 200
    assert wire == {"match": True, "meeting_time": "Monday, October 26 at 8:00 AM ET"}
    assert miss == {"match": False}
    blob = json.dumps([wire, miss, bad]) + caplog.text
    for leak in leaks:
        assert leak not in blob, leak
    for forbidden in (stated_name, "Natarajan", "Priya", phone, "2075550177", "8:00", "2026-10-26",
                      TOKEN, "bad-token-value", "@example.test"):
        assert forbidden not in caplog.text, forbidden
    assert "call_id=priv-1 result=true" in caplog.text
    assert "call_id=priv-2 result=false" in caplog.text
    lines = [r.getMessage() for r in caplog.records if r.name == "bland-relay"]
    assert all(line.startswith("meeting-check") for line in lines)


def test_privacy_errors_log_the_exception_class_only(caplog):
    secret = "s3cret-detail-xyz"
    checker, _ = make(error=RuntimeError(secret))
    with caplog.at_level(logging.DEBUG):
        ask(checker)
        checker.refresh(NOW)
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


def test_module_never_handles_a_bland_key_or_a_write_scope():
    src = inspect.getsource(mc)
    assert "api.bland.ai" not in src and "BLAND_API" not in src
    assert "auth/calendar\"" not in src and "calendar.events\"" not in src
    assert src.count('method="POST"') == 1  # the OAuth refresh; events.list is a plain GET
    for word in ("PATCH", "PUT", "DELETE"):
        assert 'method="%s"' % word not in src
    assert "auto-fleet" not in src
    assert "import bland_relay" not in src


# ------------------------------------------------------------------ latency

def test_p95_under_50ms_over_1000_requests_on_2000_cached_events():
    first = ["Alex", "Blair", "Casey", "Drew", "Emery", "Finn", "Gray", "Harper", "Indy", "Jules"]
    last = ["Abbott", "Barnes", "Cortez", "Dunlap", "Ellison", "Fowler", "Garza", "Hollis", "Irwin", "Jensen"]
    owner = {"email": "owner@example.test", "self": True, "responseStatus": "accepted"}
    items = []
    base = NOW + 7200
    for i in range(2000):
        f, l = first[i % 10], last[(i // 10) % 10]
        start = datetime.fromtimestamp(base + i * 1800, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items.append({"id": "e%d" % i, "status": "confirmed", "summary": "Booked %s%d" % (l, i),
                      "start": {"dateTime": start}, "end": {"dateTime": start},
                      "attendees": [owner, {"email": "%s.%s%d@example.test" % (f.lower(), l.lower(), i), "displayName": "%s %s" % (f, l)}],
                      "organizer": {"email": "owner@example.test"}})
    checker = mc.MeetingChecker(client=FakeClient({"items": items}), clock=lambda: NOW,
                                limits=mc.Limits(per_call=10**6, per_number=10**6, overall=10**6))
    assert checker.refresh(NOW) is True
    assert len(checker.snapshot.events) == 2000
    samples = []
    for i in range(1000):
        target = base + (i * 7 % 2000) * 1800
        stated = datetime.fromtimestamp(target, timezone.utc)
        et = stated.astimezone(mc.ET)
        start = time.perf_counter()
        res, _ = checker.check(body(name="%s %s" % (first[(i * 7) % 10], last[((i * 7) // 10) % 10]),
                                    date=et.strftime("%Y-%m-%d"), time_=et.strftime("%H:%M"),
                                    call_id="p%d" % i, phone="+1207555%04d" % (i % 9000)), NOW)
        samples.append((time.perf_counter() - start) * 1000)
        assert res["match"] is True
    samples.sort()
    assert samples[int(0.95 * 999)] < 50


# ---------------------------------------------------------- secrets / records

def test_meeting_keys_are_secret_env_keys_and_scrubbed(monkeypatch):
    expected = ("MEETING_CHECK_TOKEN", "MEETING_GCAL_CLIENT_ID", "MEETING_GCAL_CLIENT_SECRET",
                "MEETING_GCAL_REFRESH_TOKEN", "MEETING_GCAL_CALENDAR_ID", "MEETING_SELF_EMAILS")
    for key in expected:
        assert key in bland_relay.SECRET_ENV_KEYS
        monkeypatch.setenv(key, "value-of-" + key.lower())
        assert "value-of-" + key.lower() not in bland_relay._scrub("err value-of-%s end" % key.lower())
        assert "<redacted>" in bland_relay._scrub("err value-of-%s end" % key.lower())


def test_attempt_record_mode_content_and_48h_purge(tmp_path):
    path = tmp_path / "state" / "meeting_attempts.sqlite"
    store = mc.AttemptStore(path=path)
    long_name = "N" * 200
    store.record("call-a", 1000.0, mc._clip_name(long_name), "2026-10-19T19:00:00Z", True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    rows = sqlite3.connect(path).execute("SELECT call_id, ts, stated_name, stated_start, result FROM attempts").fetchall()
    assert rows == [("call-a", 1000.0, "N" * 60, "2026-10-19T19:00:00Z", 1)]
    store.record("call-b", 1000.0 + 48 * 3600 - 1, "x", "", False)
    assert sqlite3.connect(path).execute("SELECT count(*) FROM attempts").fetchone()[0] == 2
    store.record("call-c", 1000.0 + 48 * 3600 + 1, "y", "", False)
    ids = [r[0] for r in sqlite3.connect(path).execute("SELECT call_id FROM attempts ORDER BY ts")]
    assert ids == ["call-b", "call-c"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_checks_write_one_row_each_and_the_logger_never_reads_the_store(caplog, tmp_path):
    checker, _ = make(load("booking_page.json"), tmp=tmp_path, limits=mc.Limits(per_call=1, per_number=99, overall=99))
    with caplog.at_level(logging.DEBUG):
        ask(checker, call_id="r1")
        ask(checker, call_id="r1")  # denied by the per-call cap, still recorded
        ask(checker, call_id="r2", name="Nobody Here")
    rows = sqlite3.connect(tmp_path / "meeting_attempts.sqlite").execute(
        "SELECT call_id, stated_name, stated_start, result FROM attempts ORDER BY rowid").fetchall()
    assert [(r[0], r[3]) for r in rows] == [("r1", 1), ("r1", 0), ("r2", 0)]
    assert rows[0][2] == "2026-10-19T19:00:00Z"
    assert "Nobody" not in caplog.text and "Jordan" not in caplog.text
    src = inspect.getsource(mc.handle) + inspect.getsource(mc.MeetingChecker.check)
    assert "SELECT" not in src
    assert inspect.getsource(mc.AttemptStore).count("log.") == 1  # the failure line, exception class only


def test_attempt_store_failure_never_breaks_the_answer(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    checker, _ = make(load("booking_page.json"), tmp=tmp_path)
    checker.store = mc.AttemptStore(path=blocker / "sub" / "a.sqlite")
    assert ask(checker)["match"] is True


# ------------------------------------------------------------ docs / relay wiring

ROOT = Path(__file__).resolve().parent.parent


def test_relay_wiring_and_readme():
    cfg = (ROOT / "relay_config.py").read_text()
    assert "import meeting_check" in cfg
    assert "meeting_check" in bland_relay.__dict__ or "meeting_check" in vars(bland_relay)
    readme = (ROOT / "README.md").read_text()
    for needle in ("POST /bland/meeting-check", "MEETING_CHECK_TOKEN", "MEETING_CHECK_DISABLED", "MEETING_GCAL_CLIENT_ID",
                   "MEETING_GCAL_CLIENT_SECRET", "MEETING_GCAL_REFRESH_TOKEN", "MEETING_GCAL_CALENDAR_ID",
                   "MEETING_SELF_EMAILS", "MEETING_SYNC_S", "calendar.events.readonly", "View events on all your calendars",
                   "Testing", "Rotate `MEETING_CHECK_TOKEN`", "meeting_check.py"):
        assert needle in readme, needle
    setkey = (ROOT / "bland-relay-setkey").read_text()
    for key in ("MEETING_CHECK_TOKEN", "MEETING_CHECK_DISABLED", "MEETING_GCAL_REFRESH_TOKEN", "MEETING_SELF_EMAILS"):
        assert key in setkey


def test_main_logs_the_startup_line_and_does_not_start_a_thread_without_credentials(monkeypatch, caplog):
    started = []
    monkeypatch.setattr(bland_relay, "ThreadingHTTPServer", lambda *a, **k: type("S", (), {"serve_forever": lambda s: None})())
    monkeypatch.setattr(bland_relay, "start_turo_email_listener", lambda: None)
    monkeypatch.setattr(bland_relay, "turo_prune_inbox", lambda force=False: None)
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        bland_relay.main()
    assert "meeting-check: token=set calendar=unset disabled=0" in caplog.text
    assert "meeting-check-sync" not in started
    assert bland_relay.meeting_check.ALERT_SINK is not None
