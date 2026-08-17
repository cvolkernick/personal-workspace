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
    funded_unique_fleet_items,
    parse_money,
    parse_personal_rows,
    parse_sheet_date,
    rows_from_csv,
)
from treasury.policy import evaluate_treasury  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FLEET_2026_08_17_CSV = (FIXTURE_DIR / "fleet_tab_2026_08_17.csv").read_text(
    encoding="utf-8"
)

PERSONAL_CSV = """Date,From,Item,Daily,Weekly,Bi-Weekly,Monthly,Annually,Budget Allocation
4/1/2026,Coinbase,Rent,$276.16,"$1,933.15",3866.30,"$8,400.00","$8,400.00",51.67%
7/17/2026,Coinbase,Gym,$0.89,$6.21,12.43,$27.00,$324.00,0.17%
,,Total,$277.05,"$1,939.36","$3,878.73","$8,427.00","$8,724.00",100.00%
"""

FLEET_CSV = """Date,From,Item,Daily,Weekly,Bi-Weekly,Monthly,Annually,Budget Allocation
5/21/2026,X Money,Santander,$36.08,$252.59,$505.18,"$1,082.52","$1,082.52",24.09%
8/21/2026,X Money,Fleet Insurance,$18.48,$129.34,$258.67,$562.00,"$6,744.00",12.34%
,,Total,$54.56,$381.93,$763.85,"$1,644.52","$7,826.52",100.00%
"""

