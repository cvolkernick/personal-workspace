"""Trends weight chart: body-fat line is a 14-calendar-day mean (2026-11-25).

Display only. The body-fat-series module default (7) stays, the chart passes
14. Weight datasets, phase / auto-flip, targets and weight-rate math are not
touched (no server or Python change in this slice).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
BF = (ROOT / "static" / "body-fat-series.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _chart_block() -> str:
    start = APP.index("const bfApi = typeof FitDashBodyFat")
    end = APP.index('$("weight-trend-note").textContent', start)
    return APP[start:end]


class BodyFat14Day(unittest.TestCase):
    def test_chart_passes_14_calendar_days(self):
        block = _chart_block()
        self.assertIn("const BF_CHART_AVG_DAYS = 14;", block)
        self.assertIn("const avgLead = BF_CHART_AVG_DAYS - 1;", block)
        self.assertIn(
            "clipToRecentCalendarDays(bodyFatSorted, 90 + avgLead)", block
        )
        self.assertEqual(
            block.count("bfApi.trailingAverage(bodyFatForAvg,"), 2, block
        )
        self.assertEqual(block.count(", BF_CHART_AVG_DAYS)"), 2)
        self.assertNotIn("bfApi.WINDOW_DAYS", block)

    def test_labels_name_the_window(self):
        block = _chart_block()
        self.assertIn('label: "Body fat 14-day avg"', block)
        self.assertNotIn("Body fat 7-day avg", APP)
        self.assertIn("body fat 14-day avg", APP)
        self.assertIn("14-calendar-day body fat average", HTML)

    def test_gap_stays_a_gap(self):
        block = _chart_block()
        avg = block.split('label: "Body fat 14-day avg"', 1)[1].split("});", 1)[0]
        self.assertIn("spanGaps: false", avg)

    def test_module_default_window_unchanged(self):
        self.assertIn("var WINDOW_DAYS = 7;", BF)

    def test_weight_datasets_unchanged(self):
        block = _chart_block()
        self.assertIn('label: "Weight (lb)"', block)
        self.assertIn("data: chartWeightVals", block)
        self.assertIn('label: "Trend"', block)
        self.assertIn("data: chartWeightTrend", block)
        self.assertIn("const weights = downsamplePoints(weightWindow, 45);", APP)
        self.assertIn("linearTrend(weightVals)", APP)
        # No weight series is fed through the body-fat window.
        self.assertIsNone(re.search(r"trailingAverage\([^)]*weight", APP, re.I))


if __name__ == "__main__":
    unittest.main()
