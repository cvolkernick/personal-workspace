"""#1070 Trends layout: AZM+RHR half row, energy card beside calories.

Macro split stays a full-width card. The Chairman has not confirmed a new
row partner, so sleep, weight, and hydration stay put.
"""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def _section(src: str, element_id: str) -> str:
    at = src.find(f'id="{element_id}"')
    if at < 0:
        raise AssertionError(f"{element_id} missing")
    open_idx = src.rfind("<section", 0, at)
    close_idx = src.find("</section>", at)
    if open_idx < 0 or close_idx < 0:
        raise AssertionError(f"{element_id} section bounds failed")
    return src[open_idx:close_idx]


class TrendsLayout1070(unittest.TestCase):
    def test_azm_and_rhr_share_one_half_row(self):
        row = _section(HTML, "azm-rhr-row")
        self.assertIn("grid two equal-cards", row)
        self.assertEqual(row.count('class="card card-fill"'), 2)
        azm = row.find('id="azm-trend-card"')
        rhr = row.find('id="rhr-trend-card"')
        self.assertGreater(azm, -1)
        self.assertLess(azm, rhr)
        self.assertIn('id="chart-rhr"', row)
        self.assertIn('id="azm-sparkline"', row)
        self.assertNotIn("<section", row[row.find(">") + 1 :])

    def test_energy_card_sits_beside_calories(self):
        row = _section(HTML, "calories-energy-row")
        self.assertIn("grid two equal-cards", row)
        cal = row.find('id="calories-intake-card"')
        energy = row.find('id="energy-scale-card"')
        mount = row.find('id="energy-scale-mount"')
        self.assertGreater(cal, -1)
        self.assertLess(cal, energy)
        self.assertLess(energy, mount)
        cal_close = row.find("</article>", cal)
        self.assertLess(cal_close, energy)
        self.assertNotIn("energy-scale-mount", row[cal:cal_close])
        self.assertNotIn('id="chart-macros"', row)
        self.assertNotIn("calories-macros-charts", HTML)
        self.assertIn("Calories intake vs burned · 90d", row)
        self.assertIn(">Energy vs scale<", row)

    def test_macro_split_is_not_reshuffled(self):
        energy = HTML.find('id="calories-energy-row"')
        macro = HTML.find('id="macro-split-card"')
        weight = HTML.find('id="weight-hydration-row"')
        sleep = HTML.find('id="sleep-trend-card"')
        self.assertLess(sleep, energy)
        self.assertLess(energy, macro)
        self.assertLess(macro, weight)
        macro_block = _section(HTML, "macro-split-card")
        self.assertNotIn("grid two", macro_block)
        self.assertIn('id="chart-macros"', macro_block)
        self.assertNotIn('id="chart-hydration"', macro_block)
        self.assertNotIn('id="chart-sleep"', macro_block)
        weight_block = _section(HTML, "weight-hydration-row")
        self.assertIn("grid two equal-cards", weight_block)
        self.assertIn('id="chart-weight"', weight_block)
        self.assertIn('id="hydration-card"', weight_block)
        self.assertNotIn('id="chart-macros"', weight_block)
        self.assertNotIn('id="chart-sleep"', weight_block)
        sleep_block = _section(HTML, "sleep-trend-card")
        self.assertIn('id="chart-sleep"', sleep_block)
        self.assertNotIn("grid two", sleep_block)

    def test_insight_moves_mount_without_rewriting_copy(self):
        marker = APP_JS.find('const scaleMount = $("energy-scale-mount");')
        self.assertGreater(marker, -1)
        block = APP_JS[marker : APP_JS.find("if (meta.error)", marker)]
        note_start = block.find("note.innerHTML = `")
        note_end = block.find("scaleMount.innerHTML = alignHtml", note_start)
        self.assertGreater(note_start, -1)
        self.assertGreater(note_end, note_start)
        note = block[note_start:note_end]
        self.assertNotIn("alignHtml", note)
        self.assertNotIn("energy-weight-insight", note)
        self.assertIn("Σ intake", note)
        self.assertIn("Σ burned", note)
        self.assertIn('scaleMount.innerHTML = ""', block)
        self.assertIn("scaleMount.innerHTML = alignHtml", block)
        start = APP_JS.find('class="energy-weight-insight')
        advice = APP_JS.find("ewi-advice", start)
        block = APP_JS[start:advice]
        self.assertIn("From calories", block)
        self.assertIn("On scale", block)
        self.assertIn("Gap", block)
        self.assertIn("scale − expected", block)
        self.assertIn("const CAL_IN_OUT_SPAN_DAYS = 90;", APP_JS)
        self.assertIn(".grid.equal-cards > .card-fill", CSS)
        self.assertNotIn("#calories-energy-row", CSS)
        self.assertNotIn("#azm-rhr-row", CSS)

    def test_cache_bumped(self):
        self.assertIn('const CACHE = "fitdash-shell-v135"', SW)
        self.assertIn("/app.js?v=trends-layout-1070-1", HTML)
        self.assertIn("/app.js?v=trends-layout-1070-1", SW)
        self.assertNotIn("/app.js?v=recipe-dishes-1069-1", HTML)
        self.assertNotIn("/app.js?v=recipe-dishes-1069-1", SW)
        self.assertNotIn("fitdash-shell-v134", SW)


if __name__ == "__main__":
    unittest.main()
