"""Caller-context endpoint (#1096). Fixtures only: no Bland API, no live inbound number."""
import inspect
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import bland_relay
import caller_context

FIX = Path(__file__).resolve().parent / "fixtures" / "caller_context"
CHRIS = "+12075550100"
YARD = "+19045550199"
NAPLES = "+12395550101"
TOKEN = "caller-context-test-token"
FORGET = "caller-forget-test-token"
CRM_TOKEN = "crm-caller-context-token"
AT_1021 = datetime(2026, 10, 9, 14, 21, tzinfo=timezone.utc)
AT_1253 = datetime(2026, 10, 9, 16, 53, tzinfo=timezone.utc)

EMAIL_RE = caller_context.EMAIL_RE
CARD_RE = caller_context.CARD_RE
ADDRESS_RE = caller_context.ADDRESS_RE
PLATE_RE = caller_context.PLATE_RE


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "CALLER_CONTEXT_TOKEN", "CALLER_FORGET_TOKEN", "CALLER_CONTEXT_RPM",
        "CALLER_INDEX_PATH", "CRM_API_BASE", "CRM_API_TOKEN", "BLAND_RELAY_STATE_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", TOKEN)
    monkeypatch.setenv("CALLER_FORGET_TOKEN", FORGET)
    caller_context.reset_limits()
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call in test")
    )


@pytest.fixture
def http():
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def get(base, pn, token=TOKEN, method="GET"):
    url = base + "/bland/caller-context?pn=" + urllib.request.quote(pn)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if raw else {}


def index():
    return caller_context.CallerIndex()


def test_e164_and_dedupe():
    assert caller_context.normalize_e164("(239) 555-0101") == NAPLES
    assert caller_context.normalize_e164("+1 239 555 0101") == NAPLES
    store = index()
    payload = load("call_webhook_inbound.json")
    assert caller_context.ingest_call_webhook(store, payload) is True
    assert caller_context.ingest_call_webhook(store, payload) is False
    assert store.count() == 1
    found = caller_context.lookup_caller("(239) 555-0101", AT_1021, index=store)
    assert found["known"] is True
    assert found["backend"] == "index"
    assert "you called this line" in found["caller_context"]
    assert "asked about a truck" in found["caller_context"]


def test_ingest_shapes_and_provider_dedupe():
    store = index()
    assert caller_context.ingest_polled_call(store, load("call_c5959d5f.json")) is True
    assert caller_context.ingest_polled_call(store, load("call_c5959d5f.json")) is False
    assert caller_context.ingest_polled_call(store, load("call_concurrency.json")) is True
    assert caller_context.ingest_bland_sms(store, load("sms_bland.json")) is True
    assert caller_context.ingest_bland_sms(store, load("sms_bland.json")) is False
    assert caller_context.ingest_smsgate(store, load("sms_smsgate.json")) is True
    assert caller_context.ingest_smsgate(store, load("sms_smsgate.json")) is False
    assert store.count() == 4
    naples = caller_context.lookup_caller(NAPLES, AT_1021, index=store)
    assert "line was busy" in naples["caller_context"]
    assert "fleet prospect follow-up" in naples["caller_context"]
    sms = caller_context.lookup_caller(NAPLES, AT_1021, index=store)
    assert "you texted this line" in sms["caller_context"]


def test_replay_2026_10_09_callbacks():
    store = index()
    caller_context.ingest_polled_call(store, load("call_c5959d5f.json"))
    # eaa9d7bf is the 10:21 callback. dynamic_data runs at call start, so that
    # inbound call is not in the index yet.
    callback = load("call_eaa9d7bf.json")
    assert callback["created_at"] == "2026-10-09T14:21:00Z"
    body = caller_context.lookup_caller(callback["from"], AT_1021, index=store)
    assert body["known"] is True
    assert "Today 10:20 AM" in body["caller_context"]
    assert "we called you from this line; it went to voicemail" in body["caller_context"]
    assert "purpose: not recorded" in body["caller_context"]
    assert body["last_contact_at"] == "2026-10-09 10:20 AM ET"

    caller_context.ingest_polled_call(store, load("call_storage_yard_out.json"))
    returned = load("call_6e046a93.json")
    assert returned["from"] == YARD
    assert returned["created_at"] == "2026-10-09T16:53:00Z"
    yard = caller_context.lookup_caller(YARD, AT_1253, index=store)
    assert "Today 12:10 PM" in yard["caller_context"]
    assert "we called you from this line" in yard["caller_context"]
    assert "outdoor vehicle storage lease" in yard["caller_context"]
    assert yard["open_followups"] == ["Chris to call back about the storage yard"]
    assert "6e046a93" not in yard["caller_context"]


