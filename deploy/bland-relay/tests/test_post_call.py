"""Post-call write-back (#1231). Fixtures only: no Bland, no calendar, no live webhook."""
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import stat
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
import post_call

FIX = Path(__file__).resolve().parent / "fixtures" / "post_call"
ET = caller_context.ET
ROOT = Path(__file__).resolve().parent.parent
PII = (caller_context.EMAIL_RE, caller_context.CARD_RE, caller_context.ADDRESS_RE, caller_context.PLATE_RE)
CLASSES = (
    ("meeting_confirm_match.json", "meeting-confirmation-requested"),
    ("meeting_confirm_nomatch.json", "meeting-confirmation-requested"),
    ("meeting_confirm_summary.json", "meeting-confirmation-requested"),
    ("meeting_reschedule.json", "meeting-reschedule-requested"),
    ("callback.json", "callback-requested"),
    ("voicemail.json", "voicemail"),
    ("concurrency.json", "concurrency-rejected"),
    ("info_only.json", "info-only"),
    ("unknown.json", "unknown"),
    ("sensitive.json", "info-only"),
)
FOLLOWUP = {
    "meeting-confirmation-requested",
    "meeting-reschedule-requested",
    "callback-requested",
    "concurrency-rejected",
}


def load(name):
    return json.loads((FIX / name).read_text())


def seed_attempt(call_id, name, start_iso, result, ts):
    path = Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "meeting_attempts.sqlite"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE IF NOT EXISTS attempts (call_id TEXT, ts REAL, stated_name TEXT, "
        "stated_start TEXT, result INTEGER)"
    )
    db.execute("INSERT INTO attempts VALUES (?,?,?,?,?)", (call_id, ts, name, start_iso, 1 if result else 0))
    db.commit()
    db.close()
    os.chmod(path, 0o600)


def outbox():
    path = Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "crm_outbox.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def index_rows():
    path = Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "caller_index.sqlite"
    if not path.is_file():
        return []
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        cols = [row[1] for row in db.execute("PRAGMA table_info(interaction)")]
        qa = ", qa" if "qa" in cols else ""
        return [dict(row) for row in db.execute(f"SELECT *{'' if not qa else ''} FROM interaction").fetchall()]
    finally:
        db.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "BLAND_WEBHOOK_SECRET", "RELAY_PATH",
        "POST_CALL_INGEST", "POST_CALL_PUSH_DISABLED", "CRM_INGEST_MODE", "CRM_OUTBOX_PATH",
        "CALLER_INDEX_PATH", "CRM_API_BASE", "CRM_API_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    monkeypatch.setattr(post_call, "_SLEEP", lambda _s: None)


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


def post(base, path, body, headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        return err.code, json.loads(raw) if raw else {}


def _sign(secret, body):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _Alex(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        _Alex.seen.append(json.loads(raw.decode()))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, fmt, *args):
        return


@pytest.fixture
def alex():
    _Alex.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Alex)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address
    os.environ["ALEXANDRA_ALERT_URL"] = f"http://{host}:{port}/hook"
    try:
        yield _Alex.seen
    finally:
        srv.shutdown()
        srv.server_close()
        os.environ.pop("ALEXANDRA_ALERT_URL", None)


def _prepare(name):
    payload = load(name)
    if name == "meeting_confirm_match.json":
        seed_attempt("call-match-1", "Alex Rivera", "2026-10-19T19:00:00Z", True, 2)
    elif name == "meeting_confirm_nomatch.json":
        seed_attempt("call-nomatch-1", "Alex Rivera", "2026-10-19T19:00:00Z", False, 2)
    return payload


def _assert_clean(record):
    assert "body" not in record and "transcript" not in record
    text = json.dumps(record)
    for pattern in PII:
        assert pattern.search(text) is None
    for secret in ("TRANSCRIPT_ONLY_SECRET", "VARIABLE_ONLY_SECRET", "ASSISTANT_ONLY_SECRET", "ada@example.com"):
        assert secret not in text
    assert record["party_hint"]["display_name"] in (None, "Alex Rivera")


