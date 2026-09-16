"""#636 Weekly review card lives at the top of Trends, not Today."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _card(src: str) -> str:
    start = src.find('id="weekly-review-card"')
    if start < 0:
        raise AssertionError("weekly-review-card missing")
    open_idx = src.rfind("<section", 0, start)
    close_idx = src.find("</section>", start)
    if open_idx < 0 or close_idx < 0:
        raise AssertionError("weekly-review-card section bounds failed")
    return src[open_idx : close_idx + len("</section>")]


def _fn(src: str, start: str, end: str) -> str:
    a = src.find(start)
    b = src.find(end)
    if a < 0 or b < 0 or b <= a:
        raise AssertionError(f"slice {start!r} → {end!r} failed")
    return src[a:b]


class WeeklyReviewPlacement(unittest.TestCase):
    def test_card_is_trends_not_today(self):
        card = _card(HTML)
        self.assertIn('data-m-panel="trends"', card)
        self.assertNotIn('data-m-panel="today"', card)
        self.assertIn("<h2>Weekly review</h2>", card)
        self.assertIn('id="weekly-review-bullets"', card)

    def test_first_on_trends(self):
        weekly = HTML.find('id="weekly-review-card"')
        sleep = HTML.find('id="sleep-trend-card"')
        cals = HTML.find('id="calories-macros-charts"')
        vol = HTML.find('id="charts-volume-strength"')
        azm = HTML.find('id="azm-trend-card"')
        self.assertGreater(weekly, -1)
        self.assertLess(weekly, sleep, "Weekly review is above Sleep on Trends")
        self.assertLess(sleep, cals)
        self.assertLess(cals, vol)
        self.assertLess(vol, azm)

    def test_today_hub_does_not_include_card(self):
        hub = _fn(HTML, 'id="today-hub"', 'id="recovery-card"')
        recovery = _fn(HTML, 'id="recovery-card"', 'id="hydration-pacing-section"')
        self.assertNotIn("weekly-review-card", hub)
        self.assertNotIn("weekly-review-card", recovery)
        self.assertNotIn("weekly-review-bullets", hub)
        self.assertNotIn("weekly-review-bullets", recovery)


class WeeklyReviewBehaviorUnchanged(unittest.TestCase):
    def test_js_still_fills_bullets_from_coach(self):
        fill = _fn(JS, 'const bullets = $("weekly-review-bullets")', "async function phaseBaroAct")
        self.assertIn("data.coach.weekly_review.bullets", fill)
        self.assertIn("phaseBaroEsc(b)", fill)
        self.assertNotIn("goMobileTab", fill)


class WeeklyReviewCache(unittest.TestCase):
    def test_cache_bumped(self):
        self.assertIn("/app.js?v=rhr-recovery-660-1", HTML)
        self.assertIn("/app.js?v=rhr-recovery-660-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v108"', SW)
        self.assertNotIn("/app.js?v=weekly-review-trends-636-1", HTML)
        self.assertNotIn("/app.js?v=weekly-review-trends-636-1", SW)
        self.assertNotIn("/app.js?v=ing-micros-612-1", HTML)
        self.assertNotIn("/app.js?v=ing-micros-612-1", SW)
        self.assertNotIn("fitdash-shell-v107", SW)
        self.assertNotIn("fitdash-shell-v105", SW)
        self.assertNotIn("fitdash-shell-v104", SW)


if __name__ == "__main__":
    unittest.main()
