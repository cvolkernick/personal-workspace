"""Approved first-contact outbound (#1100). Fixtures only: SMSGate is mocked, no live sends."""
import hashlib
import json
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

LEAD = "+19045550123"
CHRIS = "+12075550100"
SEND_TOK = "tok-send-test"
APPROVE_TOK = "tok-approve-test"
FC = "/fc-secret-path"
SEND = "/send-secret-path"
ENV_KEYS = (
    "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "BLAND_WEBHOOK_SECRET", "RELAY_PATH",
    "FWD_TOKEN", "FWD_PATH", "SEND_TOKEN", "SEND_PATH", "SMSGW_URL", "SMSGW_USER", "SMSGW_PASS",
    "SMSGW_HOOK_PATH", "SMSGW_SIGNING_KEY", "RCS_HOOK_PATH", "RCS_HOOK_TOKEN", "RCS_NAME_MAP",
    "SMS_SEND_DISABLED", "FC_PATH", "FC_APPROVE_TOKEN_SHA256", "FC_APPROVERS", "FC_AGENT_SEATS",
    "FC_APPROVER_NUMBERS", "FC_DAILY_MAX", "FC_TTL_DAYS", "FC_MAX_SENDS_BEFORE_REPLY", "FC_DISABLED",
    "FC_AUDIT_PATH", "CRM_INGEST_MODE", "CRM_OUTBOX_PATH",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    monkeypatch.setattr(bland_relay, "_post_github", lambda *a, **k: {"posted": False, "skipped": "test"})
    monkeypatch.setenv("SEND_PATH", SEND.lstrip("/"))
    monkeypatch.setenv("SEND_TOKEN", SEND_TOK)
    monkeypatch.setenv("SMSGW_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setenv("SMSGW_USER", "user-test")
    monkeypatch.setenv("SMSGW_PASS", "pass-test")
    monkeypatch.setenv("FC_PATH", FC.lstrip("/"))
    monkeypatch.setenv("FC_APPROVE_TOKEN_SHA256", hashlib.sha256(APPROVE_TOK.encode()).hexdigest())
    monkeypatch.setenv("FC_APPROVER_NUMBERS", f"chris={CHRIS}")


@pytest.fixture
def gateway(monkeypatch):
    """Mocked SMSGate client: records calls, never touches the network."""
    calls = []

    def fake(to, message, opener=None):
        calls.append((to, message))
        return {"id": f"gw-{len(calls)}", "state": "Pending"}, ""

    monkeypatch.setattr(bland_relay, "gateway_send", fake)
    monkeypatch.setattr(bland_relay.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("network call in test"))
    return calls


@pytest.fixture
def http():
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def post(base, path, body, token=SEND_TOK):
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=hdrs, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, json.loads(raw) if raw else {}


def request_body(to=LEAD, **kw):
    body = {"action": "request", "to": to, "name": "Dana Lead", "purpose_kind": "lead",
            "purpose": "GVG lead asked about fleet rental", "requested_by": "alexandra"}
    body.update(kw)
    return body


def approve_body(code, to=LEAD, **kw):
    body = {"action": "approve", "to": to, "code": code, "approver": "chris",
            "approval_text": f"yes {code}", "approval_ref": "grok-chat:msg-123"}
    body.update(kw)
    return body


def approved(http, to=LEAD):
    code, res = post(http, FC, request_body(to=to))
    assert code == 200, res
    code, res2 = post(http, FC, approve_body(res["code"], to=to), APPROVE_TOK)
    assert code == 200, res2
    return res2


def audit(tmp_path):
    p = tmp_path / "first_contact_audit.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def outbox(tmp_path):
    p = tmp_path / "crm_outbox.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def send(http, to=LEAD, msg="Hi Dana, this is Panamerica Auto.", **kw):
    body = {"to": to, "message": msg}
    body.update(kw)
    return post(http, SEND, body)


# --- approval required -------------------------------------------------------

def test_unapproved_first_contact_is_still_refused(http, gateway):
    code, res = send(http)
    assert code == 403 and res["error"] == "not_allowlisted"
    code, res = post(http, FC, request_body())
    assert code == 200 and res["status"] == "pending"
    code, res2 = send(http)
    assert code == 403 and res2["error"] == "not_allowlisted"  # pending is not approved
    assert gateway == []


def test_request_validates_and_normalizes(http):
    code, res = post(http, FC, request_body(to="(904) 555-0123"))
    assert code == 200 and res["to"] == LEAD and len(res["code"]) == 6
    assert res["approve_sms"] == f"YES {res['code']}" and res["code"] in res["ask"]
    for bad in ({"to": "+441234567890"}, {"to": "555-0123"}, {"to": "+10045550123"}, {"to": 9045550123},
                {"name": ""}, {"name": "x" * 65}, {"purpose_kind": "friend"}, {"purpose": "hi"},
                {"purpose": None}, {"requested_by": ""}, {"requested_by": "bad seat!"}):
        c, r = post(http, FC, request_body(**bad))
        assert c == 400, (bad, r)


def test_full_chat_approval_flow_then_send(http, gateway, tmp_path):
    code, req = post(http, FC, request_body())
    code, res = post(http, FC, approve_body(req["code"]), APPROVE_TOK)
    assert code == 200 and res["status"] == "active"
    e = res["entry"]
    assert e["approved_by"] == "chris" and e["approval_text"] == f"yes {req['code']}"
    assert e["approval_ref"] == "grok-chat:msg-123" and e["approved_via"] == "chat"
    assert e["approved_at_iso"].endswith("Z") and e["expires_at_iso"]
    code, out = send(http)
    assert code == 200 and out["id"] == "gw-1"
    assert gateway == [(LEAD, "Hi Dana, this is Panamerica Auto.")]


def test_send_token_cannot_approve(http, gateway, tmp_path):
    code, req = post(http, FC, request_body())
    code, res = post(http, FC, approve_body(req["code"]), SEND_TOK)
    assert code == 403 and "approver token" in res["error"]
    assert send(http)[0] == 403
    assert any(r["event"] == "deny" and r["reason"] == "approve_without_approver_token" for r in audit(tmp_path))


def test_bad_token_401_and_unset_path_404(http, monkeypatch):
    assert post(http, FC, request_body(), "nope")[0] == 401
    assert post(http, FC, request_body(), None)[0] == 401
    monkeypatch.delenv("FC_PATH")
    assert post(http, FC, request_body())[0] == 404


def test_approve_refuses_wrong_code_text_ref(http):
    code, req = post(http, FC, request_body())
    good = req["code"]
    wrong = "%06d" % ((int(good) + 1) % 1_000_000)
    assert post(http, FC, approve_body(wrong, approval_text=f"yes {wrong}"), APPROVE_TOK)[0] == 403
    assert post(http, FC, approve_body(good, approval_text="yes"), APPROVE_TOK)[0] == 400
    assert post(http, FC, approve_body(good, approval_text=f"no {good}"), APPROVE_TOK)[0] == 400
    assert post(http, FC, approve_body(good, approval_ref=""), APPROVE_TOK)[0] == 400
    assert post(http, FC, approve_body("12ab"), APPROVE_TOK)[0] == 400
    assert post(http, FC, approve_body(good), APPROVE_TOK)[0] == 200  # still pending after refusals


@pytest.mark.parametrize("approver", ["alexandra", "grok", "forge", "buzz", "somebody", ""])
def test_no_self_or_agent_approval(http, approver):
    code, req = post(http, FC, request_body())
    code, res = post(http, FC, approve_body(req["code"], approver=approver), APPROVE_TOK)
    assert code == 403 and res["error"] == "approver_not_allowed"


def test_requester_cannot_approve_even_if_listed(http, monkeypatch):
    monkeypatch.setenv("FC_APPROVERS", "chris,dana")
    code, req = post(http, FC, request_body(requested_by="dana"))
    code, res = post(http, FC, approve_body(req["code"], approver="dana"), APPROVE_TOK)
    assert code == 403 and res["error"] == "approver_not_allowed"


def test_approver_token_equal_to_send_token_never_approves(http, monkeypatch):
    monkeypatch.setenv("FC_APPROVE_TOKEN_SHA256", hashlib.sha256(SEND_TOK.encode()).hexdigest())
    code, req = post(http, FC, request_body())
    code, res = post(http, FC, approve_body(req["code"]), SEND_TOK)
    assert code == 403


def test_pending_code_expires_after_24h():
    code, req = bland_relay.fc_request(request_body(), now=1_000_000)
    code, res = bland_relay.fc_approve(approve_body(req["code"]), now=1_000_000 + bland_relay.FC_PENDING_S + 1)
    assert code == 403


# --- SMS approval -------------------------------------------------------------

def test_sms_yes_code_from_approver_number_approves(http, gateway, tmp_path):
    code, req = post(http, FC, request_body())
    bland_relay.note_inbound(CHRIS, f"Yes {req['code']}", via="smsgate")
    _, lst = post(http, FC, {"action": "list", "status": "active"})
    [row] = lst["entries"]
    assert row["to"] == LEAD and row["approved_by"] == "chris" and row["approved_via"] == "sms:smsgate"
    assert row["approval_text"] == f"Yes {req['code']}" and row["approval_ref"].startswith("904-inbound:smsgate:")
    assert send(http)[0] == 200


def test_sms_yes_from_other_number_or_rcs_name_map_does_not_approve(http, gateway, tmp_path):
    code, req = post(http, FC, request_body())
    bland_relay.note_inbound("+19045550999", f"YES {req['code']}", via="smsgate")
    bland_relay.note_inbound(CHRIS, f"YES {req['code']}", via="rcs-name_map")
    bland_relay.note_inbound(CHRIS, "YES 000000", via="smsgate")
    assert send(http)[0] == 403
    reasons = [r.get("reason") for r in audit(tmp_path) if r["event"] == "deny"]
    assert "via_not_allowed" in reasons and "no_matching_pending" in reasons


def test_approver_number_must_be_a_human_approver(monkeypatch):
    monkeypatch.setenv("FC_APPROVER_NUMBERS", f"alexandra={CHRIS};mallory=+12075550111")
    assert bland_relay.fc_approver_numbers() == {}


# --- opt-out ------------------------------------------------------------------

def test_opted_out_number_cannot_be_requested_approved_or_sent(http, gateway, tmp_path):
    bland_relay.note_inbound(LEAD, "STOP", now=1_000)  # long ago, outside 72h
    c, r = post(http, FC, request_body())
    assert c == 403 and r["error"] == "opt_out"
    bland_relay.note_inbound(LEAD, "START")
    approved(http)
    bland_relay.note_inbound(LEAD, "unsubscribe")
    c, r = send(http)
    assert c == 403 and r["error"] == "opt_out"
    _, lst = post(http, FC, {"action": "list"})
    assert lst["entries"][0]["status"] == "opted_out"
    assert gateway == []


def test_stop_between_request_and_approve_blocks(http, gateway):
    _, req = post(http, FC, request_body())
    bland_relay.note_inbound(LEAD, "STOP")
    c, r = post(http, FC, approve_body(req["code"]), APPROVE_TOK)
    assert c == 403
    assert send(http)[0] == 403 and gateway == []


def test_start_reenables_after_opt_out(http, gateway):
    approved(http)
    bland_relay.note_inbound(LEAD, "STOP")
    assert send(http)[1]["error"] == "opt_out"
    bland_relay.note_inbound(LEAD, "START")  # opted back in: now inside the inbound window
    assert send(http)[0] == 200


# --- rate limits and caps -------------------------------------------------------

def test_daily_cap_on_new_numbers(http, monkeypatch):
    monkeypatch.setenv("FC_DAILY_MAX", "2")
    approved(http, "+19045550101")
    approved(http, "+19045550102")
    c, r = post(http, FC, request_body(to="+19045550103"))
    assert c == 429 and r["error"] == "daily_cap"
    # renewing an existing entry does not count against the cap
    c, r = post(http, FC, request_body(to="+19045550101"))
    assert c == 200 and r["renewal"] is True


def test_daily_cap_rolls_after_24h(monkeypatch):
    monkeypatch.setenv("FC_DAILY_MAX", "1")
    t0 = 2_000_000
    _, req = bland_relay.fc_request(request_body(to="+19045550101"), now=t0)
    assert bland_relay.fc_approve(approve_body(req["code"], to="+19045550101"), now=t0)[0] == 200
    assert bland_relay.fc_request(request_body(to="+19045550102"), now=t0 + 60)[0] == 429
    assert bland_relay.fc_request(request_body(to="+19045550102"), now=t0 + bland_relay.FC_DAY_S + 1)[0] == 200


def test_daily_cap_rechecked_at_approve(monkeypatch):
    monkeypatch.setenv("FC_DAILY_MAX", "1")
    t = 3_000_000
    _, a = bland_relay.fc_request(request_body(to="+19045550101"), now=t)
    _, b = bland_relay.fc_request(request_body(to="+19045550102"), now=t)
    assert bland_relay.fc_approve(approve_body(a["code"], to="+19045550101"), now=t)[0] == 200
    c, r = bland_relay.fc_approve(approve_body(b["code"], to="+19045550102"), now=t)
    assert c == 429 and r["error"] == "daily_cap"


def test_existing_recipient_rate_limit_applies(http, gateway, monkeypatch):
    monkeypatch.setenv("FC_MAX_SENDS_BEFORE_REPLY", "20")
    approved(http)
    for _ in range(bland_relay.RECIPIENT_MAX):
        assert send(http)[0] == 200
    c, r = send(http)
    assert c == 429 and r["error"] == "rate_limited"


def test_existing_global_rate_limit_applies(gateway, monkeypatch):
    monkeypatch.setenv("FC_DAILY_MAX", "50")
    now = 4_000_000
    for i in range(bland_relay.GLOBAL_MAX):
        num = "+1904555%04d" % (2000 + i)
        bland_relay.note_inbound(num, "hi", now=now)
        assert bland_relay.reserve_send(num, now=now) is None
    _, req = bland_relay.fc_request(request_body(), now=now)
    bland_relay.fc_approve(approve_body(req["code"]), now=now)
    assert bland_relay.reserve_send(LEAD, now=now) == "rate_limited"


def test_sends_before_reply_cap(http, gateway, monkeypatch):
    monkeypatch.setenv("FC_MAX_SENDS_BEFORE_REPLY", "2")
    approved(http)
    assert send(http)[0] == 200 and send(http)[0] == 200
    c, r = send(http)
    assert c == 429 and r["error"] == "first_contact_send_cap"
    bland_relay.note_inbound(LEAD, "sure, tell me more")  # reply -> normal inbound window
    assert send(http)[0] == 200


# --- expiry, renewal, conversion -----------------------------------------------

def test_expiry_and_renewal(monkeypatch):
    monkeypatch.setenv("FC_TTL_DAYS", "30")
    t = 5_000_000
    _, req = bland_relay.fc_request(request_body(), now=t)
    bland_relay.fc_approve(approve_body(req["code"]), now=t)
    ttl = 30 * bland_relay.FC_DAY_S
    assert bland_relay.reserve_send(LEAD, now=t + ttl - 10) is None
    assert bland_relay.reserve_send(LEAD, now=t + ttl + 1) == "first_contact_expired"
    _, lst = bland_relay.fc_list({"status": "expired"}, now=t + ttl + 1)
    assert [r["to"] for r in lst["entries"]] == [LEAD]
    t2 = t + ttl + 100
    _, req2 = bland_relay.fc_request(request_body(), now=t2)
    assert req2["renewal"] is True
    code, res = bland_relay.fc_approve(approve_body(req2["code"]), now=t2)
    assert code == 200 and res["entry"]["sends"] == 0
    assert bland_relay.reserve_send(LEAD, now=t2 + ttl - 10) is None


def test_ttl_is_configurable(monkeypatch):
    monkeypatch.setenv("FC_TTL_DAYS", "7")
    t = 6_000_000
    _, req = bland_relay.fc_request(request_body(), now=t)
    bland_relay.fc_approve(approve_body(req["code"]), now=t)
    assert bland_relay.reserve_send(LEAD, now=t + 7 * bland_relay.FC_DAY_S + 1) == "first_contact_expired"


def test_reply_converts_to_inbound_window(tmp_path):
    t = 7_000_000
    _, req = bland_relay.fc_request(request_body(), now=t)
    bland_relay.fc_approve(approve_body(req["code"]), now=t)
    bland_relay.note_inbound(LEAD, "who is this?", now=t + 60)
    _, lst = bland_relay.fc_list({}, now=t + 60)
    assert lst["entries"][0]["status"] == "converted"
    assert bland_relay.reserve_send(LEAD, now=t + 120) is None
    # after the 72h inbound window the approval does not come back
    later = t + 60 + bland_relay.ALLOWLIST_S + 1
    assert bland_relay.reserve_send(LEAD, now=later) == "not_allowlisted"
    assert any(r["event"] == "convert" for r in audit(tmp_path))


# --- kill switch -----------------------------------------------------------------

def test_kill_switch_blocks_send_request_and_approve(http, gateway, monkeypatch):
    _, req = post(http, FC, request_body())
    monkeypatch.setenv("SMS_SEND_DISABLED", "1")
    assert post(http, FC, approve_body(req["code"]), APPROVE_TOK) == (503, {"ok": False, "error": "send disabled"})
    assert post(http, FC, request_body(to="+19045550124"))[0] == 503
    bland_relay.note_inbound(CHRIS, f"YES {req['code']}", via="smsgate")
    monkeypatch.delenv("SMS_SEND_DISABLED")
    assert send(http)[0] == 403  # SMS approval during kill switch was ignored
    _, req = post(http, FC, request_body())
    post(http, FC, approve_body(req["code"]), APPROVE_TOK)
    monkeypatch.setenv("SMS_SEND_DISABLED", "1")
    assert send(http)[0] == 503
    assert post(http, FC, {"action": "list"})[0] == 200  # list/revoke still work
    assert post(http, FC, {"action": "revoke", "to": LEAD, "reason": "kill", "revoked_by": "forge"})[0] == 200
    assert gateway == []


def test_fc_disabled_stops_first_contact_only(http, gateway, monkeypatch):
    approved(http)
    bland_relay.note_inbound("+19045550888", "hi")
    monkeypatch.setenv("FC_DISABLED", "1")
    c, r = send(http)
    assert c == 403 and r["error"] == "first_contact_disabled"
    assert post(http, FC, request_body(to="+19045550124"))[0] == 503
    assert send(http, to="+19045550888")[0] == 200  # normal replies unaffected
    assert gateway == [("+19045550888", "Hi Dana, this is Panamerica Auto.")]


# --- list / revoke -------------------------------------------------------------

def test_list_and_revoke(http, gateway, tmp_path):
    approved(http)
    post(http, FC, request_body(to="+19045550124", name="Vendor Vic", purpose_kind="vendor",
                                purpose="tow quote"))
    _, lst = post(http, FC, {"action": "list"})
    st = {r["to"]: r["status"] for r in lst["entries"]}
    assert st == {LEAD: "active", "+19045550124": "pending"}
    assert all("code" not in r and "code_sha256" not in r for r in lst["entries"])
    assert lst["daily_used"] == 1 and lst["daily_max"] == 20 and lst["ttl_days"] == 30
    c, r = post(http, FC, {"action": "revoke", "to": LEAD, "reason": "wrong number", "revoked_by": "grok"})
    assert c == 200
    assert send(http)[0] == 403
    assert post(http, FC, {"action": "revoke", "to": "+19045550124", "reason": "x", "revoked_by": "grok"})[0] == 200
    assert post(http, FC, {"action": "revoke", "to": "+19045550199", "reason": "x", "revoked_by": "grok"})[0] == 404
    assert post(http, FC, {"action": "revoke", "to": LEAD})[0] == 400
    assert post(http, FC, {"action": "bogus"})[0] == 400
    ev = [r["event"] for r in audit(tmp_path)]
    assert ev.count("revoke") == 2


# --- audit + CRM outbox ----------------------------------------------------------

def test_audit_and_crm_records(http, gateway, tmp_path):
    approved(http)
    secret_msg = "Hi Dana, your fleet quote is ready"
    assert send(http, msg=secret_msg, placed_by="alexandra")[0] == 200
    rows = audit(tmp_path)
    assert [r["event"] for r in rows] == ["request", "approve", "send"]
    ap = rows[1]
    assert ap["approved_by"] == "chris" and ap["approval_ref"] == "grok-chat:msg-123"
    assert ap["requested_by"] == "alexandra" and ap["purpose_kind"] == "lead" and ap["at"].endswith("Z")
    [rec] = outbox(tmp_path)
    assert rec["schema"] == "panamerica.crm.interaction.v1"
    assert (rec["channel"], rec["direction"], rec["provider"]) == ("sms", "outbound", "smsgate")
    assert rec["provider_msg_id"] == "smsgate:gw-1" and rec["outcome"] == "sent"
    assert rec["from_addr"] == bland_relay.LINE_904 and rec["to_addr"] == LEAD
    assert rec["metadata"]["purpose"] == "GVG lead asked about fleet rental"
    assert rec["metadata"]["placed_by"] == "alexandra" and rec["metadata"]["first_contact"] is True
    assert rec["metadata"]["approval"]["approved_by"] == "chris"
    assert rec["party_hint"] == {"phone": LEAD, "display_name": "Dana Lead", "type": "lead",
                                 "tags": ["904-first-contact"], "source": "904-first-contact"}
    for p in ("first_contact_audit.jsonl", "crm_outbox.jsonl"):
        text = (tmp_path / p).read_text()
        assert secret_msg not in text
        for s in (SEND_TOK, APPROVE_TOK, "pass-test", "fc-secret-path", "send-secret-path"):
            assert s not in text
        assert oct((tmp_path / p).stat().st_mode & 0o777) == "0o600"


def test_renter_maps_to_customer_turo_guest(http, gateway, tmp_path):
    _, req = post(http, FC, request_body(purpose_kind="renter", purpose="Turo trip pickup details"))
    post(http, FC, approve_body(req["code"]), APPROVE_TOK)
    send(http)
    [rec] = outbox(tmp_path)
    assert rec["party_hint"]["type"] == "customer" and "turo-guest" in rec["party_hint"]["tags"]


def test_reply_sends_also_emit_crm_record_and_failures_are_recorded(http, monkeypatch, tmp_path):
    monkeypatch.setattr(bland_relay, "gateway_send", lambda *a, **k: (None, "HTTP 500"))
    bland_relay.note_inbound("+19045550888", "hi")
    assert send(http, to="+19045550888", purpose="answer tow question")[0] == 502
    [rec] = outbox(tmp_path)
    assert rec["outcome"] == "failed" and rec["error_code"] == "HTTP 500"
    assert rec["metadata"]["first_contact"] is False and rec["metadata"]["purpose"] == "answer tow question"
    assert rec["provider_msg_id"].startswith("relay:")


def test_crm_sink_failure_never_blocks_send(http, gateway, monkeypatch):
    class Boom:
        def emit(self, record):
            raise OSError("disk full")
    monkeypatch.setattr(bland_relay, "crm_sink", lambda: Boom())
    approved(http)
    assert send(http)[0] == 200


def test_crm_ingest_off(http, gateway, monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_INGEST_MODE", "off")
    approved(http)
    send(http)
    assert outbox(tmp_path) == []


def test_logs_never_show_full_number_or_secrets(http, gateway, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="bland-relay")
    approved(http)
    send(http)
    text = caplog.text
    assert "0123" in text and LEAD not in text and "5550123" not in text
    for s in (SEND_TOK, APPROVE_TOK, "fc-secret-path", "pass-test"):
        assert s not in text


def test_scrub_covers_new_secrets(monkeypatch):
    assert "fc-secret-path" not in bland_relay._scrub("x fc-secret-path y")


# --- CLI wrapper -----------------------------------------------------------------

def _cli(http, tmp_path, args, stdin="", extra_env=None):
    import os
    import subprocess
    import sys
    from pathlib import Path
    home = tmp_path / "home"
    (home / ".config" / "bland-relay").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "bland-relay" / "env").write_text(
        f"SEND_TOKEN={SEND_TOK}\nFC_PATH={FC.lstrip('/')}\n")
    envv = {"HOME": str(home), "SEND904_BASE": http, "PATH": os.environ.get("PATH", ""),
            "NO_PROXY": "*", "no_proxy": "*"}
    envv.update(extra_env or {})
    cli = Path(bland_relay.__file__).with_name("send-904-fc")
    p = subprocess.run([sys.executable, str(cli)] + args, input=stdin, capture_output=True,
                       text=True, env=envv, timeout=20)
    return p.returncode, p.stdout, p.stderr


def test_cli_request_approve_list_revoke(http, gateway, tmp_path):
    rc, out, err = _cli(http, tmp_path, ["request", "--to", "904-555-0123", "--name", "Dana Lead",
                                         "--kind", "lead", "--purpose", "GVG fleet lead", "--by", "alexandra"])
    assert rc == 0, err
    res = json.loads(out.split("\n", 1)[1])
    code = res["code"]
    approve = ["approve", "--to", LEAD, "--code", code, "--approver", "chris",
               "--text", f"yes {code}", "--ref", "grok-chat:1"]
    rc, out, err = _cli(http, tmp_path, approve)  # no approver token
    assert rc == 3 and "approver token" in err
    rc, out, err = _cli(http, tmp_path, approve + ["--token-stdin"], stdin=APPROVE_TOK + "\n")
    assert rc == 0, out + err
    rc, out, _ = _cli(http, tmp_path, ["list", "--status", "active"])
    assert rc == 0 and LEAD in out and SEND_TOK not in out and "fc-secret-path" not in out
    rc, out, _ = _cli(http, tmp_path, ["revoke", "--to", LEAD, "--reason", "done", "--by", "forge"])
    assert rc == 0 and '"revoked"' in out
    assert gateway == []
