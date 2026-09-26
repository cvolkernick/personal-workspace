"""Precedence and staleness for the One Card balance (#944)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.card_balance import resolve_card_balance  # noqa: E402

NOW = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)


class TestResolveCardBalance(unittest.TestCase):
    def test_unsourced_manual_loses_to_healthy_ynab(self):
        resolved = resolve_card_balance(
            {"card_balance": 499.23},
            {"source": "ynab", "card_balance": 440.18},
            now=NOW,
        )
        self.assertAlmostEqual(resolved["card_balance"], 440.18)
        self.assertEqual(resolved["card_source"], "ynab")
        self.assertFalse(resolved["card_stale"])

    def test_explicit_manual_beats_disagreeing_ynab(self):
        resolved = resolve_card_balance(
            {
                "card_balance": "498.82",
                "card_balance_source": "manual",
                "card_balance_as_of": "2026-09-26T19:46:55Z",
            },
            {
                "source": "ynab",
                "card_balance": 19.04,
                "as_of": "2026-09-26T19:00:00Z",
                "balance_as_of": "2026-09-20",
            },
            now=NOW,
        )
        self.assertAlmostEqual(resolved["card_balance"], 498.82)
        self.assertEqual(resolved["card_source"], "manual")
        self.assertFalse(resolved["card_stale"])
        self.assertTrue(resolved["ynab_disagrees"])
        self.assertAlmostEqual(resolved["ynab_card_balance"], 19.04)

    def test_ynab_within_dollar_heals_manual_pin(self):
        resolved = resolve_card_balance(
            {
                "card_balance": 498.82,
                "card_balance_source": "manual",
                "card_balance_as_of": "2026-09-26T19:46:55Z",
            },
            {"source": "ynab", "card_balance": 499.4, "balance_as_of": "2026-09-26"},
            now=NOW,
        )
        self.assertAlmostEqual(resolved["card_balance"], 499.4)
        self.assertEqual(resolved["card_source"], "ynab")
        self.assertFalse(resolved["ynab_disagrees"])

    def test_import_error_is_stale_even_when_just_fetched(self):
        resolved = resolve_card_balance(
            {},
            {
                "source": "ynab",
                "card_balance": 19.04,
                "as_of": "2026-09-26T19:50:00Z",
                "balance_as_of": "2026-09-26",
                "direct_import_in_error": True,
            },
            now=NOW,
        )
        self.assertTrue(resolved["card_stale"])
        self.assertIn("import", resolved["card_stale_reason"])

    def test_weeks_old_balance_evidence_is_stale(self):
        resolved = resolve_card_balance(
            {},
            {
                "source": "ynab",
                "card_balance": 19.04,
                "as_of": "2026-09-26T19:50:00Z",
                "balance_as_of": "2026-09-01",
            },
            now=NOW,
        )
        self.assertAlmostEqual(resolved["card_balance"], 19.04)
        self.assertTrue(resolved["card_stale"])
        self.assertIn("balance", resolved["card_stale_reason"])

    def test_undated_manual_is_not_current(self):
        resolved = resolve_card_balance(
            {"card_balance": 498.82, "card_balance_source": "manual"},
            {"source": "ynab", "card_balance": 19.04, "as_of": "2026-09-26T19:00:00Z"},
            now=NOW,
        )
        self.assertEqual(resolved["card_source"], "manual")
        self.assertTrue(resolved["card_stale"])
        self.assertIn("no as-of", resolved["card_stale_reason"])


if __name__ == "__main__":
    unittest.main()
