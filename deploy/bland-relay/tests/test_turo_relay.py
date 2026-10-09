"""Turo notification relay on the RCS route (#1104). Fixtures only, no network."""
import json
import logging
import os
import threading
import urllib.error
import urllib.request

import pytest

import bland_relay

RCS = "/rcs-secret-path"
TOKEN = "rcs-token-abc123"
TURO = "com.relayrides.android.relayrides"
MSGS = "com.google.android.apps.messaging"
OWNER = "+12025550123"
HELM_URL = "https://helm.example.invalid/hook"
HELM_KEY = "helm-key-xyz789"
ENV_KEYS = (
    "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "BLAND_WEBHOOK_SECRET", "RELAY_PATH",
    "FWD_TOKEN", "FWD_PATH", "SEND_TOKEN", "SEND_PATH", "SMSGW_URL", "SMSGW_USER",
    "SMSGW_PASS", "SMS_SEND_DISABLED", "SMSGW_HOOK_PATH", "SMSGW_SIGNING_KEY",
    "RCS_HOOK_PATH", "RCS_HOOK_TOKEN", "RCS_NAME_MAP", "RCS_HOOK_DISABLED", "RCS_DEDUPE_WAIT_S",
    "TURO_HOOK_DISABLED", "TURO_OWNER_ALERT_TO", "TURO_OWNER_ALERT_DISABLED", "TURO_SUPPRESS_RE",
    "TURO_URGENT_NEG", "TURO_URGENT_RE_LOCKOUT", "TURO_URGENT_RE_NO_START",
    "TURO_URGENT_RE_ACCIDENT_DAMAGE", "TURO_URGENT_RE_CHARGING", "TURO_URGENT_RE_SAFETY",
    "TURO_DEDUPE_WINDOW_S", "TURO_ALERT_GUEST_WINDOW_S", "TURO_ALERT_GUEST_MAX", "TURO_ALERT_HOUR_MAX",
    "TURO_INBOX_PATH", "TURO_RETENTION_DAYS", "TURO_WEBHOOK_TIMEOUT_S", "TURO_ALEX_PUSH_DISABLED",
    "HELM_TURO_WEBHOOK_URL", "HELM_TURO_WEBHOOK_KEY", "HELM_TURO_WEBHOOK_HEADER",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("RCS_DEDUPE_WAIT_S", "0")
    monkeypatch.setenv("RCS_HOOK_PATH", RCS.lstrip("/"))
    monkeypatch.setenv("RCS_HOOK_TOKEN", TOKEN)
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    monkeypatch.setattr(bland_relay, "_post_github", lambda *_a, **_k: {"skipped": "test"})
    bland_relay._seen_events.clear()
    bland_relay._recent_msgs.clear()
    bland_relay._turo_prune["at"] = 0.0

    def no_network(*_a, **_k):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(bland_relay.urllib.request, "urlopen", no_network)
    yield


class Rig:
    def __init__(self):
        self.sms = []
        self.helm = []
        self.alex = []
        self.helm_status = 200
        self.alex_status = 200
        self.gw_ok = True


@pytest.fixture
def rig(monkeypatch):
    r = Rig()

    def gw(to, message, opener=None):
        r.sms.append((to, message))
        return ({"id": "m1", "state": "Pending"}, "") if r.gw_ok else (None, "HTTP 500")

    def post_json(url, obj, headers=None, timeout=5.0, tries=2):
        r.helm.append({"url": url, "obj": json.loads(json.dumps(obj)), "headers": dict(headers or {}),
                       "timeout": timeout})
        return (r.helm_status, "", 1) if r.helm_status else (None, "TimeoutError", 2)

    def fwd(body, timeout=10):
        r.alex.append({"obj": json.loads(body.decode()), "timeout": timeout})
        return (r.alex_status, "", 1) if r.alex_status else (None, "HTTP 502", 2)

    monkeypatch.setattr(bland_relay, "gateway_send", gw)
    monkeypatch.setattr(bland_relay, "post_json", post_json)
    monkeypatch.setattr(bland_relay, "forward", fwd)
    return r


@pytest.fixture
def live(monkeypatch, rig):
    """Everything configured: owner alert, gateway, Helm and Alexandra."""
    monkeypatch.setenv("TURO_OWNER_ALERT_TO", OWNER)
    monkeypatch.setenv("SMSGW_URL", "https://gw.example.invalid")
    monkeypatch.setenv("SMSGW_USER", "u")
    monkeypatch.setenv("SMSGW_PASS", "p")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", HELM_URL)
    monkeypatch.setenv("HELM_TURO_WEBHOOK_KEY", HELM_KEY)
    monkeypatch.setenv("ALEXANDRA_ALERT_URL", "https://alex.example.invalid/hook")
    monkeypatch.setenv("ALEXANDRA_ALERT_AUTH", "Bearer alex-secret")
    return rig


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


_real_urlopen = urllib.request.urlopen


def post(base, body, path=RCS, tok=TOKEN):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json"}
    if tok:
        hdrs["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method="POST")
    try:
        with _real_urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        return err.code, json.loads(raw) if raw else {}


def turo(text="Can I pick up at 3pm?", title="Sam", big_text=None, key="0|com.relayrides|1|null|10001",
         **extra):
    d = {"package": TURO, "app": "Turo", "title": title, "text": text, "postedAt": 1760040000000,
         "key": key, "category": "msg", "ongoing": False, "group_summary": False}
    if big_text is not None:
        d["big_text"] = big_text
    d.update(extra)
    return d


def handle(body):
    return bland_relay.turo_handle(bland_relay.parse_rcs_notification(json.dumps(body).encode()))


def inbox():
    p = bland_relay.turo_inbox_path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# --- route / branch -------------------------------------------------------

def test_turo_branch_same_path_and_token(http, live):
    code, body = post(http, turo(text="Hi, running 10 min late"))
    assert code == 200 and body == {"ok": True, "turo": "accepted", "urgent": False}
    assert len(inbox()) == 1 and len(live.helm) == 1


def test_turo_branch_requires_token(http, live):
    code, _ = post(http, turo(), tok="wrong")
    assert code == 401
    assert inbox() == [] and live.helm == []


def test_turo_wrong_path_404(http, live):
    code, _ = post(http, turo(), path="/nope")
    assert code == 404


def test_other_package_still_ignored(http, live):
    code, body = post(http, turo(package="com.whatsapp"))
    assert (code, body) == (200, {"ok": True, "ignored": "package"})
    assert inbox() == [] and live.helm == []


def test_messages_route_unchanged(http, live):
    code, body = post(http, {"package": MSGS, "title": "(202) 555-0177", "text": "hello there",
                             "postedAt": "1759550000000"})
    assert (code, body) == (200, {"ok": True})
    assert len(live.alex) == 1 and live.alex[0]["obj"]["via"] == "rcs-notification"
    assert live.alex[0]["timeout"] == 10
    assert inbox() == [] and live.helm == [] and live.sms == []


def test_rcs_kill_switch_still_wins(http, live, monkeypatch):
    monkeypatch.setenv("RCS_HOOK_DISABLED", "1")
    code, _ = post(http, turo())
    assert code == 503


def test_group_summary_ignored(live):
    assert handle(turo(group_summary=True)) == (200, {"ok": True, "ignored": "empty"})
    assert inbox() == []


def test_empty_text_ignored(live):
    assert handle(turo(text="")) == (200, {"ok": True, "ignored": "empty"})


# --- normalization and parsing ---------------------------------------------

def test_big_text_preferred(live):
    handle(turo(text="I'm locked out of the...", big_text="I'm locked out of the Rivian, phone key is not working"))
    ev = live.helm[0]["obj"]
    assert ev["text"].endswith("phone key is not working")
    assert ev["urgent_class"] == "lockout"


def test_normalized_shape(live):
    handle(turo(text="Pickup at 3pm works", title="Sam • 2024 Toyota Corolla", sub_text="Trip 12345678"))
    ev = live.helm[0]["obj"]
    # Helm gets exactly the trimmed field set (#1106 review).
    assert set(ev) == set(bland_relay.TURO_HELM_FIELDS) == {
        "kind", "schema", "event_id", "guest", "reservation_id", "vehicle", "plate", "text",
        "received_at", "urgent", "urgent_class"}
    assert ev["guest"] == "Sam"
    assert ev["reservation_id"] == "12345678"
    assert ev["vehicle"] == "Toyota Corolla"
    row = inbox()[0]  # the local inbox keeps the extra context
    assert row["sub_text"] == "Trip 12345678" and row["category"] == "msg"
    assert row["posted_at"] == "2025-10-09T20:00:00Z"
    assert row["key"] == "0|com.relayrides|1|null|10001"
    assert ev["kind"] == "turo_event" and ev["urgent"] is False and ev["urgent_class"] is None


def test_unknown_fields_are_null(live):
    handle(turo(text="See you tomorrow", title="Sam"))
    ev = live.helm[0]["obj"]
    assert ev["reservation_id"] is None and ev["vehicle"] is None and ev["plate"] is None
    assert inbox()[0]["sub_text"] is None


@pytest.mark.parametrize("fields,want", [
    ((None, "Trip #87654321", "x"), "87654321"),
    (("Sam", None, "My reservation number is 1234567, running late"), "1234567"),
    (("Sam", None, "Booking ID: 123456789"), "123456789"),
    (("Sam", "Trip 7654321", "Trip #12345678"), "7654321"),
    (("Sam", None, "call me at 207-555-1234"), None),
    (("Sam", None, "call me at (207) 555-1234 or 2075551234"), None),
    (("Sam", None, "gate code 1234"), None),
    (("Sam", None, "it cost $1,250.00"), None),
    (("Sam", None, "number 1234567890"), None),
    (("Sam", None, ""), None),
])
def test_reservation_parse(fields, want):
    assert bland_relay.parse_turo_reservation(*fields) == want


@pytest.mark.parametrize("fields,want", [
    (("2024 Toyota Corolla", None, "x"), ("Toyota Corolla", None)),
    ((None, "24EWUH", "x"), ("Toyota Corolla 24EWUH", "24EWUH")),
    ((None, None, "plate 25 EWUH has a light on"), ("Toyota Corolla 25EWUH", "25EWUH")),
    ((None, None, "the R1S is great"), ("Rivian R1S", None)),
    (("Rivian R1S", None, None), ("Rivian R1S", None)),
    ((None, None, "the corolla smells"), ("Toyota Corolla", None)),
    ((None, None, "the Tesla Model 3 is fine"), ("Tesla Model 3", None)),
    ((None, None, "hello"), (None, None)),
])
def test_vehicle_parse(fields, want):
    assert bland_relay.parse_turo_vehicle(*fields) == want


def test_guest_split_from_title(live):
    handle(turo(title="Jordan · Rivian R1S", text="hello"))
    ev = live.helm[0]["obj"]
    assert ev["guest"] == "Jordan" and ev["vehicle"] == "Rivian R1S"


# --- dedupe ---------------------------------------------------------------

def test_dedupe_same_key_and_text(live):
    assert handle(turo())[1]["turo"] == "accepted"
    assert handle(turo())[1] == {"ok": True, "turo": "duplicate"}
    assert len(inbox()) == 1 and len(live.helm) == 1


def test_dedupe_same_guest_text_new_key(live):
    handle(turo(key="k1"))
    assert handle(turo(key="k2"))[1]["turo"] == "duplicate"


def test_dedupe_text_whitespace_case(live):
    handle(turo(text="Running  Late"))
    assert handle(turo(text="running late", key="k9"))[1]["turo"] == "duplicate"


def test_prefix_repost_dropped_longer_passes(live):
    handle(turo(text="I am running late but will be there soon", key="a"))
    assert handle(turo(text="I am running late but", key="b"))[1]["turo"] == "duplicate"
    assert handle(turo(text="I am running late but will be there soon. Also where is the key?",
                       key="c"))[1]["turo"] == "accepted"


def test_different_guest_same_text_not_dup(live):
    handle(turo(title="Sam", key="a"))
    assert handle(turo(title="Alex", key="b"))[1]["turo"] == "accepted"


def test_dedupe_window_expiry(live):
    p = bland_relay.parse_rcs_notification(json.dumps(turo()).encode())
    assert bland_relay.turo_handle(p, now=1000.0)[1]["turo"] == "accepted"
    assert bland_relay.turo_handle(p, now=1000.0 + 3600)[1]["turo"] == "duplicate"
    assert bland_relay.turo_handle(p, now=1000.0 + 24 * 3600 + 5)[1]["turo"] == "accepted"


def test_dedupe_survives_restart_state_file(live):
    handle(turo())
    assert bland_relay._turo_state_path().exists()
    assert handle(turo())[1]["turo"] == "duplicate"


# --- suppression ----------------------------------------------------------

def test_suppressed_default_safewheels(live):
    code, body = handle(turo(text="I'm locked out", title="Pat • SafeWheels Camry"))
    assert body == {"ok": True, "turo": "suppressed"}
    assert live.sms == [] and live.helm == [] and live.alex == []
    rows = inbox()
    assert len(rows) == 1 and rows[0]["suppressed"] is True


def test_suppress_env_regex(live, monkeypatch):
    monkeypatch.setenv("TURO_SUPPRESS_RE", r"model 3|camry")
    assert handle(turo(text="the tesla model 3 won't start"))[1]["turo"] == "suppressed"
    assert live.sms == [] and live.helm == []
    assert handle(turo(text="the rivian won't start", key="z", title="Kim"))[1]["turo"] == "accepted"
    assert len(live.sms) == 1


def test_bad_suppress_regex_falls_back(live, monkeypatch):
    monkeypatch.setenv("TURO_SUPPRESS_RE", "(unclosed")
    assert handle(turo(title="Pat • Safe Wheels"))[1]["turo"] == "suppressed"


# --- urgent classes -------------------------------------------------------

@pytest.mark.parametrize("text,cls", [
    ("I'm locked out of the car", "lockout"),
    ("The phone key doesn't work and the doors are locked", "lockout"),
    ("The Rivian won't start", "no_start"),
    ("Screen is black, no power at all", "no_start"),
    ("Someone rear-ended me at a light", "accident_damage"),
    ("There's a new dent on the door, I hit a pole", "accident_damage"),
    ("Charger error at the supercharger, it won't charge", "charging"),
    ("Battery is at 5, I'm stranded", "charging"),
    ("There is smoke coming from the hood", "safety"),
    ("Emergency, I need help!", "safety"),
])
def test_each_urgent_class(text, cls):
    assert bland_relay.classify_turo(text)[0] == cls


def test_safety_outranks_others():
    assert bland_relay.classify_turo("I crashed and I'm hurt")[0] == "safety"


@pytest.mark.parametrize("text", [
    "The scratch on the bumper was already there.",
    "I'll charge it before return",
    "What are the key pickup instructions?",
    "Thanks for the help!",
    "Is smoking allowed?",
    "No damage, all good. Returned at 5.",
    "Can I pick up at 3pm tomorrow?",
    "Had a great trip, thanks!",
    "Filing the incident report from my student account",
])
def test_negative_list_and_benign(text):
    assert bland_relay.classify_turo(text) == (None, None)


def test_negative_sentence_does_not_mask_real_problem():
    assert bland_relay.classify_turo("The scratch was already there. But I just got rear-ended!")[0] == \
        "accident_damage"


def test_urgent_env_overrides(monkeypatch):
    monkeypatch.setenv("TURO_URGENT_RE_LOCKOUT", r"pineapple")
    assert bland_relay.classify_turo("pineapple")[0] == "lockout"
    assert bland_relay.classify_turo("I'm locked out")[0] is None
    monkeypatch.setenv("TURO_URGENT_NEG", r"rear")
    assert bland_relay.classify_turo("I got rear-ended")[0] is None


# --- owner alert ----------------------------------------------------------

def test_owner_alert_urgent_template(live):
    long = "I'm locked out of the Rivian and the phone key is not working at all " * 4
    handle(turo(text=long, title="Sam"))
    assert len(live.sms) == 1
    to, msg = live.sms[0]
    assert to == OWNER
    assert msg == "URGENT Turo [lockout]: Sam: " + " ".join(long.split())[:140]


def test_non_urgent_never_texts(live):
    for i, t in enumerate(["Pickup at 3pm?", "Dropping off at 5 tomorrow", "Thanks, great car!"]):
        handle(turo(text=t, key=str(i)))
    assert live.sms == [] and len(live.helm) == 3 and live.alex == []


def test_owner_alert_env_number_is_the_only_recipient(live, monkeypatch, caplog):
    monkeypatch.setenv("TURO_OWNER_ALERT_TO", "+12025550177")
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        handle(turo(text="I'm locked out"))
    assert [to for to, _ in live.sms] == ["+12025550177"]
    assert "2025550177" not in caplog.text and "***0177" in caplog.text


@pytest.mark.parametrize("bad", ["(202) 555-0123", "2025550123", "12025550123", "+1202555012",
                                 "+12025550123x", "+11025550123", "not-a-number"])
def test_owner_alert_invalid_e164_disabled_and_not_logged(live, monkeypatch, caplog, bad):
    monkeypatch.setenv("TURO_OWNER_ALERT_TO", bad)
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        handle(turo(text="I'm locked out"))
    assert live.sms == []
    assert "not valid E.164" in caplog.text
    assert "5550123" not in caplog.text and "555012" not in caplog.text
    assert len(live.helm) == 1 and len(live.alex) == 1  # handoff still happens


def test_no_owner_number_in_code():
    assert not hasattr(bland_relay, "TURO_OWNER_ALERT_ALLOWED")


def test_owner_alert_unset_no_sms(live, monkeypatch):
    monkeypatch.delenv("TURO_OWNER_ALERT_TO")
    handle(turo(text="I'm locked out"))
    assert live.sms == [] and len(live.alex) == 1


def test_owner_alert_skips_72h_window_only_for_owner(live):
    # Owner never texted 904; the alert still goes. Normal sends stay gated.
    handle(turo(text="I'm locked out"))
    assert len(live.sms) == 1
    assert bland_relay.reserve_send("+12025550177") == "not_allowlisted"


def test_owner_alert_respects_send_disabled(live, monkeypatch):
    monkeypatch.setenv("SMS_SEND_DISABLED", "1")
    handle(turo(text="I'm locked out"))
    assert live.sms == [] and len(live.helm) == 1 and len(live.alex) == 1


def test_owner_alert_kill_switch(live, monkeypatch):
    monkeypatch.setenv("TURO_OWNER_ALERT_DISABLED", "1")
    handle(turo(text="I'm locked out"))
    assert live.sms == [] and len(live.helm) == 1


def test_owner_alert_respects_stop(live):
    bland_relay.note_inbound(OWNER, "STOP")
    handle(turo(text="I'm locked out"))
    assert live.sms == []
    bland_relay.note_inbound(OWNER, "START")
    handle(turo(text="The car won't start", key="2"))
    # same guest within 10 min would be rate limited only after a send; none was sent yet
    assert len(live.sms) == 1


def test_owner_alert_dedupe_direct(live):
    ev = bland_relay.normalize_turo(bland_relay.parse_rcs_notification(json.dumps(
        turo(text="I'm locked out")).encode()))
    assert bland_relay.owner_alert(ev, now=1000.0) == "sent"
    assert bland_relay.owner_alert(ev, now=1000.0 + 3600) == "duplicate"
    assert len(live.sms) == 1


def test_owner_alert_not_urgent_direct(live):
    ev = bland_relay.normalize_turo(bland_relay.parse_rcs_notification(json.dumps(turo()).encode()))
    assert bland_relay.owner_alert(ev) == "not_urgent"
    assert live.sms == []


def test_rate_limit_per_guest(live):
    p1 = bland_relay.parse_rcs_notification(json.dumps(turo(text="I'm locked out", key="1")).encode())
    p2 = bland_relay.parse_rcs_notification(json.dumps(turo(text="Car won't start either", key="2")).encode())
    p3 = bland_relay.parse_rcs_notification(json.dumps(turo(text="Now there is smoke", key="3")).encode())
    bland_relay.turo_handle(p1, now=10000.0)
    bland_relay.turo_handle(p2, now=10000.0 + 300)
    assert len(live.sms) == 1
    bland_relay.turo_handle(p3, now=10000.0 + 601)
    assert len(live.sms) == 2
    assert len(live.helm) == 3 and len(live.alex) == 3  # handoff is not rate limited


def test_rate_limit_hourly_overall(live, monkeypatch):
    monkeypatch.setenv("TURO_ALERT_HOUR_MAX", "3")
    for i in range(5):
        p = bland_relay.parse_rcs_notification(json.dumps(
            turo(text=f"I'm locked out #{i}", title=f"Guest{i}", key=str(i))).encode())
        bland_relay.turo_handle(p, now=20000.0 + i)
    assert len(live.sms) == 3
    p = bland_relay.parse_rcs_notification(json.dumps(
        turo(text="I'm locked out late", title="Late", key="late")).encode())
    bland_relay.turo_handle(p, now=20000.0 + 3601)
    assert len(live.sms) == 4


def test_rate_limit_configurable_guest(live, monkeypatch):
    monkeypatch.setenv("TURO_ALERT_GUEST_MAX", "2")
    handle(turo(text="I'm locked out", key="1"))
    handle(turo(text="The car won't start", key="2"))
    handle(turo(text="Smoke now", key="3"))
    assert len(live.sms) == 2


def test_owner_alert_consumes_relay_wide_slot(live):
    handle(turo(text="I'm locked out"))
    st = json.loads(bland_relay._sms_state_path().read_text())
    assert [r["to"] for r in st["sends"]] == [OWNER]


def test_owner_alert_gateway_failure_alerts(live, monkeypatch):
    live.gw_ok = False
    seen = []
    monkeypatch.setattr(bland_relay, "alert_failure", lambda *a, **k: seen.append(a))
    handle(turo(text="I'm locked out"))
    assert seen and seen[0][0] == "turo-owner-alert"
    assert len(live.helm) == 1


# --- kill switch ----------------------------------------------------------

def test_turo_kill_switch(http, live, monkeypatch):
    monkeypatch.setenv("TURO_HOOK_DISABLED", "1")
    code, body = post(http, turo(text="I'm locked out"))
    assert (code, body) == (200, {"ok": True, "ignored": "turo_disabled"})
    assert live.sms == [] and live.helm == [] and inbox() == []
    code, body = post(http, {"package": MSGS, "title": "(202) 555-0177", "text": "still works"})
    assert (code, body) == (200, {"ok": True})


# --- handoff --------------------------------------------------------------

def test_helm_post_shape_and_default_bearer(live):
    handle(turo(text="I'm locked out", title="Sam • Rivian R1S", sub_text="Trip 12345678"))
    h = live.helm[0]
    assert h["url"] == HELM_URL
    assert h["headers"] == {"Authorization": "Bearer " + HELM_KEY}
    assert h["timeout"] == 5.0
    ev = h["obj"]
    assert ev["kind"] == "turo_event" and ev["schema"] == "panamerica.turo.event.v1"
    assert ev["urgent"] is True and ev["urgent_class"] == "lockout"
    assert ev["guest"] == "Sam" and ev["vehicle"] == "Rivian R1S" and ev["reservation_id"] == "12345678"
    assert ev["text"] == "I'm locked out"


def test_helm_key_with_scheme_sent_as_is(live, monkeypatch):
    monkeypatch.setenv("HELM_TURO_WEBHOOK_KEY", "Bearer already")
    handle(turo())
    assert live.helm[0]["headers"] == {"Authorization": "Bearer already"}


def test_helm_custom_header(live, monkeypatch):
    monkeypatch.setenv("HELM_TURO_WEBHOOK_HEADER", "X-Webhook-Key")
    handle(turo())
    assert live.helm[0]["headers"] == {"X-Webhook-Key": HELM_KEY}


def test_helm_bad_header_name_falls_back(live, monkeypatch):
    monkeypatch.setenv("HELM_TURO_WEBHOOK_HEADER", "Bad Header\r\n")
    handle(turo())
    assert list(live.helm[0]["headers"]) == ["Authorization"]


def test_helm_no_key_no_header(live, monkeypatch):
    monkeypatch.delenv("HELM_TURO_WEBHOOK_KEY")
    handle(turo())
    assert live.helm[0]["headers"] == {}


def test_helm_timeout_configurable(live, monkeypatch):
    monkeypatch.setenv("TURO_WEBHOOK_TIMEOUT_S", "2")
    handle(turo(text="I'm locked out"))
    assert live.helm[0]["timeout"] == 2.0 and live.alex[0]["timeout"] == 2.0


def test_webhooks_unset_jsonl_only(rig, monkeypatch):
    code, body = handle(turo(text="I'm locked out"))
    assert body["turo"] == "accepted"
    assert rig.helm == [] and rig.alex == [] and rig.sms == []
    assert len(inbox()) == 1 and inbox()[0]["urgent_class"] == "lockout"


def test_alexandra_urgent_push_tagged(live):
    handle(turo(text="I'm locked out"))
    assert len(live.alex) == 1
    obj = live.alex[0]["obj"]
    assert obj["kind"] == "turo_urgent" and obj["urgent_class"] == "lockout"
    assert set(obj) == set(bland_relay.TURO_ALEX_FIELDS) == {
        "kind", "guest", "reservation_id", "vehicle", "text", "urgent_class", "received_at"}


def test_alexandra_push_disable(live, monkeypatch):
    monkeypatch.setenv("TURO_ALEX_PUSH_DISABLED", "1")
    handle(turo(text="I'm locked out"))
    assert live.alex == [] and len(live.sms) == 1


def test_webhook_failure_isolated(live, monkeypatch):
    live.helm_status = None
    live.alex_status = None
    seen = []
    monkeypatch.setattr(bland_relay, "alert_failure", lambda *a, **k: seen.append(a[0]))
    code, body = handle(turo(text="I'm locked out"))
    assert (code, body["turo"]) == (200, "accepted")
    assert len(live.sms) == 1
    assert sorted(seen) == ["turo-alexandra-push", "turo-helm-webhook"]


def test_webhook_exception_isolated(live, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(bland_relay, "post_json", boom)
    monkeypatch.setattr(bland_relay, "forward", boom)
    monkeypatch.setattr(bland_relay, "alert_failure", lambda *a, **k: None)
    assert handle(turo(text="I'm locked out"))[0] == 200
    assert len(live.sms) == 1


def test_owner_alert_crash_does_not_block_webhooks(live, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(bland_relay, "gateway_send", boom)
    handle(turo(text="I'm locked out"))
    assert len(live.helm) == 1 and len(live.alex) == 1


def test_post_json_short_timeout_and_retry(monkeypatch):
    calls = []

    def fake(req, timeout):
        calls.append((req.get_header("Authorization"), timeout))
        raise urllib.error.URLError("down")

    monkeypatch.setattr(bland_relay.urllib.request, "urlopen", fake)
    status, err, attempts = bland_relay.post_json("https://x.invalid", {"a": 1}, {"Authorization": "Bearer k"}, 3.0)
    assert status is None and attempts == 2 and err == "URLError"
    assert calls == [("Bearer k", 3.0), ("Bearer k", 3.0)]


# --- JSONL inbox ----------------------------------------------------------

def test_inbox_mode_and_redaction(live):
    handle(turo(text="I'm locked out, call me at (207) 555-1234 or sam@example.com"))
    p = bland_relay.turo_inbox_path()
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    row = inbox()[0]
    assert "555-1234" not in row["text"] and "sam@example.com" not in row["text"]
    assert "[phone]" in row["text"] and "[email]" in row["text"]
    assert row["suppressed"] is False and row["urgent_class"] == "lockout"
    assert "555-1234" in live.helm[0]["obj"]["text"]  # full text only goes to Helm


def test_inbox_retention_prune(live, monkeypatch):
    p = bland_relay.turo_inbox_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"received_at": "2000-01-01T00:00:00Z"}) + "\n" +
                 json.dumps({"received_at": "2999-01-01T00:00:00Z"}) + "\n")
    assert bland_relay.turo_prune_inbox(force=True) == 1
    assert [r["received_at"] for r in inbox()] == ["2999-01-01T00:00:00Z"]


def test_logs_have_no_body_or_secrets(live, caplog):
    with caplog.at_level(logging.INFO, logger="bland-relay"):
        handle(turo(text="I'm locked out at 12 Elm St", title="Samantha", sub_text="Trip 12345678"))
    assert "Elm St" not in caplog.text and "Samantha" not in caplog.text
    assert "12345678" not in caplog.text and HELM_KEY not in caplog.text
    assert OWNER.lstrip("+") not in caplog.text and OWNER[2:] not in caplog.text


def test_scrub_redacts_helm_secrets(live):
    assert HELM_KEY not in bland_relay._scrub("x " + HELM_KEY)
    assert HELM_URL not in bland_relay._scrub("x " + HELM_URL)


# --- RCS_HOOK_TOKEN is mandatory for the route that carries Turo ----------

@pytest.mark.parametrize("val", [None, "", "   "])
def test_turo_route_refused_without_rcs_token(http, live, monkeypatch, val):
    if val is None:
        monkeypatch.delenv("RCS_HOOK_TOKEN", raising=False)
    else:
        monkeypatch.setenv("RCS_HOOK_TOKEN", val)
    for tok in (None, "", TOKEN, "anything"):
        code, _ = post(http, turo(text="I'm locked out"), tok=tok)
        assert code in (401, 503)
    code, _ = post(http, {"package": MSGS, "title": "Sam", "text": "hello"}, tok=None)
    assert code in (401, 503)  # whole RCS route, not just Turo
    assert live.sms == [] and live.helm == [] and live.alex == [] and inbox() == []


def test_turo_route_missing_token_401(http, live):
    code, _ = post(http, turo(text="I'm locked out"), tok=None)
    assert code == 401
    assert live.sms == [] and live.helm == [] and inbox() == []
