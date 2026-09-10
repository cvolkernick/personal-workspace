"""Today meal-plan carousel fills the card height vs Today so far (#577).

Desktop 2-col (≥800px): meal card does not contribute max-content to the
grid row (height: 0; min-height: 100%); inner carousel row is
minmax(0, 1fr) so extra meals scroll instead of clipping.
Stacked (≤799px): 14rem cap so extra meals do not grow the page.
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
    def test_two_col_card_zeroes_max_content_contribution(self):
        marker = "2-col: size the row from Today so far; meal card fills leftover."
        media = CSS.split(marker, 1)[1]
        self.assertTrue(media.lstrip().startswith("*/\n@media (min-width: 800px)"))
        card = _block_after(media, "#today-exec-row #meal-plan-card")
        self.assertIn("height: 0", card)
        self.assertIn("min-height: 100%", card)
        self.assertIn("overflow: hidden", card)
        first = CSS.find("#today-exec-row #meal-plan-card")
        self.assertGreater(first, CSS.find(marker))
        self.assertEqual(CSS.find("#today-exec-row #meal-plan-card", first + 1), -1)

    def test_two_col_inner_row_caps_grid_track(self):
        two_col = CSS.split(
            "2-col: size the row from Today so far; meal card fills leftover.", 1
        )[1]
        row = _block_after(two_col, "#today-exec-row .meal-vcarousel-row")
        self.assertIn("grid-template-rows: minmax(0, 1fr)", row)
        unscoped = _block_after(CSS, "#today-exec-row .meal-vcarousel-row {")
        self.assertNotIn("grid-template-rows", unscoped)

    def test_desktop_carousel_drops_14rem_cap_only_in_two_col(self):
        unscoped = _block_after(CSS, "#today-exec-row .meal-vcarousel {")
        self.assertTrue(unscoped)
        self.assertNotIn("max-height: none", unscoped)
        self.assertNotIn("14rem", unscoped)
        self.assertIn("min-height: 0", unscoped)
        self.assertIn("flex: 1 1 auto", unscoped)
        two_col = CSS.split(
            "2-col: size the row from Today so far; meal card fills leftover.", 1
        )[1]
        desktop = _block_after(two_col, "#today-exec-row .meal-vcarousel {")
        self.assertIn("max-height: none", desktop)
        self.assertIn("height: 100%", desktop)
        self.assertNotIn("14rem", desktop)

    def test_stacked_cap_covers_all_single_column_widths(self):
        stacked = CSS.split(
            "Stacked: cap height so extra meals don't lengthen the page.", 1
        )[1]
        self.assertTrue(stacked.lstrip().startswith("*/\n@media (max-width: 799px)"))
        mobile = _block_after(stacked, "#today-exec-row .meal-vcarousel {")
        self.assertTrue(mobile)
        self.assertIn("max-height: 14rem", mobile)
        self.assertIn("height: auto", mobile)
        self.assertIn("min-height: 11rem", mobile)
        phone = CSS.split("Stack so-far + meal plan on narrow phones", 1)[1]
        phone_only = phone.split(".pace-fill.band-green", 1)[0]
        self.assertNotIn("14rem", phone_only)
        self.assertNotIn("meal-vcarousel", phone_only)

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
        self.assertIn("/styles.css?v=meal-carousel-fill-3", HTML)
        self.assertIn("/styles.css?v=meal-carousel-fill-3", SW)
        self.assertIn('const CACHE = "fitdash-shell-v89"', SW)
        self.assertNotIn("/styles.css?v=meal-carousel-fill-2", HTML)
        self.assertNotIn("/styles.css?v=meal-carousel-fill-2", SW)
        self.assertNotIn("/styles.css?v=phase-baro-1", HTML)
        self.assertNotIn("/styles.css?v=phase-baro-1", SW)
        self.assertNotIn("fitdash-shell-v88", SW)
        self.assertNotIn("fitdash-shell-v87", SW)
        self.assertNotIn("fitdash-shell-v86", SW)


if __name__ == "__main__":
    unittest.main()
