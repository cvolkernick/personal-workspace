"""#692 recipes UI + Vercel routes. No extra Hobby function."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import dispatch_client_route, inventory_write
from rt_dashboard.recipe_store import IngredientInUseError, RecipeNotADishError

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


class RecipesSurface(unittest.TestCase):
    def test_kitchen_section_and_routes(self):
        self.assertIn('id="recipes-section"', HTML)
        self.assertIn('data-m-panel="kitchen"', HTML)
        self.assertIn("Ate servings of", JS)
        self.assertIn("/api/recipes/log", JS)
        self.assertIn("ingredient_in_use", JS)
        self.assertIn("recipe-open", JS)
        self.assertIn("meal-recipe-item", CSS)
        self.assertIn("goMobileTab(\"kitchen\")", JS)
        self.assertIn("/api/recipes", VERCEL)
        self.assertIn("/api/dashboard?_r=recipes", VERCEL)
        self.assertIn("/api/recipes/log", VERCEL)
        self.assertFalse((ROOT / "api" / "recipes.py").exists())

    def test_recipe_is_a_dish_not_a_grouping(self):
        self.assertIn("Dish name", HTML)
        self.assertIn("Makes (servings of this dish)", HTML)
        self.assertIn("Method (required", HTML)
        self.assertIn('id="recipe-steps"', HTML)
        self.assertIn("required", HTML[HTML.find('id="recipe-steps"') : HTML.find('id="recipe-steps"') + 200])
        self.assertLess(HTML.find("recipe-steps"), HTML.find("recipe-ing-rows"))
        self.assertIn("transformed by a method", HTML)
        self.assertIn("meal-plan quick-add", HTML)
        self.assertIn("of ${dishName}", JS)
        self.assertIn("yield_label", JS)
        self.assertIn("What went in (batch → one serving of the dish)", JS)
        self.assertIn("Add a method — a recipe is a dish, not a pile of ingredients", JS)
        self.assertIn("meal-recipe-item", JS)
        self.assertNotIn("Coach composes from pantry", HTML)
        self.assertNotIn("No steps yet.", JS)

    def test_cookie_less_recipes_is_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = dispatch_client_route({}, "", "GET", path="/api/recipes")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("<html", json.dumps(body).lower())

    def test_inventory_remove_blocks_when_recipe_uses_ingredient(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        recs = [{"id": "r1", "name": "Bowl"}]
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "rt_dashboard.inventory_store.load_preview_inventory",
            return_value=({"ingredients": [{"id": "chicken", "name": "Chicken"}]}, "turso"),
        ), mock.patch(
            "rt_dashboard.recipe_store.assert_ingredient_not_in_use",
            side_effect=IngredientInUseError("chicken", recs),
        ):
            status, body = inventory_write(
                {"Cookie": f"{SESSION_COOKIE}={make_session({'id': 'sub-1', 'email': 'c@x.com'})}"},
                "inv_remove",
                {"id": "chicken"},
            )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "ingredient_in_use")
        self.assertEqual(body["recipes"][0]["name"], "Bowl")

    def test_save_without_method_is_400(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "rt_dashboard.inventory_store.load_preview_inventory",
            return_value=({"ingredients": []}, "turso"),
        ), mock.patch(
            "rt_dashboard.recipe_store.upsert_recipe",
            side_effect=RecipeNotADishError(),
        ):
            status, body = dispatch_client_route(
                {
                    "Cookie": f"{SESSION_COOKIE}={make_session({'id': 'sub-1', 'email': 'c@x.com'})}"
                },
                "",
                "POST",
                payload={
                    "name": "Chicken + Rice",
                    "ingredients": [{"ingredient_id": "chicken", "grams_batch": 170}],
                },
                path="/api/recipes",
            )
        self.assertEqual(status, 400)
        self.assertIn("dish", body["error"])


if __name__ == "__main__":
    unittest.main()
