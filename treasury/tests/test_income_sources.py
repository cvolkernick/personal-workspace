"""Shared Lyft / Grubhub / Turo matcher (#936)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.income_sources import (  # noqa: E402
    INCOME_SOURCE_LINES,
    classify_income_source,
    income_source_public,
)


class TestClassifyIncomeSource(unittest.TestCase):
    def test_colors_are_the_chart_contract(self) -> None:
        public = {row["id"]: row for row in income_source_public()}
        self.assertEqual(public["lyft"]["color"], "#ff69b4")
        self.assertEqual(public["grubhub"]["color"], "#ff8c1a")
        self.assertEqual(public["turo"]["color"], "#b7c0c8")
        self.assertEqual(public["lyft"]["label"], "Lyft")
        self.assertEqual(public["grubhub"]["label"], "Grubhub")
        self.assertEqual(public["turo"]["label"], "Turo")
        self.assertEqual(
            [row["id"] for row in INCOME_SOURCE_LINES],
            ["lyft", "grubhub", "turo"],
        )
        for row in income_source_public():
            self.assertNotIn("needles", row)

    def test_payee_and_category_are_case_insensitive_tokens(self) -> None:
        self.assertEqual(classify_income_source("Lyft Inc"), "lyft")
        self.assertEqual(classify_income_source("LYFT"), "lyft")
        self.assertEqual(classify_income_source("Payout", "Lyft"), "lyft")
        self.assertEqual(classify_income_source("HW*GrubHub Holdings Inc."), "grubhub")
        self.assertEqual(classify_income_source("Daily pay", "Grub"), "grubhub")
        self.assertEqual(classify_income_source("grubhub"), "grubhub")
        self.assertEqual(classify_income_source("TURO payout"), "turo")
        self.assertEqual(classify_income_source("Stripe", "Turo"), "turo")

    def test_short_grub_needle_does_not_eat_unrelated_words(self) -> None:
        self.assertIsNone(classify_income_source("Grubby"))
        self.assertIsNone(classify_income_source("Groceries"))
        self.assertIsNone(classify_income_source("Tutorial"))
        self.assertIsNone(classify_income_source(""))
        self.assertIsNone(classify_income_source(None, None))

    def test_payee_wins_when_category_disagrees(self) -> None:
        self.assertEqual(classify_income_source("Lyft", "Turo"), "lyft")
        self.assertEqual(classify_income_source("Turo", "Grubhub"), "turo")
        self.assertEqual(classify_income_source("Daily pay", "Grubhub"), "grubhub")


if __name__ == "__main__":
    unittest.main()
