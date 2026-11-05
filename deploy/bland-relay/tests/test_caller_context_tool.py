"""POST /bland/caller-context mid-call tool. Local relay only: no Bland, no prism, no network."""
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import bland_relay
import caller_context

FIX = Path(__file__).resolve().parent / "fixtures" / "caller_context"
ROOT = Path(__file__).resolve().parent.parent
TOKEN = "caller-context-test-token"
OTHER = "meeting-check-test-token"
CHRIS = "+12075550100"
PROMPT = (
    "CALLER HISTORY: Greet the caller first. After the greeting, and not before, "
    "call the caller_context tool once. If {{caller_context}} shows earlier contact, "
    "treat them as a returning caller and pick up the open item (for example, "
    "'I see we tried to reach you this morning'). Never deny that we called. "
    "A matching number is not proof of identity: discuss booking specifics only after "
    "the caller gives a name and trip number that match. If there is no history, or "
    "the tool is slow or fails, continue normally."
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "CALLER_CONTEXT_TOKEN", "CALLER_FORGET_TOKEN", "CALLER_CONTEXT_RPM",
        "CALLER_INDEX_PATH", "CRM_API_BASE", "CRM_API_TOKEN", "BLAND_RELAY_STATE_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", TOKEN)
    caller_context.reset_limits()
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call in test")
    )


@pytest.fixture
def http():
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        yield "http://%s:%d" % (host, port)
    finally:
        srv.shutdown()
        srv.server_close()


def load(name):
    return json.loads((FIX / name).read_text())


def post(base, payload, token=TOKEN, header="Authorization", raw=None, path="/bland/caller-context", length=None):
    data = raw if raw is not None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if length is not None:
        headers["Content-Length"] = str(length)
    if token:
        headers[header] = ("Bearer " + token) if header == "Authorization" else token
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode()
        return exc.code, json.loads(raw_body) if raw_body else {}


def get(base, pn, token=TOKEN):
    url = base + "/bland/caller-context?pn=" + urllib.request.quote(pn)
    headers = {"Authorization": "Bearer " + token} if token else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode()
        return exc.code, json.loads(raw_body) if raw_body else {}


def seed():
    store = caller_context.CallerIndex()
    caller_context.ingest_polled_call(store, load("call_c5959d5f.json"))
    store.close()


def test_auth_missing_wrong_and_unset(http, monkeypatch):
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("lookup on a rejected token")

    monkeypatch.setattr(caller_context, "lookup_caller", boom)
    for token in (None, "", "wrong", OTHER):
        status, doc = post(http, {"call_id": "a", "phone_number": CHRIS}, token=token)
        assert status == 401
        assert doc == {"ok": False, "error": "unauthorized"}
        assert "caller_context" not in doc
    assert called == []
    monkeypatch.delenv("CALLER_CONTEXT_TOKEN")
    assert post(http, {"call_id": "a", "phone_number": CHRIS})[0] == 401
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", "")
    assert post(http, {"call_id": "a", "phone_number": CHRIS}, token="")[0] == 401


def test_happy_path_matches_get_and_logs_no_pii(http, caplog):
    seed()
    body = {"call_id": "tool-call-1", "phone_number": "+1 207 555 0100"}
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        status, doc = post(http, body)
    assert status == 200
    assert set(doc) == set(caller_context.TOOL_KEYS)
    assert doc["known"] is True
    assert doc["backend"] == "index"
    assert "voicemail" in doc["caller_context"]
    assert "10:20 AM" in doc["caller_context"]
    assert "we called you from this line" in doc["caller_context"]
    got_status, got = get(http, CHRIS)
    assert got_status == 200
    assert got == doc
    assert "caller-context tool: call_id=tool-call-1 known=true ms=" in caplog.text
    assert CHRIS not in caplog.text
    assert "207" not in caplog.text
    assert TOKEN not in caplog.text
    assert "voicemail" not in caplog.text
    assert "Bearer" not in caplog.text


def test_alternate_header_and_unknown_number(http):
    seed()
    status, doc = post(http, {"call_id": "h", "phone_number": CHRIS}, header="X-Caller-Context-Token")
    assert status == 200 and doc["known"] is True
    status, doc = post(http, {"call_id": "u", "phone_number": "+19045550999"})
    assert status == 200
    assert doc == caller_context.empty_context("index")


