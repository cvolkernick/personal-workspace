"""RCS (Google Messages notification) inbound route (#1056)."""
import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

RCS = "/rcs-secret-path"
SG = "/smsgate-secret-path"
TOKEN = "rcs-token-abc123"
NUM = "+12025550177"
PKG = "com.google.android.apps.messaging"
ENV_KEYS = (
    "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "BLAND_WEBHOOK_SECRET", "RELAY_PATH",
    "FWD_TOKEN", "FWD_PATH", "SEND_TOKEN", "SEND_PATH", "SMSGW_URL", "SMSGW_USER",
    "SMSGW_PASS", "SMS_SEND_DISABLED", "SMSGW_HOOK_PATH", "SMSGW_SIGNING_KEY",
    "RCS_HOOK_PATH", "RCS_HOOK_TOKEN", "RCS_NAME_MAP", "RCS_HOOK_DISABLED", "RCS_DEDUPE_WAIT_S",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RCS_DEDUPE_WAIT_S", "0")
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    bland_relay._seen_events.clear()
    bland_relay._recent_msgs.clear()
    yield
    bland_relay._recent_msgs.clear()


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


@pytest.fixture
def fwd(monkeypatch):
    monkeypatch.setenv("RCS_HOOK_PATH", RCS.lstrip("/"))
    monkeypatch.setenv("RCS_HOOK_TOKEN", TOKEN)
    monkeypatch.setenv("SMSGW_HOOK_PATH", SG.lstrip("/"))
    seen = []

    def fake(body):
        seen.append(json.loads(body.decode()))
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "forward", fake)
    return seen


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


def bearer(tok=TOKEN):
    return {"Authorization": f"Bearer {tok}"}


def notif(title="(202) 555-0177", text="need a tow", package=PKG, **extra):
    d = {"package": package, "title": title, "text": text, "postedAt": "1759550000000"}
    d.update(extra)
    return d


def sg_event(sender=NUM, message="need a tow", event_id="evt-1"):
    return {
        "deviceId": "dev", "event": "sms:received", "id": event_id, "webhookId": "wh",
        "payload": {"messageId": "m", "message": message, "sender": sender,
                    "recipient": "+19043343975", "simNumber": 1,
                    "receivedAt": "2026-10-03T23:00:00.000-04:00"},
    }


def _inbound():
    p = bland_relay._sms_state_path()
    return json.loads(p.read_text())["inbound"] if p.exists() else {}


# --- parsing ---


def test_parse_template_json_and_aliases():
    p = bland_relay.parse_rcs_notification(json.dumps(
        {"packageName": PKG, "title": "Bob", "content": "hi", "post_time": "t"}).encode())
    assert p["package"] == PKG and p["title"] == "Bob" and p["text"] == "hi" and p["posted_at"] == "t"


def relay_app_payload(title="(202) 555-0177", text="need a tow", summary=False):
    # Fixed body sent by Notification Relay Webhook (com.notifrelay.app) v1.0.0.
    return {
        "package": PKG, "packageName": PKG, "app": "Messages", "appName": "Messages",
        "title": title, "text": text, "sub_text": None, "big_text": None, "category": "msg",
        "post_time": "2026-10-04T03:59:00Z", "postedAt": "2026-10-04T03:59:00Z",
        "key": "0|com.google.android.apps.messaging|0|abc|10123", "notificationKey": "k",
        "ongoing": False, "group_summary": summary,
    }


def test_parse_notification_relay_webhook_payload():
    p = bland_relay.parse_rcs_notification(json.dumps(relay_app_payload()).encode())
    assert p["package"] == PKG and p["title"] == "(202) 555-0177" and p["text"] == "need a tow"
    assert p["posted_at"] == "2026-10-04T03:59:00Z" and p["summary"] is False
    big = relay_app_payload(text=None)
    big["big_text"] = "long body"
    assert bland_relay.parse_rcs_notification(json.dumps(big).encode())["text"] == "long body"


def test_notification_relay_webhook_payload_end_to_end(http, fwd):
    assert post(http, RCS, relay_app_payload(), bearer()) == (200, {"ok": True})
    assert post(http, RCS, relay_app_payload(summary=True, text="2 new messages"), bearer())[1]["ignored"] == "empty"
    assert len(fwd) == 1 and fwd[0]["from"] == NUM and fwd[0]["received_at"] == "2026-10-04T03:59:00Z"


def test_parse_lenient_when_template_breaks_json():
    raw = ('{"package":"%s","title":"Bob","text":"he said "hi" ok","postedAt":"1"}' % PKG).encode()
    p = bland_relay.parse_rcs_notification(raw)
    assert p["title"] == "Bob" and p["text"] == 'he said "hi" ok' and p["posted_at"] == "1"


def test_parse_garbage_is_none():
    assert bland_relay.parse_rcs_notification(b"") is None
    assert bland_relay.parse_rcs_notification(b"not json at all") is None


