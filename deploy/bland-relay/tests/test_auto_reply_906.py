"""906 fixed auto-reply. Gateway mocked. No live SMS and no Bland config changes."""
import hashlib
import hmac
import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

LINE = "+19062406221"
USER = "+19045550199"
OTHER = "+12025550100"
INBOUND = "need a tow"
EXACT = bland_relay.AUTO_REPLY_906_TEXT_DEFAULT


def _inline(target, args=()):
    target(*args)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "ALEXANDRA_ALERT_URL",
        "ALEXANDRA_ALERT_AUTH",
        "BLAND_WEBHOOK_SECRET",
        "RELAY_PATH",
        "SEND_TOKEN",
        "SEND_PATH",
        "SMSGW_URL",
        "SMSGW_USER",
        "SMSGW_PASS",
        "SMS_SEND_DISABLED",
        "AUTO_REPLY_906_ENABLED",
        "AUTO_REPLY_906_TEXT",
        "AUTO_REPLY_906_NUMBER",
        "AUTO_REPLY_906_COOLDOWN_S",
        "POST_CALL_INGEST",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("POST_CALL_INGEST", "off")
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    monkeypatch.setenv("SMSGW_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setenv("SMSGW_USER", "user-test")
    monkeypatch.setenv("SMSGW_PASS", "pass-test")
    monkeypatch.setattr(bland_relay, "_spawn", _inline)
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)


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


@pytest.fixture
def gateway(monkeypatch):
    calls = []

    def fake(to, message, opener=None):
        calls.append((to, message))
        return {"id": "gw-906", "state": "Pending"}, ""

    monkeypatch.setattr(bland_relay, "gateway_send", fake)
    return calls


@pytest.fixture
def forwarded(monkeypatch):
    seen = []

    def fake(body, timeout=10, call_id=None):
        seen.append(body)
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "forward", fake)
    return seen


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


def sms(phone=USER, message=INBOUND, **extra):
    payload = {
        "type": "message",
        "channel": "sms",
        "sender": "USER",
        "agent_number": LINE,
        "phone_number": phone,
        "message": message,
        "conversation_id": "conv-test",
    }
    payload.update(extra)
    return payload


def webhook(http, payload, secret="bland-secret"):
    raw = json.dumps(payload).encode()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign(secret, raw)})
    return code, body, raw


def enable(monkeypatch):
    monkeypatch.setenv("AUTO_REPLY_906_ENABLED", "1")


def test_sends_the_exact_text(http, monkeypatch, gateway, forwarded, caplog):
    enable(monkeypatch)
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        code, body, raw = webhook(http, sms())
    assert code == 200 and body == {"ok": True}
    assert forwarded == [raw]
    assert gateway == [(USER, EXACT)]
    joined = caplog.text
    assert "906 auto-reply ok" in joined
    assert "***0199" in joined and f"len={len(EXACT)}" in joined
    assert USER not in joined
    assert EXACT not in joined
    assert INBOUND not in joined
    assert "pass-test" not in joined
    state = json.dumps(json.loads(bland_relay._sms_state_path().read_text()))
    assert EXACT not in state and INBOUND not in state
    monkeypatch.setenv("AUTO_REPLY_906_TEXT", "Custom fixed reply.")
    code, body, _raw = webhook(http, sms(phone=OTHER, message="hello"))
    assert code == 200 and body == {"ok": True}
    assert gateway == [(USER, EXACT), (OTHER, "Custom fixed reply.")]


def test_cooldown(http, monkeypatch, gateway, forwarded):
    enable(monkeypatch)
    code, _body, _raw = webhook(http, sms())
    assert code == 200
    code, _body, _raw = webhook(http, sms(message="second text"))
    assert code == 200
    code, body, _raw = webhook(http, sms(message="dry", dry_run=True))
    assert code == 200 and body == {"ok": True}
    assert gateway == [(USER, EXACT)]
    path = bland_relay._sms_state_path()
    state = json.loads(path.read_text())
    # Still inside the default 24h window, then one second past it.
    state["auto_reply_906"][USER] = bland_relay.time.time() - bland_relay.AUTO_REPLY_906_COOLDOWN_DEFAULT_S + 60
    path.write_text(json.dumps(state))
    code, _body, _raw = webhook(http, sms(message="still inside"))
    assert code == 200
    assert gateway == [(USER, EXACT)]
    state = json.loads(path.read_text())
    state["auto_reply_906"][USER] = bland_relay.time.time() - bland_relay.AUTO_REPLY_906_COOLDOWN_DEFAULT_S - 60
    path.write_text(json.dumps(state))
    code, _body, _raw = webhook(http, sms(message="window open"))
    assert code == 200
    assert gateway == [(USER, EXACT), (USER, EXACT)]


