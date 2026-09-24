"""#912: veg restock copy wins over a high protein-density ratio."""

from __future__ import annotations

import unittest
from copy import deepcopy
from unittest import mock

from rt_dashboard.inventory_store import (
    SOT_TURSO,
    apply_kirkland_stirfry_label,
    load_preview_inventory,
)
from rt_dashboard.nutrition_planner import (
    _protein_density,
    suggest_inventory_staples,
)

VEG_OUT = "Out of stock veg — restock for volume/fiber."
VEG_LOW = "Running low on veg — restock for volume/fiber."
PROTEIN_OUT = "Out of stock and high protein density — restock for meal plans."
PROTEIN_LOW = "Running low and high protein density — restock before empty."
KIRKLAND_ID = "kirkland-stir-fry-vegetable-blend"


def _restock(row):
    out = suggest_inventory_staples(
        {"ingredients": [row]},
        catalog=[],
        novel_catalog=[],
        max_suggestions=4,
    )
    hits = [s for s in out["suggestions"] if s.get("action") == "restock"]
    return hits[0]


class ProteinDensity(unittest.TestCase):
    def test_missing_or_zero_calories_are_zero(self):
        self.assertEqual(_protein_density({}), 0.0)
        self.assertEqual(_protein_density({"protein_g": 20}), 0.0)
        self.assertEqual(_protein_density({"calories": 0, "protein_g": 20}), 0.0)
        self.assertEqual(_protein_density({"calories": None, "protein_g": 20}), 0.0)
        self.assertEqual(_protein_density({"calories": "", "protein_g": 20}), 0.0)

    def test_real_ratio_unchanged(self):
        self.assertAlmostEqual(_protein_density({"calories": 35, "protein_g": 2}), 2 / 35)
        self.assertAlmostEqual(_protein_density({"calories": 220, "protein_g": 45}), 45 / 220)


class RestockVegBeforeProtein(unittest.TestCase):
    def test_out_of_stock_veg_with_high_ratio_uses_veg_copy(self):
        row = _restock(
            {
                "id": "pepper-mix",
                "name": "Pepper mix",
                "category": "veg",
                "calories": 30,
                "protein_g": 4,
                "stock": "out",
                "in_stock": False,
            }
        )
        self.assertGreaterEqual(_protein_density(row), 0.08)
        self.assertEqual(row["reason"], VEG_OUT)
        self.assertNotIn("high protein density", row["reason"])

    def test_running_low_veg_with_high_ratio_uses_veg_copy(self):
        row = _restock(
            {
                "id": "pepper-mix",
                "name": "Pepper mix",
                "category": "veg",
                "calories": 30,
                "protein_g": 4,
                "stock": "low",
                "in_stock": True,
            }
        )
        self.assertGreaterEqual(_protein_density(row), 0.08)
        self.assertEqual(row["reason"], VEG_LOW)
        self.assertNotIn("high protein density", row["reason"])

    def test_kirkland_blend_name_is_veg_even_when_category_is_carb(self):
        row = _restock(
            {
                "id": "kirkland-stir-fry-vegetable-blend",
                "name": "Kirkland Stir-Fry Vegetable Blend",
                "category": "carb",
                "calories": 35,
                "protein_g": 20,
                "serving_g": 93,
                "stock": "out",
                "in_stock": False,
            }
        )
        self.assertEqual(row["reason"], VEG_OUT)

    def test_zero_calorie_veg_is_not_high_protein(self):
        row = _restock(
            {
                "id": "broccoli",
                "name": "Broccoli",
                "category": "veg",
                "calories": 0,
                "protein_g": 20,
                "stock": "out",
                "in_stock": False,
            }
        )
        self.assertEqual(_protein_density(row), 0.0)
        self.assertEqual(row["reason"], VEG_OUT)

    def test_tilapia_still_uses_protein_copy(self):
        out = _restock(
            {
                "id": "tilapia",
                "name": "Tilapia",
                "category": "protein",
                "calories": 220,
                "protein_g": 45,
                "stock": "out",
                "in_stock": False,
            }
        )
        low = _restock(
            {
                "id": "tilapia",
                "name": "Tilapia",
                "category": "protein",
                "calories": 220,
                "protein_g": 45,
                "stock": "low",
                "in_stock": True,
            }
        )
        self.assertEqual(out["reason"], PROTEIN_OUT)
        self.assertEqual(low["reason"], PROTEIN_LOW)


