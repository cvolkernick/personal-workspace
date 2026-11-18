"""Turo booking mail -> kind=booking_change. Subjects are redacted fixtures."""

import json
from pathlib import Path

import pytest

import booking_alert as ba

NOW = 1_791_750_000.0
ROOT = Path(__file__).resolve().parents[1]


def mail(subject, **extra):
    rec = {
        "from": "Turo <noreply@mail.turo.com>",
        "subject": subject,
        "body": extra.pop("body", ""),
        "message_id": extra.pop("message_id", "m-1"),
    }
    rec.update(extra)
    return rec


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("HELM_TURO_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("HELM_TURO_WEBHOOK_KEY", raising=False)
    return tmp_path


def test_shipped_turo_patterns_are_verified():
    data = json.loads((ROOT / "patterns.json").read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in data["changes"]}
    assert by_id["turo_new"]["verified"] is True
    assert by_id["turo_modified"]["verified"] is True
    assert by_id["turo_cancelled"]["verified"] is True
    assert by_id["turo_new"]["change_type"] == "new"
    assert by_id["turo_modified"]["change_type"] == "modified"
    assert by_id["turo_cancelled"]["change_type"] == "cancelled"


@pytest.mark.parametrize(
    "subject,change",
    [
        ("Alex’s trip with your 2022 Toyota Corolla is booked!", "new"),
        ("(Mike's vehicle) - Alex's trip with your Toyota Corolla is booked!", "new"),
        ("Your reservation confirmed", "new"),
        ("(Mike's vehicle) - Alex has changed their trip", "modified"),
        ("Your booking was modified", "modified"),
        ("Trip canceled", "cancelled"),
        ("Your trip was cancelled", "cancelled"),
        ("Turo trip notification", "unknown"),
    ],
)
def test_booking_subjects(subject, change):
    assert ba.classify(mail(subject)) == change


@pytest.mark.parametrize(
    "subject",
    [
        "Sam has sent you a message about your Rivian R1S",
        "(Mike's vehicle) - Sam has sent you a message about your Toyota Corolla",
        "You earned $42.00",
        "Your payout is on the way",
        "Trip earnings are on the way",
        "Sam left you a review",
        "Rate your trip with Sam",
        "How was your trip?",
        "Your Turo invoice is ready",
        "A receipt for your recent trip",
    ],
)
def test_non_booking_subjects_are_not_flagged(subject):
    assert ba.classify(mail(subject)) is None


def test_guest_named_alex_is_a_booking_alex_host_and_safewheels_are_not():
    assert ba.classify(mail("Alex's trip with your Toyota Corolla is booked!")) == "new"
    assert ba.classify(mail("(Alex's vehicle) - Sam's trip with your 2023 Honda Civic is booked!")) is None
    assert ba.classify(mail("Sam's trip with your Kia Telluride is booked!", body="SafeWheels co-host")) is None
    assert ba.classify(mail("(Safe Wheels) - Sam's trip with your car is booked!")) is None


def test_unverified_pattern_is_ignored(tmp_path):
    path = tmp_path / "patterns.json"
    path.write_text(json.dumps({
        "ignore": [],
        "exclude": [],
        "changes": [{
            "id": "draft",
            "verified": False,
            "change_type": "new",
            "re": "(?i)is booked",
        }],
    }), encoding="utf-8")
    pats = ba.load_patterns(path)
    assert ba.classify(mail("Alex's trip with your Toyota Corolla is booked!"), pats) is None


def test_non_turo_from_is_not_flagged():
    assert ba.classify(mail("Your trip is booked!", **{"from": "News <news@example.com>"})) is None


def test_deploy_checklist_names_the_pi_loader_path():
    booking = (ROOT / "README.md").read_text(encoding="utf-8")
    relay = (ROOT.parent / "deploy" / "bland-relay" / "README.md").read_text(encoding="utf-8")
    for text in (booking, relay):
        assert "~/apps/booking-alert" in text
        assert "gmail-turo-watch.service" in text
        assert "bland-relay.service" in text
        assert "HELM_TURO_WEBHOOK_URL" in text
        assert "TURO_EMAIL_TOKEN" in text
        assert "failed status=err" in text


def test_turo_from_is_false_when_from_is_missing_empty_or_has_no_domain():
    assert ba._turo_from({}) is False
    assert ba._turo_from({"from": ""}) is False
    assert ba._turo_from({"from": "Turo"}) is False
    assert ba._turo_from({"from": "Turo <noreply@mail.turo.com>"}) is True
    booked = {"subject": "Alex's trip with your Toyota Corolla is booked!"}
    assert ba.classify(dict(booked)) is None
    assert ba.classify(dict(booked, **{"from": ""})) is None
    assert ba.classify(dict(booked, **{"from": "Turo"})) is None


def test_payload_shape(state):
    payload = ba.build_payload(
        mail("Trip canceled", message_id="mid-9", received_at="2026-11-18T15:04:00-05:00"),
        "cancelled",
        now=NOW,
    )
    assert payload == {
        "kind": "booking_change",
        "schema": "panamerica.booking.event.v1",
        "change_type": "cancelled",
        "message_id": "mid-9",
        "subject": "Trip canceled",
        "received_at": "2026-11-18T20:04:00Z",
    }


def test_unknown_same_subject_wakes_twice_and_message_id_still_dedupes(state, monkeypatch):
    calls = []

    def opener(url, payload, headers, timeout):
        calls.append(payload["message_id"])
        return 200

    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "https://helm.example.invalid/hook")
    first = mail("Turo trip notification", message_id="unk-1")
    second = mail("Turo trip notification", message_id="unk-2")
    p1 = ba.prepare(first, state_dir=state, now=NOW)
    p2 = ba.prepare(second, state_dir=state, now=NOW)
    assert p1["change_type"] == "unknown"
    assert p2["change_type"] == "unknown"
    assert ba.webhook_wake(p1, opener=opener) == "ok"
    assert ba.webhook_wake(p2, opener=opener) == "ok"
    assert calls == ["unk-1", "unk-2"]
    assert ba.prepare(mail("Turo trip notification", message_id="unk-1"), state_dir=state, now=NOW) is None


