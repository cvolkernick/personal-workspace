"""Gmail push path for Turo guest messages (#1142). Fixtures only, no network.

Gmail history and messages come from tests/fixtures/gmail_turo (synthetic,
555 numbers). The subscriber's relay handoff is wired straight into
bland_relay.turo_email_handle, so every case runs through the same
turo_ingest() path as the 904 phone notifications. Helm, SMS gateway and
Alexandra are stubbed.
"""
import json
import threading
import urllib.error
import urllib.request  # noqa: F401  (patched in isolated)
from pathlib import Path

import pytest

import bland_relay
import gmail_turo_watch as gtw
import turo_email

FIX = Path(__file__).parent / "fixtures" / "gmail_turo"
MESSAGES = json.loads((FIX / "messages.json").read_text(encoding="utf-8"))
HISTORY = json.loads((FIX / "history.json").read_text(encoding="utf-8"))
NOW = MESSAGES["now_ms"] / 1000.0
MSG = MESSAGES["messages"]
OWNER = "+12025550123"
TOKEN = "email-token-abc123"
SUPPRESS = r"(?<![a-z0-9])(safe ?wheels|pat example|telluride)(?![a-z0-9])"
TURO_PKG = "com.relayrides.android.relayrides"

ENV_KEYS = (
    "ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH", "SMSGW_URL", "SMSGW_USER", "SMSGW_PASS",
    "SMS_SEND_DISABLED", "TURO_HOOK_DISABLED", "TURO_OWNER_ALERT_TO", "TURO_OWNER_ALERT_DISABLED",
    "TURO_SUPPRESS_RE", "TURO_DEDUPE_WINDOW_S", "TURO_XDEDUPE_WINDOW_S", "TURO_INBOX_PATH",
    "HELM_TURO_WEBHOOK_URL", "HELM_TURO_WEBHOOK_KEY", "HELM_TURO_WEBHOOK_HEADER", "TURO_ALEX_PUSH_DISABLED",
    "TURO_EMAIL_DISABLED", "TURO_EMAIL_SHADOW", "TURO_EMAIL_TOKEN", "TURO_EMAIL_PORT",
    "TURO_EMAIL_MAX_AGE_S", "TURO_EMAIL_REQUIRE_DKIM", "TURO_EMAIL_FROM_DOMAINS", "TURO_EMAIL_RELAY_URL",
    "GMAIL_WATCH_EMAIL", "GMAIL_WATCH_TOPIC", "GMAIL_WATCH_LABEL_IDS", "GMAIL_WATCH_STATE_DIR",
    "TURO_ALERT_GUEST_WINDOW_S", "TURO_ALERT_GUEST_MAX", "TURO_ALERT_HOUR_MAX",
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path / "relay"))
    monkeypatch.setenv("GMAIL_WATCH_STATE_DIR", str(tmp_path / "watch"))
    monkeypatch.setenv("TURO_SUPPRESS_RE", SUPPRESS)
    monkeypatch.setattr(bland_relay, "_spawn", lambda target, args=(): target(*args))
    monkeypatch.setattr(bland_relay.time, "sleep", lambda _s: None)
    monkeypatch.setattr(bland_relay, "_post_github", lambda *_a, **_k: {"skipped": "test"})
    bland_relay._recent_msgs.clear()
    bland_relay._turo_prune["at"] = 0.0

    def no_network(*_a, **_k):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(bland_relay.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(gtw.urllib.request, "urlopen", no_network)
    yield


class Rig:
    def __init__(self):
        self.sms, self.helm, self.alex = [], [], []
        self.handoffs = []
        self.relay_ok = True


@pytest.fixture
def rig(monkeypatch):
    r = Rig()
    monkeypatch.setenv("TURO_OWNER_ALERT_TO", OWNER)
    monkeypatch.setenv("SMSGW_URL", "https://gw.example.invalid")
    monkeypatch.setenv("SMSGW_USER", "u")
    monkeypatch.setenv("SMSGW_PASS", "p")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "https://helm.example.invalid/hook")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_KEY", "helm-key")
    monkeypatch.setenv("ALEXANDRA_ALERT_URL", "https://alex.example.invalid/hook")
    monkeypatch.setenv("ALEXANDRA_ALERT_AUTH", "Bearer alex")

    def gw(to, message, opener=None):
        r.sms.append((to, message))
        return {"id": "m1"}, ""

    def post_json(url, obj, headers=None, timeout=5.0, tries=2):
        r.helm.append(json.loads(json.dumps(obj)))
        return 200, "", 1

    def fwd(body, timeout=10):
        r.alex.append(json.loads(body.decode()))
        return 200, "", 1

    monkeypatch.setattr(bland_relay, "gateway_send", gw)
    monkeypatch.setattr(bland_relay, "post_json", post_json)
    monkeypatch.setattr(bland_relay, "forward", fwd)
    return r


