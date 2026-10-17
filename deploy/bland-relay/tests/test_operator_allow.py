"""Operator number (TURO_OWNER_ALERT_TO) bypasses the 72-hour send allowlist only.

Ported from the live-only prism hotfix of 2026-10-14 (is_operator_number and the
kind="operator" branch in reserve_send_ex). Opt-out and rate limits still apply.
"""
import pytest

import bland_relay

OPERATOR = "+19045550142"
OTHER = "+12025550100"
T0 = 1_000_000.0


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TURO_OWNER_ALERT_TO", OPERATOR)
    monkeypatch.delenv("FC_DISABLED", raising=False)


def test_operator_bypasses_72h_allowlist_with_no_inbound():
    reason, grant = bland_relay.reserve_send_ex(OPERATOR, now=T0)
    assert reason is None
    assert grant == {"kind": "operator", "entry": None}


def test_operator_bypasses_after_allowlist_window_expired():
    bland_relay.note_inbound(OPERATOR, "hi", now=T0)
    late = T0 + bland_relay.ALLOWLIST_S + 10
    reason, grant = bland_relay.reserve_send_ex(OPERATOR, now=late)
    assert reason is None and grant["kind"] == "operator"


def test_fresh_inbound_still_reports_inbound_kind():
    bland_relay.note_inbound(OPERATOR, "hi", now=T0)
    reason, grant = bland_relay.reserve_send_ex(OPERATOR, now=T0 + 5)
    assert reason is None and grant["kind"] == "inbound"


def test_non_operator_without_allowlist_is_still_blocked():
    assert bland_relay.reserve_send(OTHER, now=T0) == "not_allowlisted"
    bland_relay.note_inbound(OTHER, "hi", now=T0)
    assert bland_relay.reserve_send(OTHER, now=T0 + bland_relay.ALLOWLIST_S + 1) == "not_allowlisted"


def test_operator_compare_normalizes_phone_forms():
    for form in ("+19045550142", "9045550142", "19045550142", "(904) 555-0142", "+1 904 555 0142"):
        assert bland_relay.is_operator_number(form) is True
    assert bland_relay.is_operator_number("+19045550143") is False
    assert bland_relay.is_operator_number(None) is False
    assert bland_relay.is_operator_number("") is False


def test_operator_env_in_loose_form_still_matches(monkeypatch):
    monkeypatch.setenv("TURO_OWNER_ALERT_TO", "(904) 555-0142")
    assert bland_relay.reserve_send(OPERATOR, now=T0) is None


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-number", "+442071838750", "+19045"])
def test_unset_or_invalid_env_means_no_bypass(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TURO_OWNER_ALERT_TO", raising=False)
    else:
        monkeypatch.setenv("TURO_OWNER_ALERT_TO", value)
    assert bland_relay.is_operator_number(OPERATOR) is False
    assert bland_relay.is_operator_number(None) is False
    assert bland_relay.is_operator_number("") is False
    assert bland_relay.reserve_send(OPERATOR, now=T0) == "not_allowlisted"


def test_opt_out_still_blocks_operator_even_after_window():
    bland_relay.note_inbound(OPERATOR, "STOP", now=T0)
    assert bland_relay.reserve_send(OPERATOR, now=T0 + 5) == "opt_out"
    assert bland_relay.reserve_send(OPERATOR, now=T0 + bland_relay.ALLOWLIST_S + 10) == "opt_out"
    bland_relay.note_inbound(OPERATOR, "START", now=T0 + 20)
    assert bland_relay.reserve_send(OPERATOR, now=T0 + 30) is None


def test_recipient_rate_limit_still_applies_to_operator():
    for _ in range(bland_relay.RECIPIENT_MAX):
        assert bland_relay.reserve_send(OPERATOR, now=T0) is None
    assert bland_relay.reserve_send(OPERATOR, now=T0) == "rate_limited"
    assert bland_relay.reserve_send(OPERATOR, now=T0 + bland_relay.RECIPIENT_WINDOW_S + 1) is None


def test_global_rate_limit_still_applies_to_operator():
    for n in range(bland_relay.GLOBAL_MAX):
        num = f"+1202555{n:04d}"
        bland_relay.note_inbound(num, "hi", now=T0)
        assert bland_relay.reserve_send(num, now=T0) is None
    assert bland_relay.reserve_send(OPERATOR, now=T0) == "rate_limited"
