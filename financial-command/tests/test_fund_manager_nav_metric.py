"""Fund-manager card metric is NAV only (#850)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "financial-command" / "index.html"

CARD_LABELS = ("NAV", "Of NAV", "Watchlist", "Hints", "Cadence", "Team")


class TestFundManagerNavOnlyMetric(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        m = re.search(
            r'document\.getElementById\("fund-manager-metrics"\)\.innerHTML = fma\.ok\s*\?\s*\[(.*?)\n          \]\.join\(""\)',
            cls.html,
            re.S,
        )
        if not m:
            raise AssertionError("fund-manager-metrics render block missing")
        cls.block = m.group(1)

    def test_nav_metric_is_nav_only(self):
        self.assertIn('metric("NAV", money(fma.nav_usd))', self.block)
        self.assertNotIn("NAV / BP / cash", self.html)
        self.assertNotIn("buying_power_usd", self.block)
        self.assertNotIn("fma.cash_usd", self.block)
        self.assertNotIn('metric("BP"', self.block)
        self.assertNotIn('metric("cash"', self.block)
        self.assertNotIn('metric("Cash"', self.block)

    def test_other_card_metrics_undisturbed(self):
        labels = tuple(re.findall(r'metric\(\s*"([^"]+)"', self.block))
        self.assertEqual(labels, CARD_LABELS)

    def test_no_empty_metric_slot(self):
        self.assertNotIn('metric("",', self.block)
        self.assertNotRegex(self.block, r'metric\(\s*"[^"]*"\s*,\s*""\)')

    def test_payload_cash_still_used_outside_card(self):
        """Allocation/DCA still reads cash_usd; BP/cash are not card metrics."""
        self.assertIn("fmaK.cash_usd", self.html)
        self.assertNotIn("fma.buying_power_usd", self.html)


if __name__ == "__main__":
    unittest.main()