def test_last_three_newest_first_et():
    store = index()
    base = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
    for minutes, purpose in ((0, "oldest"), (5, "third"), (10, "second"), (15, "newest")):
        caller_context.ingest_polled_call(store, {
            "call_id": purpose,
            "from": "+19062406221",
            "to": CHRIS,
            "inbound": False,
            "status": "completed",
            "answered_by": "human",
            "metadata": {"purpose": purpose},
            "created_at": (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    body = caller_context.lookup_caller(CHRIS, AT_1021, index=store)
    text = body["caller_context"]
    assert text.index("purpose: newest") < text.index("purpose: second") < text.index("purpose: third")
    assert "purpose: oldest" not in text
    assert "Today 10:15 AM" in text
    assert "Today 10:00 AM" not in text


def test_sensitive_patterns_stripped():
    view = {
        "known": True,
        "caller_type": "renter",
        "turo_booking": True,
        "interactions": [{
            "channel": "call",
            "direction": "out",
            "outcome": "completed",
            "purpose": "fleet quote 4111 1111 1111 1111",
            "occurred_at": "2026-10-09T14:20:00Z",
        }],
        "followups": [
            "Chris to call back about the truck. guest@example.com ABC1234 123 Main Street"
        ],
    }
    body = caller_context.render(view, AT_1021, "crm")
    blob = json.dumps(body)
    assert not EMAIL_RE.search(blob)
    assert not CARD_RE.search(blob)
    assert not ADDRESS_RE.search(blob)
    assert not PLATE_RE.search(blob)
    assert "Chris to call back about the truck" in blob
    assert "Has a Turo booking on file" in body["caller_context"]
    assert "guest@" not in blob
    assert "ABC1234" not in blob
    assert len(body["caller_context"]) <= 600


def test_retention_and_forget():
    store = index()
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    caller_context.ingest_polled_call(store, {
        "call_id": "ancient",
        "to": CHRIS,
        "from": "+19062406221",
        "inbound": False,
        "status": "completed",
        "metadata": {"purpose": "too old"},
        "created_at": old.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    caller_context.ingest_polled_call(store, load("call_c5959d5f.json"))
    store.purge(AT_1021)
    assert store.count() == 1
    assert caller_context.lookup_caller(CHRIS, AT_1021, index=store)["known"] is True
    assert store.forget("(207) 555-0100") == 1
    assert caller_context.lookup_caller(CHRIS, AT_1021, index=store)["known"] is False


def test_poller_get_only_rate_limit_and_5xx():
    store = index()
    sms = load("sms_bland.json")

    class Fake:
        def __init__(self, table):
            self.table = table
            self.calls = []

        def get(self, path, params=None):
            self.calls.append(("GET", path, params))
            return self.table(path)

        def post(self, *args, **kwargs):
            raise AssertionError("poller issued a non-GET")

    limited = Fake(lambda path: (429, {"error": "slow down"}))
    result = caller_context.poll_once(limited, store, AT_1021)
    assert result["rate_limited"] is True
    assert result["ingested"] == 0
    assert [call[1] for call in limited.calls] == ["/v1/calls"]

    def both(path):
        if path == "/v1/calls":
            return 500, {"error": "upstream"}
        if path == "/v1/sms/conversations":
            return 200, {"conversations": [{"id": "conv-9"}]}
        if path == "/v1/sms/conversations/conv-9":
            return 200, {"messages": [sms]}
        raise AssertionError(path)

    flaky = Fake(both)
    result = caller_context.poll_once(flaky, store, AT_1021)
    assert result["rate_limited"] is False
    assert result["errors"]
    assert result["ingested"] == 1
    assert flaky.calls[0][0] == "GET"
    assert store.count() == 1
    assert caller_context.poll_once(flaky, store, AT_1021)["ingested"] == 0
    assert store.count() == 1

    src = inspect.getsource(caller_context.poll_once)
    assert ".post(" not in src
    assert "api.bland.ai" not in inspect.getsource(caller_context)
    assert "api.bland.ai" not in inspect.getsource(bland_relay.main)


def test_read_client_is_get_only():
    seen = []

    class Resp:
        status = 200

        def read(self):
            return b'{"calls": []}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Opener:
        def open(self, req, timeout=None):
            seen.append(req)
            return Resp()

    with pytest.raises(ValueError):
        caller_context.BlandReadClient("", "key")
    client = caller_context.BlandReadClient("http://127.0.0.1:9", "test-key", opener=Opener())
    status, body = client.get("/v1/calls", {"start_date": "2026-10-09"})
    assert status == 200
    assert body == {"calls": []}
    assert seen[0].get_method() == "GET"
    assert seen[0].full_url.startswith("http://127.0.0.1:9/v1/calls?")
    assert not hasattr(client, "post")


def test_endpoint_auth_unknown_error_and_forget(http, monkeypatch):
    store = index()
    caller_context.ingest_polled_call(store, load("call_c5959d5f.json"))
    store.close()

    status, body = get(http, CHRIS, token=None)
    assert status == 401
    assert "caller_context" not in body

    status, body = get(http, CHRIS)
    assert status == 200
    assert body["known"] is True
    assert "voicemail" in body["caller_context"]

    status, body = get(http, "+19045550999")
    assert status == 200
    assert body == caller_context.empty_context("index")

    original = caller_context.lookup_caller

    def boom(phone, now=None, index=None, opener=None):
        raise RuntimeError("disk")

    monkeypatch.setattr(caller_context, "lookup_caller", boom)
    status, body = get(http, CHRIS)
    assert status == 200
    assert body["known"] is False
    assert body["caller_context"] == "No previous contact on file."
    assert "disk" not in json.dumps(body)
    monkeypatch.setattr(caller_context, "lookup_caller", original)

    status, body = get(http, CHRIS, token=TOKEN, method="DELETE")
    assert status == 401
    status, body = get(http, CHRIS, token=FORGET, method="DELETE")
    assert status == 200
    assert body["deleted"] == 1
    status, body = get(http, CHRIS)
    assert body["known"] is False


def test_rate_limit(http, monkeypatch):
    monkeypatch.setenv("CALLER_CONTEXT_RPM", "2")
    caller_context.reset_limits()
    assert get(http, CHRIS)[0] == 200
    assert get(http, CHRIS)[0] == 200
    status, body = get(http, CHRIS)
    assert status == 429
    assert body["error"] == "rate_limited"


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def test_fixture_crm_and_fallback(monkeypatch):
    captured = {}

    class Crm(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["auth"] = self.headers.get("Authorization")
            captured["scope"] = self.headers.get("X-CRM-Scope")
            captured["path"] = self.path
            if "pn=%2B12075550100" not in self.path and "pn=+12075550100" not in self.path:
                self.send_response(404)
                self.end_headers()
                return
            doc = {
                "caller_type": "owner_prospect",
                "type": "lead",
                "tags": ["storage"],
                "identities": [
                    {"kind": "email", "value": "guest@example.com"},
                    {"kind": "turo_guest", "value": "hidden-guest"},
                ],
                "vehicles": [{"plate": "ABC1234", "location": "123 Main Street"}],
                "interactions": [{
                    "channel": "call",
                    "direction": "outbound",
                    "outcome": "voicemail",
                    "occurred_at": "2026-10-09T14:20:00Z",
                    "metadata": {"purpose": "returning Chris's missed call"},
                    "summary": "plate ABC1234 lives at 123 Main Street card 4111 1111 1111 1111",
                }],
                "open_followups": ["Chris to call back about the storage yard"],
                "turo_booking": True,
            }
            raw = json.dumps(doc).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            pass

    srv = _serve(Crm)
    host, port = srv.server_address
    monkeypatch.setenv("CRM_API_BASE", f"http://{host}:{port}")
    monkeypatch.setenv("CRM_API_TOKEN", CRM_TOKEN)
    try:
        body = caller_context.lookup_caller(CHRIS, AT_1021)
    finally:
        srv.shutdown()
        srv.server_close()
    assert captured["auth"] == f"Bearer {CRM_TOKEN}"
    assert captured["scope"] == "caller-context"
    assert body["backend"] == "crm"
    assert body["caller_type"] == "owner_prospect"
    assert "returning Chris's missed call" in body["caller_context"]
    assert "voicemail" in body["caller_context"]
    assert body["open_followups"] == ["Chris to call back about the storage yard"]
    blob = json.dumps(body)
    assert not EMAIL_RE.search(blob)
    assert not CARD_RE.search(blob)
    assert not ADDRESS_RE.search(blob)
    assert not PLATE_RE.search(blob)

    store = index()
    caller_context.ingest_polled_call(store, load("call_storage_yard_out.json"))
    store.close()

    class Missing(BaseHTTPRequestHandler):
        def do_GET(self):
            raw = b'{"error":"not found"}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            pass

    miss = _serve(Missing)
    host, port = miss.server_address
    monkeypatch.setenv("CRM_API_BASE", f"http://{host}:{port}")
    try:
        missed = caller_context.lookup_caller(YARD, AT_1253)
    finally:
        miss.shutdown()
        miss.server_close()
    assert missed["backend"] == "crm"
    assert missed["known"] is False

    class Down(BaseHTTPRequestHandler):
        def do_GET(self):
            raw = b'{"error":"unavailable"}'
            self.send_response(503)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            pass

    down = _serve(Down)
    host, port = down.server_address
    monkeypatch.setenv("CRM_API_BASE", f"http://{host}:{port}")
    try:
        fell = caller_context.lookup_caller(YARD, AT_1253)
    finally:
        down.shutdown()
        down.server_close()
    assert fell["backend"] == "index"
    assert "outdoor vehicle storage lease" in fell["caller_context"]


def test_crm_base_accepts_only_loopback():
    good = {
        "http://127.0.0.1:8799": "http://127.0.0.1:8799",
        "http://localhost:9/": "http://localhost:9",
        "HTTP://LocalHost:80": "http://localhost:80",
    }
    bad = (
        "http://203.0.113.9:8799",
        "https://127.0.0.1:8799",
        "http://127.0.0.1",
        "http://127.0.0.1:8799/v1",
        "http://user:crm-secret-token@127.0.0.1:8799",
        "http://127.0.0.1:8799?pn=1",
        "http://[::1]:8799",
        "http://127.0.0.1:8799#token",
        "",
    )
    for raw, want in good.items():
        assert caller_context.loopback_http_base(raw) == want
    for raw in bad:
        assert caller_context.loopback_http_base(raw) is None


def test_crm_base_non_local_uses_index_without_request(monkeypatch):
    store = index()
    caller_context.ingest_polled_call(store, load("call_c5959d5f.json"))
    store.close()
    token = "crm-secret-token"
    poisoned = f"http://user:{token}@203.0.113.9:8799/steal"
    monkeypatch.setenv("CRM_API_BASE", poisoned)
    monkeypatch.setenv("CRM_API_TOKEN", token)
    seen = []

    class Opener:
        def open(self, req, timeout=None):
            seen.append(getattr(req, "full_url", ""))
            raise AssertionError("request on a rejected CRM base")

    body = caller_context.lookup_caller(CHRIS, AT_1021, opener=Opener())
    status, view = caller_context.crm_lookup(poisoned, token, CHRIS, opener=Opener())
    assert seen == []
    assert status == "down" and view is None
    assert body["backend"] == "index"
    assert "voicemail" in body["caller_context"]
    blob = json.dumps(body)
    assert token not in blob
    assert "203.0.113.9" not in blob
    assert poisoned not in blob


def test_crm_timeout_stays_inside_budget(monkeypatch):
    store = index()
    caller_context.ingest_polled_call(store, load("call_c5959d5f.json"))
    store.close()

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(1.2)
            self.send_response(200)
            self.end_headers()

        def log_message(self, fmt, *args):
            pass

    srv = _serve(Slow)
    host, port = srv.server_address
    monkeypatch.setenv("CRM_API_BASE", f"http://{host}:{port}")
    monkeypatch.setenv("CRM_API_TOKEN", CRM_TOKEN)
    started = time.perf_counter()
    try:
        body = caller_context.lookup_caller(CHRIS, AT_1021)
        elapsed = time.perf_counter() - started
    finally:
        threading.Thread(target=srv.shutdown, daemon=True).start()
    assert elapsed < 0.5
    assert body["backend"] == "index"
    assert "voicemail" in body["caller_context"]


def test_p95_under_300ms_for_10k():
    store = index()
    stamp = "2026-10-09T14:00:00Z"
    rows = [
        ("bland", f"call:fill-{i}", f"+1999{i:07d}", "call", "out", "completed",
         "filler", "", "unknown", 0, stamp)
        for i in range(10000)
    ]
    for offset, purpose in enumerate(("one", "two", "three")):
        rows.append((
            "bland", f"call:keep-{purpose}", CHRIS, "call", "out", "completed",
            purpose, "", "unknown", 0, f"2026-10-09T14:{10 + offset:02d}:00Z",
        ))
    store.add_many(rows)
    assert store.count() == 10003
    caller_context.lookup_caller(CHRIS, AT_1021, index=store)
    samples = []
    for _ in range(40):
        started = time.perf_counter()
        body = caller_context.lookup_caller(CHRIS, AT_1021, index=store)
        samples.append(time.perf_counter() - started)
    samples.sort()
    p95 = samples[int(0.95 * (len(samples) - 1))]
    assert p95 < 0.3, p95
    assert body["known"] is True
    assert "purpose: three" in body["caller_context"]


def test_scrub_removes_tokens(monkeypatch):
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", "super-secret-token")
    monkeypatch.setenv("CALLER_FORGET_TOKEN", "forget-secret-token")
    monkeypatch.setenv("CRM_API_TOKEN", "crm-secret-token")
    text = bland_relay._scrub(
        "saw super-secret-token and forget-secret-token and crm-secret-token"
    )
    assert "super-secret-token" not in text
    assert "forget-secret-token" not in text
    assert "crm-secret-token" not in text
    assert "<redacted>" in text
