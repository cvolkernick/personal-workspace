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
        self.assertNotIn("/app.js?v=hsa-834-1", SW)
        self.assertIn("/app.js?v=last-perf-920-1", HTML)
        self.assertIn("/app.js?v=last-perf-920-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v115"', SW)
        self.assertNotIn("/app.js?v=weekly-review-trends-636-1", HTML)
        self.assertNotIn("fitdash-shell-v108", SW)
        self.assertNotIn("fitdash-shell-v107", SW)

    def test_trend_and_baseline_sit_behind_daily_points(self):
        start = JS.find("const rhrDatasets = [")
        end = JS.find('if ($("rhr-trend-note"))', start)
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        block = JS[start:end]
        daily = block.split('label: "14d median"', 1)[0]
        self.assertIn('label: "RHR (bpm)"', daily)
        self.assertIn('borderColor: "#f07178"', daily)
        self.assertNotIn("borderDash", daily)
        self.assertIn("order: 2", daily)
        median = block.split('label: "14d median"', 1)[1].split("if (rhrTrend)", 1)[0]
        self.assertIn('borderColor: "#3d9cf0"', median)
        self.assertIn("borderDash: [6, 4]", median)
        self.assertIn("order: 1", median)
        self.assertIn('label: "Trend"', block)
        self.assertIn('borderColor: "#c084fc"', block)
        self.assertIn("borderDash: [10, 4]", block)
        self.assertIn("order: 3", block)
        self.assertIn('label: "Baseline"', block)
        self.assertIn('borderColor: "#5ce1a8"', block)
        self.assertIn("borderDash: [2, 4]", block)
        self.assertIn("order: 4", block)
        # Chart.js 4 draws a higher order first, so 3 and 4 sit behind daily order 2.
        self.assertLess(block.find("order: 2"), block.find('label: "Trend"'))
        self.assertLess(block.find('label: "Trend"'), block.find('label: "Baseline"'))
        self.assertIn("const rSlope = trendSlopePerDay(rhrVals);", JS)
        self.assertIn(
            "const rhrTrend = rSlope == null ? null : linearTrend(rhrVals);",
            JS,
        )
        self.assertIn("if (rhrTrend)", block)
        self.assertIn("!recIn.rhr_skipped", JS)
        self.assertIn("Number.isFinite(Number(baselineRaw))", JS)
        self.assertIn("rhrVals.map(() => Number(baselineRaw))", JS)
        self.assertIn("suggestedMin: 45", block)
        self.assertIn("suggestedMax: 80", block)

    def test_note_appends_bpm_per_week_without_rewriting_baseline_copy(self):
        self.assertIn(
            'rSlope == null\n'
            '            ? ""\n'
            '            : ` · trend ${rSlope >= 0 ? "+" : ""}${(rSlope * 7).toFixed(2)} bpm/week`;',
            JS,
        )
        self.assertIn(
            "Today ${Number(todayBpm).toFixed(0)} bpm vs ${window || 14}d median ${Number(baseline).toFixed(0)} (${sign}${Number(delta).toFixed(0)})${flag}${slopeTxt} · ${rhrPresent} days",
            JS,
        )
        self.assertIn(" · under-recovered", JS)
        self.assertIn("(baseline needs more days)", JS)
        self.assertIn("No resting heart rate yet — skipped.", JS)


if __name__ == "__main__":
    unittest.main()
