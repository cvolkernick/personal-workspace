"""Kitchen ingredient + recipe inventory sections collapse by default (#764)."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _section(html: str, start_id: str, end_id: str) -> str:
    a = html.find(f'id="{start_id}"')
    b = html.find(f'id="{end_id}"')
    assert a >= 0 and b > a, (start_id, end_id, a, b)
    return html[a:b]


class KitchenInventoryCollapse(unittest.TestCase):
    def test_both_kitchen_lists_start_collapsed(self):
        recipes = _section(HTML, "recipes-section", "inventory-section")
        pantry = _section(HTML, "inventory-section", "targets-config-section")

        self.assertIn('data-collapse="recipes"', recipes)
        self.assertIn('data-collapse-body="recipes"', recipes)
        self.assertIn('aria-expanded="false"', recipes)
        self.assertIn("is-collapsed", recipes)
        self.assertIn("collapse-chevron", recipes)
        rec_head = recipes.find('data-collapse="recipes"')
        rec_body = recipes.find('data-collapse-body="recipes"')
        rec_list = recipes.find('id="recipes-list"')
        rec_form = recipes.find('id="recipe-form"')
        self.assertLess(rec_head, rec_body)
        self.assertLess(rec_body, rec_form)
        self.assertLess(rec_body, rec_list)
        body_open = recipes[rec_body : rec_body + 80]
        self.assertIn("hidden", body_open)

        self.assertIn('data-collapse="inventory"', pantry)
        self.assertIn('data-collapse-body="inventory"', pantry)
        self.assertIn('aria-expanded="false"', pantry)
        self.assertIn("is-collapsed", pantry)
        self.assertIn("collapse-chevron", pantry)
        inv_head = pantry.find('data-collapse="inventory"')
        inv_body = pantry.find('data-collapse-body="inventory"')
        inv_list = pantry.find('id="inventory-list"')
        inv_form = pantry.find('id="ingredient-form"')
        self.assertLess(inv_head, inv_body)
        self.assertLess(inv_body, inv_form)
        self.assertLess(inv_body, inv_list)
        self.assertIn("hidden", pantry[inv_body : inv_body + 80])
        # Titles stay on the header so a collapsed Kitchen still names both lists.
        self.assertIn("Recipes", recipes[: rec_body])
        self.assertIn("Ingredient Inventory", pantry[: inv_body])

    def test_defaults_and_click_handler_cover_kitchen_keys(self):
        defaults = JS.split("const COLLAPSE_DEFAULTS = {", 1)[1].split("};", 1)[0]
        self.assertIn("recipes: false", defaults)
        self.assertIn("inventory: false", defaults)
        self.assertIn('data-collapse-body="${key}"', JS)
        self.assertIn("onCollapsibleHeadClick", JS)
        # Restore must see Kitchen heads, which live outside #today-hub.
        apply = JS.split("function applyStaticCollapseState", 1)[1].split(
            "function bindCollapsibles", 1
        )[0]
        self.assertIn("const el = document", apply)
        self.assertNotIn('key === "inventory"', apply)

    def test_section_collapse_does_not_zero_the_pantry_list(self):
        """#118: overflow/flex collapse hid #inventory-list. Disclosure uses [hidden]."""
        pantry = _section(HTML, "inventory-section", "targets-config-section")
        self.assertIn('id="inventory-list"', pantry)
        self.assertIn("inv-list-scroll", pantry)
        self.assertIn(".collapsible-body[hidden]", CSS)
        self.assertIn("display: none !important", CSS)
        self.assertIn(".kitchen-collapse-head", CSS)

    def test_cache_bumped(self):
        self.assertNotIn("/app.js?v=hsa-834-1", SW)
        self.assertIn("/app.js?v=home-tag-919-1", HTML)
        self.assertIn("/app.js?v=home-tag-919-1", SW)
        self.assertIn("/styles.css?v=pace-rows-650-1", HTML)
        self.assertIn("/styles.css?v=pace-rows-650-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v114"', SW)
        self.assertNotIn("fitdash-shell-v104", SW)
        self.assertNotIn("fitdash-shell-v103", SW)
        self.assertNotIn("fitdash-shell-v102", SW)
        self.assertNotIn("/app.js?v=vol-7d-1", HTML)
        self.assertNotIn("/app.js?v=vol-7d-1", SW)
        self.assertNotIn("/styles.css?v=recipes-dish-1", HTML)
        self.assertNotIn("fitdash-shell-v101", SW)


if __name__ == "__main__":
    unittest.main()