@pytest.mark.parametrize("name,kind", CLASSES)
def test_classifier_fixtures(name, kind):
    payload = _prepare(name)
    now = datetime(2026, 10, 19, 8, 0, tzinfo=ET)
    post_call.handle(json.dumps(payload).encode(), now=now)
    [row] = outbox()
    assert row["outcome"] == kind
    assert row["schema"] == "panamerica.crm.interaction.v1"
    assert row["channel"] == "call" and row["provider"] == "bland"
    assert row["provider_msg_id"] == payload["call_id"]
    assert (kind in FOLLOWUP) is ("followup" in row)
    _assert_clean(row)
    if name == "sensitive.json":
        assert "Oak" not in row["summary"] and "4111" not in row["summary"] and "ABC1234" not in row["summary"]
    if name == "meeting_confirm_match.json":
        assert row["party_hint"]["display_name"] == "Alex Rivera"
        assert "Jordan Quinn" not in json.dumps(row)
        assert row["followup"]["title"] == "Confirm Mon Oct 19 3:00 PM meeting with Alex R."
        assert row["followup"]["due_at"] == "2026-10-19T09:00:00-04:00"
        assert row["followup"]["calendar_check"] == "match"
        assert row["followup"]["assignee_seat"] == "grok"
        assert row["followup"]["kind"] == kind
    if name == "meeting_confirm_nomatch.json":
        assert row["followup"]["calendar_check"] == "no-match"
        assert row["followup"]["title"] == "Confirm Mon Oct 19 3:00 PM meeting with Alex R."
    if name == "info_only.json":
        assert row["party_hint"]["type"] == "vendor"
    if name == "voicemail.json":
        assert row["direction"] == "out"
    mode = Path(os.environ["BLAND_RELAY_STATE_DIR"], "crm_outbox.jsonl").stat().st_mode & 0o777
    assert mode == 0o600


def test_summary_only_says_see_call_summary_and_replays_in_et():
    payload = load("meeting_confirm_summary.json")
    now = datetime(2026, 10, 16, 18, 39, tzinfo=ET)
    post_call.handle(json.dumps(payload).encode(), now=now)
    [row] = outbox()
    assert row["outcome"] == "meeting-confirmation-requested"
    assert row["party_hint"]["display_name"] is None
    assert row["followup"]["calendar_check"] == "not-run"
    assert row["followup"]["title"] == "Confirm meeting — see call summary."
    assert row["followup"]["due_at"] == "2026-10-17T09:00:00-04:00"
    assert "see call summary" in row["followup"]["title"]
    got = caller_context.lookup_caller("+12395550101", now=datetime(2026, 10, 17, 12, 0, tzinfo=ET))
    assert "confirm" in got["caller_context"].lower()
    assert "Open follow-up" in got["caller_context"]
    assert "see call summary" in got["caller_context"]
    assert got["last_contact_at"].endswith("ET")
    assert "6:39 PM" in got["last_contact_at"]


def test_due_at_et_dst_and_weekend():
    def at(y, m, d, hh, mm):
        return datetime(y, m, d, hh, mm, tzinfo=ET)

    assert post_call._et_iso(post_call.followup_due(at(2026, 10, 17, 8, 0), None)) == "2026-10-17T09:00:00-04:00"
    assert post_call._et_iso(post_call.followup_due(at(2026, 10, 17, 10, 0), None)) == "2026-10-18T09:00:00-04:00"
    assert post_call._et_iso(post_call.followup_due(at(2026, 1, 14, 10, 0), None)) == "2026-01-15T09:00:00-05:00"
    meeting = at(2026, 10, 19, 15, 0)
    assert post_call._et_iso(post_call.followup_due(at(2026, 10, 19, 10, 0), meeting)) == "2026-10-19T14:00:00-04:00"
    assert post_call._et_iso(post_call.followup_due(at(2026, 10, 19, 14, 30), meeting)) == "2026-10-20T09:00:00-04:00"


def test_latest_attempt_wins_and_c_id_is_the_fallback():
    seed_attempt("call-match-1", "Old Name", "2026-10-19T19:00:00Z", True, 1)
    seed_attempt("call-match-1", "Alex Rivera", "2026-10-20T19:00:00Z", False, 5)
    payload = load("meeting_confirm_match.json")
    post_call.handle(json.dumps(payload).encode(), now=datetime(2026, 10, 19, 8, 0, tzinfo=ET))
    [row] = outbox()
    assert row["followup"]["calendar_check"] == "no-match"
    assert row["party_hint"]["display_name"] == "Alex Rivera"
    assert "Tue Oct 20 3:00 PM" in row["followup"]["title"]
    only_c = {
        "c_id": "cid-only-1", "from": "+12395550117", "to": "+19062406221", "inbound": True,
        "completed": True, "summary": "Asked what trucks are available next month.",
        "created_at": "2026-10-18T16:00:00Z",
    }
    post_call.handle(json.dumps(only_c).encode(), now=datetime(2026, 10, 19, 8, 0, tzinfo=ET))
    assert {r["provider_msg_id"] for r in outbox()} >= {"call-match-1", "cid-only-1"}


