"""Tests for Personal Expense Sheet parser (no network required for unit path)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.expenses_sync import (  # noqa: E402
    build_expenses_snapshot,
    parse_money,
    parse_personal_rows,
    rows_from_csv,
)
from treasury.policy import evaluate_treasury  # noqa: E402

PERSONAL_CSV = """Date,From,Item,Daily,Weekly,Bi-Weekly,Monthly,Annually,Budget Allocation
4/1/2026,Coinbase,Rent,$276.16,"$1,933.15",3866.30,"$8,400.00","$8,400.00",51.67%
7/17/2026,Coinbase,Gym,$0.89,$6.21,12.43,$27.00,$324.00,0.17%
7/21/2026,RH Checking,Fleet Insurance,$18.41,$128.88,257.75,$560.00,"$6,720.00",3.44%
,,Total,$534.51,"$3,741.56","$7,483.11","$16,211.68","$53,285.38",100.00%
"""

DISC_CSV = """Item,Date,Daily,Weekly,Bi-Weekly,Monthly,Annually,From,To
ASIC,8/1/2026,$83.33,$583.33,"$1,166.67","$2,500.00","$30,000.00",,
Tesla TPMS,8/1/2026,$3.83,$26.83,$53.67,$115.00,"$1,380.00",,
Total,,$84.93,$594.53,"$1,189.07","$2,548.00","$30,576.00",,
"""


class TestParseMoney(unittest.TestCase):
    def test_money(self):
        self.assertAlmostEqual(parse_money("$1,933.15"), 1933.15)
        self.assertAlmostEqual(parse_money("27.00"), 27.0)


class TestPersonal(unittest.TestCase):
    def test_rows_and_totals(self):
        items, totals = parse_personal_rows(rows_from_csv(PERSONAL_CSV))
        self.assertEqual(len(items), 3)
        self.assertAlmostEqual(totals["monthly"], 16211.68)
        rent = [i for i in items if i["item"] == "Rent"][0]
        self.assertAlmostEqual(rent["monthly"], 8400.0)
        self.assertEqual(rent["from"], "Coinbase")


class TestSnapshot(unittest.TestCase):
    def test_build(self):
        snap = build_expenses_snapshot(
            PERSONAL_CSV, DISC_CSV, sheet_id="abc", source="test"
        )
        self.assertEqual(snap["source"], "test")
        s = snap["summary"]
        self.assertAlmostEqual(s["personal_monthly"], 16211.68)
        self.assertAlmostEqual(s["discretionary_monthly"], 2548.0)
        self.assertAlmostEqual(s["combined_monthly"], 16211.68 + 2548.0)
        self.assertGreater(s["coinbase_funded_monthly"], 8000)
        self.assertIn("Coinbase", snap["tabs"]["Personal"]["by_source_monthly"])


class TestPolicyExpenses(unittest.TestCase):
    def test_expense_fields_in_eval(self):
        snap = {
            "coinbase": {"liquid_usdc": 10, "liquid_btc": 0, "source": "live"},
            "coinbase_manual": {"ltv": 0.3},
            "one_card": {"source": "ynab", "card_balance": 100},
            "expenses": build_expenses_snapshot(
                PERSONAL_CSV, DISC_CSV, sheet_id="x", source="google_sheets"
            ),
            "robinhood": {
                "buying_power": 2000,
                "cash": 1000,
                "equity_value": 5000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["expenses_combined_monthly"], 16211.68 + 2548.0)
        self.assertIn("expense_burn", [a["kind"] for a in ev["actions"]])
        self.assertIn("expenses", ev["data_quality"]["sources"])


if __name__ == "__main__":
    unittest.main()
