"""SMSGate sms:received webhook route (#1054)."""
import hashlib
import hmac
import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

TO = "+19045550199"
HOOK = "/smsgate-secret-path"
SG_KEY = "sg-signing-key"
ENV_KEYS = (
    "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "BLAND_WEBHOOK_SECRET", "RELAY_PATH",
    "FWD_TOKEN", "FWD_PATH", "SEND_TOKEN", "SEND_PATH", "SMSGW_URL", "SMSGW_USER",
    "SMSGW_PASS", "SMS_SEND_DISABLED", "SMSGW_HOOK_PATH", "SMSGW_SIGNING_KEY",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    bland_relay._seen_events.clear()


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


def post(base, path, body, headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        return err.code, json.loads(raw) if raw else {}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- SMSGate sms:received webhook (#1054) ---


def sg_event(sender="+19045550199", message="need a tow", event_id="Ey6ECgOkVVFjz3CL48B8C", event="sms:received"):
    return json.dumps({
        "deviceId": "dev-test",
        "event": event,
        "id": event_id,
        "payload": {
            "messageId": "abc123",
            "message": message,
            "sender": sender,
            "recipient": "+19043343975",
            "simNumber": 1,
            "receivedAt": "2026-10-03T23:00:00.000-04:00",
        },
        "webhookId": "wh-test",
    }).encode()


def sg_headers(raw, key=SG_KEY, ts=None):
    ts = str(int(bland_relay.time.time()) if ts is None else ts)
    sig = hmac.new(key.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Signature": sig}


@pytest.fixture
def sg(monkeypatch):
    monkeypatch.setenv("SMSGW_HOOK_PATH", HOOK.lstrip("/"))
    seen = []

    def fake(body):
        seen.append(json.loads(body.decode()))
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "forward", fake)
    return seen


def _inbound():
    p = bland_relay._sms_state_path()
    return json.loads(p.read_text())["inbound"] if p.exists() else {}


def test_smsgate_signature_ok_forwards_same_shape_and_allowlists(http, monkeypatch, sg):
    monkeypatch.setenv("SMSGW_SIGNING_KEY", SG_KEY)
    raw = sg_event(sender="9045550199")
    code, body = post(http, HOOK, raw, sg_headers(raw))
    assert code == 200 and body == {"ok": True}
    assert len(sg) == 1
    out = sg[0]
    assert set(out) == {"source", "channel", "line", "from", "body", "received_at", "relayed_at", "test", "raw"}
    assert out["source"] == "t-mobile-904-forwarder"
    assert out["channel"] == "sms" and out["line"] == "+19043343975"
    assert out["from"] == "9045550199"
    assert out["body"] == "need a tow"
    assert out["received_at"] == "2026-10-03T23:00:00.000-04:00"
    assert out["test"] is False
    assert TO in _inbound()
    assert bland_relay.reserve_send(TO) is None


def test_smsgate_shape_matches_forwarder_keys():
    fwd = bland_relay.normalize_904(b'{"from":"+19045550199","text":"x"}', "application/json")
    sgo = bland_relay.normalize_smsgate(json.loads(sg_event()))
    assert set(fwd) == set(sgo)
    assert {k: sgo[k] for k in ("source", "channel", "line")} == {k: fwd[k] for k in ("source", "channel", "line")}


def test_smsgate_bad_signature_is_401_and_does_not_record(http, monkeypatch, sg):
    monkeypatch.setenv("SMSGW_SIGNING_KEY", SG_KEY)
    raw = sg_event()
    code, body = post(http, HOOK, raw, sg_headers(raw, key="wrong-key"))
    assert code == 401 and body["error"] == "bad signature"
    tampered = sg_event(message="tampered")
    code, _ = post(http, HOOK, tampered, sg_headers(raw))
    assert code == 401
    assert sg == [] and _inbound() == {}


def test_smsgate_unsigned_is_401_when_key_set(http, monkeypatch, sg):
    monkeypatch.setenv("SMSGW_SIGNING_KEY", SG_KEY)
    code, body = post(http, HOOK, sg_event())
    assert code == 401 and body["error"] == "bad signature"
    assert sg == [] and _inbound() == {}


def test_smsgate_stale_timestamp_is_401(http, monkeypatch, sg):
    monkeypatch.setenv("SMSGW_SIGNING_KEY", SG_KEY)
    raw = sg_event()
    old = int(bland_relay.time.time()) - bland_relay.SMSGATE_SKEW_S - 5
    code, _ = post(http, HOOK, raw, sg_headers(raw, ts=old))
    assert code == 401
    future = int(bland_relay.time.time()) + bland_relay.SMSGATE_SKEW_S + 5
    code, _ = post(http, HOOK, raw, sg_headers(raw, ts=future))
    assert code == 401
    assert sg == [] and _inbound() == {}


def test_smsgate_signature_helper_reasons():
    raw = b"{}"
    now = 1_800_000_000
    good = hmac.new(b"k", raw + str(now).encode(), hashlib.sha256).hexdigest()
    assert bland_relay.smsgate_signature_ok("k", raw, str(now), good, now=now) == (True, "ok")
    assert bland_relay.smsgate_signature_ok("k", raw, str(now), good.upper(), now=now) == (True, "ok")
    assert bland_relay.smsgate_signature_ok("k", raw, "", good, now=now) == (False, "missing")
    assert bland_relay.smsgate_signature_ok("k", raw, "abc", good, now=now) == (False, "bad")
    assert bland_relay.smsgate_signature_ok("k", raw, str(now), "00", now=now) == (False, "bad")
    assert bland_relay.smsgate_signature_ok("k", raw, str(now - 301), good, now=now)[1] == "stale"


def test_smsgate_secret_path_only_mode_accepts_unsigned(http, sg):
    code, body = post(http, HOOK, sg_event())
    assert code == 200 and body == {"ok": True}
    assert TO in _inbound()


def test_smsgate_wrong_or_missing_path_is_404(http, monkeypatch, sg):
    code, body = post(http, "/smsgate-wrong-path", sg_event())
    assert code == 404 and body["error"] == "not found"
    code, _ = post(http, "/", sg_event())
    assert code == 404
    monkeypatch.delenv("SMSGW_HOOK_PATH")
    code, _ = post(http, HOOK, sg_event())
    assert code == 404
    assert sg == [] and _inbound() == {}


def test_smsgate_stop_sets_opt_out_and_start_clears(http, sg):
    code, _ = post(http, HOOK, sg_event(message="Stop.", event_id="e1"))
    assert code == 200
    assert _inbound()[TO]["opt_out"] is True
    assert bland_relay.reserve_send(TO) == "opt_out"
    post(http, HOOK, sg_event(message="ok thanks", event_id="e2"))
    assert _inbound()[TO]["opt_out"] is True
    post(http, HOOK, sg_event(message="UNSUBSCRIBE", event_id="e3"))
    assert _inbound()[TO]["opt_out"] is True
    post(http, HOOK, sg_event(message="START", event_id="e4"))
    assert _inbound()[TO]["opt_out"] is False
    assert len(sg) == 4


def test_smsgate_other_events_are_ignored_200(http, sg):
    code, body = post(http, HOOK, sg_event(event="sms:sent"))
    assert code == 200 and body.get("ignored") is True
    assert sg == [] and _inbound() == {}


def test_smsgate_bad_json_and_missing_sender_are_400(http, sg):
    code, _ = post(http, HOOK, b"not json")
    assert code == 400
    code, _ = post(http, HOOK, json.dumps({"event": "sms:received", "payload": {"message": "x"}}).encode())
    assert code == 400
    assert sg == []


def test_smsgate_duplicate_event_id_forwards_once(http, sg):
    raw = sg_event(event_id="dup-1")
    assert post(http, HOOK, raw)[0] == 200
    code, body = post(http, HOOK, raw)
    assert code == 200 and body.get("duplicate") is True
    assert len(sg) == 1


def test_smsgate_forward_failure_is_502_and_retry_forwards(http, monkeypatch, sg):
    calls = []
    monkeypatch.setattr(bland_relay, "forward", lambda body: calls.append(1) or (None, "HTTP 500", 2))
    monkeypatch.setattr(bland_relay, "_post_github", lambda title, text: {"ok": True, "posted": False, "skipped": "test"})
    raw = sg_event(event_id="retry-1")
    code, body = post(http, HOOK, raw)
    assert code == 502 and body["error"] == "forward failed"
    assert TO in _inbound()
    monkeypatch.setattr(bland_relay, "forward", lambda body: (200, "", 1))
    code, body = post(http, HOOK, raw)
    assert code == 200 and body == {"ok": True}


def test_smsgate_logs_have_no_body_key_or_path(http, monkeypatch, sg, caplog):
    monkeypatch.setenv("SMSGW_SIGNING_KEY", SG_KEY)
    caplog.set_level(logging.INFO, logger="bland-relay")
    raw = sg_event(message="secret body words")
    post(http, HOOK, raw, sg_headers(raw))
    post(http, HOOK, raw, sg_headers(raw, key="nope"))
    text = caplog.text
    assert "secret body words" not in text
    assert SG_KEY not in text
    assert HOOK.lstrip("/") not in text
    assert "5550199" not in text
    assert "***0199" in text


def test_scrub_strips_smsgate_secrets(monkeypatch):
    monkeypatch.setenv("SMSGW_SIGNING_KEY", "sg-key-value-xyz")
    monkeypatch.setenv("SMSGW_HOOK_PATH", "sg-path-value-xyz")
    out = bland_relay._scrub("a sg-key-value-xyz b sg-path-value-xyz")
    assert "sg-key-value-xyz" not in out and "sg-path-value-xyz" not in out


def test_old_forwarder_route_still_works_alongside_smsgate(http, monkeypatch, sg):
    monkeypatch.setenv("FWD_PATH", "fwd-secret-path")
    monkeypatch.setenv("FWD_TOKEN", "fwd-token")
    code, _ = post(http, "/fwd-secret-path", {"from": TO, "text": "hi"}, auth("fwd-token"))
    assert code == 200
    code, _ = post(http, "/fwd-secret-path", {"from": TO, "text": "hi"})
    assert code == 401
