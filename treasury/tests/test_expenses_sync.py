"""Tests for Personal Expense Sheet parser (no network required for unit path)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.expenses_sync import (  # noqa: E402
    _upcoming_sorted,
    build_expenses_snapshot,
    parse_money,
    parse_personal_rows,
    parse_sheet_date,
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

CONSUMER_CSV = """Item,Date,Daily,Weekly,Bi-Weekly,Monthly,Annually,From,To
Noise Cancelling Headphones,9/1/2026,$1.00,$7.00,$14.00,$30.00,$360.00,,
Total,,$1.00,$7.00,$14.00,$30.00,$360.00,,
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


class TestDateSort(unittest.TestCase):
    def test_parse_sheet_date(self):
        self.assertEqual(parse_sheet_date("7/17/2026").year, 2026)
        self.assertEqual(parse_sheet_date("7/17/2026").month, 7)
        self.assertEqual(parse_sheet_date("7/17/2026").day, 17)
        self.assertIsNone(parse_sheet_date(""))
        self.assertIsNone(parse_sheet_date(None))

    def test_upcoming_sorted_chronological_not_lexicographic(self):
        items = [
            {"date": "1/9/2027", "item": "Amazon", "from": "Coinbase", "monthly": 12},
            {"date": "4/1/2026", "item": "Rent", "from": "Coinbase", "monthly": 8400},
            {"date": "7/17/2026", "item": "Gym", "from": "Coinbase", "monthly": 27},
            {"date": None, "item": "No date", "from": "Coinbase", "monthly": 1},
        ]
        ranked = _upcoming_sorted(items, n=10)
        self.assertEqual([r["item"] for r in ranked], ["Rent", "Gym", "Amazon", "No date"])


class TestSnapshot(unittest.TestCase):
    def test_build(self):
        snap = build_expenses_snapshot(
            PERSONAL_CSV, DISC_CSV, sheet_id="abc", source="test"
        )
        self.assertEqual(snap["source"], "test")
        s = snap["summary"]
        self.assertAlmostEqual(s["personal_monthly"], 16211.68)
        self.assertAlmostEqual(s["upcoming_expense_monthly"], 16211.68)
        # Discretionary is capital targets, not expense burn
        self.assertAlmostEqual(s["capital_targets_monthly"], 2548.0)
        self.assertAlmostEqual(s["discretionary_monthly"], 2548.0)
        # combined burn = personal only
        self.assertAlmostEqual(s["combined_monthly"], 16211.68)
        self.assertGreater(s["coinbase_funded_monthly"], 8000)
        self.assertIn("Coinbase", snap["tabs"]["Personal"]["by_source_monthly"])
        self.assertEqual(snap["tabs"]["Personal"]["role"], "upcoming_expense_estimates")
        self.assertEqual(
            snap["tabs"]["Productive Discretionary"]["role"], "productive_capital_outlay"
        )
        # Legacy alias still present
        self.assertEqual(snap["tabs"]["Discretionary"]["alias_of"], "Productive Discretionary")
        self.assertAlmostEqual(s["productive_discretionary_monthly"], 2548.0)
        # Chronological order: Rent (4/1) before Gym (7/17) before Fleet (7/21)
        by_date = snap["tabs"]["Personal"]["upcoming_by_date"]
        self.assertEqual(by_date[0]["item"], "Rent")
        self.assertEqual(by_date[1]["item"], "Gym")


    def test_productive_and_consumer(self):
        snap = build_expenses_snapshot(
            PERSONAL_CSV,
            DISC_CSV,
            consumer_csv=CONSUMER_CSV,
            sheet_id="abc",
            source="test",
        )
        s = snap["summary"]
        self.assertAlmostEqual(s["productive_discretionary_monthly"], 2548.0)
        self.assertAlmostEqual(s["consumer_discretionary_monthly"], 30.0)
        # Capital targets / burn aliases
        self.assertAlmostEqual(s["capital_targets_monthly"], 2548.0)
        self.assertAlmostEqual(s["discretionary_monthly"], 2548.0)  # productive alias
        self.assertAlmostEqual(s["combined_monthly"], 16211.68)  # personal only
        self.assertEqual(
            snap["tabs"]["Consumer Discretionary"]["role"], "consumer_wishlist"
        )
        self.assertEqual(snap["tabs"]["Productive Discretionary"]["priority"], 1)
        self.assertEqual(snap["tabs"]["Consumer Discretionary"]["priority"], 2)


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
        # burn uses Personal only
        self.assertAlmostEqual(ev["inputs"]["expenses_combined_monthly"], 16211.68)
        self.assertAlmostEqual(ev["inputs"]["expenses_capital_targets_monthly"], 2548.0)
        self.assertIn("expense_burn", [a["kind"] for a in ev["actions"]])
        self.assertIn("expenses", ev["data_quality"]["sources"])


if __name__ == "__main__":
    unittest.main()
