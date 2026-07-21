"""Tests for agentic fund manager policy + sleeve weights."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.fund_manager import (  # noqa: E402
    analyze_agentic_book,
    append_decision,
    load_decision_log,
    load_fund_policy,
    rules_based_review,
    sleeve_for_symbol,
)


class TestFundPolicy(unittest.TestCase):
    def test_policy_v1_autopilot_agentic_only(self):
        p = load_fund_policy()
        self.assertEqual(p["account"]["scope"], "agentic_only")
        self.assertFalse(p["approval"]["require_user_confirm"])
        self.assertIsNone(p["limits"]["max_single_order_notional_usd"])
        self.assertAlmostEqual(p["targets"]["btc_digital_credit_pct"], 0.4)
        self.assertAlmostEqual(p["targets"]["stocks_growth_pct"], 0.6)
        self.assertIn("BITA", p["sleeves"]["btc_digital_credit"]["symbols"])
        self.assertNotIn("BITA", p["sleeves"]["stocks_growth"]["symbols"])
        self.assertIn("TSLA", p["sleeves"]["stocks_growth"]["symbols"])
        self.assertEqual(p["cadence"]["reviews_per_day"], 1)
        self.assertIsNone(p["cadence"].get("max_trades_per_day"))
        self.assertIsNone(p["cadence"].get("max_trades_per_review"))
        self.assertFalse(p["approval"].get("require_owner_feedback", False))
        self.assertEqual(p["rationale"].get("owner_intervention"), "optional")
        self.assertTrue(p["automation"].get("unattended"))
        self.assertTrue(p["team"]["enabled"])
        self.assertTrue(p["rationale"]["required_on_every_decision"])
        self.assertTrue(p["team"]["roles"]["executor"]["writes_orders"])
        self.assertFalse(p["team"]["roles"]["critic"]["writes_orders"])

    def test_sleeve_tags(self):
        p = load_fund_policy()
        self.assertEqual(sleeve_for_symbol("BITA", p), "btc_digital_credit")
        self.assertEqual(sleeve_for_symbol("mstr", p), "btc_digital_credit")
        self.assertEqual(sleeve_for_symbol("TSLA", p), "stocks_growth")
        self.assertEqual(sleeve_for_symbol("XYZ", p), "other")


class TestDecisionLog(unittest.TestCase):
    def test_append_and_load(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            append_decision(
                {
                    "kind": "hold",
                    "summary": "test hold",
                    "rationale": {"why_now": "in band"},
                    "team_votes": {"risk": {"vote": "ok", "note": "fine"}},
                    "actions": [],
                },
                path=p,
                also_journal=False,
            )
            rows = load_decision_log(path=p, limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "hold")


class TestRulesReview(unittest.TestCase):
    def test_hold_when_in_band(self):
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 0.17,
                "buying_power": 0.17,
                "total_value": 100,
                "positions": [
                    {"symbol": "MSTR", "quantity": 1, "average_buy_price": 40},
                    {"symbol": "TSLA", "quantity": 1, "average_buy_price": 60},
                ],
            }
        }
        fm = rules_based_review(rh_snapshot=rh, log=False)
        rr = fm["rules_review"]
        self.assertFalse(rr["need_llm"])
        self.assertEqual(rr["outcome"], "hold")

    def test_need_llm_when_cash_idle(self):
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 50,
                "buying_power": 50,
                "total_value": 50,
                "positions": [],
            }
        }
        fm = rules_based_review(rh_snapshot=rh, log=False)
        self.assertTrue(fm["rules_review"]["need_llm"])


class TestAnalyze(unittest.TestCase):
    def test_all_cash_hints_deploy(self):
        p = load_fund_policy()
        rh = {
            "agentic": {
                "account_number": "674601752",
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 8.37,
                "buying_power": 8.37,
                "total_value": 8.37,
                "positions": [],
            }
        }
        a = analyze_agentic_book(rh, p)
        self.assertTrue(a["ok"])
        self.assertAlmostEqual(a["nav_usd"], 8.37)
        self.assertTrue(a["fair_game"])
        self.assertFalse(a["approval"]["require_user_confirm"])
        self.assertIsNone(a["approval"]["max_single_order_notional_usd"])
        self.assertTrue(any("cash" in h.lower() or "Deploy" in h for h in a["manager_hints"]))

    def test_deployed_weights(self):
        p = load_fund_policy()
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 0,
                "buying_power": 0,
                "total_value": 100,
                "positions": [
                    {"symbol": "MSTR", "quantity": 1, "average_buy_price": 40},
                    {"symbol": "TSLA", "quantity": 1, "average_buy_price": 60},
                ],
            }
        }
        a = analyze_agentic_book(rh, p)
        self.assertTrue(a["ok"])
        self.assertAlmostEqual(a["weights_of_deployed"]["btc_digital_credit"], 0.4, places=2)
        self.assertAlmostEqual(a["weights_of_deployed"]["stocks_growth"], 0.6, places=2)


if __name__ == "__main__":
    unittest.main()
