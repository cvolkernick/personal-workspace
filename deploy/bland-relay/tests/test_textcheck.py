"""904 inbound text -> Alexandra textcheck webhook. One POST, fail-soft."""

import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

URL = "https://textcheck.example.invalid/hook"
KEY = "textcheck-test-key"
REC = {
    "from": "+19045550199",
    "body": "need a tow",
    "received_at": "2026-10-03T23:00:00.000-04:00",
    "line": "+19043343975",
}


class Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    for key in (
        "ALEXANDRA_TEXTCHECK_WEBHOOK_URL",
        "ALEXANDRA_TEXTCHECK_WEBHOOK_KEY",
        "HELM_INBOUND_SMS_FORWARD",
        "HELM_TURO_WEBHOOK_URL",
        "HELM_TURO_WEBHOOK_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))


def _calls():
    seen = []

    def opener(req, timeout=None):
        seen.append({
            "timeout": timeout,
            "auth": req.get_header("Authorization"),
            "ctype": req.get_header("Content-type"),
            "body": json.loads(req.data.decode()),
            "url": req.full_url,
        })
        return Resp(204)

    return seen, opener


def test_payload_shape_and_bearer_header_not_logged(isolated, monkeypatch, caplog):
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", URL)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", KEY)
    seen, opener = _calls()
    caplog.set_level(logging.INFO)
    bland_relay.textcheck_post(REC, "smsgate:abc123", opener=opener)
    assert len(seen) == 1
    body = seen[0]["body"]
    assert set(body) == {"line", "from", "to", "body", "received_at", "message_id"}
    assert body["line"] == "+19043343975"
    assert body["to"] == "+19043343975"
    assert body["from"] == "+19045550199"
    assert body["body"] == "need a tow"
    assert body["received_at"] == "2026-10-04T03:00:00Z"
    assert body["message_id"] == "smsgate:abc123"
    assert seen[0]["auth"] == bland_relay.textcheck_authorization(KEY)
    assert seen[0]["auth"] == "Bearer " + KEY
    assert seen[0]["ctype"] == "application/json"
    assert seen[0]["timeout"] == bland_relay.TEXTCHECK_TIMEOUT_S
    text = caplog.text
    assert "textcheck webhook sent status=204" in text
    assert KEY not in text
    assert URL not in text
    assert "Authorization" not in text
    assert "Bearer " not in text


def test_duplicate_message_id_posts_once(isolated, monkeypatch):
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", URL)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", KEY)
    seen, opener = _calls()
    bland_relay.textcheck_post(REC, "smsgate:abc123", opener=opener)
    bland_relay.textcheck_post(dict(REC, body="same id, retry"), "smsgate:abc123", opener=opener)
    assert len(seen) == 1


def test_http_error_and_exception_are_swallowed(isolated, monkeypatch, caplog):
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", URL)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", KEY)
    caplog.set_level(logging.INFO)

    def http_error(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "no", None, None)

    bland_relay.textcheck_post(REC, "smsgate:http", opener=http_error)
    assert "textcheck webhook failed status=503" in caplog.text

    def boom(req, timeout=None):
        raise TimeoutError("timed out " + req.full_url)

    bland_relay.textcheck_post(REC, "smsgate:boom", opener=boom)
    assert "textcheck webhook failed status=err" in caplog.text
    assert URL not in caplog.text
    assert KEY not in caplog.text
    assert "Bearer " not in caplog.text


def test_unset_vars_make_no_call(isolated, monkeypatch, caplog):
    seen, opener = _calls()
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", KEY)
    bland_relay.textcheck_post(REC, "smsgate:a", opener=opener)
    monkeypatch.delenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", raising=False)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", URL)
    bland_relay.textcheck_post(REC, "smsgate:b", opener=opener)
    monkeypatch.delenv("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", raising=False)
    bland_relay.textcheck_post(REC, "smsgate:c", opener=opener)
    assert seen == []
    assert "textcheck webhook" not in caplog.text


def test_spawn_is_a_daemon_and_helm_dispatch_stays(isolated, monkeypatch):
    spawned = []
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): spawned.append(target))
    monkeypatch.setenv("HELM_INBOUND_SMS_FORWARD", "1")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "https://helm.example.invalid/hook")
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", URL)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", KEY)
    threads = []

    class T:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon
            threads.append(self)

        def start(self):
            return None

    monkeypatch.setattr(bland_relay.threading, "Thread", T)
    bland_relay.helm_sms_spawn(REC, "smsgate:abc123")
    assert spawned == [bland_relay.helm_forward_inbound_sms]
    assert len(threads) == 1 and threads[0].daemon is True
    assert threads[0].args[1] == "smsgate:abc123"


def test_unset_does_not_start_a_thread(isolated, monkeypatch):
    started = []

    class T:
        def __init__(self, *args, **kwargs):
            started.append(1)

        def start(self):
            return None

    monkeypatch.setattr(bland_relay.threading, "Thread", T)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", KEY)
    bland_relay.helm_sms_spawn(REC, "smsgate:abc123")
    assert started == []


def test_accepted_smsgate_posts_once_and_still_forwards(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SMSGW_HOOK_PATH", "smsgate-secret-path")
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", URL)
    monkeypatch.setenv("ALEXANDRA_TEXTCHECK_WEBHOOK_KEY", KEY)
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): None)
    bland_relay._seen_events.clear()
    bland_relay._recent_msgs.clear()
    forwarded = []
    monkeypatch.setattr(
        bland_relay, "forward",
        lambda body: forwarded.append(json.loads(body.decode())) or (200, "", 1),
    )
    posted = []
    ready = threading.Event()
    real_open = urllib.request.urlopen

    def urlopen(req, timeout=None):
        posted.append(json.loads(req.data.decode()))
        ready.set()
        return Resp(204)

    monkeypatch.setattr(bland_relay.urllib.request, "urlopen", urlopen)
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    raw = json.dumps({
        "event": "sms:received",
        "id": "evt-1",
        "payload": {
            "messageId": "abc123",
            "message": "need a tow",
            "sender": "+19045550199",
            "recipient": "+19043343975",
            "receivedAt": "2026-10-03T23:00:00.000-04:00",
        },
    }).encode()
    try:
        req = urllib.request.Request(
            "http://%s:%s/smsgate-secret-path" % (host, port),
            data=raw, headers={"Content-Type": "application/json"}, method="POST",
        )
        with real_open(req, timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode()) == {"ok": True}
        assert ready.wait(2)
        assert len(forwarded) == 1
        assert forwarded[0]["body"] == "need a tow"
        assert forwarded[0]["line"] == "+19043343975"
        assert len(posted) == 1
        assert posted[0]["message_id"] == "smsgate:abc123"
        req2 = urllib.request.Request(
            "http://%s:%s/smsgate-secret-path" % (host, port),
            data=raw, headers={"Content-Type": "application/json"}, method="POST",
        )
        with real_open(req2, timeout=5) as resp:
            assert json.loads(resp.read().decode())["duplicate"] is True
        assert len(posted) == 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_names_are_documented_and_setkey_allows_them():
    readme = (bland_relay.Path(__file__).resolve().parents[1] / "README.md").read_text()
    setkey = (bland_relay.Path(__file__).resolve().parents[1] / "bland-relay-setkey").read_text()
    for name in ("ALEXANDRA_TEXTCHECK_WEBHOOK_URL", "ALEXANDRA_TEXTCHECK_WEBHOOK_KEY"):
        assert name in bland_relay.SECRET_ENV_KEYS
        assert name in readme
        assert name in setkey
    assert "~/.config/bland-relay/env" in readme
