"""Unit tests for shipped treasury.policy (fixtures drive real functions)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.policy import (  # noqa: E402
    DEFAULT_POLICY,
    classify_liquid_usdc,
    dca_governor,
    evaluate_treasury,
)


class TestClassifyLiquid(unittest.TestCase):
    def test_shortfall_when_under_floors(self):
        r = classify_liquid_usdc(
            100,
            card_float=500,
            loan_buffer=1000,
            bridge_dry_powder=200,
        )
        self.assertGreater(r["shortfall"], 0)
        self.assertEqual(r["status"], "red")
        self.assertEqual(r["filled"]["card_float"], 100)
        self.assertEqual(r["excess"], 0)

    def test_excess_after_floors(self):
        r = classify_liquid_usdc(
            2000,
            card_float=500,
            loan_buffer=1000,
            bridge_dry_powder=200,
        )
        self.assertEqual(r["shortfall"], 0)
        self.assertAlmostEqual(r["excess"], 300)
        self.assertEqual(r["status"], "green")


class TestDcaGovernor(unittest.TestCase):
    def test_pause_low_bp(self):
        r = dca_governor(50, bp_floor=500)
        self.assertFalse(r["allow_dca"])
        self.assertEqual(r["throttle"], "pause")
        self.assertIn("below floor", r["reason"])

    def test_pause_margin_heat(self):
        r = dca_governor(5000, bp_floor=500, margin_use=0.8, margin_use_max=0.4)
        self.assertFalse(r["allow_dca"])
        self.assertIn("margin", r["reason"])

    def test_allow_healthy(self):
        r = dca_governor(2000, bp_floor=500, margin_use=0.1)
        self.assertTrue(r["allow_dca"])
        self.assertEqual(r["throttle"], "normal")


class TestEvaluateTreasury(unittest.TestCase):
    def test_high_ltv_first_priority(self):
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0.1},
            "coinbase_manual": {
                "loan_principal_usdc": 60000,
                "collateral_btc_usd": 100000,
                "ltv": 0.60,
                "vault_usdc": 10000,
                "card_balance": 0,
            },
            "robinhood": {"buying_power": 3000, "cash": 1000, "equity_value": 50000},
        }
        ev = evaluate_treasury(snap, policy=DEFAULT_POLICY)
        self.assertEqual(ev["stress"]["coinbase_ltv"], "red")
        self.assertTrue(ev["actions"])
        self.assertEqual(ev["actions"][0]["kind"], "ltv_protect")
        self.assertEqual(ev["actions"][0]["priority"], 1)

    def test_under_card_float_and_low_bp(self):
        snap = {
            "coinbase": {"liquid_usdc": 50, "liquid_btc": 0},
            "coinbase_manual": {
                "ltv": 0.30,
                "card_balance": 200,
                "card_available_credit": 50,
                "vault_usdc": 5000,
            },
            "robinhood": {"buying_power": 10, "cash": 10, "equity_value": 1000},
        }
        ev = evaluate_treasury(snap)
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertIn("card_float", kinds)
        self.assertIn("dca_pause", kinds)
        self.assertIn("vault_pull", kinds)
        self.assertEqual(ev["buckets"]["status"], "red")
        dca = [a for a in ev["actions"] if a["kind"] == "dca_pause"][0]
        self.assertTrue(dca["api_reachable"])
        self.assertEqual(dca["actor"], "agent")

    def test_excess_allocation_when_green(self):
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0},
            "coinbase_manual": {"ltv": 0.30, "card_balance": 0, "vault_usdc": 0},
            "robinhood": {
                "buying_power": 2000,
                "cash": 1500,
                "equity_value": 20000,
                "margin_use": 0.1,
            },
        }
        ev = evaluate_treasury(snap)
        self.assertEqual(ev["stress"]["overall"], "green")
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertIn("excess_allocate", kinds)
        self.assertIn("dca_ok", kinds)
        self.assertTrue(ev["dca"]["allow_dca"])

    def test_bridge_recommend_cb_to_rh(self):
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0},
            "coinbase_manual": {"ltv": 0.25, "card_balance": 0},
            "robinhood": {"buying_power": 100, "cash": 50, "equity_value": 10000},
        }
        ev = evaluate_treasury(snap)
        bridges = [a for a in ev["actions"] if a["kind"] == "bridge_cb_to_rh"]
        self.assertTrue(bridges)
        self.assertFalse(bridges[0]["api_reachable"])
        self.assertIn("Recommend", bridges[0]["title"])

    def test_derive_ltv_from_principal_collateral(self):
        snap = {
            "coinbase": {"liquid_usdc": 100, "liquid_btc": 0},
            "coinbase_manual": {
                "loan_principal_usdc": 40_000,
                "collateral_btc_usd": 100_000,
            },
            "robinhood": {"buying_power": 1000, "cash": 1000, "equity_value": 5000},
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["ltv"], 0.4)
        self.assertEqual(ev["stress"]["coinbase_ltv"], "green")


if __name__ == "__main__":
    unittest.main()