def _kirkland(**extra):
    row = {
        "id": "kirkland-stir-fry-vegetable-blend",
        "name": "Kirkland Stir-Fry Vegetable Blend",
        "category": "carb",
        "calories": 35.0,
        "protein_g": 20.0,
        "carbs_g": 7.0,
        "fat_g": 0.0,
        "fiber_g": None,
        "serving_g": 93.0,
        "serving_label": "93g",
        "in_stock": False,
        "stock": "out",
    }
    row.update(extra)
    return row


class KirklandLabel(unittest.TestCase):
    def test_corrects_protein_on_the_93g_cup_only(self):
        oats = {"id": "oats", "name": "Oats", "calories": 150, "protein_g": 5, "serving_g": 40}
        before = {"ingredients": [_kirkland(), oats], "updated_at": "2026-09-24"}
        after, changed = apply_kirkland_stirfry_label(before)
        self.assertTrue(changed)
        blend = next(i for i in after["ingredients"] if i["id"] == KIRKLAND_ID)
        self.assertEqual(blend["protein_g"], 2.0)
        self.assertEqual(blend["calories"], 35.0)
        self.assertEqual(blend["carbs_g"], 7.0)
        self.assertEqual(blend["serving_g"], 93.0)
        self.assertEqual(blend["category"], "carb")
        self.assertEqual(blend["stock"], "out")
        self.assertEqual(after["ingredients"][1], oats)
        self.assertEqual(before["ingredients"][0]["protein_g"], 20.0)

    def test_idempotent_when_protein_already_matches_label(self):
        after, changed = apply_kirkland_stirfry_label(
            {"ingredients": [_kirkland(protein_g=2.0)]}
        )
        self.assertFalse(changed)
        self.assertEqual(after["ingredients"][0]["protein_g"], 2.0)

    def test_does_not_rewrite_a_different_serving(self):
        after, changed = apply_kirkland_stirfry_label(
            {"ingredients": [_kirkland(calories=90, serving_g=279, protein_g=20)]}
        )
        self.assertFalse(changed)
        self.assertEqual(after["ingredients"][0]["protein_g"], 20)

    def test_missing_item_is_not_invented(self):
        inv = {"ingredients": [{"id": "oats", "name": "Oats", "calories": 150}]}
        after, changed = apply_kirkland_stirfry_label(inv)
        self.assertFalse(changed)
        self.assertEqual(len(after["ingredients"]), 1)

    def test_turso_load_persists_once(self):
        stored = {"ingredients": [_kirkland()], "updated_at": "2026-09-24"}
        puts = []

        def put(uid, inv):
            puts.append(deepcopy(inv))
            stored["ingredients"] = deepcopy(inv["ingredients"])

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_get_inventory",
            side_effect=lambda uid: deepcopy(stored),
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_put_inventory",
            side_effect=put,
        ):
            inv, src = load_preview_inventory("chris-sub")
        self.assertEqual(src, SOT_TURSO)
        self.assertEqual(len(puts), 1)
        blend = next(i for i in inv["ingredients"] if i["id"] == KIRKLAND_ID)
        self.assertEqual(blend["protein_g"], 2.0)
        self.assertEqual(blend["calories"], 35.0)

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_get_inventory",
            side_effect=lambda uid: deepcopy(stored),
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_put_inventory",
            side_effect=put,
        ):
            load_preview_inventory("chris-sub")
        self.assertEqual(len(puts), 1)


if __name__ == "__main__":
    unittest.main()
