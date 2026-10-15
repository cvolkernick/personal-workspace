"""#1133: Capital Flows per-card YNAB inflows (synthetic fixtures only)."""

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "financial-command"))

from treasury.capital_flows_income import category_map, income_by_card  # noqa: E402

TODAY = date(2026, 10, 13)
SOURCES = json.loads(
    (ROOT / "investment" / "capital_flows.json").read_text(encoding="utf-8")
)["income_sources"]


def tx(i, day, amount, payee, cat="", group="Income", subs=None):
    return {
        "id": f"t{i}",
        "date": day,
        "amount": int(round(amount * 1000)),
        "payee_name": payee,
        "category_name": cat,
        "category_group_name": group,
        "account_id": "acct-a",
        "subtransactions": subs or [],
    }


class TestIncomeByCard(unittest.TestCase):
    def test_category_map_from_model(self):
        self.assertEqual(
            category_map(SOURCES), {("income", "panamerica auto"): "panamerica_auto"}
        )

    def test_panamerica_category_counts(self):
        out = income_by_card(
            [tx(1, "2026-09-29", 550.0, "Example Rentals LLC", "Panamerica Auto")],
            income_sources=SOURCES,
            today=TODAY,
        )
        self.assertEqual(out["totals"]["panamerica_auto"], 550.0)
        self.assertEqual(out["totals"]["turo"], 0.0)

    def test_no_double_count_with_turo(self):
        txs = [
            # Own-fleet Turo payout
            tx(1, "2026-10-01", 300.0, "Turo Inc", "Ready to Assign", "Inflow"),
            # Vehicle owner's share booked to Panamerica even though payee is Turo
            tx(2, "2026-10-02", 120.0, "Turo Inc", "Panamerica Auto"),
            # Mixed Turo payout split per trip
            tx(3, "2026-10-03", 0.0, "Turo Inc", "", subs=[
                {"amount": 200000, "category_name": "Ready to Assign", "category_group_name": "Inflow"},
                {"amount": 80000, "category_name": "Panamerica Auto", "category_group_name": "Income"},
            ]),
            # Outflow to the owner is not income
            tx(4, "2026-10-04", -50.0, "Vehicle owner payout", "Panamerica Auto"),
            # Out of window
            tx(5, "2026-08-01", 999.0, "Example Rentals LLC", "Panamerica Auto"),
        ]
        out = income_by_card(txs, income_sources=SOURCES, today=TODAY)
        self.assertEqual(out["totals"]["panamerica_auto"], 200.0)
        self.assertEqual(out["totals"]["turo"], 500.0)
        self.assertEqual(out["counts"]["panamerica_auto"], 2)
        total_in = 300 + 120 + 200 + 80
        self.assertEqual(
            out["totals"]["panamerica_auto"] + out["totals"]["turo"], total_in
        )

    def test_category_match_is_case_insensitive(self):
        out = income_by_card(
            [tx(1, "2026-10-10", 10.0, "x", "panamerica auto", "income")],
            income_sources=SOURCES,
            today=TODAY,
        )
        self.assertEqual(out["totals"]["panamerica_auto"], 10.0)


class TestCapitalFlowsPayload(unittest.TestCase):
    def test_payload_live_income_by_card(self):
        import server

        fake = {
            "ok": True,
            "transactions": [
                tx(1, date.today().isoformat(), 550.0, "Example Rentals LLC", "Panamerica Auto")
            ],
            "category_groups": [],
            "on_budget_ids": None,
        }
        with mock.patch.object(server, "_capital_flows_ynab_fetch", return_value=fake):
            data = server._capital_flows_payload()
        self.assertTrue(data["ok"])
        self.assertEqual(data["live"]["income_by_card_30d"]["panamerica_auto"], 550.0)

    def test_payload_survives_ynab_failure(self):
        import server

        with mock.patch.object(
            server, "_capital_flows_ynab_fetch", side_effect=RuntimeError("boom")
        ):
            data = server._capital_flows_payload()
        self.assertTrue(data["ok"])
        self.assertNotIn("income_by_card_30d", data["live"])


if __name__ == "__main__":
    unittest.main()