def test_duplicate_delivery_writes_one_of_each(alex):
    payload = load("meeting_confirm_summary.json")
    now = datetime(2026, 10, 16, 18, 39, tzinfo=ET)
    raw = json.dumps(payload).encode()
    post_call.handle(raw, now=now)
    post_call.handle(raw, now=now)
    assert len(outbox()) == 1
    assert len(index_rows()) == 1
    assert len(alex) == 1
    event = alex[0]
    assert event["kind"] == "call_followup"
    assert event["schema"] == "panamerica.call.followup.v1"
    assert event["class"] == "meeting-confirmation-requested"
    assert event["call_id"] == "3730e695-shape"
    assert event["calendar_check"] == "not-run"
    assert event["stated_name"] is None
    assert event["stated_meeting"] is None
    assert "TRANSCRIPT" not in json.dumps(event)


def test_off_line_call_writes_nothing(alex):
    payload = {
        "call_id": "other-line", "from": "+12395550198", "to": "+12395550199",
        "inbound": True, "completed": True, "summary": "Please confirm the meeting.",
        "created_at": "2026-10-18T16:00:00Z",
    }
    post_call.handle(json.dumps(payload).encode(), now=datetime(2026, 10, 19, 8, 0, tzinfo=ET))
    assert outbox() == []
    assert index_rows() == []
    assert alex == []


def test_kill_switches(monkeypatch, alex):
    payload = load("callback.json")
    raw = json.dumps(payload).encode()
    now = datetime(2026, 10, 16, 12, 0, tzinfo=ET)
    monkeypatch.setenv("POST_CALL_INGEST", "off")
    post_call.handle(raw, now=now)
    assert outbox() == [] and index_rows() == [] and alex == []
    monkeypatch.setenv("POST_CALL_INGEST", "on")
    monkeypatch.setenv("POST_CALL_PUSH_DISABLED", "1")
    post_call.handle(raw, now=now)
    assert len(outbox()) == 1 and len(index_rows()) == 1 and alex == []
    monkeypatch.delenv("POST_CALL_PUSH_DISABLED")
    monkeypatch.setenv("CRM_INGEST_MODE", "off")
    other = dict(payload, call_id="call-callback-2")
    post_call.handle(json.dumps(other).encode(), now=now)
    assert [row["provider_msg_id"] for row in outbox()] == ["call-callback-1"]
    assert len(index_rows()) == 2
    assert len(alex) == 1 and alex[0]["call_id"] == "call-callback-2"


def test_qa_skips_followup_and_push_and_tags_the_index(alex):
    payload = load("meeting_confirm_summary.json")
    payload["metadata"] = dict(payload["metadata"], qa=True)
    post_call.handle(json.dumps(payload).encode(), now=datetime(2026, 10, 16, 18, 39, tzinfo=ET))
    [row] = outbox()
    assert row["outcome"] == "meeting-confirmation-requested"
    assert "followup" not in row
    assert row["metadata"]["qa"] is True
    assert alex == []
    [stored] = index_rows()
    assert stored["qa"] == 1
    assert "confirm" in stored["followup"].lower()


def test_logs_omit_name_number_summary_and_token(caplog, monkeypatch):
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "super-secret-token-value")
    seed_attempt("call-match-1", "Alex Rivera", "2026-10-19T19:00:00Z", True, 1)
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        post_call.handle(json.dumps(load("meeting_confirm_match.json")).encode(),
                         now=datetime(2026, 10, 19, 8, 0, tzinfo=ET))
    text = caplog.text
    assert "post-call: call_id=call-match-1 class=meeting-confirmation-requested" in text
    for secret in ("Alex Rivera", "Alex R.", "+12395550111", "2395550111", "hung up", "Jordan Quinn",
                   "super-secret-token-value", "thanks"):
        assert secret not in text