def test_dedupe_same_message_and_same_reservation(state, monkeypatch):
    calls = []

    def opener(url, payload, headers, timeout):
        calls.append(payload["message_id"])
        return 204

    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "https://helm.example.invalid/hook")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_KEY", "helm-test-key")
    first = mail(
        "Alex's trip with your Toyota Corolla is booked!",
        message_id="gmail-a",
        body="Reservation ID #60615645",
    )
    second = mail(
        "(Mike's vehicle) - Alex's trip with your Toyota Corolla is booked!",
        message_id="gmail-b",
        body="Reservation ID #60615645",
    )
    assert ba.prepare(first, state_dir=state, now=NOW)["change_type"] == "new"
    assert ba.webhook_wake(ba.build_payload(first, "new", NOW), opener=opener) == "ok"
    assert ba.prepare(dict(first), state_dir=state, now=NOW) is None
    assert ba.prepare(second, state_dir=state, now=NOW) is None
    assert calls == ["gmail-a"]


def test_webhook_sets_bearer_and_does_not_log_it(state, monkeypatch, caplog):
    seen = {}

    def opener(url, payload, headers, timeout):
        seen["url"] = url
        seen["headers"] = dict(headers)
        seen["timeout"] = timeout
        seen["body"] = payload
        return 200

    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "https://helm.example.invalid/turo-inbound")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_KEY", "helm-test-key")
    caplog.set_level("INFO")
    payload = ba.build_payload(mail("Trip canceled", message_id="m"), "cancelled", NOW)
    assert ba.webhook_wake(payload, opener=opener) == "ok"
    assert seen["headers"]["Authorization"] == ba.booking_authorization("helm-test-key")
    assert seen["headers"]["Authorization"] == "Bearer helm-test-key"
    assert seen["headers"]["Content-Type"] == "application/json"
    assert seen["body"]["kind"] == "booking_change"
    assert seen["timeout"] == ba.WAKE_TIMEOUT_S
    text = caplog.text
    assert "booking wake sent status=200" in text
    assert "helm-test-key" not in text
    assert "helm.example.invalid" not in text
    assert "Authorization" not in text
    assert "Bearer " not in text


def test_webhook_http_error_and_exception(monkeypatch, caplog):
    monkeypatch.setenv("HELM_TURO_WEBHOOK_URL", "https://helm.example.invalid/hook")
    monkeypatch.setenv("HELM_TURO_WEBHOOK_KEY", "helm-test-key")
    caplog.set_level("INFO")

    def http_error(url, payload, headers, timeout):
        raise ba.urllib.error.HTTPError(url, 503, "no", hdrs=None, fp=None)

    assert ba.webhook_wake({"kind": "booking_change"}, opener=http_error) == "failed"
    assert "booking wake failed status=503" in caplog.text

    def boom(url, payload, headers, timeout):
        raise TimeoutError("timed out talking to " + url)

    assert ba.webhook_wake({"kind": "booking_change"}, opener=boom) == "failed"
    assert "booking wake failed status=err" in caplog.text
    assert "helm.example.invalid" not in caplog.text
    assert "helm-test-key" not in caplog.text


def test_webhook_unset_url_does_not_post(monkeypatch):
    called = []
    monkeypatch.delenv("HELM_TURO_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("HELM_TURO_WEBHOOK_KEY", "helm-test-key")
    assert ba.webhook_wake({"kind": "booking_change"}, opener=lambda *a: called.append(a)) == "unset"
    assert called == []


def test_gmail_fixture_subject_is_new(tmp_path):
    msg = {
        "id": "n2",
        "internalDate": "1791749970000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Turo <noreply@mail.turo.com>"},
                {"name": "Subject", "value": "Avery’s trip with your Rivian R1S is booked!"},
            ],
            "body": {"data": "UmVzZXJ2YXRpb24gSUQgIzEwMDAwMDAy"},
        },
    }
    payload = ba.prepare_gmail(msg, state_dir=tmp_path, now=1791750000.0)
    assert payload["kind"] == "booking_change"
    assert payload["schema"] == "panamerica.booking.event.v1"
    assert payload["change_type"] == "new"
    assert payload["message_id"] == "n2"
    assert "booked" in payload["subject"]
