"""FCC flags a stale expense snapshot with its as-of age (#1053)."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "financial-command" / "index.html"


class TestExpensesStaleFlag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_banner_is_in_the_coach_card(self):
        self.assertIn('id="expenses-stale-flag"', self.html)
        self.assertIn("expensesFreshnessFromSnapshot", self.html)
        self.assertIn("applyExpensesStaleFlag", self.html)
        self.assertIn("Expenses snapshot stale · as of ", self.html)
        self.assertIn("h old", self.html)

    def test_dashboard_and_coach_both_apply_the_flag(self):
        self.assertIn(
            "applyExpensesStaleFlag(expensesFreshnessFromSnapshot(ex))",
            self.html,
        )
        self.assertIn("applyExpensesStaleFlag(plan.expenses_freshness)", self.html)


if __name__ == "__main__":
    unittest.main()