def test_hook_failure_stays_inside_the_hook(caplog, monkeypatch):
    def boom(record):
        raise RuntimeError("Alex Rivera +12395550111 hung up")

    class Sink:
        def emit(self, record):
            boom(record)

    monkeypatch.setattr(bland_relay, "crm_sink", lambda: Sink())
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        post_call.handle(json.dumps(load("info_only.json")).encode(),
                         now=datetime(2026, 10, 19, 8, 0, tzinfo=ET))
    assert "post-call: failed: RuntimeError" in caplog.text
    assert "Alex Rivera" not in caplog.text and "hung up" not in caplog.text and "+12395550117" not in caplog.text


def test_http_hook_runs_before_forward_and_does_not_change_status(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    order = []
    real = post_call.handle

    def wrapped(body, now=None):
        order.append("hook")
        return real(body, now=now)

    monkeypatch.setattr(post_call, "handle", wrapped)
    monkeypatch.setattr(bland_relay, "forward", lambda body, timeout=10, call_id=None: order.append("forward") or (200, "", 1))
    raw = json.dumps(load("info_only.json")).encode()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("bland-secret", raw)})
    assert code == 200 and body == {"ok": True}
    assert order[0] == "hook" and "forward" in order
    monkeypatch.setattr(bland_relay, "forward", lambda body, timeout=10, call_id=None: (None, "HTTP 500", 2))
    monkeypatch.setattr(bland_relay, "_post_github", lambda *a, **k: {"ok": True, "posted": False, "skipped": "test"})
    raw = json.dumps(dict(load("info_only.json"), call_id="call-info-fail")).encode()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("bland-secret", raw)})
    assert code == 502 and body["error"] == "forward failed"
    assert {row["provider_msg_id"] for row in outbox()} >= {"call-info-1", "call-info-fail"}


def test_slow_hook_does_not_change_latency_or_status(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    gate = threading.Event()
    spawned = []

    class Slow:
        def emit(self, record):
            gate.wait(5)

    def real_spawn(target, args=()):
        thread = threading.Thread(target=target, args=args, daemon=True)
        spawned.append(thread)
        thread.start()

    monkeypatch.setattr(bland_relay, "crm_sink", lambda: Slow())
    monkeypatch.setattr(bland_relay, "_spawn", real_spawn)
    monkeypatch.setattr(bland_relay, "forward", lambda body, timeout=10, call_id=None: (200, "", 1))
    raw = json.dumps(load("info_only.json")).encode()
    started = time.perf_counter()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("bland-secret", raw)})
    assert code == 200 and body == {"ok": True}
    assert time.perf_counter() - started < 0.5
    monkeypatch.setattr(bland_relay, "forward", lambda body, timeout=10, call_id=None: (None, "HTTP 500", 2))
    monkeypatch.setattr(bland_relay, "_post_github", lambda *a, **k: {"ok": True, "posted": False, "skipped": "test"})
    raw = json.dumps(dict(load("info_only.json"), call_id="call-info-slow")).encode()
    started = time.perf_counter()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("bland-secret", raw)})
    assert code == 502 and body["error"] == "forward failed"
    assert time.perf_counter() - started < 0.5
    gate.set()
    for thread in spawned:
        thread.join(2)


