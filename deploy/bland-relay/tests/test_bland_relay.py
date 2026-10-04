"""Relay send route, 904 inbound, and Bland webhook regressions."""
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
BODY_TEXT = "tow truck is twenty minutes out"


def _inline(target, args=()):
    target(*args)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "ALEXANDRA_ALERT_URL",
        "ALEXANDRA_ALERT_AUTH",
        "BLAND_WEBHOOK_SECRET",
        "RELAY_PATH",
        "FWD_TOKEN",
        "FWD_PATH",
        "SEND_TOKEN",
        "SEND_PATH",
        "SMSGW_URL",
        "SMSGW_USER",
        "SMSGW_PASS",
        "SMS_SEND_DISABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
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


def configure_send(monkeypatch, token="tok-test-value"):
    monkeypatch.setenv("SEND_PATH", "send-secret-path")
    monkeypatch.setenv("SEND_TOKEN", token)
    monkeypatch.setenv("SMSGW_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setenv("SMSGW_USER", "user-test")
    monkeypatch.setenv("SMSGW_PASS", "pass-test")


def auth(token="tok-test-value"):
    return {"Authorization": f"Bearer {token}"}


def allow(sender=TO, text="hi there"):
    bland_relay.note_inbound(sender, text)


def test_bad_and_missing_token_are_401(http, monkeypatch):
    configure_send(monkeypatch)
    allow()
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hi"}, {"X-Relay-Token": "nope"})
    assert code == 401
    assert body["error"] == "unauthorized"
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hi"})
    assert code == 401
    assert body["error"] == "unauthorized"


def test_kill_switch_503_even_with_a_valid_token(http, monkeypatch):
    configure_send(monkeypatch)
    monkeypatch.setenv("SMS_SEND_DISABLED", "1")
    allow()
    called = {}
    monkeypatch.setattr(bland_relay, "gateway_send", lambda *a, **k: called.setdefault("hit", True))
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hi"}, auth())
    assert code == 503
    assert body["error"] == "send disabled"
    assert "hit" not in called


def test_missing_gateway_config_is_503(http, monkeypatch):
    monkeypatch.setenv("SEND_PATH", "send-secret-path")
    monkeypatch.setenv("SEND_TOKEN", "tok-test-value")
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hi"}, auth())
    assert code == 503
    assert body["error"] == "relay not configured"


def test_validation_refuses_bad_to_and_message(http, monkeypatch):
    configure_send(monkeypatch)
    allow()
    code, body = post(http, "/send-secret-path", {"to": "+441234567890", "message": "hi"}, auth())
    assert code == 400 and body["error"] == "bad to"
    code, body = post(http, "/send-secret-path", {"to": "9045550199", "message": "hi"}, auth())
    assert code == 400 and body["error"] == "bad to"
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "   "}, auth())
    assert code == 400 and body["error"] == "bad message"
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "x" * 641}, auth())
    assert code == 400 and body["error"] == "bad message"
    code, body = post(http, "/send-secret-path", {"to": TO}, auth())
    assert code == 400 and body["error"] == "bad message"


def test_non_allowlisted_to_is_refused(http, monkeypatch):
    configure_send(monkeypatch)
    called = {}
    monkeypatch.setattr(bland_relay, "gateway_send", lambda *a, **k: called.setdefault("hit", True))
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hi"}, auth())
    assert code == 403
    assert body["error"] == "not_allowlisted"
    assert "hit" not in called


def test_allowlisted_send_returns_gateway_id(http, monkeypatch):
    configure_send(monkeypatch)
    allow("+1 (904) 555-0199", "need a tow")
    seen = {}

    def fake(to, message, opener=None):
        seen["to"] = to
        seen["message"] = message
        return {"id": "gw-1", "state": "Pending"}, ""

    monkeypatch.setattr(bland_relay, "gateway_send", fake)
    code, body = post(
        http,
        "/send-secret-path",
        {"to": TO, "message": "  " + BODY_TEXT + "  ", "in_reply_to": "inbound-1"},
        {"X-Relay-Token": "tok-test-value"},
    )
    assert code == 200
    assert body == {"ok": True, "id": "gw-1", "state": "Pending"}
    assert seen == {"to": TO, "message": BODY_TEXT}


