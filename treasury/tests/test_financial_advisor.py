"""Unit tests for FCC financial advisor context (no live xAI calls)."""

from __future__ import annotations

import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from treasury.financial_advisor import (
    ADVISOR_SUGGESTIONS,
    AdvisorError,
    ask_financial_advisor,
    build_treasury_context,
    grok_login_status,
    parse_grok_login_output,
    reset_grok_login_state,
    start_grok_login,
)


class FinancialAdvisorContextTests(unittest.TestCase):
    def test_build_context_includes_core_keys(self) -> None:
        treasury = {
            "snapshot": {
                "as_of": "2026-07-29T00:00:00+00:00",
                "coinbase_manual": {
                    "ltv": "0.45",
                    "vault_usdc": "100",
                    "loan_principal_usdc": "50",
                },
                "x_money": {"source": "ynab", "cash": 200.0, "as_of": "2026-07-29T00:00:00+00:00"},
                "one_card": {"balance_owed": 100.0, "source": "ynab"},
                "solana": {
                    "source": "live",
                    "wallet": "CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm",
                    "book_usd": 7.8,
                    "jr_strcusx": 3.3,
                    "jr_strcusx_usd": 3.4,
                },
            },
            "evaluation": {
                "stress": {"overall": "yellow"},
                "inputs": {
                    "working_usdc": 100.0,
                    "liquid_usdc": 0.0,
                    "solana_book_usd": 7.8,
                    "solana_jr_strcusx": 3.3,
                },
                "actions": [
                    {
                        "priority": 1,
                        "kind": "ltv_check",
                        "title": "Check LTV",
                        "actor": "human",
                        "detail": "Confirm Morpho",
                    }
                ],
            },
            "braiins": {"ok": True, "days_to_next_payout_est": 20, "hash_rate_24h": 1e5},
            "fund_manager": {
                "ok": True,
                "analysis": {"ok": True, "nav_usd": 173.0, "weights_of_deployed": {"btc_digital_credit": 0.4}},
            },
        }
        coach = {
            "ok": True,
            "advice": ["Pay overdue first"],
            "obligations": [
                {
                    "item": "Electric",
                    "venue": "x_money",
                    "amount_due": 100,
                    "gap": 50,
                    "status": "partial",
                }
            ],
            "habits": {"total_liquid_available": 300},
            "unfunded": [{"item": "Rent"}],
        }
        ctx = build_treasury_context(treasury, coach=coach)
        self.assertEqual(ctx["stress"]["overall"], "yellow")
        self.assertEqual(ctx["inputs"]["working_usdc"], 100.0)
        self.assertEqual(ctx["x_money"]["cash"], 200.0)
        self.assertAlmostEqual(ctx["solana"]["book_usd"], 7.8)
        self.assertFalse(ctx["solana"]["counts_toward_hy"])
        self.assertEqual(ctx["actions"][0]["title"], "Check LTV")
        self.assertIsNotNone(ctx["coach"])
        self.assertEqual(ctx["coach"]["top_obligations"][0]["item"], "Electric")
        self.assertEqual(ctx["fund_manager"]["nav_usd"], 173.0)
        self.assertTrue(ctx["braiins"]["ok"])

    def test_ask_requires_question(self) -> None:
        with self.assertRaises(AdvisorError) as cm:
            ask_financial_advisor("", {"snapshot": {}, "evaluation": {}})
        self.assertEqual(cm.exception.status, 400)

    def test_suggestions_nonempty(self) -> None:
        self.assertGreaterEqual(len(ADVISOR_SUGGESTIONS), 3)


_FAKE_GROK = """#!/usr/bin/env python3
import os
import sys
import time
print("Please visit https://auth.x.ai/activate", flush=True)
print("And enter code: ABCD-WXYZ", flush=True)
if os.environ.get("FAKE_GROK_LEAK"):
    print("access_token=super-secret-token-value", flush=True)
time.sleep(float(os.environ.get("FAKE_GROK_SLEEP", "0.15")))
raise SystemExit(int(os.environ.get("FAKE_GROK_EXIT", "0")))
"""


