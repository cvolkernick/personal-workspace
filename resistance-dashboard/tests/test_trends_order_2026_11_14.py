"""Trends visual order: macro split is Carbs, Protein, Fat; muscle sets sit under tonnage.

Phone, tablet, and desktop share one DOM order. Panel CSS shows or hides
``data-m-panel`` and stacks two-column grids; it does not reorder these cards.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

MACRO_LABELS = (
    "Carbs %",
    "Protein %",
    "Fat %",
    "Carbs 7d avg",
    "Protein 7d avg",
    "Fat 7d avg",
)


def _macros_chart_block() -> str:
    start = APP.find('macrosChart = new Chart($("chart-macros")')
    if start < 0:
        raise AssertionError("macros chart missing")
    end = APP.find("destroyChart(hydrationChart)", start)
    if end < 0:
        raise AssertionError("macros chart block did not end")
    return APP[start:end]


class TrendsMacroOrder(unittest.TestCase):
    def test_dataset_legend_and_tooltip_are_carbs_protein_fat(self):
        block = _macros_chart_block()
        positions = [block.find(f'label: "{label}"') for label in MACRO_LABELS]
        self.assertTrue(all(pos > 0 for pos in positions), positions)
        self.assertEqual(positions, sorted(positions))
        carbs = block[positions[0] : positions[1]]
        protein = block[positions[1] : positions[2]]
        fat = block[positions[2] : positions[3]]
        self.assertIn('backgroundColor: "rgba(61,156,240,0.45)"', carbs)
        self.assertIn('backgroundColor: "rgba(240,180,41,0.45)"', protein)
        self.assertIn('backgroundColor: "rgba(240,113,120,0.4)"', fat)
        self.assertIn('borderColor: "#3d9cf0"', block[positions[3] : positions[4]])
        self.assertIn('borderColor: "#f0b429"', block[positions[4] : positions[5]])
        self.assertIn('borderColor: "#f07178"', block[positions[5] :])
        self.assertLess(block.find("sort(a, b)"), block.find("itemSort(a, b)"))
        self.assertIn("return a.datasetIndex - b.datasetIndex;", block)
        self.assertIn('label === "Carbs %" ? "c"', block)
        self.assertNotIn('["p", "c", "f"][ctx.datasetIndex]', block)

    def test_summary_chips_and_copy_read_carbs_protein_fat(self):
        block = _macros_chart_block()
        note = block[block.find('id="macros-note"') if 'id="macros-note"' in block else block.find("note.innerHTML") :]
        self.assertIn("Calorie share from carbs / protein / fat (4 / 4 / 9 kcal per gram)", note)
        self.assertIn("C×4 / P×4 / F×9", note)
        self.assertNotIn("protein / carbs / fat", note)
        self.assertNotIn("P×4 / C×4 / F×9", note)
        rows = note.split('class="chart-summary-row"')[1:]
        self.assertEqual(len(rows), 4)
        for row in rows:
            c_at = row.find("chip-carbs")
            p_at = row.find("chip-protein")
            f_at = row.find("chip-fat")
            self.assertTrue(0 <= c_at < p_at < f_at, row[:180])
        pills = note[note.find("macro-target-bar-pills") :]
        self.assertLess(pills.find("pill-c"), pills.find("pill-p"))
        self.assertLess(pills.find("pill-p"), pills.find("pill-f"))

    def test_today_tiles_stay_protein_carbs_fat(self):
        protein = HTML.find('id="stat-protein"')
        carbs = HTML.find('id="stat-carbs"')
        fat = HTML.find('id="stat-fat"')
        self.assertGreater(protein, -1)
        self.assertLess(protein, carbs)
        self.assertLess(carbs, fat)


class TrendsMuscleSetsPlacement(unittest.TestCase):
    def test_muscle_sets_follow_volume_on_every_viewport(self):
        vol = HTML.find('id="charts-volume-strength"')
        muscle = HTML.find('id="muscle-sets-card"')
        steps = HTML.find('id="steps-row"')
        self.assertGreater(vol, -1)
        vol_close = HTML.find("</section>", vol)
        muscle_open = HTML.find("<section", vol_close)
        self.assertIn('id="muscle-sets-card"', HTML[muscle_open : muscle_open + 240])
        self.assertLess(muscle_open, muscle)
        self.assertNotIn("<section", HTML[vol_close:muscle_open])
        muscle_close = HTML.find("</section>", muscle)
        steps_open = HTML.rfind("<section", 0, steps)
        self.assertLess(muscle_close, steps_open)
        self.assertNotIn("<section", HTML[muscle_close:steps_open])
        self.assertNotIn("#muscle-sets-card", CSS)
        self.assertNotIn("muscle-sets-card", APP)
        wide = CSS[CSS.find("@media (min-width: 720px)") :]
        self.assertIn("grid-template-columns: minmax(0, 420px) minmax(0, 1fr)", wide)
        self.assertIn("#muscle-sets-list {\n    grid-column: 2;", wide)

    def test_cache_bumped(self):
        self.assertIn("/app.js?v=weekly-review-off-1", HTML)
        self.assertIn("/app.js?v=weekly-review-off-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v179"', SW)
        self.assertNotIn("/app.js?v=muscle-map-1399", HTML)
        self.assertNotIn("/app.js?v=muscle-map-1399", SW)
        self.assertIn("/styles.css?v=guardrail-eq-1", HTML)


if __name__ == "__main__":
    unittest.main()