def test_message_of_640_chars_is_allowed(http, monkeypatch):
    configure_send(monkeypatch)
    allow()
    monkeypatch.setattr(bland_relay, "gateway_send", lambda to, message, opener=None: ({"id": "i", "state": "Pending"}, ""))
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "y" * 640}, auth())
    assert code == 200
    assert body["ok"] is True


def test_stop_blocks_until_start(http, monkeypatch):
    configure_send(monkeypatch)
    monkeypatch.setenv("FWD_PATH", "fwd-secret-path")
    monkeypatch.setenv("FWD_TOKEN", "fwd-token")
    monkeypatch.setattr(bland_relay, "forward", lambda body: (200, "", 1))
    hits = []
    monkeypatch.setattr(bland_relay, "gateway_send", lambda *a, **k: hits.append(a) or ({"id": "i", "state": "Pending"}, ""))

    code, _ = post(http, "/fwd-secret-path", {"from": "9045550199", "text": "STOP."}, auth("fwd-token"))
    assert code == 200
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hello"}, auth())
    assert code == 403 and body["error"] == "opt_out"

    code, _ = post(http, "/fwd-secret-path", {"from": TO, "text": "need a tow"}, auth("fwd-token"))
    assert code == 200
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hello"}, auth())
    assert code == 403 and body["error"] == "opt_out"
    assert hits == []

    code, _ = post(http, "/fwd-secret-path", {"from": TO, "text": "START"}, auth("fwd-token"))
    assert code == 200
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "hello"}, auth())
    assert code == 200 and body["ok"] is True
    assert len(hits) == 1


def test_stale_opt_out_still_blocks():
    bland_relay.note_inbound(TO, "STOP", now=1_000)
    assert bland_relay.reserve_send(TO, now=1_000 + bland_relay.ALLOWLIST_S + 10) == "opt_out"


def test_allowlist_window_is_72_hours():
    bland_relay.note_inbound(TO, "hi", now=5_000)
    assert bland_relay.reserve_send(TO, now=5_000 + bland_relay.ALLOWLIST_S) is None
    bland_relay.note_inbound("+12025550100", "hi", now=5_000)
    assert bland_relay.reserve_send("+12025550100", now=5_000 + bland_relay.ALLOWLIST_S + 1) == "not_allowlisted"


def test_recipient_rate_limit_is_5_per_10_minutes(http, monkeypatch):
    configure_send(monkeypatch)
    allow()
    monkeypatch.setattr(bland_relay, "gateway_send", lambda *a, **k: ({"id": "i", "state": "Pending"}, ""))
    for _ in range(5):
        code, body = post(http, "/send-secret-path", {"to": TO, "message": "ok"}, auth())
        assert code == 200 and body["ok"] is True
    code, body = post(http, "/send-secret-path", {"to": TO, "message": "ok"}, auth())
    assert code == 429 and body["error"] == "rate_limited"


def test_global_rate_limit_is_30_per_hour():
    now = 10_000.0
    for n in range(30):
        num = f"+1202555{n:04d}"
        bland_relay.note_inbound(num, "hi", now=now)
        assert bland_relay.reserve_send(num, now=now) is None
    bland_relay.note_inbound(TO, "hi", now=now)
    assert bland_relay.reserve_send(TO, now=now) == "rate_limited"