class FakeGmail:
    """users.history / messages / profile / watch from fixtures."""

    def __init__(self, history=None, messages=None, history_404=False, fail_ids=()):
        self.history_pages = history if history is not None else HISTORY
        self.messages = messages if messages is not None else MSG
        self.history_404 = history_404
        self.fail_ids = set(fail_ids)
        self.calls = []
        self.watch_body = None

    def history(self, start, page_token=None):
        self.calls.append(("history", str(start), page_token))
        if self.history_404:
            raise gtw.GmailError(404, "GET history")
        return self.history_pages["page2" if page_token == "p2" else "page1"]

    def message(self, mid):
        self.calls.append(("message", mid))
        if mid in self.fail_ids:
            raise gtw.GmailError(500, "GET messages")
        if mid not in self.messages:
            raise gtw.GmailError(404, "GET messages")
        return json.loads(json.dumps(self.messages[mid]))

    def list_ids(self, query):
        self.calls.append(("list", query))
        return ["j1", "a1"]  # newest first, like Gmail

    def profile(self):
        return {"historyId": "2000"}

    def watch(self, topic, label_ids=None):
        self.watch_body = {"topic": topic, "labels": label_ids}
        return {"historyId": "1000", "expiration": str(MESSAGES["now_ms"] + 7 * 86400 * 1000)}


def relay_poster(r, now=NOW):
    """Hand-off straight into the relay's email handler (same code as the loopback route)."""
    def poster(fields):
        r.handoffs.append(fields)
        if not r.relay_ok:
            return False
        code, _body = bland_relay.turo_email_handle(json.loads(json.dumps(fields)), now)
        return 200 <= code < 300
    return poster


def notification(guest, vehicle, body, res="10000003", now=NOW):
    parsed = bland_relay.parse_rcs_notification(json.dumps({
        "package": TURO_PKG, "app": "Turo", "title": "Turo",
        "text": f"About {vehicle} from {guest}:\n{body}", "postedAt": int(now * 1000),
        "key": f"0|{TURO_PKG}|{res}|null|10331",
    }).encode())
    return bland_relay.turo_handle(parsed, now)


def inbox():
    p = bland_relay.turo_inbox_path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def run_sync(r, gmail=None, start="1000", now=NOW):
    st = {"history_id": start}
    ok = gtw.sync(gmail or FakeGmail(), st, relay_poster(r, now), now)
    return ok, st


# --- parser ----------------------------------------------------------------

def test_parse_guest_message_both_copies():
    a, why = turo_email.parse_gmail_message(MSG["a1"], NOW)
    b, why2 = turo_email.parse_gmail_message(MSG["a2"], NOW)
    assert why is None and why2 is None
    for f in (a, b):
        assert f["guest"] == "Avery" and f["vehicle"] == "Rivian R1S"
        assert f["reservation_id"] == "10000001"
        assert f["text"] == "Hi! What time can I pick up tomorrow?"
    assert "555" not in a["text"]


def test_parse_multiline_body():
    f, _ = turo_email.parse_gmail_message(MSG["b1"], NOW)
    assert f["text"] == "I'm at the car now.\nThe phone key isn't working and the doors are still locked."


def test_parse_html_only():
    f, why = turo_email.parse_gmail_message(MSG["h1"], NOW)
    assert why is None and f["text"] == "Is early return OK?" and f["reservation_id"] == "10000006"


@pytest.mark.parametrize("mid,reason", [
    ("n1", "not_turo"), ("n2", "not_guest_message"), ("s1", "dkim"), ("o1", "too_old"),
])
def test_parse_ignores(mid, reason):
    assert turo_email.parse_gmail_message(MSG[mid], NOW) == (None, reason)


