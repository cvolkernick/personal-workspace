"""Inbound 904 SMS -> Helm: id dedupe, content dedupe of phone re-sends, lag telemetry."""
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import bland_relay

ET = ZoneInfo("America/New_York")
MOBI = "+19045556630"
CARLUX = "+19045557531"


def ep(hms):
    return datetime.fromisoformat("2026-10-25T" + hms).replace(tzinfo=ET).timestamp()


def sms(frm, body, ts):
    return {"from": frm, "body": body, "received_at": "2026-10-25T%s-04:00" % ts, "via": "sms"}


def notif(frm, body, posted):
    return {"from": frm, "body": body, "received_at": posted, "via": "rcs-notification"}


@pytest.fixture
def posts(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HELM_INBOUND_SMS_FORWARD", "1")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "http://127.0.0.1:9/helm")
    monkeypatch.delenv("HELM_SMS_CONTENT_WINDOW_D", raising=False)
    sent = []

    def fake_post(url, payload, *a, **k):
        sent.append(payload)
        return 200, None, 1
    monkeypatch.setattr(bland_relay, "post_json", fake_post)
    return sent


def fwd(rec, now, sid=None):
    return bland_relay.helm_forward_inbound_sms(rec, sid, now=now)


def test_reconnect_resend_batch_is_dropped(posts):
    """2026-10-25: the same batch came at 11:39 and again at 13:57 ET with new ids."""
    batch = [(CARLUX, "Thank you"), ("Capital One", "Card alert " + "a" * 120),
             ("GM Financial", "Payment notice " + "b" * 300), ("Santander", "Reminder " + "c" * 100)]
    for frm, body in batch:
        assert fwd(notif(frm, body, "2026-10-25T15:39:23Z"), ep("11:39:33")) == "ok"
    assert fwd(sms(MOBI, "Mobi 7am", "07:00:00"), ep("11:39:06"), "smsgate:a") == "ok"
    for frm, body in batch:
        assert fwd(notif(frm, body, "2026-10-25T17:57:58Z"), ep("13:58:09")) == "duplicate"
    assert fwd(sms(MOBI, "  mobi   7AM ", "07:00:00"), ep("13:58:10"), "smsgate:b") == "duplicate"
    assert len(posts) == 5


def test_genuinely_new_texts_pass(posts):
    assert fwd(sms(MOBI, "Same words", "07:00:00"), ep("07:00:05"), "smsgate:1") == "ok"
    assert fwd(sms(MOBI, "Same words", "09:00:00"), ep("09:00:05"), "smsgate:2") == "ok"
    assert fwd(notif(CARLUX, "Hello", "2026-10-25T13:00:00Z"), ep("09:00:01")) == "ok"
    assert fwd(notif(CARLUX, "Different", "2026-10-25T13:01:00Z"), ep("09:01:01")) == "ok"
    assert fwd(sms("+19045550001", "Book Direct request", "09:02:00"), ep("09:02:01"), "smsgate:3") == "ok"
    assert len(posts) == 5


def test_cross_path_copies_both_orders(posts):
    assert fwd(sms(MOBI, "Book Direct", "10:00:00"), ep("10:00:02"), "smsgate:x") == "ok"
    assert fwd(notif(MOBI, "Book Direct", "2026-10-25T14:00:01Z"), ep("10:00:04")) == "duplicate"
    assert fwd(notif(CARLUX, "Notif first", "2026-10-25T14:05:00Z"), ep("10:05:01")) == "ok"
    assert fwd(sms(CARLUX, "Notif first", "10:04:58"), ep("10:05:06"), "smsgate:y") == "duplicate"
    assert len(posts) == 2


def test_same_words_after_our_reply_pass(posts, tmp_path):
    assert fwd(notif(CARLUX, "Yes", "2026-10-25T14:00:00Z"), ep("10:00:01")) == "ok"
    assert fwd(notif(CARLUX, "Yes", "2026-10-25T14:30:00Z"), ep("10:30:01")) == "duplicate"
    path = bland_relay._sms_state_path()
    st = bland_relay._load_json(path)
    st.setdefault("sends", []).append({"to": CARLUX, "at": ep("10:40:00")})
    bland_relay._save_json(path, st)
    assert fwd(notif(CARLUX, "Yes", "2026-10-25T15:00:00Z"), ep("11:00:01")) == "ok"


def test_notification_repeat_past_24h_passes(posts):
    assert fwd(notif(CARLUX, "See you at 9", "2026-10-25T14:00:00Z"), ep("10:00:00")) == "ok"
    assert fwd(notif(CARLUX, "See you at 9", "2026-10-26T14:01:00Z"), ep("10:00:00") + 24 * 3600 + 60) == "ok"


def test_notification_repeat_within_24h_dropped(posts):
    assert fwd(notif(CARLUX, "See you at 9", "2026-10-25T14:00:00Z"), ep("10:00:00")) == "ok"
    assert fwd(notif(CARLUX, "See you at 9", "2026-10-26T13:00:00Z"), ep("10:00:00") + 23 * 3600) == "duplicate"


def test_carrier_timestamp_key_at_3_days_dropped(posts):
    assert fwd(sms(MOBI, "Mobi 7am", "07:00:00"), ep("07:00:05"), "smsgate:a") == "ok"
    assert fwd(sms(MOBI, "Mobi 7am", "07:00:00"), ep("07:00:05") + 3 * 86400, "smsgate:b") == "duplicate"
    assert fwd(sms(MOBI, "Mobi 7am", "07:00:00"), ep("07:00:05") + 7.1 * 86400, "smsgate:c") == "ok"


def test_window_floors(posts, monkeypatch):
    monkeypatch.setenv("HELM_SMS_CONTENT_WINDOW_D", "1")
    monkeypatch.setenv("INBOUND_CONTENT_NOTS_WINDOW_H", "0.01")
    assert bland_relay._helm_sms_content_window_s() == 7 * 86400
    assert bland_relay._helm_sms_nots_window_s() == 3600
    monkeypatch.setenv("INBOUND_CONTENT_NOTS_WINDOW_H", "junk")
    assert bland_relay._helm_sms_nots_window_s() == 24 * 3600
    monkeypatch.setenv("INBOUND_CONTENT_NOTS_WINDOW_H", "48")
    assert fwd(notif(CARLUX, "Hi", "a"), ep("10:00:00")) == "ok"
    assert fwd(notif(CARLUX, "Hi", "b"), ep("10:00:00") + 40 * 3600) == "duplicate"


def test_pruning_uses_each_keys_window(posts, tmp_path):
    assert fwd(sms(MOBI, "Timed", "07:00:00"), ep("07:00:05"), "smsgate:a") == "ok"   # e + c(src)
    assert fwd(notif(CARLUX, "Untimed", "x"), ep("07:00:06")) == "ok"                # c(rcs)
    assert fwd(notif("+19045550002", "Later", "y"), ep("07:00:06") + 2 * 86400) == "ok"  # triggers a prune
    keys = json.loads((tmp_path / "helm_sms_content.json").read_text())["keys"]
    assert sum(1 for v in keys.values() if "v" not in v) == 1   # the 2-day-old "e" key survives
    assert sum(1 for v in keys.values() if "v" in v) == 1       # only the fresh "c" key survives


def test_store_is_hashed_and_survives_reload(posts, tmp_path):
    assert fwd(notif(CARLUX, "Secret words 123", "2026-10-25T14:00:00Z"), ep("10:00:00")) == "ok"
    raw = (tmp_path / "helm_sms_content.json").read_text()
    assert "Secret" not in raw and "7531" not in raw
    store = json.loads(raw)
    assert len(store["salt"]) == 32 and all(len(k) == 32 for k in store["keys"])
    assert bland_relay.helm_sms_content_check(notif(CARLUX, "secret  WORDS 123", "z"), ep("12:00:00"),
                                              claim=False)[0] == "duplicate"


def test_failed_post_rolls_back_so_retry_passes(posts, monkeypatch):
    monkeypatch.setattr(bland_relay, "post_json", lambda *a, **k: (None, "URLError", 2))
    assert fwd(notif(CARLUX, "Retry me", "2026-10-25T14:00:00Z"), ep("10:00:00")) == "failed"
    monkeypatch.setattr(bland_relay, "post_json", lambda *a, **k: (200, None, 1))
    assert fwd(notif(CARLUX, "Retry me", "2026-10-25T14:00:05Z"), ep("10:00:06")) == "ok"


def test_test_records_skip_content_dedupe(posts):
    rec = dict(notif(CARLUX, "Test", "2026-10-25T14:00:00Z"), test=True)
    assert fwd(rec, ep("10:00:00")) == "ok"
    assert fwd(dict(rec, received_at="2026-10-25T14:10:00Z"), ep("10:10:00")) == "ok"


def test_lag_telemetry_on_log_line(posts, caplog):
    caplog.set_level(logging.INFO, logger=bland_relay.log.name)
    assert fwd(sms(MOBI, "Late one", "07:00:00"), ep("11:39:06"), "smsgate:lag") == "ok"
    line = [r.getMessage() for r in caplog.records if "helm inbound_sms ok" in r.getMessage()][-1]
    assert "src_at=2026-10-25T07:00:00-04:00" in line and "lag_s=" in line
    assert "Late one" not in line and MOBI not in line