def test_gateway_failure_is_502_and_alerts_without_the_body(http, monkeypatch):
    configure_send(monkeypatch)
    allow()
    monkeypatch.setattr(bland_relay, "gateway_send", lambda *a, **k: (None, "HTTP 500"))
    posted = []

    def fake_post(title, text):
        posted.append((title, text))
        return {"ok": True, "posted": True, "status": 201, "issue": "701"}

    monkeypatch.setattr(bland_relay, "_post_github", fake_post)
    code, body = post(http, "/send-secret-path", {"to": TO, "message": BODY_TEXT}, auth())
    assert code == 502 and body["error"] == "gateway failed"
    assert posted
    title, text = posted[0]
    assert title == "904 SMS send failed"
    assert BODY_TEXT not in text
    assert "tok-test-value" not in text
    assert "pass-test" not in text
    assert "****0199" in text


def test_success_log_has_last4_and_length_only(http, monkeypatch, caplog):
    configure_send(monkeypatch)
    allow()
    monkeypatch.setattr(bland_relay, "gateway_send", lambda *a, **k: ({"id": "i", "state": "Pending"}, ""))
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        code, _ = post(http, "/send-secret-path", {"to": TO, "message": BODY_TEXT}, auth())
    assert code == 200
    joined = caplog.text
    assert BODY_TEXT not in joined
    assert "tok-test-value" not in joined
    assert "pass-test" not in joined
    assert "send-secret-path" not in joined
    assert "***0199" in joined
    assert f"len={len(BODY_TEXT)}" in joined
    state = json.loads((bland_relay._sms_state_path()).read_text())
    assert BODY_TEXT not in json.dumps(state)


def test_scrub_strips_new_secrets(monkeypatch):
    configure_send(monkeypatch)
    monkeypatch.setenv("SEND_PATH", "send-secret-path")
    text = bland_relay._scrub("token tok-test-value url https://api.sms-gate.app/3rdparty/v1 path /send-secret-path")
    assert "tok-test-value" not in text
    assert "api.sms-gate.app" not in text
    assert "send-secret-path" not in text


def test_gateway_request_shape(monkeypatch):
    configure_send(monkeypatch)
    seen = {}

    class Resp:
        status = 202

        def read(self):
            return b'{"id":"abc","state":"Pending"}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode())
        seen["timeout"] = timeout
        return Resp()

    result, err = bland_relay.gateway_send(TO, "hello", opener=opener)
    assert err == ""
    assert result == {"id": "abc", "state": "Pending"}
    assert seen["url"] == "https://api.sms-gate.app/3rdparty/v1/messages"
    assert seen["timeout"] == 15
    assert seen["auth"].startswith("Basic ")
    assert seen["body"] == {"phoneNumbers": [TO], "textMessage": {"text": "hello"}}


def test_gateway_url_keeps_an_explicit_message_path(monkeypatch):
    configure_send(monkeypatch)
    monkeypatch.setenv("SMSGW_URL", "http://phone.example:8080/message")
    assert bland_relay.gateway_message_url() == "http://phone.example:8080/message"
    monkeypatch.setenv("SMSGW_URL", "https://api.sms-gate.app/3rdparty/v1/messages/")
    assert bland_relay.gateway_message_url() == "https://api.sms-gate.app/3rdparty/v1/messages"
    monkeypatch.setenv("SMSGW_URL", "")
    assert bland_relay.gateway_message_url() == ""


def test_gateway_http_error_does_not_include_the_body(monkeypatch):
    configure_send(monkeypatch)

    def opener(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 503, BODY_TEXT, hdrs=None, fp=None)

    result, err = bland_relay.gateway_send(TO, BODY_TEXT, opener=opener)
    assert result is None
    assert err == "HTTP 503"
    assert BODY_TEXT not in err


def test_unknown_path_is_404(http, monkeypatch):
    configure_send(monkeypatch)
    monkeypatch.setenv("RELAY_PATH", "bland-secret")
    monkeypatch.setenv("FWD_PATH", "fwd-secret")
    code, body = post(http, "/nope", {"to": TO, "message": "hi"}, auth())
    assert code == 404 and body["error"] == "not found"


