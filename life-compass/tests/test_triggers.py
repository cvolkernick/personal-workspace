"""V1 trigger evaluation against injected snapshots."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from domain import ATTENDED, UNAVAILABLE, UNATTENDED, UNDER_ATTENDED, WIRING_PLANNED  # noqa: E402
from triggers import FITDASH_DEP, INVOICE_DEP, evaluate_triggers  # noqa: E402

NOW = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)


def _by_id(rows):
    return {r["id"]: r for r in rows}


class TestFitnessWiring(unittest.TestCase):
    def test_unwired_fitdash_is_wiring_planned_with_dependency(self) -> None:
        snap = {"fitness": {"wired": False, "as_of": "2026-09-19T15:00:00+00:00"}}
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        for tid in (
            "fitness.resistance_gap",
            "fitness.volume_decline",
            "fitness.weight_stall",
            "fitness.calorie_over",
        ):
            self.assertEqual(rows[tid]["observed_state"], WIRING_PLANNED)
            self.assertEqual(rows[tid]["wiring_dependency"], FITDASH_DEP)

    def test_wired_resistance_gap(self) -> None:
        snap = {"fitness": {"wired": True, "days_since_resistance": 4}}
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["fitness.resistance_gap"]["observed_state"], UNATTENDED)

    def test_wired_calories_and_volume(self) -> None:
        snap = {
            "fitness": {
                "wired": True,
                "days_since_resistance": 1,
                "volume_declining_two_weeks": True,
                "weight_stalled_two_weeks": True,
                "calorie_over_days_this_week": 3,
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["fitness.volume_decline"]["observed_state"], UNDER_ATTENDED)
        self.assertEqual(rows["fitness.weight_stall"]["observed_state"], UNDER_ATTENDED)
        self.assertEqual(rows["fitness.calorie_over"]["observed_state"], UNATTENDED)
        self.assertEqual(rows["fitness.resistance_gap"]["observed_state"], ATTENDED)


class TestFinancial(unittest.TestCase):
    def test_unscheduled_bill_due_within_two_days(self) -> None:
        snap = {
            "financial": {
                "wired": True,
                "bills": [
                    {
                        "payee": "Insurance",
                        "date": "2026-09-20",
                        "unscheduled": True,
                        "paid": False,
                    }
                ],
                "categories": [],
                "payoff": [],
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["financial.bill_due_unscheduled"]["observed_state"], UNATTENDED)

    def test_scheduled_unpaid_bill_due_soon_is_unattended(self) -> None:
        snap = {
            "financial": {
                "wired": True,
                "bills": [
                    {
                        "payee": "Rent",
                        "date": "2026-09-20",
                        "scheduled": True,
                        "unscheduled": False,
                        "paid": False,
                    }
                ],
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["financial.bill_due_unscheduled"]["observed_state"], UNATTENDED)

    def test_paid_or_handled_bill_is_attended(self) -> None:
        snap = {
            "financial": {
                "wired": True,
                "bills": [
                    {
                        "payee": "Rent",
                        "date": "2026-09-20",
                        "scheduled": True,
                        "paid": True,
                    }
                ],
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["financial.bill_due_unscheduled"]["observed_state"], ATTENDED)

    def test_overspent_and_underfunded(self) -> None:
        snap = {
            "financial": {
                "wired": True,
                "categories": [
                    {"name": "Dining", "balance": -40.0, "goal_under_funded": 0},
                    {"name": "Car", "balance": 10.0, "goal_under_funded": 200.0},
                ],
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["financial.category_overspent"]["observed_state"], UNATTENDED)
        self.assertIn("Dining overspent", rows["financial.category_overspent"]["detail"])
        self.assertIn("Car underfunded", rows["financial.category_overspent"]["detail"])

    def test_payoff_stalled_requires_14d_history(self) -> None:
        snap = {
            "financial": {
                "wired": True,
                "payoff": [{"name": "AmEx", "amount": 900, "amount_14d_ago": 900}],
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["financial.payoff_stalled"]["observed_state"], UNDER_ATTENDED)

    def test_payoff_watching_without_history_is_attended(self) -> None:
        snap = {
            "financial": {
                "wired": True,
                "payoff": [{"name": "AmEx", "amount": 900, "amount_14d_ago": None}],
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["financial.payoff_stalled"]["observed_state"], ATTENDED)
        self.assertIn("watching", rows["financial.payoff_stalled"]["detail"].lower())

    def test_ynab_unavailable_is_not_ok(self) -> None:
        snap = {"financial": {"wired": False, "error": "no token"}}
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["financial.bill_due_unscheduled"]["observed_state"], UNAVAILABLE)


class TestOperations(unittest.TestCase):
    def test_turo_today_unprepped(self) -> None:
        snap = {
            "operations": {
                "turo_trips": [
                    {
                        "id": "ev1",
                        "title": "Turo pickup Tesla",
                        "kind": "pickup",
                        "today": True,
                        "prepped": False,
                    }
                ]
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW))
        self.assertEqual(rows["ops.turo_today_unprepped"]["observed_state"], UNATTENDED)

    def test_turo_prepped_via_state_set(self) -> None:
        snap = {
            "operations": {
                "turo_trips": [
                    {
                        "id": "ev1",
                        "title": "Turo pickup Tesla",
                        "kind": "pickup",
                        "today": True,
                        "prepped": False,
                    }
                ]
            }
        }
        rows = _by_id(evaluate_triggers(snap, now=NOW, prepped_event_ids={"ev1"}))
        self.assertEqual(rows["ops.turo_today_unprepped"]["observed_state"], ATTENDED)

    def test_invoice_ready_wiring_planned(self) -> None:
        rows = _by_id(evaluate_triggers({}, now=NOW))
        self.assertEqual(rows["ops.invoice_ready_unassigned"]["observed_state"], WIRING_PLANNED)
        self.assertEqual(rows["ops.invoice_ready_unassigned"]["wiring_dependency"], INVOICE_DEP)


if __name__ == "__main__":
    unittest.main()
