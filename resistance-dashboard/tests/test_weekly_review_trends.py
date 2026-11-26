"""Weekly review card removed from Trends (2026-11-26, Chris via Frankenfit). Front end only.

History: #636 moved the card to the top of Trends; FF-4 follow-up removes the rendered card.
The server still builds coach.weekly_review for the payload, the brief and the agent feed.
"""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
COACH = (ROOT / "rt_dashboard" / "coach.py").read_text(encoding="utf-8")


class WeeklyReviewCardRemoved(unittest.TestCase):
    def test_card_not_rendered(self):
        self.assertNotIn('id="weekly-review-card"', HTML)
        self.assertNotIn('id="weekly-review-bullets"', HTML)
        self.assertNotIn("<h2>Weekly review</h2>", HTML)
        self.assertNotIn("renderWeeklyReview", JS)
        self.assertNotIn("weeklyReviewRows", JS)
        self.assertNotIn("#weekly-review-bullets", CSS)

    def test_trends_order_closes_up(self):
        guard = HTML.find('id="guardrail-monitor"')
        sleep = HTML.find('id="sleep-trend-card"')
        cals = HTML.find('id="calories-energy-row"')
        vol = HTML.find('id="charts-volume-strength"')
        azm = HTML.find('id="azm-trend-card"')
        self.assertGreater(guard, -1)
        self.assertLess(guard, sleep, "Sleep follows Guardrails directly")
        between = HTML[HTML.find("</section>", guard) : HTML.rfind("<section", 0, sleep)]
        self.assertNotIn("<section", between)
        self.assertLess(sleep, cals)
        self.assertLess(cals, vol)
        self.assertLess(vol, azm)

    def test_server_data_unchanged(self):
        self.assertIn("weekly_review", COACH)


class WeeklyReviewCache(unittest.TestCase):
    def test_cache_bumped(self):
        self.assertIn("/app.js?v=weekly-review-off-1", HTML)
        self.assertIn("/app.js?v=weekly-review-off-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v178"', SW)
        self.assertNotIn("/app.js?v=phase-check-1", SW)
        self.assertNotIn("fitdash-shell-v177", SW)


if __name__ == "__main__":
    unittest.main()