def test_parse_dkim_can_be_relaxed(monkeypatch):
    monkeypatch.setenv("TURO_EMAIL_REQUIRE_DKIM", "0")
    f, _ = turo_email.parse_gmail_message(MSG["s1"], NOW)
    assert f["guest"] == "Mallory"


def test_parse_skips_spam_and_sent():
    m = json.loads(json.dumps(MSG["a1"]))
    m["labelIds"] = ["SPAM"]
    assert turo_email.parse_gmail_message(m, NOW) == (None, "label")


def test_lookalike_sender_domain_rejected():
    m = json.loads(json.dumps(MSG["a1"]))
    m["payload"]["headers"] = [h if h["name"] != "From" else {"name": "From", "value": "x <a@notturo.com>"}
                               for h in m["payload"]["headers"]]
    assert turo_email.parse_gmail_message(m, NOW) == (None, "not_turo")


# --- end to end through the relay ingest path -------------------------------

def test_history_sync_end_to_end(rig):
    ok, st = run_sync(rig)
    assert ok and st["history_id"] == "1010"
    guests = [h["guest"] for h in rig.helm]
    # Avery once (two copies + a repeat id), Blake (urgent), Jordan. Casey suppressed;
    # non-Turo, booking, spoofed and old mail ignored; SENT never fetched.
    assert guests == ["Avery", "Blake", "Jordan"]
    assert set(rig.helm[0]) == set(bland_relay.TURO_HELM_FIELDS)
    assert rig.helm[0]["reservation_id"] == "10000001" and rig.helm[0]["vehicle"] == "Rivian R1S"
    assert len([f for f in rig.handoffs if f["guest"] == "Avery"]) == 2


def test_sent_messages_not_fetched(rig):
    g = FakeGmail()
    gtw.sync(g, {"history_id": "1000"}, relay_poster(rig), NOW)
    ids = [c[1] for c in g.calls if c[0] == "message"]
    assert "sent1" not in ids
    assert ids.count("a1") == 1  # repeated id in history fetched once


def test_two_gmail_copies_one_helm_post(rig):
    for mid in ("a1", "a2"):
        f, _ = turo_email.parse_gmail_message(MSG[mid], NOW)
        bland_relay.turo_email_handle(f, NOW)
    assert len(rig.helm) == 1


def test_relay_already_sent_no_double(rig):
    code, body = notification("Jordan", "Rivian R1S", "Can I bring a bike rack for the trip?", now=NOW - 40)
    assert body["turo"] == "accepted" and len(rig.helm) == 1
    f, _ = turo_email.parse_gmail_message(MSG["j1"], NOW)
    code, body = bland_relay.turo_email_handle(f, NOW)
    assert (code, body) == (200, {"ok": True, "turo": "duplicate"})
    assert len(rig.helm) == 1


