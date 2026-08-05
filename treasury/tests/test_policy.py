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
    cashflow_allocation_guidance,
    classify_liquid_usdc,
    dca_governor,
    evaluate_treasury,
    expense_due_window,
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


class TestOneCardAvailableCredit(unittest.TestCase):
    def test_available_credit_from_deposit_minus_balance(self):
        snap = {
            "coinbase": {"liquid_usdc": 0, "source": "live"},
            "coinbase_manual": {
                "vault_usdc": 200,
                "one_card_security_deposit_usdc": 500,
            },
            "one_card": {
                "source": "ynab",
                "card_balance": 418.55,
                "balance_owed": 418.55,
            },
            "rh_checking": {"source": "ynab", "cash": 10},
            "robinhood": {
                "buying_power": 1000,
                "cash": 10,
                "equity_value": 5000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["card_available_credit"], 81.45, places=2)
        self.assertEqual(ev["inputs"]["card_available_credit_source"], "deposit_minus_balance")
        self.assertAlmostEqual(ev["inputs"]["card_security_deposit_usdc"], 500.0)

    def test_ynab_card_balance_beats_stale_manual(self):
        """Stale config/UI card_balance must not pin owed when YNAB is live."""
        snap = {
            "coinbase": {"liquid_usdc": 0, "source": "live"},
            "coinbase_manual": {
                "vault_usdc": 200,
                "one_card_security_deposit_usdc": 500,
                "card_balance": 499.23,  # stale override
            },
            "one_card": {
                "source": "ynab",
                "card_balance": 440.18,
                "balance_owed": 440.18,
            },
            "rh_checking": {"source": "ynab", "cash": 10},
            "robinhood": {
                "buying_power": 1000,
                "cash": 10,
                "equity_value": 5000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["card_balance"], 440.18, places=2)
        self.assertEqual(ev["inputs"]["card_source"], "ynab")
        self.assertAlmostEqual(ev["inputs"]["card_available_credit"], 59.82, places=2)

    def test_manual_card_balance_used_when_ynab_empty(self):
        snap = {
            "coinbase": {"liquid_usdc": 100, "source": "live"},
            "coinbase_manual": {
                "vault_usdc": 200,
                "one_card_security_deposit_usdc": 500,
                "card_balance": 100.0,
            },
            "one_card": {"source": "empty"},
            "rh_checking": {"source": "ynab", "cash": 10},
            "robinhood": {
                "buying_power": 1000,
                "cash": 10,
                "equity_value": 5000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["card_balance"], 100.0, places=2)
        self.assertEqual(ev["inputs"]["card_source"], "manual")


class TestVaultWorkingUsdc(unittest.TestCase):
    def test_zero_spot_vault_covers_buffers(self):
        """Idle spot ~0 is OK when High Yield vault holds working float."""
        snap = {
            "coinbase": {"liquid_usdc": 0.0, "liquid_btc": 0.1, "source": "live"},
            "coinbase_manual": {
                "ltv": 0.35,
                "vault_usdc": 5000,
                "card_balance": 400,
                "card_available_credit": 1000,
                "loan_principal_usdc": 10000,
                "collateral_btc_usd": 30000,
            },
            "one_card": {"source": "ynab", "card_balance": 400},
            "rh_checking": {"source": "ynab", "cash": 500},
            "robinhood": {
                "buying_power": 2000,
                "cash": 100,
                "equity_value": 20000,
                "margin_use": 0.1,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["working_usdc"], 5000.0)
        self.assertEqual(ev["stress"]["coinbase_liquid"], "green")
        self.assertEqual(ev["buckets"]["spot_only_status"], "red")
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertNotIn("card_float", kinds)

    def test_zero_spot_unknown_vault_is_yellow_not_false_red(self):
        snap = {
            "coinbase": {"liquid_usdc": 0.0, "liquid_btc": 0, "source": "live"},
            "coinbase_manual": {"ltv": 0.3},
            "one_card": {"source": "ynab", "card_balance": 100},
            "robinhood": {
                "buying_power": 2000,
                "cash": 500,
                "equity_value": 10000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertEqual(ev["stress"]["coinbase_liquid"], "yellow")
        self.assertIn("vault_unknown", [a["kind"] for a in ev["actions"]])


class TestEvaluateTreasury(unittest.TestCase):
    def test_high_ltv_first_priority(self):
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0.1, "source": "live"},
            "coinbase_manual": {
                "loan_principal_usdc": 60000,
                "collateral_btc_usd": 100000,
                "ltv": 0.60,
                "vault_usdc": 10000,
                "card_balance": 0,
                "card_available_credit": 5000,
            },
            "robinhood": {
                "buying_power": 3000,
                "cash": 1000,
                "equity_value": 50000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap, policy=DEFAULT_POLICY)
        self.assertEqual(ev["stress"]["coinbase_ltv"], "red")
        self.assertTrue(ev["actions"])
        protect = [a for a in ev["actions"] if a["kind"] == "ltv_protect"]
        self.assertTrue(protect)
        self.assertEqual(protect[0]["priority"], 1)
        # LTV protect is first risk action after any fill_manual (none when complete)
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertEqual(kinds[0], "ltv_protect")

    def test_under_card_float_and_low_bp(self):
        # Spot low AND vault too small to cover buffer floors
        snap = {
            "coinbase": {"liquid_usdc": 50, "liquid_btc": 0, "source": "live"},
            "coinbase_manual": {
                "ltv": 0.30,
                "card_balance": 200,
                "card_available_credit": 50,
                "vault_usdc": 100,
            },
            "robinhood": {
                "buying_power": 10,
                "cash": 10,
                "equity_value": 1000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        kinds = [a["kind"] for a in ev["actions"]]
        # SNR: one cash_stack hero (not card_float + vault_pull + card_paydown)
        self.assertIn("cash_stack", kinds)
        self.assertNotIn("card_float", kinds)
        self.assertNotIn("vault_pull", kinds)
        # MO: rh_bp_floor=0 → dust BP is deployable, not DCA pause
        self.assertNotIn("dca_pause", kinds)
        self.assertTrue(ev["dca"]["allow_dca"])
        stack = [a for a in ev["actions"] if a["kind"] == "cash_stack"][0]
        self.assertEqual(stack["actor"], "human")
        self.assertIn("meta", stack)
        self.assertGreater(stack["meta"]["shortfall"], 0)
        self.assertEqual(ev["buckets"]["status"], "red")
        self.assertAlmostEqual(ev["inputs"]["working_usdc"], 150.0)
        self.assertIn("data_quality", ev)
        self.assertIn("agent_brief", ev)
        self.assertTrue(ev["agent_brief"])

    def test_unknown_card_not_green(self):
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0, "source": "live"},
            "coinbase_manual": {"ltv": 0.3},
            "robinhood": {
                "buying_power": 2000,
                "cash": 1500,
                "equity_value": 20000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertEqual(ev["stress"]["coinbase_card"], "yellow")
        self.assertIn("card_balance", ev["data_quality"]["missing_manual_fields"])
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertIn("fill_manual", kinds)


    def test_excess_allocation_when_green(self):
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0, "source": "live"},
            "coinbase_manual": {
                "ltv": 0.30,
                "loan_principal_usdc": 30000,
                "collateral_btc_usd": 100000,
                "card_balance": 0,
                "card_available_credit": 4000,
                "vault_usdc": 0,
            },
            "robinhood": {
                "buying_power": 2000,
                "cash": 1500,
                "equity_value": 20000,
                "margin_use": 0.1,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertEqual(ev["stress"]["coinbase_ltv"], "green")
        self.assertEqual(ev["stress"]["robinhood"], "green")
        kinds = [a["kind"] for a in ev["actions"]]
        self.assertIn("excess_allocate", kinds)
        self.assertIn("dca_ok", kinds)
        self.assertTrue(ev["dca"]["allow_dca"])

    def test_bridge_recommend_cb_to_rh(self):
        # Explicit BP floor still enables CB→RH bridge recommend
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0},
            "coinbase_manual": {"ltv": 0.25, "card_balance": 0},
            "robinhood": {"buying_power": 100, "cash": 50, "equity_value": 10000},
        }
        ev = evaluate_treasury(snap, policy={"rh_bp_floor": 500})
        bridges = [a for a in ev["actions"] if a["kind"] == "bridge_cb_to_rh"]
        self.assertTrue(bridges)
        self.assertFalse(bridges[0]["api_reachable"])
        self.assertIn("Recommend", bridges[0]["title"])

    def test_no_bp_floor_skips_cb_to_rh_bridge(self):
        snap = {
            "coinbase": {"liquid_usdc": 5000, "liquid_btc": 0},
            "coinbase_manual": {"ltv": 0.25, "card_balance": 0, "vault_usdc": 0},
            "robinhood": {"buying_power": 0.09, "cash": 0.09, "equity_value": 10000},
        }
        ev = evaluate_treasury(snap)  # default floor 0
        bridges = [a for a in ev["actions"] if a["kind"] == "bridge_cb_to_rh"]
        self.assertEqual(bridges, [])
        self.assertTrue(ev["dca"]["allow_dca"])

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



class TestCashflowAllocationGuidance(unittest.TestCase):
    def test_waterfall_expenses_first_when_critical_due(self):
        snap = {
            "coinbase": {"liquid_usdc": 100, "source": "live"},
            "coinbase_manual": {
                "ltv": 0.30,
                "vault_usdc": 2000,
                "card_balance": 200,
                "card_available_credit": 300,
                "one_card_security_deposit_usdc": 500,
            },
            "one_card": {"source": "ynab", "card_balance": 200, "balance_owed": 200},
            "rh_checking": {"source": "ynab", "cash": 50},
            "x_money": {"source": "ynab", "cash": 0},
            "robinhood": {
                "buying_power": 5000,
                "cash": 100,
                "equity_value": 20000,
                "margin_use": 0.1,
                "source": "live",
            },
            "expenses": {
                "source": "google_sheets",
                "summary": {
                    "capital_targets_monthly": 500,
                    "discretionary_monthly": 500,
                },
                "tabs": {
                    "Personal": {
                        "items": [
                            {
                                "item": "Overdue bill",
                                "date": "1/1/2026",
                                "monthly": 400,
                                "from": "Coinbase",
                            }
                        ]
                    },
                    "Discretionary": {
                        "items": [
                            {"item": "ASIC", "monthly": 500},
                        ]
                    },
                },
            },
        }
        ev = evaluate_treasury(snap)
        ca = ev["cashflow_allocation"]
        self.assertIn("steps", ca)
        self.assertEqual(ca["active_step_id"], "expenses")
        self.assertEqual(ca["steps"][0]["id"], "expenses")
        self.assertIn(ca["steps"][0]["status"], ("gap", "partial"))
        # Capex is margin-sourced, not free-dollar residual
        capex = next(s for s in ca["steps"] if s["id"] == "capex_margin")
        self.assertEqual(capex["fund_from"], "collateralized_margin")
        self.assertEqual(capex["status"], "available")
        self.assertGreater(capex["need"], 0)

    def test_excess_to_collateral_when_stack_clear(self):
        buckets = classify_liquid_usdc(
            3000,
            card_float=500,
            loan_buffer=1000,
            bridge_dry_powder=200,
        )
        exp = {
            "critical_total": 0,
            "overdue_count": 0,
            "due_soon_count": 0,
            "overdue_total": 0,
            "due_soon_total": 0,
            "as_of_date": "2026-08-05",
            "due_within_days": 7,
        }
        ca = cashflow_allocation_guidance(
            expense_window=exp,
            card_balance=0,
            buckets=buckets,
            working_usdc=3000,
            bank_cash=0,
            rh_buying_power=2000,
            dca={"allow_dca": True, "reason": "ok"},
            discretionary={"monthly": 100, "items": [{"item": "ASIC", "monthly": 100}]},
            free_dollar_red=False,
        )
        self.assertEqual(ca["active_step_id"], "collateral")
        coll = next(s for s in ca["steps"] if s["id"] == "collateral")
        self.assertEqual(coll["status"], "ready")
        self.assertGreater(coll["meta"]["to_btc_collateral"], 0)
        self.assertGreater(coll["meta"]["to_rh_securities"], 0)

    def test_capex_blocked_on_margin_heat(self):
        buckets = classify_liquid_usdc(
            2000,
            card_float=500,
            loan_buffer=1000,
            bridge_dry_powder=200,
        )
        ca = cashflow_allocation_guidance(
            expense_window={
                "critical_total": 0,
                "overdue_count": 0,
                "due_soon_count": 0,
                "as_of_date": "2026-08-05",
                "due_within_days": 7,
            },
            card_balance=0,
            buckets=buckets,
            working_usdc=2000,
            bank_cash=0,
            rh_buying_power=8000,
            dca={"allow_dca": False, "reason": "margin use 50% exceeds max 40%"},
            discretionary={"monthly": 2500, "items": []},
            free_dollar_red=False,
        )
        capex = next(s for s in ca["steps"] if s["id"] == "capex_margin")
        self.assertEqual(capex["status"], "blocked_margin")

    def test_expense_due_window_counts_overdue(self):
        snap = {
            "expenses": {
                "source": "google_sheets",
                "tabs": {
                    "Personal": {
                        "items": [
                            {"item": "A", "date": "1/1/2020", "monthly": 100},
                            {"item": "B", "date": "1/1/2099", "monthly": 999},
                        ]
                    }
                },
            }
        }
        w = expense_due_window(snap)
        self.assertEqual(w["overdue_count"], 1)
        self.assertAlmostEqual(w["overdue_total"], 100)
        self.assertLess(w["critical_total"], 999)


if __name__ == "__main__":
    unittest.main()
