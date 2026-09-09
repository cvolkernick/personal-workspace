"""FitDash #551: Trends hydration target green, rolling 7-day actual red.

Colors only. Numbers / windows / 35 ml/kg target math stay put.
Legend + tooltip follow dataset borderColor/backgroundColor (no extra swatches).
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _hydration_chart_block() -> str:
    start = APP_JS.find("const HYD_TREND_TARGET")
    end = APP_JS.find("hydrationChart = new Chart")
    if start < 0 or end < 0 or end <= start:
        raise AssertionError("hydration Trends chart block not found")
    return APP_JS[start:end]


class TrendsHydrationColors(unittest.TestCase):
    def test_target_green_rolling_actual_red(self):
        block = _hydration_chart_block()
        self.assertIn('const HYD_TREND_TARGET = "#5ce1a8"', block)
        self.assertIn('const HYD_TREND_ACTUAL_ROLL = "#f07178"', block)

        avg = block.split('label: "7d rolling avg"', 1)[1].split(
            'label: "Trend"', 1
        )[0]
        self.assertIn("HYD_TREND_ACTUAL_ROLL", avg)
        self.assertNotIn("HYD_TREND_TARGET", avg)
        self.assertIn("borderColor: HYD_TREND_ACTUAL_ROLL", avg)
        self.assertIn("backgroundColor: HYD_TREND_ACTUAL_ROLL", avg)

        tgt = block.split('label: "7d rolling target"', 1)[1]
        self.assertIn("HYD_TREND_TARGET", tgt)
        self.assertNotIn("HYD_TREND_ACTUAL_ROLL", tgt)
        self.assertIn("borderColor: HYD_TREND_TARGET", tgt)
        self.assertIn("backgroundColor: HYD_TREND_TARGET", tgt)

        # Old pairing must not remain on these two series.
        self.assertNotIn('borderColor: "#5ce1a8"', avg)
        self.assertNotIn("rgba(240, 113, 120", tgt)

    def test_numbers_windows_and_other_charts_unchanged(self):
        self.assertIn("rollingAverage(hydVals, 7)", APP_JS)
        self.assertIn("rollingAverage(hydTargetDay, 7)", APP_JS)
        self.assertIn("fillHydrationCalendarDays(hydrationRaw, 90)", APP_JS)
        self.assertIn("return Math.round(kg * 35);", APP_JS)
        self.assertIn('label: "Day target (35 ml/kg)"', APP_JS)
        # Day target stays the dashed purple guide — not the 7d pairing.
        day = APP_JS.split('label: "Day target (35 ml/kg)"', 1)[1].split(
            'label: "7d rolling target"', 1
        )[0]
        self.assertIn("rgba(192, 132, 252, 0.9)", day)
        # Sleep rolling avg is its own map (blue), not this pairing.
        sleep = APP_JS.split('$("chart-sleep")', 1)[1].split(
            "chart-hydration", 1
        )[0]
        self.assertIn('label: "7d rolling avg"', sleep)
        self.assertIn('borderColor: "#3d9cf0"', sleep)


if __name__ == "__main__":
    unittest.main()