def test_unsigned_qa_webhook_is_rejected(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    payload = load("meeting_confirm_summary.json")
    payload["metadata"] = dict(payload["metadata"], qa=True)
    raw = json.dumps(payload).encode()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": "00"})
    assert code == 401 and body["error"] == "bad signature"
    assert outbox() == [] and index_rows() == []


def test_docs_and_source_do_not_call_bland():
    src = (ROOT / "post_call.py").read_text()
    assert "api.bland.ai" not in src and "urllib" not in src and "smtp" not in src.lower()
    assert "import post_call" in (ROOT / "relay_config.py").read_text()
    http = (ROOT / "relay_http.py").read_text()
    assert http.index("_spawn(post_call.handle, (body,))") < http.index("status, err, attempts = forward(body)")
    readme = (ROOT / "README.md").read_text()
    for needle in (
        "post_call.handle", "panamerica.call.followup.v1", "POST_CALL_INGEST",
        "POST_CALL_PUSH_DISABLED", "call_followup", "see call summary",
        "post_call_deadletter.jsonl", "--replay-dead-letters", "concurrency-rejected",
    ):
        assert needle in readme
    setkey = (ROOT / "bland-relay-setkey").read_text()
    assert "POST_CALL_INGEST" in setkey and "POST_CALL_PUSH_DISABLED" in setkey
    assert "build_record" in src


def _clock_and_sleep():
    now = {"t": 1000.0}
    slept = []

    def clock():
        return now["t"]

    def sleep(seconds):
        slept.append(seconds)
        now["t"] += seconds

    return now, slept, clock, sleep


def test_concurrency_followup_only_for_valid_e164(alex):
    now = datetime(2026, 10, 19, 8, 0, tzinfo=ET)
    valid = load("concurrency.json")
    invalid = dict(valid, call_id="call-busy-bad", to="not-a-number")
    post_call.handle(json.dumps(valid).encode(), now=now)
    post_call.handle(json.dumps(invalid).encode(), now=now)
    rows = {row["provider_msg_id"]: row for row in outbox()}
    assert set(rows) == {"call-busy-1", "call-busy-bad"}
    assert rows["call-busy-1"]["outcome"] == "concurrency-rejected"
    assert rows["call-busy-1"]["followup"]["kind"] == "concurrency-rejected"
    assert rows["call-busy-1"]["followup"]["title"].startswith("Call")
    assert "followup" not in rows["call-busy-bad"]
    assert rows["call-busy-bad"]["party_hint"]["phone"] is None
    stored = {row["provider_msg_id"]: row for row in index_rows()}
    assert set(stored) == {"call:call-busy-1", "call:call-busy-bad"}
    assert stored["call:call-busy-1"]["outcome"] == "concurrency-rejected"
    assert stored["call:call-busy-bad"]["outcome"] == "concurrency-rejected"
    assert alex == []


def test_hangup_before_greeting_writes_no_followup(alex):
    now = datetime(2026, 10, 19, 8, 0, tzinfo=ET)
    payload = {
        "call_id": "call-short-1",
        "from": "+12395550131",
        "to": "+19062406221",
        "inbound": True,
        "completed": True,
        "status": "completed",
        "answered_by": "human",
        "call_length": 0.05,
        "summary": "Caller hung up.",
        "transcripts": [{"user": "assistant", "text": "Hello, Panamerica."}],
        "created_at": "2026-10-18T16:00:00Z",
        "metadata": {"purpose": "inbound", "placed_by": "caller"},
    }
    post_call.handle(json.dumps(payload).encode(), now=now)
    [row] = outbox()
    assert row["outcome"] == "short"
    assert "followup" not in row
    [stored] = index_rows()
    assert stored["outcome"] == "short"
    assert stored["followup"] == ""
    assert alex == []
    longer = dict(payload, call_id="call-short-2", call_length=0.2)
    post_call.handle(json.dumps(longer).encode(), now=now)
    spoke = dict(payload, call_id="call-short-3", transcripts=[{"user": "user", "text": "Hi"}])
    post_call.handle(json.dumps(spoke).encode(), now=now)
    kinds = {row["provider_msg_id"]: row["outcome"] for row in outbox()}
    assert kinds["call-short-2"] == "info-only"
    assert kinds["call-short-3"] == "info-only"
    assert len(alex) == 0


def test_agent_error_and_transfer_classify_with_callback(alex):
    now = datetime(2026, 10, 19, 8, 0, tzinfo=ET)
    base = {
        "from": "+12395550121",
        "to": "+19062406221",
        "inbound": True,
        "completed": False,
        "created_at": "2026-10-18T16:00:00Z",
        "summary": "Line dropped.",
        "metadata": {"purpose": "fleet", "placed_by": "caller"},
    }
    err = dict(base, call_id="call-agent-1", error_message="model timeout", status="error")
    transferred = dict(base, call_id="call-xfer-1", completed=True, status="completed", transferred_to="+19045550199")
    post_call.handle(json.dumps(err).encode(), now=now)
    post_call.handle(json.dumps(transferred).encode(), now=now)
    rows = {row["provider_msg_id"]: row for row in outbox()}
    assert rows["call-agent-1"]["outcome"] == "agent-error"
    assert rows["call-xfer-1"]["outcome"] == "transferred"
    for row in rows.values():
        assert row["followup"]["kind"] == row["outcome"]
        assert row["followup"]["title"].startswith("Call")
        assert row["followup"]["assignee_seat"] == "grok"
    assert len(index_rows()) == 2
    assert {event["class"] for event in alex} == {"agent-error", "transferred"}
    assert len(alex) == 2
    assert "+19045550199" not in json.dumps(rows)


def test_push_failure_retries_then_dead_letters(monkeypatch, caplog):
    _now, slept, clock, sleep = _clock_and_sleep()
    monkeypatch.setattr(post_call, "_CLOCK", clock)
    monkeypatch.setattr(post_call, "_SLEEP", sleep)
    monkeypatch.setenv("ALEXANDRA_ALERT_URL", "http://127.0.0.1:9/hook")
    pushes = {"n": 0}

    def forward(body, timeout=10, call_id=None):
        doc = json.loads(body)
        if isinstance(doc, dict) and doc.get("kind") == "call_followup":
            pushes["n"] += 1
            return None, "down", 1
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "forward", forward)
    payload = load("callback.json")
    payload["transcripts"] = [{"user": "user", "text": "TRANSCRIPT_ONLY_SECRET call me back"}]
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        post_call.handle(json.dumps(payload).encode(), now=datetime(2026, 10, 19, 8, 0, tzinfo=ET))
    assert slept == [30, 120, 600]
    assert pushes["n"] == 4
    path = Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "post_call_deadletter.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    [line] = [json.loads(item) for item in text.splitlines() if item.strip()]
    assert line["done"] is False
    assert line["call_id"] == "call-callback-1"
    assert "body" not in line["record"] and "transcript" not in line["record"]
    assert "TRANSCRIPT_ONLY_SECRET" not in text
    assert "post-call: dead-letter call_id=call-callback-1" in caplog.text
    assert "TRANSCRIPT_ONLY_SECRET" not in caplog.text
    assert "+12395550114" not in caplog.text


