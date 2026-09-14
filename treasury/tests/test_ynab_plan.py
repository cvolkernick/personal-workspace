"""Unit tests for YNAB plan targets + assignment waterfall (#734)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_plan import (  # noqa: E402
    TARGETS_PATH,
    assignment_need,
    collapse_plan,
    cover_milli,
    dollars_to_milli,
    goal_payload,
    load_targets,
    plan_assignments,
    remaining_to_target_milli,
)


class TestMilli(unittest.TestCase):
    def test_round_cents(self):
        self.assertEqual(dollars_to_milli(360.84), 360840)
        self.assertEqual(dollars_to_milli(14.99), 14990)
        self.assertEqual(dollars_to_milli(427.2), 427200)


class TestCover(unittest.TestCase):
    def test_spend_is_negative_activity(self):
        self.assertEqual(cover_milli(-331340), 331340)
        self.assertEqual(cover_milli(0), 0)
        self.assertEqual(cover_milli(15000), 0)

    def test_remaining_to_target(self):
        self.assertEqual(remaining_to_target_milli(373850, 331340), 42510)
        self.assertEqual(remaining_to_target_milli(493000, 851800), 0)


class TestAssignmentNeed(unittest.TestCase):
    def test_waterfall_splits_cover_and_remaining(self):
        spec = {
            "role": "spend",
            "assign": "waterfall",
            "target_usd": 373.85,
        }
        cover, rem = assignment_need(spec, -331340)
        self.assertEqual(cover, 331340)
        self.assertEqual(rem, 42510)

    def test_off_book_zero_never_assigns(self):
        spec = {
            "role": "spend",
            "assign": "zero",
            "target_usd": 2100.0,
        }
        self.assertEqual(assignment_need(spec, 0), (0, 0))
        self.assertEqual(assignment_need(spec, -1000), (0, 0))

    def test_cc_and_income_skip(self):
        self.assertEqual(
            assignment_need({"role": "cc_payment", "assign": "skip"}, -479710),
            (0, 0),
        )
        self.assertEqual(
            assignment_need({"role": "income", "assign": "skip", "unpark": True}, 520300),
            (0, 0),
        )


class TestGoalPayload(unittest.TestCase):
    def test_monthly_need_for_positive_target(self):
        p = goal_payload(
            {
                "role": "spend",
                "target_usd": 360.84,
                "needs_whole_amount": True,
            }
        )
        self.assertEqual(p["goal_target"], 360840)
        self.assertEqual(p["goal_frequency"], "monthly")
        self.assertTrue(p["goal_needs_whole_amount"])

    def test_zero_clears_goal(self):
        p = goal_payload({"role": "spend", "target_usd": 0})
        self.assertEqual(p, {"goal_target": None})

    def test_cc_untouched(self):
        self.assertIsNone(
            goal_payload({"role": "cc_payment", "target_usd": 0})
        )
        self.assertIsNone(goal_payload({"role": "income", "target_usd": 0}))


class TestWaterfall(unittest.TestCase):
    def test_cover_first_then_fleet_then_buffer(self):
        specs = [
            {
                "id": "capone",
                "name": "Capital One",
                "group": "Bills",
                "role": "spend",
                "assign": "waterfall",
                "target_usd": 373.85,
                "priority": 12,
            },
            {
                "id": "santander",
                "name": "Santander",
                "group": "Bills",
                "role": "spend",
                "assign": "waterfall",
                "target_usd": 360.84,
                "priority": 11,
            },
            {
                "id": "gmf",
                "name": "GM Financial",
                "group": "Bills",
                "role": "spend",
                "assign": "waterfall",
                "target_usd": 501.08,
                "priority": 21,
            },
            {
                "id": "dining",
                "name": "Dining",
                "group": "Wants",
                "role": "spend",
                "assign": "waterfall",
                "target_usd": 180.0,
                "priority": 50,
            },
            {
                "id": "rent",
                "name": "Rent",
                "group": "Bills",
                "role": "spend",
                "assign": "zero",
                "target_usd": 2100.0,
                "priority": 90,
            },
        ]
        activity = {
            "capone": -331340,
            "dining": -97760,
            "santander": 0,
            "gmf": 0,
            "rent": 0,
        }
        # Tight TBB: cover CapOne+Dining, full Santander, CapOne remainder,
        # partial GMF. $50 buffer reserved.
        tbb = 1013980
        buffer = 50000
        plan = collapse_plan(plan_assignments(specs, activity, tbb, buffer))
        by_id = {t["id"]: t for t in plan["totals"]}
        self.assertEqual(by_id["capone"]["assign_milli"], 373850)  # full target
        self.assertEqual(by_id["dining"]["cover_milli"], 97760)
        self.assertEqual(by_id["dining"]["remaining_milli"], 0)
        self.assertEqual(by_id["santander"]["assign_milli"], 360840)
        self.assertEqual(by_id["gmf"]["assign_milli"], 131530)
        self.assertNotIn("rent", by_id)
        self.assertEqual(plan["leftover_after_plan_milli"], 0)
        assigned = sum(t["assign_milli"] for t in plan["totals"])
        self.assertEqual(
            assigned + plan["leftover_after_plan_milli"] + buffer, tbb
        )

    def test_negative_tbb_assigns_nothing(self):
        specs = [
            {
                "id": "u",
                "name": "Utilities",
                "group": "Bills",
                "role": "spend",
                "assign": "waterfall",
                "target_usd": 493.0,
                "priority": 13,
            }
        ]
        plan = collapse_plan(
            plan_assignments(specs, {"u": -851800}, tbb_milli=-2550340, buffer_milli=50000)
        )
        self.assertEqual(plan["totals"][0]["assign_milli"], 0)


class TestTargetsFile(unittest.TestCase):
    def test_file_covers_plan_categories(self):
        data = load_targets()
        self.assertEqual(data["budget_id"], "37502ae1-2484-4e3d-90a1-8985d775e86b")
        cats = data["categories"]
        ids = [c["id"] for c in cats]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(cats), 35)  # 37 YNAB cats minus Internal RTA + Uncategorized
        off_book = {c["name"] for c in cats if c.get("assign") == "zero" and c["target_usd"] > 0}
        self.assertEqual(off_book, {"Rivian R1S", "Thaís", "🏠 Rent/Mortgage"})
        cc = [c for c in cats if c["role"] == "cc_payment"]
        self.assertEqual(len(cc), 1)
        asic = next(c for c in cats if c["name"] == "ASIC Fleet OpEx")
        self.assertEqual(asic["target_usd"], 427.2)
        agentic = next(c for c in cats if c["name"] == "Agentic Fund Allocation")
        self.assertEqual(agentic["target_usd"], 25.0)
        # JSON still parses after load
        json.dumps(data)
        self.assertTrue(TARGETS_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