def test_resolve_number_title_map_unknown():
    r = bland_relay.resolve_rcs_sender
    assert r({"title": "(202) 555-0177"}, {}) == (NUM, None, "title")
    assert r({"title": "Bob", "number": "2025550177"}, {}) == (NUM, "Bob", "number")
    assert r({"title": "bob  "}, {"bob": NUM}) == (NUM, "bob", "name_map")
    assert r({"title": "Alice"}, {"bob": NUM}) == (None, "Alice", "unknown")


def test_name_map_env_is_case_insensitive_and_normalized(monkeypatch):
    monkeypatch.setenv("RCS_NAME_MAP", json.dumps({"Chris  V": "(202) 555-0177", "bad": "123"}))
    assert bland_relay.rcs_name_map() == {"chris v": NUM}
    monkeypatch.setenv("RCS_NAME_MAP", "{not json")
    assert bland_relay.rcs_name_map() == {}
    monkeypatch.setenv("RCS_NAME_MAP", " Chris  V = (202) 555-0177 ;Other=+12025550188;junk")
    assert bland_relay.rcs_name_map() == {"chris v": NUM, "other": "+12025550188"}
    # What a systemd EnvironmentFile leaves of an unquoted JSON value.
    monkeypatch.setenv("RCS_NAME_MAP", "{Chris V: +12025550177}")
    assert bland_relay.rcs_name_map() == {}


# --- route: auth, kill switch, filtering ---


def test_forward_same_shape_and_allowlists(http, fwd):
    code, body = post(http, RCS, notif(), bearer())
    assert code == 200 and body == {"ok": True}
    assert len(fwd) == 1
    out = fwd[0]
    smsgate_keys = set(bland_relay.normalize_smsgate(sg_event()))
    assert smsgate_keys <= set(out)
    assert set(out) - smsgate_keys == {"via", "sender_name"}
    assert out["source"] == "t-mobile-904-forwarder" and out["channel"] == "sms"
    assert out["line"] == "+19043343975" and out["from"] == NUM and out["body"] == "need a tow"
    assert out["via"] == "rcs-notification" and out["test"] is False
    assert NUM in _inbound()
    assert bland_relay.reserve_send(NUM) is None


def test_missing_or_bad_token_is_401(http, fwd):
    assert post(http, RCS, notif())[0] == 401
    assert post(http, RCS, notif(), bearer("wrong"))[0] == 401
    assert post(http, RCS, notif(), {"X-Relay-Token": TOKEN})[0] == 200
    assert len(fwd) == 1


def test_wrong_or_missing_path_is_404(http, fwd, monkeypatch):
    assert post(http, "/rcs-secret-pat", notif(), bearer())[0] == 404
    assert post(http, "/", notif(), bearer())[0] == 404
    monkeypatch.delenv("RCS_HOOK_PATH")
    assert post(http, RCS, notif(), bearer())[0] == 404
    assert fwd == []


def test_path_only_mode_when_no_token(http, fwd, monkeypatch):
    monkeypatch.delenv("RCS_HOOK_TOKEN")
    assert post(http, RCS, notif())[0] == 200


def test_kill_switch_503(http, fwd, monkeypatch):
    monkeypatch.setenv("RCS_HOOK_DISABLED", "1")
    code, body = post(http, RCS, notif(), bearer())
    assert code == 503 and body["error"] == "rcs inbound disabled"
    assert fwd == [] and _inbound() == {}


def test_other_package_summary_and_empty_ignored(http, fwd):
    assert post(http, RCS, notif(package="com.whatsapp"), bearer())[1]["ignored"] == "package"
    assert post(http, RCS, notif(group_summary=True), bearer())[1]["ignored"] == "empty"
    assert post(http, RCS, notif(text=""), bearer())[1]["ignored"] == "empty"
    assert post(http, RCS, notif(text="%content%"), bearer())[1]["ignored"] == "empty"
    assert fwd == []


def test_bad_payload_400(http, fwd):
    assert post(http, RCS, b"nonsense", bearer())[0] == 400
    assert post(http, RCS, {"text": "hi"}, bearer())[0] == 400


def test_stop_sets_opt_out_and_start_clears(http, fwd):
    assert post(http, RCS, notif(text="Stop."), bearer())[0] == 200
    assert _inbound()[NUM]["opt_out"] is True
    assert bland_relay.reserve_send(NUM) == "opt_out"
    assert post(http, RCS, notif(text="START"), bearer())[0] == 200
    assert bland_relay.reserve_send(NUM) is None


def test_name_map_resolves_and_allowlists(http, fwd, monkeypatch):
    monkeypatch.setenv("RCS_NAME_MAP", f"Test Sender={NUM}")
    assert post(http, RCS, notif(title="Test Sender"), bearer())[0] == 200
    assert fwd[0]["from"] == NUM and fwd[0]["sender_name"] == "Test Sender"
    assert NUM in _inbound()


def test_unknown_name_forwarded_not_allowlisted(http, fwd):
    assert post(http, RCS, notif(title="Mystery Person", text="STOP"), bearer())[0] == 200
    assert fwd[0]["from"] == "unknown sender Mystery Person"
    assert _inbound() == {}


