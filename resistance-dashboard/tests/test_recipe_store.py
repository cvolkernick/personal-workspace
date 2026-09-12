"""Recipes: servings math, coach compose, drift, shopping, logs (#692)."""

from __future__ import annotations

import unittest
from unittest import mock

from rt_dashboard.recipe_store import (
    IngredientInUseError,
    add_recipe_logs_to_consumed,
    attach_recipes_to_plan,
    compose_from_meal_items,
    compute_recipe_macros,
    fingerprint_ingredients,
    name_from_items,
    normalize_recipe,
    recipe_logs_as_food_entries,
    recipes_using_ingredient,
    scale_servings,
    shopping_from_plans,
)


def _inv(*rows):
    return {"ingredients": list(rows)}


CHICKEN = {
    "id": "chicken",
    "name": "Chicken",
    "serving_g": 170,
    "calories": 280,
    "protein_g": 52,
    "carbs_g": 0,
    "fat_g": 6,
    "stock": "in",
}
RICE = {
    "id": "rice",
    "name": "Rice",
    "serving_g": 150,
    "calories": 200,
    "protein_g": 4,
    "carbs_g": 44,
    "fat_g": 0.5,
    "stock": "out",
}


class ComposeAndMacros(unittest.TestCase):
    def test_compose_uses_portion_g_as_batch(self):
        items = [
            {"id": "chicken", "name": "Chicken", "portion_g": 170},
            {"id": "rice", "name": "Rice", "portion_g": 150},
        ]
        rec = compose_from_meal_items(items, yield_servings=1)
        self.assertEqual(rec["source"], "coach")
        self.assertEqual(rec["yield_servings"], 1.0)
        self.assertEqual(rec["name"], "Chicken + Rice")
        ids = [x["ingredient_id"] for x in rec["ingredients"]]
        self.assertEqual(ids, ["chicken", "rice"])
        self.assertEqual(rec["ingredients"][0]["grams_batch"], 170)

    def test_does_not_invent_grams(self):
        rec = compose_from_meal_items(
            [{"id": "mystery", "name": "Mystery"}], yield_servings=1
        )
        self.assertEqual(rec["ingredients"], [])

    def test_per_serving_is_batch_divided_by_yield(self):
        rec = normalize_recipe(
            {
                "name": "Prep",
                "yield_servings": 4,
                "ingredients": [{"ingredient_id": "chicken", "grams_batch": 680}],
            }
        )
        computed = compute_recipe_macros(rec, _inv(CHICKEN))
        self.assertEqual(computed["ingredients"][0]["per_serving_g"], 170)
        # 680g / 170g serving / 4 = 1 serving of chicken macros
        self.assertEqual(computed["macros_per_serving"]["protein_g"], 52)
        self.assertEqual(computed["macros_batch"]["protein_g"], 208)

    def test_scale_fractional_servings(self):
        rec = normalize_recipe(
            {
                "name": "Bowl",
                "yield_servings": 1,
                "ingredients": [{"ingredient_id": "chicken", "grams_batch": 170}],
            }
        )
        scaled = scale_servings(rec, 1.5, _inv(CHICKEN))
        self.assertEqual(scaled["servings"], 1.5)
        self.assertEqual(scaled["macros"]["protein_g"], 78)  # 52 * 1.5
        self.assertEqual(scaled["ingredients"][0]["grams"], 255)

    def test_missing_ingredient_marks_stale_no_invented_macros(self):
        rec = normalize_recipe(
            {
                "name": "Gone",
                "yield_servings": 1,
                "ingredients": [{"ingredient_id": "deleted", "grams_batch": 100}],
            }
        )
        computed = compute_recipe_macros(rec, _inv(CHICKEN))
        self.assertTrue(computed["stale"])
        self.assertEqual(computed["stale_ingredient_ids"], ["deleted"])
        self.assertEqual(computed["macros_per_serving"]["calories"], 0)

    def test_fingerprint_stable_across_5g_rounding(self):
        a = [{"ingredient_id": "chicken", "grams_batch": 172}]
        b = [{"ingredient_id": "chicken", "grams_batch": 168}]
        self.assertEqual(
            fingerprint_ingredients(a, 1), fingerprint_ingredients(b, 1)
        )


