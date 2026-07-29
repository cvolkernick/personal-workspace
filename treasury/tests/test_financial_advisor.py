"""Unit tests for FCC financial advisor context (no live xAI calls)."""

from __future__ import annotations

import unittest

from treasury.financial_advisor import (
    ADVISOR_SUGGESTIONS,
    AdvisorError,
    ask_financial_advisor,
    build_treasury_context,
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
            },
            "evaluation": {
                "stress": {"overall": "yellow"},
                "inputs": {"working_usdc": 100.0, "liquid_usdc": 0.0},
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


if __name__ == "__main__":
    unittest.main()