COLLATERAL_CSV = """Date,From,Item,Daily,Weekly,Bi-Weekly,Monthly,Annually,Budget Allocation
9/1/2026,X Money,ASIC Fleet OpEx,$14.41,$100.89,$201.78,$437.20,"$5,246.40",81.38%
9/1/2026,X Money,Agentic Fund Allocation,$3.30,$23.08,$46.15,$100.00,"$1,200.00",18.62%
,,Total,$17.71,$123.97,$247.94,$537.20,"$6,446.40",100.00%
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
        self.assertEqual(len(items), 2)
        self.assertAlmostEqual(totals["monthly"], 8427.0)
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
        self.assertAlmostEqual(s["personal_monthly"], 8427.0)
        self.assertAlmostEqual(s["upcoming_expense_monthly"], 8427.0)
        # Discretionary is capital targets, not expense burn
        self.assertAlmostEqual(s["capital_targets_monthly"], 2548.0)
        self.assertAlmostEqual(s["discretionary_monthly"], 2548.0)
        # combined burn = personal only when no fleet tab
        self.assertAlmostEqual(s["combined_monthly"], 8427.0)
        self.assertGreater(s["coinbase_funded_monthly"], 8000)
        self.assertIn("Coinbase", snap["tabs"]["Essential"]["by_source_monthly"])
        self.assertEqual(snap["tabs"]["Essential"]["role"], "upcoming_expense_estimates")
        # Legacy Personal alias still present
        self.assertEqual(snap["tabs"]["Personal"]["alias_of"], "Essential")
        self.assertEqual(
            snap["tabs"]["Productive Discretionary"]["role"], "productive_capital_outlay"
        )
        # Legacy Discretionary alias still present
        self.assertEqual(snap["tabs"]["Discretionary"]["alias_of"], "Productive Discretionary")
        self.assertAlmostEqual(s["productive_discretionary_monthly"], 2548.0)
        self.assertAlmostEqual(s["essential_monthly"], 8427.0)
        # Chronological order: Rent (4/1) before Gym (7/17)
        by_date = snap["tabs"]["Essential"]["upcoming_by_date"]
        self.assertEqual(by_date[0]["item"], "Rent")
        self.assertEqual(by_date[1]["item"], "Gym")

    def test_fleet_and_collateral(self):
        snap = build_expenses_snapshot(
            PERSONAL_CSV,
            DISC_CSV,
            consumer_csv=CONSUMER_CSV,
            fleet_csv=FLEET_CSV,
            collateral_csv=COLLATERAL_CSV,
            sheet_id="abc",
            source="test",
        )
        s = snap["summary"]
        self.assertAlmostEqual(s["personal_monthly"], 8427.0)
        self.assertAlmostEqual(s["fleet_monthly"], 1644.52)
        # Older FLEET_CSV: both lines have From and no Essential name overlap.
        self.assertAlmostEqual(s["upcoming_expense_monthly"], 10071.52)
        self.assertAlmostEqual(s["combined_monthly"], 10071.52)
        self.assertAlmostEqual(s["collateral_investments_monthly"], 537.20)
        self.assertAlmostEqual(s["productive_discretionary_monthly"], 2548.0)
        self.assertAlmostEqual(s["consumer_discretionary_monthly"], 30.0)
        self.assertEqual(snap["tabs"]["Fleet"]["role"], "fleet_ops")
        self.assertIn("X Money", snap["tabs"]["Fleet"]["by_source_monthly"])
        self.assertEqual(snap["tabs"]["Collateral"]["role"], "collateral_investments")
        self.assertAlmostEqual(s["by_source_monthly"]["X Money"], 1644.52)
        self.assertAlmostEqual(s["x_money_funded_monthly"], 1644.52)
        # Fleet items tagged
        self.assertEqual(snap["tabs"]["Fleet"]["items"][0]["tab"], "Fleet")

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
        self.assertAlmostEqual(s["combined_monthly"], 8427.0)  # personal only
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
                PERSONAL_CSV,
                DISC_CSV,
                fleet_csv=FLEET_CSV,
                collateral_csv=COLLATERAL_CSV,
                sheet_id="x",
                source="google_sheets",
            ),
            "robinhood": {
                "buying_power": 2000,
                "cash": 1000,
                "equity_value": 5000,
                "source": "live",
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["expenses_combined_monthly"], 10071.52)
        self.assertAlmostEqual(ev["inputs"]["expenses_fleet_monthly"], 1644.52)
        self.assertAlmostEqual(
            ev["inputs"]["expenses_collateral_investments_monthly"], 537.20
        )
        self.assertAlmostEqual(ev["inputs"]["expenses_capital_targets_monthly"], 2548.0)
        self.assertIn("expense_burn", [a["kind"] for a in ev["actions"]])
        self.assertIn("expenses", ev["data_quality"]["sources"])


class TestFleetTab20260817(unittest.TestCase):
    """Pinned live Fleet tab (gviz 2026-08-17) + overlap-once / funded-unique burn."""

    EXPECTED_ITEMS = {
        "Santander (May / June / July)": 1082.52,
        "Capital One (June / July / August)": 1121.55,
        "GM Financial (June / July / August)": 1321.66,
        "Sud Stop Car Wash": 26.58,
        "Fleet Insurance": 633.20,
        "Rivian R1S": 1350.00,
        "2022 Corolla DIMO": 9.00,
        "2024 Corolla DIMO": 9.00,
        "Premium Connectivity": 8.25,
    }

    def test_header_and_parse(self):
        header = FLEET_2026_08_17_CSV.splitlines()[0]
        self.assertTrue(header.startswith("Date,From,Item,"))
        self.assertTrue(header.endswith("Allocation"))
        items, totals = parse_personal_rows(rows_from_csv(FLEET_2026_08_17_CSV))
        by_name = {i["item"]: i for i in items}
        self.assertEqual(set(by_name), set(self.EXPECTED_ITEMS))
        for name, monthly in self.EXPECTED_ITEMS.items():
            self.assertAlmostEqual(by_name[name]["monthly"], monthly, places=2)
        self.assertIsNone(by_name["Rivian R1S"]["from"])
        self.assertEqual(by_name["2022 Corolla DIMO"]["from"], "Coinbase")
        self.assertEqual(by_name["2024 Corolla DIMO"]["from"], "Coinbase")
        self.assertAlmostEqual(totals["monthly"], 5561.76)

    def test_snapshot_role_and_no_double_count(self):
        personal_only = build_expenses_snapshot(
            PERSONAL_CSV, DISC_CSV, sheet_id="abc", source="test"
        )
        with_fleet = build_expenses_snapshot(
            PERSONAL_CSV,
            DISC_CSV,
            fleet_csv=FLEET_2026_08_17_CSV,
            sheet_id="abc",
            source="test",
        )
        fleet = with_fleet["tabs"]["Fleet"]
        self.assertEqual(fleet["role"], "fleet_ops")
        self.assertEqual(fleet["item_count"], 9)
        self.assertAlmostEqual(fleet["totals"]["monthly"], 5561.76)
        self.assertAlmostEqual(fleet["by_source_monthly"]["X Money"], 4193.76)
        self.assertAlmostEqual(fleet["by_source_monthly"]["Coinbase"], 18.0)
        self.assertAlmostEqual(fleet["by_source_monthly"]["Unspecified"], 1350.0)
        self.assertAlmostEqual(with_fleet["summary"]["fleet_monthly"], 5561.76)
        # Funded unique Fleet ($4,211.76) enters burn; Rivian $1,350 empty-From stays out.
        self.assertAlmostEqual(
            with_fleet["summary"]["combined_monthly"],
            personal_only["summary"]["combined_monthly"] + 4211.76,
        )
        self.assertAlmostEqual(with_fleet["summary"]["combined_monthly"], 12638.76)
        self.assertAlmostEqual(
            with_fleet["summary"]["upcoming_expense_monthly"],
            personal_only["summary"]["upcoming_expense_monthly"] + 4211.76,
        )
        self.assertAlmostEqual(with_fleet["summary"]["x_money_funded_monthly"], 4193.76)
        self.assertAlmostEqual(with_fleet["summary"]["coinbase_funded_monthly"], 8445.0)
        self.assertNotIn("Unspecified", with_fleet["summary"]["by_source_monthly"])
        rivian = next(i for i in fleet["items"] if i["item"] == "Rivian R1S")
        self.assertIsNone(rivian["from"])
        self.assertNotAlmostEqual(
            with_fleet["summary"]["combined_monthly"],
            personal_only["summary"]["combined_monthly"] + 5561.76,
        )

    def test_name_overlap_counted_once(self):
        overlap_personal = """Date,From,Item,Daily,Weekly,Bi-Weekly,Monthly,Annually,Budget Allocation
