"""Unit tests for financial coach ranking/allocation (no network)."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.financial_coach import (  # noqa: E402
    allocate,
    build_coach_plan,
    due_urgency_class,
    extract_venues,
    load_snapshots,
    main,
    normalize_venue,
    rank_obligations,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "coach"
TODAY = date(2026, 7, 27)


class TestVenueNormalize(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_venue("Coinbase"), "coinbase")
        self.assertEqual(normalize_venue("X Money"), "x_money")
        self.assertEqual(normalize_venue("RH Checking"), "rh_checking")


class TestDueUrgencyClass(unittest.TestCase):
    """Dashboard bill-row colors: red ≤7/overdue, yellow 8–14, green >14."""

    def test_bands(self):
        self.assertEqual(due_urgency_class(-5), "due-red")
        self.assertEqual(due_urgency_class(0), "due-red")
        self.assertEqual(due_urgency_class(7), "due-red")
        self.assertEqual(due_urgency_class(8), "due-yellow")
        self.assertEqual(due_urgency_class(14), "due-yellow")
        self.assertEqual(due_urgency_class(15), "due-green")
        self.assertEqual(due_urgency_class(None), "due-unknown")

    def test_ranked_obligations_match_dashboard_classes(self):
        ranked = rank_obligations(
            [
                {"date": "7/1/2026", "from": "Coinbase", "item": "O", "monthly": 1},
                {"date": "8/5/2026", "from": "Coinbase", "item": "Y", "monthly": 1},
                {"date": "9/1/2026", "from": "Coinbase", "item": "G", "monthly": 1},
            ],
            today=TODAY,
        )
        # TODAY = 2026-07-27
        by = {r["item"]: r for r in ranked}
        self.assertEqual(due_urgency_class(by["O"]["days_until_due"]), "due-red")
        self.assertEqual(due_urgency_class(by["Y"]["days_until_due"]), "due-yellow")
        self.assertEqual(due_urgency_class(by["G"]["days_until_due"]), "due-green")


class TestUrgencySort(unittest.TestCase):
    def test_overdue_before_future_and_more_overdue_first(self):
        items = [
            {"date": "8/15/2026", "from": "Coinbase", "item": "Future", "monthly": 10},
            {"date": "7/20/2026", "from": "Coinbase", "item": "Recent overdue", "monthly": 10},
            {"date": "7/1/2026", "from": "Coinbase", "item": "Old overdue", "monthly": 10},
            {"date": "7/28/2026", "from": "Coinbase", "item": "Soon", "monthly": 10},
            {"date": None, "from": "Coinbase", "item": "No date", "monthly": 10},
        ]
        ranked = rank_obligations(items, today=TODAY)
        names = [r["item"] for r in ranked]
        self.assertEqual(
            names,
            ["Old overdue", "Recent overdue", "Soon", "Future", "No date"],
        )
        self.assertTrue(ranked[0]["overdue"])
        self.assertTrue(ranked[1]["overdue"])
        self.assertFalse(ranked[2]["overdue"])


class TestAllocation(unittest.TestCase):
    def test_higher_urgency_funded_first_scarce_cash(self):
        # Coinbase only $150: overdue 500 + 100 → fund oldest overdue first partial
        obligations = rank_obligations(
            [
                {"date": "7/1/2026", "from": "Coinbase", "item": "Big overdue", "monthly": 500},
                {"date": "7/20/2026", "from": "Coinbase", "item": "Small overdue", "monthly": 100},
                {"date": "8/1/2026", "from": "Coinbase", "item": "Later", "monthly": 50},
            ],
            today=TODAY,
        )
        venues = {
            "coinbase": {"label": "Coinbase", "available": 150.0},
            "x_money": {"label": "X Money", "available": 0.0},
        }
        lines, residuals, unfunded = allocate(obligations, venues)
        by_item = {L["item"]: L for L in lines}
        self.assertEqual(by_item["Big overdue"]["allocated"], 150.0)
        self.assertEqual(by_item["Big overdue"]["status"], "partial")
        self.assertEqual(by_item["Small overdue"]["allocated"], 0.0)
        self.assertEqual(by_item["Later"]["allocated"], 0.0)
        self.assertEqual(residuals["coinbase"], 0.0)
        self.assertGreaterEqual(len(unfunded), 2)

    def test_residuals_never_negative(self):
        obligations = rank_obligations(
            [
                {"date": "7/1/2026", "from": "X Money", "item": "A", "monthly": 1000},
                {"date": "7/2/2026", "from": "X Money", "item": "B", "monthly": 1000},
            ],
            today=TODAY,
        )
        venues = {"x_money": {"label": "X Money", "available": 75.5}}
        _lines, residuals, _u = allocate(obligations, venues)
        for v, r in residuals.items():
            self.assertGreaterEqual(r, 0.0, msg=v)

    def test_multi_venue_match(self):
        obligations = rank_obligations(
            [
                {"date": "7/28/2026", "from": "X Money", "item": "Ins", "monthly": 200},
                {"date": "8/1/2026", "from": "RH Checking", "item": "Chk", "monthly": 50},
            ],
            today=TODAY,
        )
        venues = {
            "x_money": {"label": "X Money", "available": 150.0},
            "rh_checking": {"label": "RH Checking", "available": 40.0},
            "coinbase": {"label": "Coinbase", "available": 0.0},
        }
        lines, residuals, _ = allocate(obligations, venues)
        by = {L["item"]: L for L in lines}
        self.assertEqual(by["Ins"]["allocated"], 150.0)
        self.assertEqual(by["Ins"]["gap"], 50.0)
        self.assertEqual(by["Chk"]["allocated"], 40.0)
        self.assertEqual(by["Chk"]["gap"], 10.0)
        self.assertEqual(residuals["x_money"], 0.0)
        self.assertEqual(residuals["rh_checking"], 0.0)


class TestBuildPlanFixture(unittest.TestCase):
    def test_fixture_plan_schema_and_sort(self):
        snaps = load_snapshots(FIXTURE_DIR)
        self.assertIn("expenses", snaps)
        plan = build_coach_plan(snaps, today=TODAY)
        self.assertTrue(plan["ok"])
        self.assertIn("obligations", plan)
        self.assertIn("residuals", plan)
        self.assertIn("habits", plan)
        self.assertIn("data_requests", plan)
        self.assertIn("summary", plan)
        names = [o["item"] for o in plan["obligations"]]
        # Overdue first: 7/1 then 7/20
        self.assertEqual(names[0], "Overdue rent residual")
        self.assertEqual(names[1], "Also overdue smaller")
        # Coinbase working 150: 500 overdue gets 150 partial first
        first = plan["obligations"][0]
        self.assertEqual(first["allocated"], 150.0)
        self.assertEqual(first["status"], "partial")
        # residuals non-negative
        for r in plan["residuals"].values():
            self.assertGreaterEqual(r, 0.0)
        # habits derived from fixture burn
        self.assertIsNotNone(plan["habits"].get("personal_daily_burn_est"))
        self.assertAlmostEqual(
            plan["habits"]["personal_daily_burn_est"], 39.17, places=2
        )
        # missing LTV → data request
        fields = {d["field"] for d in plan["data_requests"]}
        self.assertIn("morpho_ltv_principal", fields)

    def test_cli_entry_fixture(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                [
                    "--snapshots-dir",
                    str(FIXTURE_DIR),
                    "--as-of",
                    "2026-07-27",
                    "--pretty",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertGreater(len(payload["obligations"]), 0)
        self.assertIn("habits", payload)


class TestExtractVenues(unittest.TestCase):
    def test_working_usdc_includes_vault_breakdown(self):
        snaps = load_snapshots(FIXTURE_DIR)
        venues = extract_venues(snaps)
        self.assertAlmostEqual(venues["coinbase"]["available"], 150.0)
        self.assertAlmostEqual(venues["coinbase"]["vault_usdc"], 100.0)
        self.assertAlmostEqual(venues["coinbase"]["liquid_spot_usdc"], 50.0)


if __name__ == "__main__":
    unittest.main()
