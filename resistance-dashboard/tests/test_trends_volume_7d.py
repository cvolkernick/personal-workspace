"""FitDash #752: 7-day rolling average on Trends Daily Volume.

Thin line overlay. Trailing mean of that day + previous 6. Rest-day
zeros stay in the window. 90d bars and linear trend stay.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
ANALYTICS = (ROOT / "rt_dashboard" / "analytics.py").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


def _volume_chart_block() -> str:
    start = APP_JS.find('volumeChart = new Chart($("chart-volume")')
    end = APP_JS.find('if ($("volume-trend-note"))')
    if start < 0 or end < 0 or end <= start:
        raise AssertionError("Daily Volume chart block not found")
    return APP_JS[start:end]


class TrendsVolume7dMarkup(unittest.TestCase):
    def test_90d_card_unchanged(self):
        self.assertIn("Daily Volume · 90d", HTML)
        self.assertIn('id="chart-volume"', HTML)
        self.assertIn("Daily volume chart last 90 days", HTML)
        self.assertIn('id="volume-trend-note"', HTML)
        self.assertNotIn("Daily Volume · 7d", HTML)

    def test_overlay_is_trailing_7d_line_not_bars(self):
        self.assertIn("rollingAverage(volVals, 7)", APP_JS)
        self.assertIn("linearTrend(volVals)", APP_JS)
        self.assertIn("const volRoll7 = rollingAverage(volVals, 7);", APP_JS)
        block = _volume_chart_block()
        self.assertIn('type: "bar",\n            label: "Daily volume (lb)"', block)
        self.assertIn('type: "line",\n            label: "7d rolling avg"', block)
        self.assertIn('type: "line",\n            label: "Trend"', block)
        roll = block.split('label: "7d rolling avg"', 1)[1].split(
            'label: "Trend"', 1
        )[0]
        self.assertNotIn('type: "bar"', roll)
        self.assertIn("data: volRoll7", roll)
        self.assertIn("borderWidth: 2", roll)
        self.assertIn("pointRadius: 0", roll)
        self.assertIn('borderColor: "#f07178"', roll)
        trend = block.split('label: "Trend"', 1)[1]
        self.assertIn("data: volTrend", trend)
        self.assertIn('borderColor: "#f0b429"', trend)
        self.assertIn("borderDash: [6, 4]", trend)
        bars = block.split('label: "Daily volume (lb)"', 1)[1].split(
            'label: "7d rolling avg"', 1
        )[0]
        self.assertIn("data: volVals", bars)

    def test_note_keeps_trend_and_adds_7d_avg(self):
        self.assertIn("7d avg ${fmtNum(lastVolRoll)} lb", APP_JS)
        self.assertIn("lastVolRoll", APP_JS)
        self.assertIn("lb/week", APP_JS)
        self.assertIn("training days", APP_JS)

    def test_rest_days_stay_zero_in_trailing_window(self):
        self.assertIn("rest days stay 0", APP_JS)
        self.assertIn("one bar per day (0 if no session)", APP_JS)
        self.assertIn(
            "if (v != null && !Number.isNaN(Number(v))) slice.push(Number(v));",
            APP_JS,
        )
        self.assertIn("i - window + 1", APP_JS)

    def test_server_window_stays_90d_with_zero_fill(self):
        self.assertIn("def volume_by_day(", ANALYTICS)
        self.assertIn("days: int = 90", ANALYTICS)
        self.assertIn("volume 0 so the chart is a full", ANALYTICS)
        self.assertIn('"volume_by_day": volume_by_day(clean, days=90)', ANALYTICS)
        self.assertNotIn("rolling_7", ANALYTICS)
        self.assertNotIn("volume_7d", ANALYTICS)

    def test_cache_bumped(self):
        self.assertIn("/app.js?v=calorie-phase-1", HTML)
        self.assertIn("/app.js?v=calorie-phase-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v102"', SW)
        self.assertNotIn("/app.js?v=vol-7d-1", HTML)
        self.assertNotIn("/app.js?v=vol-7d-1", SW)
        self.assertNotIn("/app.js?v=recipes-dish-1", HTML)
        self.assertNotIn("/app.js?v=recipes-dish-1", SW)
        self.assertNotIn("fitdash-shell-v101", SW)
        self.assertNotIn("fitdash-shell-v100", SW)

    def test_node_trailing_mean_includes_rest_zeros(self):
        script = ROOT / "tests" / "trends_volume_7d.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok trends-volume-7d", proc.stdout)


class HobbyAndIgnoreLock(unittest.TestCase):
    def test_no_new_serverless_function(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])

    def test_ignore_build_unchanged(self):
        self.assertIn(
            '"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"',
            VERCEL,
        )
        paths = (ROOT / "vercel-ignore-paths.txt").read_text(encoding="utf-8")
        self.assertIn("resistance-dashboard/", paths)


if __name__ == "__main__":
    unittest.main()