def test_forward_failure_502_then_retry_forwards(http, fwd, monkeypatch):
    calls = []
    monkeypatch.setattr(bland_relay, "forward", lambda b: calls.append(b) or (None, "HTTP 500", 2))
    monkeypatch.setattr(bland_relay, "alert_failure", lambda *a, **k: None)
    assert post(http, RCS, notif(), bearer())[0] == 502
    monkeypatch.setattr(bland_relay, "forward", lambda b: calls.append(b) or (200, "", 1))
    assert post(http, RCS, notif(), bearer()) == (200, {"ok": True})
    assert len(calls) == 2


# --- dedupe ---


def test_rcs_repeat_notification_forwards_once(http, fwd):
    assert post(http, RCS, notif(), bearer())[1] == {"ok": True}
    assert post(http, RCS, notif(text="need  a TOW"), bearer())[1]["duplicate"] is True
    assert len(fwd) == 1


def test_smsgate_first_then_rcs_notification_forwards_once(http, fwd):
    assert post(http, SG, sg_event())[1] == {"ok": True}
    assert post(http, RCS, notif(), bearer())[1]["duplicate"] is True
    assert len(fwd) == 1 and "via" not in fwd[0]


def test_rcs_first_then_smsgate_forwards_once_but_still_records(http, fwd):
    assert post(http, RCS, notif(title="Mystery Person"), bearer())[1] == {"ok": True}
    assert _inbound() == {}
    assert post(http, SG, sg_event())[1]["duplicate"] is True
    assert len(fwd) == 1
    assert NUM in _inbound()  # SMSGate still records the real number


def test_unknown_name_deduped_against_smsgate_by_text(http, fwd):
    post(http, SG, sg_event())
    assert post(http, RCS, notif(title="Mystery Person"), bearer())[1]["duplicate"] is True
    assert len(fwd) == 1


def test_different_text_or_sender_not_deduped(http, fwd):
    post(http, SG, sg_event())
    assert post(http, RCS, notif(text="different"), bearer())[1] == {"ok": True}
    assert post(http, RCS, notif(title="(202) 555-0188"), bearer())[1] == {"ok": True}
    assert len(fwd) == 3


def test_dedupe_window_expires(http, fwd, monkeypatch):
    post(http, SG, sg_event())
    real = bland_relay.time.time
    monkeypatch.setattr(bland_relay.time, "time", lambda: real() + bland_relay.RCS_DEDUPE_S + 5)
    assert post(http, RCS, notif(), bearer())[1] == {"ok": True}
    assert len(fwd) == 2


def test_wait_lets_late_smsgate_win(monkeypatch, fwd):
    monkeypatch.setenv("RCS_DEDUPE_WAIT_S", "5")
    clock = [1000.0]
    monkeypatch.setattr(bland_relay.time, "time", lambda: clock[0])

    def fake_sleep(_s):
        clock[0] += 1
        if clock[0] == 1002:
            bland_relay._recent_claim("sms", NUM, None, bland_relay._text_key("need a tow"), ("rcs",))

    monkeypatch.setattr(bland_relay.time, "sleep", fake_sleep)

    h = bland_relay.H.__new__(bland_relay.H)
    raw = json.dumps(notif()).encode()
    h.headers = {"Authorization": f"Bearer {TOKEN}", "Content-Length": str(len(raw))}
    import io
    h.rfile = io.BytesIO(raw)
    sent = []
    h._send = lambda code, obj: sent.append((code, obj))
    h._rcs_inbound()
    assert sent == [(200, {"ok": True, "duplicate": True})]
    assert fwd == []


def test_wait_s_bounds(monkeypatch):
    monkeypatch.delenv("RCS_DEDUPE_WAIT_S")
    assert bland_relay._rcs_wait_s() == bland_relay.RCS_WAIT_DEFAULT_S
    monkeypatch.setenv("RCS_DEDUPE_WAIT_S", "99")
    assert bland_relay._rcs_wait_s() == 20.0
    monkeypatch.setenv("RCS_DEDUPE_WAIT_S", "x")
    assert bland_relay._rcs_wait_s() == bland_relay.RCS_WAIT_DEFAULT_S


# --- logging ---


def test_logs_have_no_body_name_token_or_path(http, fwd, caplog):
    caplog.set_level(logging.INFO, logger="bland-relay")
    post(http, RCS, notif(title="Mystery Person", text="secret body words"), bearer())
    post(http, RCS, notif(), bearer("wrong"))
    text = caplog.text
    assert "rcs inbound ok" in text
    for bad in ("secret body words", "Mystery Person", TOKEN, RCS.lstrip("/"), "555-0177", "5550177"):
        assert bad not in text


def test_scrub_strips_rcs_secrets(monkeypatch):
    monkeypatch.setenv("RCS_HOOK_TOKEN", TOKEN)
    monkeypatch.setenv("RCS_HOOK_PATH", "abc-path-xyz")
    assert bland_relay._scrub(f"x {TOKEN} abc-path-xyz") == "x <redacted> <redacted>"
