"""SMSGate send state (#1140). Fixtures only: no device webhooks, no live sends."""
import hashlib
import hmac
import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

LEAD = "+19045550123"
SEND_TOK = "tok-send-test"
HOOK = "/smsgate-secret-path"
RCS = "/rcs-secret-path"
RCS_TOK = "rcs-token-abc123"
SEND = "/send-secret-path"
FC = "/fc-secret-path"
SG_KEY = "sg-signing-key"
SECRET = "secret body words that must not leak"
REASON = "RESULT_ERROR_LIMIT_EXCEEDED for +19045550123"
PKG = "com.google.android.apps.messaging"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in (
        "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "SMSGW_HOOK_PATH", "SMSGW_SIGNING_KEY",
        "RCS_HOOK_PATH", "RCS_HOOK_TOKEN", "SEND_FAILURE_NOTICE_RE", "FC_REQUIRE_APPROVAL",
        "FC_AUDIT_PATH", "CRM_OUTBOX_PATH", "CRM_INGEST_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SEND_PATH", SEND.lstrip("/"))
    monkeypatch.setenv("SEND_TOKEN", SEND_TOK)
    monkeypatch.setenv("SMSGW_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setenv("SMSGW_USER", "user-test")
    monkeypatch.setenv("SMSGW_PASS", "pass-test")
    monkeypatch.setenv("FC_PATH", FC.lstrip("/"))
    monkeypatch.setenv("SMSGW_HOOK_PATH", HOOK.lstrip("/"))
    monkeypatch.setenv("RCS_HOOK_PATH", RCS.lstrip("/"))
    monkeypatch.setenv("RCS_HOOK_TOKEN", RCS_TOK)
    monkeypatch.setenv("RCS_DEDUPE_WAIT_S", "0")
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    monkeypatch.setattr(bland_relay.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call in test"))
    monkeypatch.setattr(bland_relay, "_post_github", lambda *a, **k: {"posted": False, "skipped": "test"})
    bland_relay._seen_events.clear()
    bland_relay._recent_msgs.clear()
    ids = {"n": 0}

    def fake(to, message, opener=None):
        ids["n"] += 1
        return {"id": f"gw-{ids['n']}", "state": "Pending"}, ""

    monkeypatch.setattr(bland_relay, "gateway_send", fake)
    return tmp_path


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
def alerts(monkeypatch):
    posted = []

    def fake(title, text):
        posted.append({"title": title, "text": text})
        return {"ok": True, "posted": True, "status": 201, "issue": "701"}

    monkeypatch.setattr(bland_relay, "_post_github", fake)
    return posted


@pytest.fixture
def fwd(monkeypatch):
    seen = []

    def fake(body, timeout=10, call_id=None):
        seen.append(json.loads(body.decode()))
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "forward", fake)
    return seen


def post(base, path, body, headers=None, token=SEND_TOK):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        return err.code, json.loads(raw) if raw else {}


def auth(token=SEND_TOK):
    return {"Authorization": f"Bearer {token}"}


def sg_headers(raw, key=SG_KEY, ts=None):
    raw = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
    ts = str(int(bland_relay.time.time()) if ts is None else ts)
    sig = hmac.new(key.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Signature": sig}


def state_event(event="sms:failed", message_id="gw-1", event_id="evt-1", reason=REASON, parts=3):
    return {
        "deviceId": "dev",
        "event": event,
        "id": event_id,
        "webhookId": "wh",
        "payload": {
            "messageId": message_id,
            "sender": "+19043343975",
            "recipient": LEAD,
            "simNumber": 1,
            "partsCount": parts,
            "reason": reason,
            "message": SECRET,
            "failedAt": "2026-10-11T20:44:00.000Z",
        },
    }


def rows(tmp_path, name):
    path = tmp_path / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def place(http, placed_by="alexandra"):
    code, res = post(http, FC, {
        "action": "add", "to": LEAD, "name": "Dana Lead", "purpose_kind": "lead",
        "purpose": "fleet rental", "requested_by": "alexandra",
    })
    assert code == 200, res
    code, res = post(http, SEND, {
        "to": LEAD, "message": SECRET, "placed_by": placed_by,
    })
    assert code == 200, res
    return res["id"]


def failed_forwards(seen):
    return [row for row in seen if row.get("kind") == "send_failed"]


def test_failed_event_updates_audit_forwards_once_and_alerts(http, fwd, alerts, isolated):
    gid = place(http)
    raw = json.dumps(state_event(message_id=gid)).encode()
    code, body = post(http, HOOK, raw)
    assert code == 200 and body["state"] == "Failed"
    code, body = post(http, HOOK, raw)
    assert code == 200 and body.get("duplicate") is True
    assert len(failed_forwards(fwd)) == 1
    note = failed_forwards(fwd)[0]
    assert note["gateway_id"] == gid
    assert note["to_tail"] == "0123"
    assert note["placed_by"] == "alexandra"
    assert LEAD not in json.dumps(note)
    assert SECRET not in json.dumps(note)
    assert "+19045550123" not in note["reason"]
    states = [row for row in rows(isolated, "first_contact_audit.jsonl") if row["event"] == "send_state"]
    assert len(states) == 1
    assert states[0]["gateway_id"] == gid
    assert states[0]["state"] == "Failed"
    assert states[0]["parts_count"] == 3
    assert "LIMIT_EXCEEDED" in states[0]["reason"]
    assert SECRET not in json.dumps(states[0])
    updates = [row for row in rows(isolated, "crm_outbox.jsonl") if row.get("metadata", {}).get("gateway_state") == "Failed"]
    assert len(updates) == 1
    assert updates[0]["provider_msg_id"] == "smsgate:" + gid
    assert updates[0]["outcome"] == "failed"
    assert updates[0]["metadata"]["parts_count"] == 3
    assert SECRET not in json.dumps(updates[0])
    assert len(alerts) == 1
    assert alerts[0]["title"] == "904 SMS send failed"
    assert gid in alerts[0]["text"]
    assert LEAD not in alerts[0]["text"]
    assert SECRET not in alerts[0]["text"]


def test_sent_and_delivered_do_not_alert(http, fwd, alerts, isolated):
    gid = place(http)
    code, body = post(http, HOOK, state_event("sms:sent", gid, "sent-1", reason="", parts=4))
    assert code == 200 and body["state"] == "Sent"
    code, body = post(http, HOOK, state_event("sms:delivered", gid, "del-1", reason="", parts=None))
    assert code == 200 and body["state"] == "Delivered"
    assert failed_forwards(fwd) == []
    assert alerts == []
    states = [row["state"] for row in rows(isolated, "first_contact_audit.jsonl") if row["event"] == "send_state"]
    assert states == ["Sent", "Delivered"]
    sent = [row for row in rows(isolated, "crm_outbox.jsonl") if row["metadata"].get("gateway_state") == "Sent"]
    assert sent[0]["outcome"] == "sent" and sent[0]["metadata"]["parts_count"] == 4


def test_bad_signature_does_not_record(http, fwd, monkeypatch, isolated):
    monkeypatch.setenv("SMSGW_SIGNING_KEY", SG_KEY)
    gid = place(http)
    raw = json.dumps(state_event(message_id=gid)).encode()
    code, body = post(http, HOOK, raw, sg_headers(raw, key="wrong-key"))
    assert code == 401 and body["error"] == "bad signature"
    assert failed_forwards(fwd) == []
    assert [row for row in rows(isolated, "first_contact_audit.jsonl") if row["event"] == "send_state"] == []


def test_unknown_message_id_is_ignored(http, fwd, caplog):
    place(http)
    caplog.set_level(logging.INFO, logger="bland-relay")
    code, body = post(http, HOOK, state_event(message_id="missing-id", event_id="unk-1"))
    assert code == 200 and body.get("ignored") is True
    assert failed_forwards(fwd) == []
    assert "unknown message id=missing-id" in caplog.text
    assert SECRET not in caplog.text
    assert LEAD not in caplog.text


def test_logs_omit_number_and_body(http, fwd, alerts, caplog):
    caplog.set_level(logging.INFO, logger="bland-relay")
    gid = place(http)
    post(http, HOOK, state_event(message_id=gid))
    assert SECRET not in caplog.text
    assert LEAD not in caplog.text
    assert SG_KEY not in caplog.text
    assert "0123" in caplog.text


def test_second_failure_is_inside_the_15_minute_cap(http, fwd, alerts):
    first = place(http)
    post(http, HOOK, state_event(message_id=first, event_id="fail-a"))
    code, _ = post(http, FC, {
        "action": "add", "to": "+19045550124", "name": "Other Lead", "purpose_kind": "lead",
        "purpose": "second fleet note", "requested_by": "alexandra",
    })
    assert code == 200
    code, res = post(http, SEND, {"to": "+19045550124", "message": "short", "placed_by": "alexandra"})
    assert code == 200, res
    post(http, HOOK, state_event(message_id=res["id"], event_id="fail-b", reason="carrier reject"))
    assert len(failed_forwards(fwd)) == 2
    assert len(alerts) == 1


def test_failure_notice_retries_after_a_forward_failure(http, monkeypatch, isolated):
    gid = place(http)
    calls = []

    def fake(body, timeout=10, call_id=None):
        calls.append(json.loads(body.decode()))
        if len(calls) == 1:
            return None, "HTTP 500", 1
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "forward", fake)
    notice = {"package": PKG, "title": "", "text": "Message not sent"}
    assert post(http, RCS, notice, auth(RCS_TOK))[0] == 502
    code, body = post(http, RCS, notice, auth(RCS_TOK))
    assert code == 200 and body.get("ignored") == "send_failure_notice"
    assert [row["gateway_id"] for row in calls] == [gid, gid]
    marked = [row for row in rows(isolated, "first_contact_audit.jsonl") if row["event"] == "send_failed"]
    assert len(marked) == 1


def test_no_sender_failure_notice_is_not_inbound(http, fwd, caplog):
    caplog.set_level(logging.INFO, logger="bland-relay")
    notice = "Message not sent. Tap to try again on this conversation."
    code, body = post(http, RCS, {
        "package": PKG, "title": "Messages", "text": notice,
    }, auth(RCS_TOK))
    assert code == 200 and body.get("ignored") == "send_failure_notice"
    assert fwd == []
    assert notice not in caplog.text
    assert "send_failure_notice" in caplog.text


def test_failure_notice_is_the_fallback_until_sms_failed(http, fwd, isolated):
    gid = place(http)
    notice = "Message not sent. Tap to try again on this conversation."
    code, body = post(http, RCS, {"package": PKG, "title": "", "text": notice}, auth(RCS_TOK))
    assert code == 200 and body.get("ignored") == "send_failure_notice"
    assert len(failed_forwards(fwd)) == 1
    assert failed_forwards(fwd)[0]["gateway_id"] == gid
    assert failed_forwards(fwd)[0]["reason"] == "send_failure_notice"
    assert notice not in json.dumps(fwd)
    assert LEAD not in json.dumps(failed_forwards(fwd))
    code, body = post(http, HOOK, state_event(
        message_id=gid, event_id="late-fail", reason="RESULT_ERROR_LIMIT_EXCEEDED",
    ))
    assert code == 200
    assert len(failed_forwards(fwd)) == 1
    states = [row for row in rows(isolated, "first_contact_audit.jsonl") if row["event"] == "send_state"]
    assert [row["reason"] for row in states] == ["send_failure_notice", "RESULT_ERROR_LIMIT_EXCEEDED"]