def test_stop(http, monkeypatch, gateway, forwarded):
    enable(monkeypatch)
    code, _body, _raw = webhook(http, sms(message="STOP."))
    assert code == 200
    code, _body, _raw = webhook(http, sms(message="hello after stop"))
    assert code == 200
    assert gateway == []
    code, _body, _raw = webhook(http, sms(message="START"))
    assert code == 200
    assert gateway == [(USER, EXACT)]
    bland_relay.note_inbound(OTHER, "STOP")
    code, _body, _raw = webhook(http, sms(phone=OTHER, message="hi"))
    assert code == 200
    assert gateway == [(USER, EXACT)]


def test_handoff(http, monkeypatch, gateway, forwarded):
    enable(monkeypatch)
    code, _body, _raw = webhook(http, sms(metadata={"human_handoff": {"reason": "callback", "source": "api"}}))
    assert code == 200
    code, _body, _raw = webhook(http, sms(message="still handed off"))
    assert code == 200
    code, _body, _raw = webhook(
        http,
        sms(phone=OTHER, message="yard", metadata={"no_auto_replies": True}),
    )
    assert code == 200
    code, _body, _raw = webhook(http, sms(phone=OTHER, message="again"))
    assert code == 200
    code, _body, _raw = webhook(http, {
        "type": "status",
        "channel": "sms",
        "status": "human_handoff",
        "reason": "Owner: no auto-replies",
        "agent_number": LINE,
        "phone_number": "+13125550100",
        "conversation_id": "conv-marked",
    })
    assert code == 200
    code, _body, _raw = webhook(http, sms(phone="+13125550100", message="hello"))
    assert code == 200
    assert gateway == []
    code, _body, _raw = webhook(http, {
        "type": "status",
        "channel": "sms",
        "status": "human_handoff_ended",
        "agent_number": LINE,
        "phone_number": USER,
        "conversation_id": "conv-test",
    })
    assert code == 200
    code, _body, _raw = webhook(http, sms(message="after resume"))
    assert code == 200
    code, _body, _raw = webhook(http, sms(phone=OTHER, message="still marked"))
    assert code == 200
    assert gateway == [(USER, EXACT)]


def test_kill_switch(http, monkeypatch, gateway, forwarded):
    enable(monkeypatch)
    monkeypatch.setenv("SMS_SEND_DISABLED", "1")
    code, body, _raw = webhook(http, sms(dry_run=True))
    assert code == 200 and body == {"ok": True}
    code, _body, _raw = webhook(http, sms())
    assert code == 200
    assert gateway == []
    monkeypatch.delenv("SMS_SEND_DISABLED")
    code, _body, _raw = webhook(http, sms())
    assert code == 200
    assert gateway == [(USER, EXACT)]


def test_flag_off(http, monkeypatch, gateway, forwarded):
    code, body, raw = webhook(http, sms())
    assert code == 200 and body == {"ok": True}
    assert forwarded == [raw]
    assert gateway == []
    monkeypatch.setenv("AUTO_REPLY_906_ENABLED", "0")
    code, _body, _raw = webhook(http, sms(message="still off"))
    assert code == 200
    assert gateway == []
    if bland_relay._sms_state_path().exists():
        state = json.loads(bland_relay._sms_state_path().read_text())
        assert "auto_reply_906" not in state
    code, body, _raw = webhook(http, sms(), secret="nope")
    assert code == 401 and body["error"] == "bad signature"
    assert gateway == []


def test_dry_run_does_not_send(http, monkeypatch, gateway, forwarded, caplog):
    enable(monkeypatch)
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        code, body, _raw = webhook(http, sms(dry_run=True))
    assert code == 200 and body == {"ok": True}
    assert gateway == []
    joined = caplog.text
    assert "906 auto-reply dry-run" in joined
    assert "***0199" in joined and f"len={len(EXACT)}" in joined
    assert USER not in joined and EXACT not in joined
    state_path = bland_relay._sms_state_path()
    if state_path.exists():
        state = json.loads(state_path.read_text())
        assert not state.get("sends")
        assert not state.get("auto_reply_906")
    code, body, _raw = webhook(http, sms(dry_run="true"))
    assert code == 200 and body == {"ok": True}
    assert gateway == [(USER, EXACT)]


def test_agent_message_and_call_webhook_do_not_send(http, monkeypatch, gateway, forwarded):
    enable(monkeypatch)
    code, _body, _raw = webhook(http, sms(sender="AGENT", message=EXACT))
    assert code == 200
    code, _body, _raw = webhook(http, {
        "call_id": "c1",
        "to": LINE,
        "from": USER,
        "inbound": True,
        "concatenated_transcript": "user: hello",
    })
    assert code == 200
    code, _body, _raw = webhook(http, sms(agent_number="+19043343975"))
    assert code == 200
    assert gateway == []
