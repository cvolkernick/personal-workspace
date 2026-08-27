"""Tests for Personal Expense Sheet parser (no network required for unit path)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unittest.mock import patch

from treasury.expenses_sync import (  # noqa: E402
    COLLATERAL_TAB,
    ESSENTIAL_TAB,
    FLEET_TAB,
    _upcoming_sorted,
    build_expenses_snapshot,
    fetch_discretionary_csv,
    parse_discretionary_rows,
    parse_money,
    parse_personal_rows,
    parse_sheet_date,
    rows_from_csv,
)
from treasury.planned_actual import (  # noqa: E402
    FLAG_OFF_BOOK,
    FLAG_PAYMENT_SHAPED,
    build_planned_actual_strip,
    names_join,
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

FLEET_CSV = """Date,From,Item,Daily,Weekly,Bi-Weekly,Monthly,Annually,Budget Allocation
8/21/2026,X Money,Santander (June / July / August),$36.08,$252.59,$505.17,"$1,082.52","$12,990.24",
8/21/2026,X Money,Capital One (June / July / August),$37.39,$261.70,$523.39,"$1,121.55","$13,458.60",
8/23/2026,X Money,GM Financial (June / July / August),$44.06,$308.39,$616.77,"$1,321.66","$15,859.92",
8/1/2026,NFCU (Zelle),Rivian R1S,$45.00,$315.00,$630.00,"$1,350.00","$16,200.00",
7/21/2026,X Money,Fleet Insurance,$21.11,$147.75,$295.49,$633.20,"$7,598.40",
,,Total,$183.64,"$1,285.43","$2,570.82","$5,508.93","$66,107.16",
"""

COLLATERAL_CSV = """Date,From,Item,Daily,Weekly,Bi-Weekly,Monthly,Annually,Budget Allocation
8/1/2026,X Money,Agentic Fund Allocation,$3.61,$25.27,$50.54,$108.33,"$1,299.96",
8/1/2026,X Money,ASIC Fleet OpEx,$14.57,$101.99,$203.98,$437.20,"$5,246.40",
,,Total,$18.18,$127.26,$254.52,$545.53,"$6,546.36",
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
        self.assertEqual(snap["tabs"][ESSENTIAL_TAB]["role"], "upcoming_expense_estimates")
        self.assertIs(snap["tabs"][ESSENTIAL_TAB], snap["tabs"]["Personal"])
        self.assertEqual(snap["tabs"]["Discretionary"]["role"], "excess_capital_targets")
        self.assertNotIn(FLEET_TAB, snap["tabs"])
        self.assertNotIn(COLLATERAL_TAB, snap["tabs"])
        # Chronological order: Rent (4/1) before Gym (7/17) before Fleet (7/21)
        by_date = snap["tabs"]["Personal"]["upcoming_by_date"]
        self.assertEqual(by_date[0]["item"], "Rent")
        self.assertEqual(by_date[1]["item"], "Gym")

    def test_build_includes_fleet_and_collateral_from_sheet_titles(self):
        snap = build_expenses_snapshot(
            PERSONAL_CSV,
            DISC_CSV,
            sheet_id="15ZU7843pTSLSEI0U-taFZ4Qwk3bTQx6cWh2Ex0d7NJQ",
            source="test",
            fleet_csv=FLEET_CSV,
            collateral_csv=COLLATERAL_CSV,
        )
        self.assertEqual(snap["tabs"][FLEET_TAB]["role"], "fleet_ops")
        self.assertEqual(snap["tabs"][COLLATERAL_TAB]["role"], "collateral_investments")
        fleet_names = [i["item"] for i in snap["tabs"][FLEET_TAB]["items"]]
        coll_names = [i["item"] for i in snap["tabs"][COLLATERAL_TAB]["items"]]
        disc_names = [i["item"] for i in snap["tabs"]["Discretionary"]["items"]]
        self.assertIn("Santander (June / July / August)", fleet_names)
        self.assertIn("Rivian R1S", fleet_names)
        self.assertIn("Agentic Fund Allocation", coll_names)
        self.assertIn("ASIC Fleet OpEx", coll_names)
        self.assertIn("ASIC", disc_names)
        self.assertNotIn("ASIC", coll_names)
        # Burn stays Essential-only; Fleet/Collateral do not change combined_monthly.
        self.assertAlmostEqual(snap["summary"]["combined_monthly"], 16211.68)
        self.assertAlmostEqual(snap["summary"]["fleet_monthly"], 5508.93)
        self.assertAlmostEqual(snap["summary"]["collateral_monthly"], 545.53)

    def test_planned_strip_uses_ingested_tabs_and_does_not_alias_asic(self):
        snap = build_expenses_snapshot(
            PERSONAL_CSV,
            DISC_CSV,
            sheet_id="15ZU7843pTSLSEI0U-taFZ4Qwk3bTQx6cWh2Ex0d7NJQ",
            source="test",
            fleet_csv=FLEET_CSV,
            collateral_csv=COLLATERAL_CSV,
        )
        self.assertFalse(names_join("ASIC", "ASIC Fleet OpEx"))
        from treasury.ynab_category_map import validate_category_map

        cmap = validate_category_map(
            {
                "schema_version": 0,
                "budget_id": "37502ae1-2484-4e3d-90a1-8985d775e86b",
                "budget_name": "Chris's Plan",
                "allow_approve": True,
                "allow_categorize": True,
                "forbid": ["move_money", "payment", "transfer"],
                "categories": [
                    {
                        "id": "rent-cat",
                        "name": "Rent",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                    {
                        "id": "sant",
                        "name": "Santander",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                    {
                        "id": "cap",
                        "name": "Capital One",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                    {
                        "id": "gm",
                        "name": "GM Financial",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                    {
                        "id": "riv",
                        "name": "Rivian R1S",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                    {
                        "id": "af",
                        "name": "Agentic Fund Allocation",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                    {
                        "id": "asic-opex",
                        "name": "ASIC Fleet OpEx",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                    {
                        "id": "asic-disc",
                        "name": "ASIC",
                        "group_id": "bills",
                        "group_name": "Bills",
                        "hidden": False,
                        "enabled": True,
                    },
                ],
                "payee_rules": [],
            }
        )
        leftover = [
            {
                "id": "leftover-sant",
                "date": "2026-08-10",
                "payee": "Santander",
                "amount": -1082.52,
                "amount_display": -1082.52,
                "category_name": "Uncategorized",
            }
        ]
        strip = build_planned_actual_strip(
            {
                "expenses": snap,
                "x_money": {"transactions": leftover, "source": "ynab"},
                "one_card": {"transactions": [], "source": "ynab"},
                "rh_checking": {"transactions": [], "source": "ynab"},
            },
            cmap,
            as_of="2026-08-15",
        )
        by_item = {r["item"]: r for r in strip["rows"]}
        self.assertIn("Santander (June / July / August)", by_item)
        self.assertIn("Rivian R1S", by_item)
        self.assertIn("Agentic Fund Allocation", by_item)
        self.assertIn("ASIC Fleet OpEx", by_item)
        self.assertNotIn("ASIC", by_item)
        self.assertEqual(by_item["Santander (June / July / August)"]["flag"], FLAG_PAYMENT_SHAPED)
        self.assertEqual(by_item["Santander (June / July / August)"]["actual"], 0.0)
        self.assertEqual(by_item["Rivian R1S"]["flag"], FLAG_OFF_BOOK)
        self.assertEqual(by_item["Rivian R1S"]["from_venue"], "NFCU (Zelle)")
        self.assertEqual(by_item["ASIC Fleet OpEx"]["tab"], COLLATERAL_TAB)
        self.assertEqual(by_item["ASIC Fleet OpEx"]["category_id"], "asic-opex")
        self.assertEqual({r["tab"] for r in strip["rows"]}, {ESSENTIAL_TAB, FLEET_TAB, COLLATERAL_TAB})

    def test_discretionary_fetch_rejects_essential_alias(self):
        with patch("treasury.expenses_sync.try_fetch_sheet_csv") as mocked:
            mocked.side_effect = lambda sid, name, timeout=30.0: {
                "Discretionary": PERSONAL_CSV,
                "Productive Discretionary": DISC_CSV,
            }.get(name)
            text = fetch_discretionary_csv("sid", PERSONAL_CSV)
        items, _ = parse_discretionary_rows(rows_from_csv(text))
        names = [i["item"] for i in items]
        self.assertIn("ASIC", names)
        self.assertNotIn("Rent", names)


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
