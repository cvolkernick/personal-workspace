"""Today meal-plan carousel fills the card height vs Today so far (#577).

Desktop 2-col: no 14rem cap — existing flex chain stretches the carousel.
Mobile stacked: keep the 14rem cap so the page does not grow with meals.
Per-meal snap buckets keep 11.5–12.5rem. CSS only.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _block_after(src: str, needle: str) -> str:
    i = src.find(needle)
    if i < 0:
        return ""
    brace = src.find("{", i)
    if brace < 0:
        return ""
    end = src.find("}", brace)
    return src[brace : end + 1]


class MealCarouselFillLayout(unittest.TestCase):
    def test_desktop_carousel_drops_14rem_cap(self):
        desktop = _block_after(CSS, "#today-exec-row .meal-vcarousel {")
        self.assertTrue(desktop)
        self.assertIn("max-height: none", desktop)
        self.assertNotIn("14rem", desktop)
        self.assertIn("min-height: 0", desktop)
        self.assertIn("flex: 1 1 auto", desktop)
        self.assertIn("height: 100%", desktop)

    def test_mobile_keeps_bounded_max_height(self):
        stacked = CSS.split("Stack so-far + meal plan on narrow phones", 1)[1]
        mobile = _block_after(stacked, "#today-exec-row .meal-vcarousel {")
        self.assertTrue(mobile)
        self.assertIn("max-height: 14rem", mobile)
        self.assertIn("min-height: 11rem", mobile)
        desktop = _block_after(CSS, "#today-exec-row .meal-vcarousel {")
        self.assertNotEqual(desktop, mobile)

    def test_flex_chain_still_stretches(self):
        for sel in (
            "#today-exec-row #meal-plan-result",
            "#today-exec-row .meal-plan-panel",
            "#today-exec-row .meal-vcarousel-shell",
            "#today-exec-row .meal-vcarousel-row",
        ):
            block = _block_after(CSS, sel)
            self.assertIn("flex: 1 1 auto", block, sel)
            self.assertIn("min-height: 0", block, sel)
        card = _block_after(CSS, "#today-exec-row #meal-plan-card")
        self.assertIn("overflow: hidden", card)

    def test_snap_buckets_unchanged(self):
        slide = _block_after(CSS, ".meal-vslide.meal-bucket {")
        self.assertIn("scroll-snap-align: start", slide)
        self.assertIn("scroll-snap-stop: always", slide)
        self.assertIn("min-height: 11.5rem", slide)
        self.assertIn("max-height: 12.5rem", slide)
        car = _block_after(CSS, ".meal-vcarousel {")
        self.assertIn("scroll-snap-type: y mandatory", car)
        self.assertIn("overflow-y: auto", car)

    def test_markup_still_one_vcarousel(self):
        self.assertIn('id="today-exec-row"', HTML)
        self.assertIn('id="today-so-far-card"', HTML)
        self.assertIn('id="meal-plan-card"', HTML)
        self.assertIn("meal-vcarousel-shell", JS)
        self.assertIn('id="meal-plan-vcarousel"', JS)
        self.assertIn('data-axis="y"', JS)
        self.assertIn("scroll-snap-align", CSS)

    def test_cache_bumped(self):
        self.assertIn("/styles.css?v=meal-carousel-fill-1", HTML)
        self.assertIn("/styles.css?v=meal-carousel-fill-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v87"', SW)
        self.assertNotIn("/styles.css?v=phase-baro-1", HTML)
        self.assertNotIn("/styles.css?v=phase-baro-1", SW)
        self.assertNotIn("fitdash-shell-v86", SW)


if __name__ == "__main__":
    unittest.main()