4/1/2026,Coinbase,Rent,$276.16,"$1,933.15",3866.30,"$8,400.00","$8,400.00",
8/21/2026,X Money,Fleet Insurance,$18.48,$129.34,$258.67,$562.00,"$6,744.00",
,,Total,$294.64,"$2,062.49","$4,125.00","$8,962.00","$15,144.00",
"""
        snap = build_expenses_snapshot(
            overlap_personal,
            DISC_CSV,
            fleet_csv=FLEET_CSV,
            sheet_id="abc",
            source="test",
        )
        s = snap["summary"]
        # Personal $8,962 + Fleet Santander $1,082.52; Fleet Insurance already on Essential.
        self.assertAlmostEqual(s["personal_monthly"], 8962.0)
        self.assertAlmostEqual(s["fleet_monthly"], 1644.52)
        self.assertAlmostEqual(s["combined_monthly"], 10044.52)
        self.assertAlmostEqual(s["upcoming_expense_monthly"], 10044.52)
        self.assertAlmostEqual(s["by_source_monthly"]["X Money"], 1644.52)


class TestFundedUniqueFleetItems(unittest.TestCase):
    def test_skips_empty_from_and_name_overlap(self):
        essential = [
            {"item": "Fleet Insurance", "from": "X Money", "monthly": 633.20},
        ]
        fleet = [
            {"item": "Fleet Insurance", "from": "X Money", "monthly": 633.20},
            {"item": "Santander", "from": "X Money", "monthly": 1082.52},
            {"item": "Rivian R1S", "from": None, "monthly": 1350.0},
            {"item": "  santander  ", "from": "X Money", "monthly": 1.0},
        ]
        out = funded_unique_fleet_items(essential, fleet)
        self.assertEqual([i["item"] for i in out], ["Santander"])


if __name__ == "__main__":
    unittest.main()