@pytest.mark.parametrize("raw", [
    b"not json",
    b"",
    b"[1, 2]",
    b'"text"',
    b'{"call_id": 5}',
    b"x" * 4000,
])
def test_bad_body_is_empty_200(http, raw):
    seed()
    status, doc = post(http, None, raw=raw)
    assert status == 200
    assert doc == caller_context.empty_context("empty")
    assert TOKEN not in json.dumps(doc)


def test_bad_length_unfilled_template_and_query_are_empty(http):
    seed()
    status, doc = post(http, None, raw=b"{}", length="abc")
    assert (status, doc) == (200, caller_context.empty_context("empty"))
    status, doc = post(http, {"call_id": "t", "phone_number": "{{phone_number}}"})
    assert doc == caller_context.empty_context("empty")
    status, doc = post(http, {"call_id": "q"}, path="/bland/caller-context?pn=%2B12075550100")
    assert status == 200
    assert doc["known"] is False
    assert "voicemail" not in doc["caller_context"]


def test_other_path_is_404_and_get_still_requires_the_token(http):
    assert post(http, {}, path="/bland/caller-context-tool")[0] == 404
    status, doc = get(http, CHRIS, token=None)
    assert status == 401
    assert "caller_context" not in doc


def test_shared_rate_limit(http, monkeypatch):
    monkeypatch.setenv("CALLER_CONTEXT_RPM", "2")
    caller_context.reset_limits()
    assert get(http, CHRIS)[0] == 200
    assert post(http, {"call_id": "r", "phone_number": CHRIS})[0] == 200
    status, doc = post(http, {"call_id": "r2", "phone_number": CHRIS})
    assert status == 429
    assert doc == {"ok": False, "error": "rate_limited"}


def test_lookup_error_is_empty_200_without_the_exception_text(http, monkeypatch, caplog):
    def boom(phone, now=None, index=None, opener=None):
        raise RuntimeError("disk-secret")

    monkeypatch.setattr(caller_context, "lookup_caller", boom)
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        status, doc = post(http, {"call_id": "e", "phone_number": CHRIS})
    assert status == 200
    assert doc == caller_context.empty_context("empty")
    assert "disk-secret" not in json.dumps(doc)
    assert "disk-secret" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert CHRIS not in caplog.text


def test_fast_response_shape(http):
    seed()
    samples = []
    last = None
    for i in range(20):
        started = time.perf_counter()
        status, doc = post(http, {"call_id": "f%d" % i, "phone_number": CHRIS})
        samples.append(time.perf_counter() - started)
        assert status == 200
        last = doc
    samples.sort()
    p95 = samples[int(0.95 * (len(samples) - 1))]
    assert p95 < 0.3, p95
    assert set(last) == set(caller_context.TOOL_KEYS)
    assert last["known"] is True
    assert isinstance(last["caller_context"], str)
    assert isinstance(last["open_followups"], list)


def test_tool_json_and_apply_plan():
    tool = json.loads((ROOT / "caller_context_tool.json").read_text())
    assert tool["name"] == "caller_context"
    assert tool["method"] == "POST"
    assert tool["url"] == "https://prism-gateway.tailb1085a.ts.net:8443/bland/caller-context"
    assert tool["headers"]["Authorization"] == "Bearer <CALLER_CONTEXT_TOKEN>"
    assert tool["body"] == {"call_id": "{{call_id}}", "phone_number": "{{phone_number}}"}
    assert tool["input_schema"]["type"] == "object"
    assert tool["input_schema"]["properties"] == {}
    assert tool["response"]["caller_context"] == "$.caller_context"
    assert tool["timeout"] == 5000
    assert "after the greeting" in tool["description"].lower()
    blob = json.dumps(tool)
    assert TOKEN not in blob
    assert "sk-" not in blob

    plan = (ROOT / "CALLER_CONTEXT_MIDCALL_APPLY.md").read_text()
    assert "+19062406221" in plan
    assert "6221" in plan
    assert "dynamic_data" in plan
    assert "caller_context_tool.json" in plan
    assert "CALLER_CONTEXT_TOKEN" in plan
    assert "After the greeting, and not before" in plan
    assert "Do not merge" in plan or "not applied" in plan.lower()
    assert "Chris" in plan
    assert PROMPT in plan
    readme = (ROOT / "README.md").read_text()
    assert PROMPT in readme
    fixture = (ROOT / "tests" / "fixtures" / "prompts" / "inbound_blocks.txt").read_text()
    assert PROMPT in fixture
    assert "dynamic_data" in readme
    assert "removed" in readme.lower() or "to remove" in readme