def test_904_inbound_still_forwards_and_records(http, monkeypatch):
    monkeypatch.setenv("FWD_PATH", "fwd-secret-path")
    monkeypatch.setenv("FWD_TOKEN", "fwd-token")
    seen = {}

    def fake(body):
        seen["body"] = json.loads(body.decode())
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "forward", fake)
    code, body = post(
        http,
        "/fwd-secret-path",
        {"from": "(904) 555-0199", "text": "need a tow", "receivedStamp": "2026-10-03T00:00:00Z"},
        auth("fwd-token"),
    )
    assert code == 200 and body == {"ok": True}
    assert seen["body"]["source"] == "t-mobile-904-forwarder"
    assert seen["body"]["channel"] == "sms"
    assert seen["body"]["line"] == "+19043343975"
    assert seen["body"]["from"] == "(904) 555-0199"
    assert seen["body"]["body"] == "need a tow"
    assert seen["body"]["test"] is False
    state = json.loads(bland_relay._sms_state_path().read_text())
    assert TO in state["inbound"]
    assert state["inbound"][TO].get("opt_out") is not True


def test_904_bad_token_does_not_record(http, monkeypatch):
    monkeypatch.setenv("FWD_PATH", "fwd-secret-path")
    monkeypatch.setenv("FWD_TOKEN", "fwd-token")
    code, body = post(http, "/fwd-secret-path", {"from": TO, "text": "hi"}, auth("wrong"))
    assert code == 401 and body["error"] == "unauthorized"
    assert not bland_relay._sms_state_path().exists()


def test_904_test_flag_does_not_allowlist(http, monkeypatch):
    monkeypatch.setenv("FWD_PATH", "fwd-secret-path")
    monkeypatch.setenv("FWD_TOKEN", "fwd-token")
    monkeypatch.setattr(bland_relay, "forward", lambda body: (200, "", 1))
    code, _ = post(http, "/fwd-secret-path", {"from": TO, "text": "hi", "test": True}, auth("fwd-token"))
    assert code == 200
    assert not bland_relay._sms_state_path().exists()


def test_904_unset_token_is_503(http, monkeypatch):
    monkeypatch.setenv("FWD_PATH", "fwd-secret-path")
    code, body = post(http, "/fwd-secret-path", {"from": TO, "text": "hi"}, auth("anything"))
    assert code == 503 and body["error"] == "relay not configured"


def _sign(secret, body):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_bland_route_still_checks_signature_and_forwards_raw_body(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    seen = {}
    monkeypatch.setattr(bland_relay, "forward", lambda body: seen.setdefault("body", body) and (200, "", 1))
    raw = b'{"call_id":"c1","note":"hello"}'
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": "sha256=" + _sign("bland-secret", raw)})
    assert code == 200 and body == {"ok": True}
    assert seen["body"] == raw
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": "00"})
    assert code == 401 and body["error"] == "bad signature"


def test_bland_accepts_compact_json_signature(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    seen = {}
    monkeypatch.setattr(bland_relay, "forward", lambda body: seen.setdefault("body", body) and (200, "", 1))
    raw = b'{ "call_id": "c1" }'
    compact = json.dumps(json.loads(raw), separators=(",", ":"), ensure_ascii=False).encode()
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("bland-secret", compact)})
    assert code == 200 and body == {"ok": True}
    assert seen["body"] == raw


def test_bland_unset_secret_is_503(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    raw = b"{}"
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("x", raw)})
    assert code == 503 and body["error"] == "relay not configured"


def test_bland_forward_failure_still_502(http, monkeypatch):
    monkeypatch.setenv("RELAY_PATH", "bland-secret-path")
    monkeypatch.setenv("BLAND_WEBHOOK_SECRET", "bland-secret")
    monkeypatch.setattr(bland_relay, "forward", lambda body: (None, "HTTP 500", 2))
    monkeypatch.setattr(bland_relay, "_post_github", lambda title, text: {"ok": True, "posted": False, "skipped": "test"})
    raw = b'{"call_id":"c1"}'
    code, body = post(http, "/bland-secret-path", raw, {"X-Webhook-Signature": _sign("bland-secret", raw)})
    assert code == 502 and body["error"] == "forward failed"