def _write_fake_grok(tmp: Path) -> Path:
    path = tmp / "grok"
    path.write_text(_FAKE_GROK, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class GrokLoginCliTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_grok_login_state()
        for key in ("FCC_GROK_BIN", "FAKE_GROK_EXIT", "FAKE_GROK_SLEEP", "FAKE_GROK_LEAK"):
            os.environ.pop(key, None)

    def test_parse_device_auth_public_fields(self) -> None:
        parsed = parse_grok_login_output(
            "Please visit https://auth.x.ai/activate\nEnter code: ABCD-WXYZ\n"
        )
        self.assertEqual(parsed["verification_uri"], "https://auth.x.ai/activate")
        self.assertEqual(parsed["user_code"], "ABCD-WXYZ")

    def test_parse_ignores_token_urls(self) -> None:
        parsed = parse_grok_login_output(
            "https://evil.example/cb?access_token=SECRET\ncode: ABCD-WXYZ"
        )
        self.assertIsNone(parsed["verification_uri"])
        self.assertEqual(parsed["user_code"], "ABCD-WXYZ")

    def test_start_fails_when_grok_missing(self) -> None:
        reset_grok_login_state()
        with mock.patch("treasury.financial_advisor._which_grok", return_value=None):
            out = start_grok_login()
        self.assertFalse(out["ok"])
        self.assertEqual(out["phase"], "fail")
        self.assertIn("not found", out["error"])
        for banned in ("access_token", "refresh_token", "client_secret", "device_code"):
            self.assertNotIn(banned, out)

    def test_start_and_poll_report_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            grok = _write_fake_grok(Path(td))
            os.environ["FCC_GROK_BIN"] = str(grok)
            os.environ["FAKE_GROK_SLEEP"] = "0.2"
            os.environ["FAKE_GROK_EXIT"] = "0"
            started = start_grok_login()
            self.assertTrue(started["started"])
            self.assertEqual(started["method"], "grok_cli")
            self.assertNotIn("access_token", started)
            deadline = time.time() + 5
            last = started
            while time.time() < deadline:
                last = grok_login_status()
                if last["phase"] in ("ok", "fail"):
                    break
                time.sleep(0.05)
            self.assertEqual(last["phase"], "ok")
            self.assertTrue(last["ok"])
            self.assertEqual(last.get("user_code"), "ABCD-WXYZ")
            self.assertEqual(last.get("verification_uri"), "https://auth.x.ai/activate")
            for banned in ("token", "access_token", "refresh_token", "client_secret", "key"):
                self.assertNotIn(banned, last)

    def test_start_and_poll_report_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            grok = _write_fake_grok(Path(td))
            os.environ["FCC_GROK_BIN"] = str(grok)
            os.environ["FAKE_GROK_SLEEP"] = "0.05"
            os.environ["FAKE_GROK_EXIT"] = "2"
            start_grok_login()
            deadline = time.time() + 5
            last = {}
            while time.time() < deadline:
                last = grok_login_status()
                if last["phase"] in ("ok", "fail"):
                    break
                time.sleep(0.05)
            self.assertEqual(last["phase"], "fail")
            self.assertFalse(last["ok"])
            self.assertTrue(last.get("error"))

    def test_public_payload_redacts_cli_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            grok = _write_fake_grok(Path(td))
            os.environ["FCC_GROK_BIN"] = str(grok)
            os.environ["FAKE_GROK_LEAK"] = "1"
            os.environ["FAKE_GROK_EXIT"] = "1"
            os.environ["FAKE_GROK_SLEEP"] = "0.05"
            start_grok_login()
            deadline = time.time() + 5
            last = {}
            while time.time() < deadline:
                last = grok_login_status()
                if last["phase"] == "fail":
                    break
                time.sleep(0.05)
            blob = str(last)
            self.assertNotIn("super-secret-token-value", blob)
            self.assertNotIn("access_token=super", blob)


if __name__ == "__main__":
    unittest.main()
