"""Protein is the yellow family and carbs are the blue family.

Fat, --accent, --warn, and non-macro uses of those hexes stay put.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

YELLOW = ("#f0b429", "#f0c14d", "#f0d080", "240, 180, 41", "240,180,41")
BLUE = ("#3d9cf0", "#6db7f5", "#8fc4f5", "61, 156, 240", "61,156,240")


def rule_body(source: str, selector: str) -> str:
    token = selector + " {"
    start = source.find(token)
    if start < 0:
        raise AssertionError(f"missing selector {selector}")
    end = source.find("}", start)
    return source[start:end]


def dataset(label: str) -> str:
    token = f'label: "{label}"'
    start = APP.find(token)
    if start < 0:
        raise AssertionError(f"missing dataset {label}")
    nxt = APP.find("label:", start + len(token))
    end = nxt if nxt > 0 else start + 280
    return APP[start:end]


class MacroColorSwapTests(unittest.TestCase):
    def test_protein_uses_yellow_family(self):
        selectors = {
            "#nutrition-stats .macro-stat.macro-protein": ("#f0b429", "rgba(240, 180, 41, 0.14)"),
            "#nutrition-stats .macro-stat.macro-protein .label": ("#f0d080",),
            "#nutrition-stats .macro-stat.macro-protein .macro-split-v": ("#f0c14d",),
            ".macro-chip.macro-protein": ("#f0b429",),
            ".macro-progress-fill.macro-protein": ("#f0b429",),
            ".inv-macro-pill.macro-protein": ("rgba(240, 180, 41, 0.5)", "#f0c14d"),
            ".chart-summary-chip.chip-protein": ("rgba(240, 180, 41, 0.35)", "rgba(240, 180, 41, 0.1)"),
            ".chart-summary-chip.chip-protein .chip-v": ("#f0c14d",),
            ".macro-target-pill.pill-p": ("rgba(240, 180, 41, 0.45)", "rgba(240, 180, 41, 0.12)"),
            ".macro-target-pill.pill-p .mtp-v": ("#f0c14d",),
        }
        for selector, needles in selectors.items():
            body = rule_body(CSS, selector)
            for needle in needles:
                self.assertIn(needle, body, selector)
            for blue in BLUE:
                self.assertNotIn(blue, body, selector)

    def test_carbs_use_blue_family(self):
        selectors = {
            "#nutrition-stats .macro-stat.macro-carbs": ("#3d9cf0", "rgba(61, 156, 240, 0.14)"),
            "#nutrition-stats .macro-stat.macro-carbs .label": ("#8fc4f5",),
            "#nutrition-stats .macro-stat.macro-carbs .macro-split-v": ("#6db7f5",),
            ".macro-chip.macro-carbs": ("#3d9cf0",),
            ".macro-progress-fill.macro-carbs": ("#3d9cf0",),
            ".inv-macro-pill.macro-carbs": ("rgba(61, 156, 240, 0.5)", "#6db7f5"),
            ".chart-summary-chip.chip-carbs": ("rgba(61, 156, 240, 0.35)", "rgba(61, 156, 240, 0.1)"),
            ".chart-summary-chip.chip-carbs .chip-v": ("#6db7f5",),
            ".macro-target-pill.pill-c": ("rgba(61, 156, 240, 0.45)", "rgba(61, 156, 240, 0.12)"),
            ".macro-target-pill.pill-c .mtp-v": ("#6db7f5",),
        }
        for selector, needles in selectors.items():
            body = rule_body(CSS, selector)
            for needle in needles:
                self.assertIn(needle, body, selector)
            for yellow in YELLOW:
                self.assertNotIn(yellow, body, selector)

    def test_fat_stays_purple_on_tiles_and_red_on_trends(self):
        tiles = {
            "#nutrition-stats .macro-stat.macro-fat": "#c084fc",
            ".macro-chip.macro-fat": "#c084fc",
            ".macro-progress-fill.macro-fat": "#c084fc",
            ".inv-macro-pill.macro-fat": "#d0a4ff",
        }
        for selector, needle in tiles.items():
            self.assertIn(needle, rule_body(CSS, selector))
        self.assertIn("#f09098", rule_body(CSS, ".chart-summary-chip.chip-fat .chip-v"))
        self.assertIn("#f09098", rule_body(CSS, ".macro-target-pill.pill-f .mtp-v"))
        self.assertIn('backgroundColor: "rgba(240,113,120,0.4)"', dataset("Fat %"))
        self.assertIn('borderColor: "#f07178"', dataset("Fat 7d avg"))

    def test_trends_macro_chart_swaps_protein_and_carbs_only(self):
        protein_bar = dataset("Protein %")
        carbs_bar = dataset("Carbs %")
        self.assertIn('backgroundColor: "rgba(240,180,41,0.45)"', protein_bar)
        self.assertNotIn("61,156,240", protein_bar)
        self.assertIn('backgroundColor: "rgba(61,156,240,0.45)"', carbs_bar)
        self.assertNotIn("240,180,41", carbs_bar)
        self.assertIn('borderColor: "#f0b429"', dataset("Protein 7d avg"))
        self.assertNotIn("#3d9cf0", dataset("Protein 7d avg"))
        self.assertIn('borderColor: "#3d9cf0"', dataset("Carbs 7d avg"))
        self.assertNotIn("#f0b429", dataset("Carbs 7d avg"))

    def test_global_tokens_and_non_macro_series_keep_their_hexes(self):
        self.assertIn("--accent: #3d9cf0;", CSS)
        self.assertIn("--warn: #f0b429;", CSS)
        cals = rule_body(CSS, ".macro-progress-fill.macro-cals")
        self.assertIn("linear-gradient(90deg, #5ce1a8, #3d9cf0)", cals)
        self.assertIn("#3d9cf0", rule_body(CSS, ".azm-swatch-roll"))
        self.assertIn("rgba(240, 180, 41, 0.55)", rule_body(CSS, ".guardrail-chip.yellow"))

        weight = APP[APP.find('label: "Weight (lb)"') : APP.find('label: "Weight (lb)"') + 700]
        self.assertIn('borderColor: "#3d9cf0"', weight)
        body_fat = dataset("Body fat (%)")
        self.assertIn('borderColor: "#f0b429"', body_fat)

        sleep = APP[APP.find('label: "Sleep (h)"') : APP.find('label: "Sleep (h)"') + 900]
        self.assertIn('"rgba(240,180,41,0.45)"', sleep)
        self.assertIn('borderColor: "#3d9cf0"', sleep)

        rhr = dataset("14d median")
        self.assertIn('borderColor: "#3d9cf0"', rhr)

        volume = APP[APP.find('label: "Daily tonnage (lb)"') : APP.find('label: "Daily tonnage (lb)"') + 900]
        self.assertIn('backgroundColor: "rgba(61,156,240,0.55)"', volume)
        self.assertIn('borderColor: "#f0b429"', volume)

        water = APP[APP.find('label: "Water (ml)"') : APP.find('label: "Water (ml)"') + 1100]
        self.assertIn('backgroundColor: "rgba(61,156,240,0.45)"', water)
        self.assertIn('borderColor: "#f0b429"', water)

        first = APP[APP.find("first set (lb)") : APP.find("first set (lb)") + 250]
        self.assertIn('borderColor: "#3d9cf0"', first)
        e1rm = dataset("Est. 1RM (Epley)")
        self.assertIn('borderColor: "#f0b429"', e1rm)


if __name__ == "__main__":
    unittest.main()