class DriftAndShopping(unittest.TestCase):
    def test_recipes_using_ingredient(self):
        recs = [
            normalize_recipe(
                {
                    "id": "r1",
                    "name": "Bowl",
                    "ingredients": [{"ingredient_id": "chicken", "grams_batch": 170}],
                }
            ),
            normalize_recipe(
                {
                    "id": "r2",
                    "name": "Rice only",
                    "ingredients": [{"ingredient_id": "rice", "grams_batch": 150}],
                }
            ),
        ]
        hit = recipes_using_ingredient(recs, "chicken")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["id"], "r1")
        self.assertEqual(recipes_using_ingredient(recs, "missing"), [])

    def test_ingredient_in_use_error_code(self):
        recs = [
            normalize_recipe(
                {
                    "id": "r1",
                    "name": "Bowl",
                    "ingredients": [{"ingredient_id": "chicken", "grams_batch": 170}],
                }
            )
        ]
        err = IngredientInUseError("chicken", recs)
        self.assertEqual(err.error_code, "ingredient_in_use")
        self.assertIn("Bowl", str(err))

    def test_shopping_restock_out_or_low_only(self):
        rec = compute_recipe_macros(
            normalize_recipe(
                {
                    "id": "r1",
                    "name": "Bowl",
                    "yield_servings": 1,
                    "ingredients": [
                        {"ingredient_id": "chicken", "grams_batch": 170},
                        {"ingredient_id": "rice", "grams_batch": 150},
                    ],
                }
            ),
            _inv(CHICKEN, RICE),
        )
        plan = {
            "meals": [
                {"recipe_id": "r1", "servings": 2, "recipe": rec},
            ]
        }
        lines = shopping_from_plans([plan], _inv(CHICKEN, RICE), [rec])
        ids = [x["id"] for x in lines]
        self.assertIn("rice", ids)
        self.assertNotIn("chicken", ids)
        rice = next(x for x in lines if x["id"] == "rice")
        self.assertEqual(rice["grams"], 300)
        self.assertEqual(rice["action"], "restock")


class LogsAndAttach(unittest.TestCase):
    def test_log_rows_look_like_food_logs(self):
        rows = recipe_logs_as_food_entries(
            [
                {
                    "date": "2026-09-14",
                    "name": "Bowl",
                    "servings": 1.5,
                    "recipe_id": "r1",
                    "macros": {"calories": 420, "protein_g": 78, "carbs_g": 66, "fat_g": 9},
                }
            ]
        )
        self.assertEqual(rows[0]["source"], "fitdash_recipe")
        self.assertEqual(rows[0]["serving_label"], "1.5 servings")
        self.assertEqual(rows[0]["protein_g"], 78)

    def test_consumed_adds_recipe_logs(self):
        consumed = {"calories": 100, "protein_g": 10, "carbs_g": 5, "fat_g": 1}
        out = add_recipe_logs_to_consumed(
            consumed,
            [{"macros": {"calories": 50, "protein_g": 20, "carbs_g": 0, "fat_g": 2}}],
        )
        self.assertEqual(out["calories"], 150)
        self.assertEqual(out["protein_g"], 30)
        self.assertEqual(out["recipe_log_count"], 1)

    def test_attach_reuses_fingerprint(self):
        items = [{"id": "chicken", "name": "Chicken", "portion_g": 170}]
        meal = {"label": "Next meal", "items": items}
        plan = {"meals": [meal]}
        saved = {
            "id": "abc123",
            "name": "Chicken",
            "fingerprint": compose_from_meal_items(items)["fingerprint"],
            "yield_servings": 1,
            "ingredients": [{"ingredient_id": "chicken", "grams_batch": 170}],
        }
        with mock.patch(
            "rt_dashboard.recipe_store.get_recipe", return_value=None
        ), mock.patch(
            "rt_dashboard.recipe_store.find_recipe_by_fingerprint",
            return_value=saved,
        ), mock.patch(
            "rt_dashboard.recipe_store.upsert_recipe"
        ) as upsert:
            out = attach_recipes_to_plan(plan, _inv(CHICKEN), "sub-1")
        upsert.assert_not_called()
        self.assertEqual(out["meals"][0]["recipe_id"], "abc123")
        self.assertEqual(out["meals"][0]["servings"], 1.0)

    def test_attach_skips_when_no_measurable_ingredients(self):
        plan = {"meals": [{"label": "Next meal", "items": [{"name": "Mystery"}]}]}
        out = attach_recipes_to_plan(plan, _inv(), "sub-1")
        self.assertIsNone(out["meals"][0].get("recipe_id"))

    def test_name_from_items(self):
        self.assertEqual(name_from_items([{"name": "A"}]), "A")
        self.assertEqual(
            name_from_items([{"name": "A"}, {"name": "B"}]), "A + B"
        )
        self.assertEqual(
            name_from_items([{"name": "A"}, {"name": "B"}, {"name": "C"}]),
            "A, B + 1 more",
        )


if __name__ == "__main__":
    unittest.main()