def test_outbox_write_failure_dead_letters_and_status_unchanged(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    real = bland_relay.crm_sink

    def sink():
        inner = real()

        class Wrap:
            def emit(self, record):
                raise OSError("disk")

        return Wrap()

    monkeypatch.setattr(bland_relay, "crm_sink", sink)
    monkeypatch.setattr(bland_relay, "forward", lambda body, timeout=10, call_id=None: (200, "", 1))
    raw = json.dumps(load("info_only.json")).encode()
    started = time.perf_counter()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("bland-secret", raw)})
    assert code == 200 and body == {"ok": True}
    assert time.perf_counter() - started < 0.5
    path = Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "post_call_deadletter.jsonl"
    assert path.is_file()
    assert "transcript" not in path.read_text()
    assert len(index_rows()) == 1
    assert outbox() == []


def test_dead_letter_replay_is_idempotent(alex, monkeypatch):
    state = {"fail": True}
    real_sink = bland_relay.crm_sink
    real_forward = bland_relay.forward

    def sink():
        inner = real_sink()

        class Wrap:
            def emit(self, record):
                if state["fail"]:
                    raise OSError("disk")
                inner.emit(record)

        return Wrap()

    def forward(body, timeout=10, call_id=None):
        doc = json.loads(body)
        if isinstance(doc, dict) and doc.get("kind") == "call_followup" and state["fail"]:
            return None, "down", 1
        return real_forward(body, timeout=timeout, call_id=call_id)

    monkeypatch.setattr(bland_relay, "crm_sink", sink)
    monkeypatch.setattr(bland_relay, "forward", forward)
    payload = load("callback.json")
    payload["transcripts"] = [{"user": "user", "text": "TRANSCRIPT_ONLY_SECRET"}]
    post_call.handle(json.dumps(payload).encode(), now=datetime(2026, 10, 19, 8, 0, tzinfo=ET))
    path = Path(os.environ["BLAND_RELAY_STATE_DIR"]) / "post_call_deadletter.jsonl"
    before = path.read_text().splitlines()
    assert len(before) == 1 and json.loads(before[0])["done"] is False
    assert outbox() == [] and alex == []
    state["fail"] = False
    assert post_call.main(["--replay-dead-letters"]) == 0
    assert len(outbox()) == 1
    assert len(index_rows()) == 1
    assert len(alex) == 1 and alex[0]["class"] == "callback-requested"
    assert post_call.replay_dead_letters() == 0
    after = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(after) == 1
    assert after[0]["done"] is True
    assert "TRANSCRIPT_ONLY_SECRET" not in path.read_text()
    assert len(outbox()) == 1 and len(index_rows()) == 1 and len(alex) == 1
