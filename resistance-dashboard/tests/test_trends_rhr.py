"""#660 Trends RHR card: 90d series + 14d median. Missing → honest skip, never 0 bpm."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
GH = (ROOT / "rt_dashboard" / "google_health.py").read_text(encoding="utf-8")
REC = (ROOT / "rt_dashboard" / "recovery.py").read_text(encoding="utf-8")


class TrendsRhrMarkup(unittest.TestCase):
    def test_card_on_trends_under_azm(self):
        self.assertIn('id="rhr-trend-card"', HTML)
        self.assertIn('id="chart-rhr"', HTML)
        self.assertIn('id="rhr-trend-note"', HTML)
        self.assertIn("Resting HR (bpm) · 90d", HTML)
        weekly = HTML.find('id="weekly-review-card"')
        sleep = HTML.find('id="sleep-trend-card"')
        cals = HTML.find('id="calories-macros-charts"')
        vol = HTML.find('id="charts-volume-strength"')
        azm = HTML.find('id="azm-trend-card"')
        rhr = HTML.find('id="rhr-trend-card"')
        conn = HTML.find('id="connections-card"')
        self.assertLess(weekly, sleep)
        self.assertLess(sleep, cals)
        self.assertLess(cals, vol)
        self.assertLess(vol, azm)
        self.assertLess(azm, rhr, "RHR sits directly under AZM on Trends")
        self.assertLess(rhr, conn)
        between = HTML[azm:rhr]
        for other in (
            "weekly-review-card",
            "sleep-trend-card",
            "calories-macros-charts",
            "weight-hydration-row",
            "charts-volume-strength",
        ):
            self.assertNotIn(f'id="{other}"', between, other)
        card = HTML[rhr : HTML.find("</section>", rhr)]
        self.assertIn('data-m-panel="trends"', card)
        self.assertNotIn('data-m-panel="today"', card)

    def test_js_plots_median_not_zero_fill(self):
        self.assertIn("function fillRhrCalendarDays", JS)
        self.assertIn("function rollingMedian", JS)
        self.assertIn("14d median", JS)
        self.assertIn("No resting heart rate yet — skipped.", JS)
        self.assertIn("data.health.resting_heart_rate", JS)
        self.assertIn("bpm: by[key] != null ? by[key] : null", JS)
        self.assertNotIn("bpm: by[key] != null ? by[key] : 0", JS)

    def test_fetch_is_daily_resting_heart_rate_only(self):
        self.assertIn('"daily-resting-heart-rate"', GH)
        self.assertIn("def parse_daily_resting_heart_rate_points", GH)
        self.assertIn("HRV / VO2 / SpO2 / respiratory rate are out of scope", REC)
        self.assertNotIn("daily-heart-rate-variability", GH.split("fetch_resting_heart_rate", 1)[1].split("def fetch_health", 1)[0])
        self.assertNotIn("daily-vo2-max", GH.split("fetch_resting_heart_rate", 1)[1].split("def fetch_health", 1)[0])

    def test_cache_bumped(self):
        self.assertIn("/app.js?v=rhr-recovery-660-1", HTML)
        self.assertIn("/app.js?v=rhr-recovery-660-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v109"', SW)
        self.assertNotIn("/app.js?v=weekly-review-trends-636-1", HTML)
        self.assertNotIn("fitdash-shell-v108", SW)
        self.assertNotIn("fitdash-shell-v107", SW)


if __name__ == "__main__":
    unittest.main()
