"""One content decision per inbound text for BOTH sinks (Alexandra forward + Helm).

2026-10-25: the 904 phone re-sent the 11:39 ET batch at 13:57 ET with new ids
and fresh notification post times; it went to Alexandra and Helm again."""
import json
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

RCS, SG, FWD = "/rcs-secret-path", "/smsgate-secret-path", "/fwd-secret-path"
TOKEN, FWD_TOKEN = "rcs-token-abc123", "fwd-token"
PKG = "com.google.android.apps.messaging"
ENV_KEYS = (
    "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "BLAND_WEBHOOK_SECRET", "RELAY_PATH",
    "SEND_TOKEN", "SEND_PATH", "SMSGW_URL", "SMSGW_USER", "SMSGW_PASS", "SMSGW_SIGNING_KEY",
    "RCS_NAME_MAP", "RCS_HOOK_DISABLED", "INBOUND_CONTENT_DEDUPE", "HELM_SMS_CONTENT_WINDOW_D",
)
BATCH = [("(904) 555-7531", "Thank you"), ("(904) 555-6765", "Capital One: card alert " + "a" * 100),
         ("(904) 555-7898", "GM Financial: payment notice " + "b" * 300), ("(904) 555-2395", "Santander reminder")]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RCS_DEDUPE_WAIT_S", "0")
    monkeypatch.setenv("RCS_HOOK_PATH", RCS.lstrip("/"))
    monkeypatch.setenv("RCS_HOOK_TOKEN", TOKEN)
    monkeypatch.setenv("SMSGW_HOOK_PATH", SG.lstrip("/"))
    monkeypatch.setenv("FWD_PATH", FWD.lstrip("/"))
    monkeypatch.setenv("FWD_TOKEN", FWD_TOKEN)
    monkeypatch.setenv("HELM_INBOUND_SMS_FORWARD", "1")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "http://127.0.0.1:9/helm")
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    bland_relay._seen_events.clear()
    bland_relay._recent_msgs.clear()
    yield
    bland_relay._recent_msgs.clear()


@pytest.fixture
def sinks(monkeypatch):
    """Fake Alexandra forward and fake Helm post; results are switchable."""
    s = {"alex": [], "helm": [], "alex_ok": True, "helm_ok": True}

    def fake_forward(body):
        s["alex"].append(json.loads(body.decode()))
        return (200, "", 1) if s["alex_ok"] else (None, "URLError", 3)

    def fake_helm(url, payload, *a, **k):
        s["helm"].append(payload)
        return (200, None, 1) if s["helm_ok"] else (None, "URLError", 2)
    monkeypatch.setattr(bland_relay, "forward", fake_forward)
    monkeypatch.setattr(bland_relay, "post_json", fake_helm)
    monkeypatch.setattr(bland_relay, "alert_failure", lambda *a, **k: None)
    return s


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
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        return err.code, json.loads(raw) if raw else {}


def notif(title, text, posted):
    return {"package": PKG, "title": title, "text": text, "postedAt": posted}


def rcs(http, title, text, posted):
    bland_relay._recent_msgs.clear()  # outside the 120 s cross-path window, like 11:39 vs 13:57
    return post(http, RCS, notif(title, text, posted), {"Authorization": "Bearer " + TOKEN})


def sg(http, sender, text, event_id, mid, received="2026-10-25T07:00:00.000-04:00"):
    bland_relay._recent_msgs.clear()
    return post(http, SG, {"deviceId": "d", "event": "sms:received", "id": event_id, "webhookId": "w",
                           "payload": {"messageId": mid, "message": text, "sender": sender,
                                       "recipient": "+19043343975", "simNumber": 1, "receivedAt": received}})


DUP = (200, {"ok": True, "duplicate": True})
OK = (200, {"ok": True})


def test_resent_batch_dropped_for_alexandra_and_helm(http, sinks):
    for title, text in BATCH:
        assert rcs(http, title, text, "2026-10-25T15:39:23Z") == OK
    for title, text in BATCH:
        assert rcs(http, title, text, "2026-10-25T17:57:58Z") == DUP
    assert len(sinks["alex"]) == 4 and len(sinks["helm"]) == 4


def test_smsgate_resend_with_new_ids_dropped_for_both(http, sinks):
    assert sg(http, "+19045556630", "Mobi 7am", "evt-1", "m1") == OK
    assert sg(http, "+19045556630", "Mobi 7am", "evt-2", "m2") == DUP
    assert len(sinks["alex"]) == 1 and len(sinks["helm"]) == 1


def test_forwarder_path_resend_dropped(http, sinks):
    body = {"from": "(904) 555-0199", "text": "need a tow", "receivedStamp": "2026-10-25T11:00:00Z"}
    hdr = {"Authorization": "Bearer " + FWD_TOKEN}
    assert post(http, FWD, body, hdr) == OK
    assert post(http, FWD, body, hdr) == DUP
    assert len(sinks["alex"]) == 1


def test_new_text_still_reaches_both(http, sinks):
    assert rcs(http, "(904) 555-7531", "Thank you", "2026-10-25T15:39:23Z") == OK
    assert rcs(http, "(904) 555-7531", "Book Direct: can I rent Friday?", "2026-10-25T18:00:00Z") == OK
    assert sg(http, "+19045550001", "Book Direct request", "e9", "m9", "2026-10-25T14:03:00-04:00") == OK
    assert len(sinks["alex"]) == 3 and len(sinks["helm"]) == 3


def test_alexandra_failure_rolls_back_retry_passes_helm_not_doubled(http, sinks):
    sinks["alex_ok"] = False
    assert sg(http, "+19045556630", "Retry me", "evt-1", "m1")[0] == 502
    sinks["alex_ok"] = True
    assert sg(http, "+19045556630", "Retry me", "evt-1", "m1") == OK  # phone retry, same ids
    assert len(sinks["alex"]) == 2
    assert len(sinks["helm"]) == 1  # Helm id store stopped the second wake


def test_helm_failure_keeps_decision_resend_dropped_for_both(http, sinks):
    sinks["helm_ok"] = False
    assert rcs(http, "(904) 555-7531", "Thank you", "2026-10-25T15:39:23Z") == OK
    sinks["helm_ok"] = True
    assert rcs(http, "(904) 555-7531", "Thank you", "2026-10-25T17:57:58Z") == DUP
    assert len(sinks["alex"]) == 1 and len(sinks["helm"]) == 1


def test_kill_switch_restores_old_behavior(http, sinks, monkeypatch):
    monkeypatch.setenv("INBOUND_CONTENT_DEDUPE", "0")
    assert rcs(http, "(904) 555-7531", "Thank you", "2026-10-25T15:39:23Z") == OK
    assert rcs(http, "(904) 555-7531", "Thank you", "2026-10-25T17:57:58Z") == OK
    assert len(sinks["alex"]) == 2
    assert len(sinks["helm"]) == 1  # Helm's own content check still runs