def test_email_first_then_relay_no_double(rig):
    f, _ = turo_email.parse_gmail_message(MSG["j1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW)[1]["turo"] == "accepted"
    code, body = notification("Jordan", "Rivian R1S", "Can I bring a bike rack for the trip?", now=NOW + 3.6 * 3600)
    assert body == {"ok": True, "turo": "duplicate"}
    assert len(rig.helm) == 1


def test_truncated_notification_still_matches(rig):
    long_body = "Can I bring a bike rack for the trip?"
    notification("Jordan", "Rivian R1S", long_body[:30], now=NOW - 5)
    f, _ = turo_email.parse_gmail_message(MSG["j1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW)[1]["turo"] == "duplicate"


def test_cross_source_needs_same_guest_vehicle_reservation(rig):
    notification("Jordan", "Tesla Model 3", "Can I bring a bike rack for the trip?", res="10000099", now=NOW - 5)
    f, _ = turo_email.parse_gmail_message(MSG["j1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW)[1]["turo"] == "accepted"
    assert len(rig.helm) == 2


def test_cross_source_window(rig, monkeypatch):
    monkeypatch.setenv("TURO_XDEDUPE_WINDOW_S", "600")
    notification("Jordan", "Rivian R1S", "Can I bring a bike rack for the trip?", now=NOW - 3600)
    f, _ = turo_email.parse_gmail_message(MSG["j1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW)[1]["turo"] == "accepted"


def test_relay_only_behavior_unchanged(rig):
    # Same guest + body for two different trips from the relay alone: the
    # cross-source key never dedupes within one source.
    a = notification("Jordan", "Rivian R1S", "Thanks so much, all good here!", res="10000003")
    b = notification("Jordan", "Tesla Model 3", "Thanks so much, all good here!", res="10000008")
    assert a[1]["turo"] == b[1]["turo"] == "accepted"


def test_suppressed_cohost_car(rig):
    f, _ = turo_email.parse_gmail_message(MSG["c1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW) == (200, {"ok": True, "turo": "suppressed"})
    assert rig.helm == [] and rig.sms == []
    rows = inbox()
    assert len(rows) == 1 and rows[0]["suppressed"] is True and rows[0]["src"] == "gmail"


def test_suppressed_by_owner_prefix_only(rig, monkeypatch):
    monkeypatch.setenv("TURO_SUPPRESS_RE", r"pat example")
    f, _ = turo_email.parse_gmail_message(MSG["c1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW)[1]["turo"] == "suppressed"


def test_urgent_class_owner_alert_and_alexandra(rig):
    f, _ = turo_email.parse_gmail_message(MSG["b1"], NOW)
    code, body = bland_relay.turo_email_handle(f, NOW)
    assert body == {"ok": True, "turo": "accepted", "urgent": True}
    assert len(rig.sms) == 1 and rig.sms[0][0] == OWNER
    assert rig.sms[0][1].startswith("URGENT Turo [lockout]: Blake:")
    assert len(rig.alex) == 1 and rig.alex[0]["kind"] == "turo_urgent"
    assert rig.helm[0]["urgent"] is True and rig.helm[0]["urgent_class"] == "lockout"


def test_urgent_relay_first_no_second_owner_alert(rig):
    notification("Blake", "Tesla Model 3",
                 "I'm at the car now.\nThe phone key isn't working and the doors are still locked.",
                 res="10000002", now=NOW - 10)
    assert len(rig.sms) == 1
    f, _ = turo_email.parse_gmail_message(MSG["b1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW)[1]["turo"] == "duplicate"
    assert len(rig.sms) == 1 and len(rig.alex) == 1 and len(rig.helm) == 1


def test_non_turo_never_handed_off(rig):
    run_sync(rig)
    assert {f["guest"] for f in rig.handoffs} == {"Avery", "Blake", "Casey", "Jordan"}


def test_email_event_shape(rig):
    f, _ = turo_email.parse_gmail_message(MSG["a1"], NOW)
    ev = bland_relay.turo_email_event(f, NOW)
    assert ev["src"] == "gmail" and ev["line"] is None and ev["key"] == "gmail|10000001"
    assert ev["guest"] == "Avery" and ev["text"] == "Hi! What time can I pick up tomorrow?"
    assert ev["posted_at"] == bland_relay._iso(NOW - 30)


def test_email_kill_switches(rig, monkeypatch):
    f, _ = turo_email.parse_gmail_message(MSG["a1"], NOW)
    monkeypatch.setenv("TURO_EMAIL_DISABLED", "1")
    assert bland_relay.turo_email_handle(f, NOW)[1] == {"ok": True, "ignored": "turo_disabled"}
    monkeypatch.delenv("TURO_EMAIL_DISABLED")
    monkeypatch.setenv("TURO_HOOK_DISABLED", "1")
    assert bland_relay.turo_email_handle(f, NOW)[1]["ignored"] == "turo_disabled"
    assert rig.helm == []


def test_shadow_mode_writes_nothing(rig, monkeypatch):
    monkeypatch.setenv("TURO_EMAIL_SHADOW", "1")
    f, _ = turo_email.parse_gmail_message(MSG["b1"], NOW)
    code, body = bland_relay.turo_email_handle(f, NOW)
    assert body["turo"] == "shadow" and body["verdict"] == "new"
    assert rig.helm == [] and rig.sms == [] and rig.alex == [] and inbox() == []
    assert not bland_relay._turo_state_path().exists()


def test_shadow_reports_cross_duplicate(rig, monkeypatch):
    notification("Jordan", "Rivian R1S", "Can I bring a bike rack for the trip?", now=NOW - 5)
    monkeypatch.setenv("TURO_EMAIL_SHADOW", "1")
    f, _ = turo_email.parse_gmail_message(MSG["j1"], NOW)
    assert bland_relay.turo_email_handle(f, NOW)[1]["verdict"] == "cross_duplicate"


# --- sync mechanics ----------------------------------------------------------

def test_relay_failure_keeps_history_id_and_nacks(rig):
    rig.relay_ok = False
    ok, st = run_sync(rig)
    assert ok is False and st["history_id"] == "1000"
    rig.relay_ok = True
    ok = gtw.sync(FakeGmail(), st, relay_poster(rig), NOW)
    assert ok and st["history_id"] == "1010"
    assert [h["guest"] for h in rig.helm] == ["Avery", "Blake", "Jordan"]


def test_gmail_fetch_failure_nacks(rig):
    ok, st = run_sync(rig, gmail=FakeGmail(fail_ids={"b1"}))
    assert ok is False and st["history_id"] == "1000"
    assert "a1" in st["processed"] and "b1" not in st["processed"]


def test_history_404_recovers_once(rig):
    g = FakeGmail(history_404=True)
    ok, st = run_sync(rig, gmail=g)
    assert ok and st["history_id"] == "2000"
    assert [c for c in g.calls if c[0] == "list"]
    assert [h["guest"] for h in rig.helm] == ["Avery", "Jordan"]


def test_missing_history_id_recovers(rig):
    st = {}
    assert gtw.sync(FakeGmail(), st, relay_poster(rig), NOW)
    assert st["history_id"] == "2000"


def test_syncer_ack_rules(rig, monkeypatch):
    s = gtw.Syncer(FakeGmail(), poster=relay_poster(rig))
    gtw.save_state({"history_id": "1000"})
    assert s.handle(b"not json") is True
    monkeypatch.setenv("GMAIL_WATCH_EMAIL", "fleet@example.com")
    assert s.handle(json.dumps({"emailAddress": "other@example.com", "historyId": 5000}).encode()) is True
    assert rig.helm == []
    assert s.handle(json.dumps({"emailAddress": "fleet@example.com", "historyId": 1010}).encode(), NOW) is True
    assert gtw.load_state()["history_id"] == "1010" and len(rig.helm) == 3
    # Stale notification (historyId already covered): ack without any Gmail call.
    s.gmail.calls.clear()
    assert s.handle(json.dumps({"emailAddress": "fleet@example.com", "historyId": 1009}).encode(), NOW) is True
    assert s.gmail.calls == []


def test_syncer_nacks_on_relay_down(rig):
    rig.relay_ok = False
    s = gtw.Syncer(FakeGmail(), poster=relay_poster(rig))
    gtw.save_state({"history_id": "1000"})
    assert s.handle(json.dumps({"historyId": 1010}).encode(), NOW) is False
    assert gtw.load_state()["history_id"] == "1000"


def test_state_file_mode(rig):
    gtw.save_state({"history_id": "1"})
    assert (gtw.state_path().stat().st_mode & 0o777) == 0o600


def test_renew_sets_topic_and_history(rig, monkeypatch):
    monkeypatch.setenv("GMAIL_WATCH_TOPIC", "projects/example-project/topics/gmail-turo")
    g = FakeGmail()
    assert gtw.cmd_renew(g) == 0
    assert g.watch_body == {"topic": "projects/example-project/topics/gmail-turo", "labels": []}
    st = gtw.load_state()
    assert st["history_id"] == "1000" and st["watch_expiration_ms"] > MESSAGES["now_ms"]
    gtw.save_state(dict(st, history_id="1500"))
    assert gtw.cmd_renew(g) == 0 and gtw.load_state()["history_id"] == "1500"


def test_renew_label_filter_and_bad_topic(rig, monkeypatch):
    monkeypatch.setenv("GMAIL_WATCH_TOPIC", "gmail-turo")
    assert gtw.cmd_renew(FakeGmail()) == 2
    monkeypatch.setenv("GMAIL_WATCH_TOPIC", "projects/p/topics/t")
    monkeypatch.setenv("GMAIL_WATCH_LABEL_IDS", "Label_1, INBOX")
    g = FakeGmail()
    gtw.cmd_renew(g)
    assert g.watch_body["labels"] == ["Label_1", "INBOX"]


def test_relay_url_must_be_loopback(monkeypatch):
    monkeypatch.setenv("TURO_EMAIL_RELAY_URL", "https://relay.example.invalid/turo/email")
    with pytest.raises(SystemExit):
        gtw.relay_url()


def test_post_to_relay_without_token_fails_closed(monkeypatch):
    assert gtw.post_to_relay({"message_id": "x"}) is False


def test_gmail_client_refresh_and_errors(tmp_path):
    tok = tmp_path / "tok.json"
    tok.write_text(json.dumps({"refresh_token": "r", "client_id": "c", "client_secret": "s"}))
    seen = []

    class Resp:
        def __init__(self, body, status=200):
            self.body, self.status = body, status

        def read(self):
            return json.dumps(self.body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        seen.append((req.get_method(), req.full_url.split("?")[0]))
        if "oauth2" in req.full_url:
            return Resp({"access_token": "at", "expires_in": 3600})
        if "/history" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return Resp({"historyId": "7"})

    g = gtw.Gmail(token_path=tok, opener=opener)
    assert g.profile() == {"historyId": "7"}
    with pytest.raises(gtw.GmailError) as e:
        g.history(1)
    assert e.value.status == 404 and "tok" not in str(e.value)
    assert sum(1 for m, u in seen if "oauth2" in u) == 1  # access token cached


# --- loopback listener -------------------------------------------------------

@pytest.fixture
def listener(monkeypatch, rig):
    monkeypatch.setenv("TURO_EMAIL_TOKEN", TOKEN)
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.TuroEmailHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _post(base, obj, tok=TOKEN, path=bland_relay.TURO_EMAIL_PATH, extra=None):
    import http.client
    host, port = base.rsplit("//", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    body = json.dumps(obj).encode()
    hdrs = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    if tok:
        hdrs["Authorization"] = "Bearer " + tok
    hdrs.update(extra or {})
    conn.request("POST", path, body=body, headers=hdrs)
    r = conn.getresponse()
    out = (r.status, json.loads(r.read().decode() or "{}"))
    conn.close()
    return out


def test_listener_accepts_with_token(listener, rig):
    f, _ = turo_email.parse_gmail_message(MSG["a1"], NOW)
    code, body = _post(listener, f)
    assert code == 200 and body["turo"] == "accepted" and len(rig.helm) == 1


def test_listener_rejects_bad_token_wrong_path_and_proxied(listener, rig):
    f, _ = turo_email.parse_gmail_message(MSG["a1"], NOW)
    assert _post(listener, f, tok="nope")[0] == 401
    assert _post(listener, f, path="/other")[0] == 404
    assert _post(listener, f, extra={"Tailscale-Funnel-Request": "?1"})[0] == 403
    assert _post(listener, f, extra={"X-Forwarded-For": "203.0.113.9"})[0] == 403
    assert rig.helm == []


def test_listener_off_without_port_or_token(monkeypatch):
    assert bland_relay.start_turo_email_listener() is None
    monkeypatch.setenv("TURO_EMAIL_PORT", "8797")
    assert bland_relay.start_turo_email_listener() is None  # no token


def test_token_is_secret_key():
    assert "TURO_EMAIL_TOKEN" in bland_relay.SECRET_ENV_KEYS


# --- units: no polling ------------------------------------------------------

UNIT_DIR = Path(__file__).resolve().parent.parent


def test_only_timer_is_daily_renew():
    timers = sorted(p.name for p in UNIT_DIR.glob("gmail-turo-watch*.timer"))
    assert timers == ["gmail-turo-watch-renew.timer"]
    t = (UNIT_DIR / "gmail-turo-watch-renew.timer").read_text()
    assert "OnCalendar=daily" in t and "OnUnitActiveSec" not in t and "Persistent=true" in t
    svc = (UNIT_DIR / "gmail-turo-watch-renew.service").read_text()
    assert "gmail_turo_watch.py renew" in svc and "Type=oneshot" in svc
    run = (UNIT_DIR / "gmail-turo-watch.service").read_text()
    assert "gmail_turo_watch.py run" in run and "Restart=always" in run


def test_repo_files_have_no_real_contact_data():
    import re
    phone = re.compile(r"\(?\b\d{3}\)?[\s.-]?(\d{3})[\s.-]?\d{4}\b")
    for p in [*UNIT_DIR.glob("gmail-turo-watch*"), UNIT_DIR / "gmail_turo_watch.py", UNIT_DIR / "turo_email.py",
              *FIX.glob("*.json")]:
        text = p.read_text(encoding="utf-8")
        for m in phone.finditer(text):
            assert m.group(1) == "555", p.name
        assert "gmail.com" not in text.replace("gmail.googleapis.com", ""), p.name
