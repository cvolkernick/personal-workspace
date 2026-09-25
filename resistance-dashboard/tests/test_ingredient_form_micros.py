"""#612 Kitchen ingredient form: optional fiber / sugar / sodium."""

from __future__ import annotations

from pathlib import Path
import unittest

from rt_dashboard.nutrition_planner import (
    add_ingredient,
    normalize_ingredient,
    update_ingredient,
)

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _slice(src: str, start: str, end: str) -> str:
    a = src.find(start)
    b = src.find(end)
    if a < 0 or b < 0 or b <= a:
        raise AssertionError(f"slice {start!r} → {end!r} failed")
    return src[a:b]


def _fn(src: str, start: str, end: str) -> str:
    return _slice(src, start, end)


class IngredientFormMarkup(unittest.TestCase):
    def test_add_form_has_optional_micros(self):
        form = _slice(HTML, 'id="ingredient-form"', 'id="inventory-list"')
        self.assertIn('id="ing-fiber"', form)
        self.assertIn('id="ing-sugar"', form)
        self.assertIn('id="ing-sodium"', form)
        self.assertIn("Fiber (g)", form)
        self.assertIn("Sugar (g)", form)
        self.assertIn("Salt (sodium mg)", form)
        for field_id in ("ing-fiber", "ing-sugar", "ing-sodium"):
            attrs = form.split(f'id="{field_id}"', 1)[1].split(">", 1)[0]
            self.assertNotIn("required", attrs)

    def test_macros_stay_required(self):
        form = _slice(HTML, 'id="ingredient-form"', 'id="inventory-list"')
        for field_id in ("ing-cal", "ing-p", "ing-c", "ing-f"):
            attrs = form.split(f'id="{field_id}"', 1)[1].split(">", 1)[0]
            self.assertIn("required", attrs)


class IngredientFormJs(unittest.TestCase):
    def test_submit_passes_micros_when_filled(self):
        submit = _fn(JS, "async function submitIngredient", "function renderCoachTargetRec")
        self.assertIn("optionalMicroNumber", submit)
        self.assertIn('$("ing-fiber")', submit)
        self.assertIn('$("ing-sugar")', submit)
        self.assertIn('$("ing-sodium")', submit)
        self.assertIn("body.fiber_g = fiber", submit)
        self.assertIn("body.sugar_g = sugar", submit)
        self.assertIn("body.sodium_mg = sodium", submit)
        self.assertIn('fetch("/api/inventory/add"', submit)

    def test_edit_collects_and_prefills_micros(self):
        collect = _fn(JS, "function collectInventoryEdit", "function assertServingGramsOrExplain")
        self.assertIn('fiber_g: optionalMicroNumber(get("fiber_g"))', collect)
        self.assertIn('sugar_g: optionalMicroNumber(get("sugar_g"))', collect)
        self.assertIn('sodium_mg: optionalMicroNumber(get("sodium_mg"))', collect)
        html_fn = _fn(JS, "function inventoryEditFormHtml", "function renderInventory")
        self.assertIn('data-edit-field="fiber_g"', html_fn)
        self.assertIn('data-edit-field="sugar_g"', html_fn)
        self.assertIn('data-edit-field="sodium_mg"', html_fn)


class IngredientMicrosBackend(unittest.TestCase):
    def test_normalize_keeps_micros(self):
        row = normalize_ingredient(
            {
                "name": "Broccoli",
                "calories": 55,
                "protein_g": 4,
                "carbs_g": 11,
                "fat_g": 0,
                "fiber_g": 5,
                "sugar_g": 2,
                "sodium_mg": 80,
            }
        )
        self.assertEqual(row["fiber_g"], 5.0)
        self.assertEqual(row["sugar_g"], 2.0)
        self.assertEqual(row["sodium_mg"], 80.0)

    def test_add_persists_micros(self):
        inv = add_ingredient(
            {"ingredients": []},
            {
                "name": "Broccoli",
                "calories": 55,
                "protein_g": 4,
                "carbs_g": 11,
                "fat_g": 0,
                "fiber_g": 5,
                "sugar_g": 2,
                "sodium_mg": 80,
            },
        )
        row = inv["ingredients"][0]
        self.assertEqual(row["fiber_g"], 5.0)
        self.assertEqual(row["sugar_g"], 2.0)
        self.assertEqual(row["sodium_mg"], 80.0)

    def test_update_keeps_micros_when_omitted(self):
        inv = add_ingredient(
            {"ingredients": []},
            {
                "id": "broccoli",
                "name": "Broccoli",
                "calories": 55,
                "protein_g": 4,
                "carbs_g": 11,
                "fat_g": 0,
                "fiber_g": 5,
                "sugar_g": 2,
                "sodium_mg": 80,
            },
        )
        updated = update_ingredient(
            inv,
            {"id": "broccoli", "name": "Broccoli florets", "calories": 50},
        )
        row = updated["ingredients"][0]
        self.assertEqual(row["name"], "Broccoli florets")
        self.assertEqual(row["calories"], 50.0)
        self.assertEqual(row["fiber_g"], 5.0)
        self.assertEqual(row["sugar_g"], 2.0)
        self.assertEqual(row["sodium_mg"], 80.0)

    def test_update_sets_and_clears_micros(self):
        inv = add_ingredient(
            {"ingredients": []},
            {
                "id": "oats",
                "name": "Oats",
                "calories": 150,
                "protein_g": 5,
                "carbs_g": 27,
                "fat_g": 3,
            },
        )
        set_row = update_ingredient(
            inv,
            {"id": "oats", "fiber_g": 4, "sugar_g": 1, "sodium_mg": 2},
        )["ingredients"][0]
        self.assertEqual(set_row["fiber_g"], 4.0)
        self.assertEqual(set_row["sugar_g"], 1.0)
        self.assertEqual(set_row["sodium_mg"], 2.0)
        cleared = update_ingredient(
            {"ingredients": [set_row]},
            {"id": "oats", "fiber_g": None, "sugar_g": None, "sodium_mg": None},
        )["ingredients"][0]
        self.assertNotIn("fiber_g", cleared)
        self.assertNotIn("sugar_g", cleared)
        self.assertNotIn("sodium_mg", cleared)


class IngredientFormCache(unittest.TestCase):
    def test_cache_bumped(self):
        self.assertNotIn("/app.js?v=hsa-834-1", SW)
        self.assertIn("/app.js?v=last-perf-920-1", HTML)
        self.assertIn("/app.js?v=last-perf-920-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v115"', SW)
        self.assertNotIn("/app.js?v=ing-micros-612-1", HTML)
        self.assertNotIn("/app.js?v=ing-micros-612-1", SW)
        self.assertNotIn("fitdash-shell-v105", SW)
        self.assertNotIn("/app.js?v=pf-applink-582-1", HTML)
        self.assertNotIn("/app.js?v=pf-applink-582-1", SW)
        self.assertNotIn("/app.js?v=log-date-771-1", HTML)
        self.assertNotIn("/app.js?v=log-date-771-1", SW)
        self.assertNotIn("fitdash-shell-v104", SW)
        self.assertNotIn("fitdash-shell-v103", SW)


if __name__ == "__main__":
    unittest.main()
