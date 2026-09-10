"""#581: honey uncrustable serving is a sold 2-pack. Turso row only."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from rt_dashboard.inventory_store import (
    SOT_FILE,
    SOT_TURSO,
    UNCRUSTABLE_2PACK_LABEL,
    apply_honey_uncrustable_2pack,
    load_preview_inventory,
    load_workspace_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent


def _uncrustable(**extra) -> dict:
    row = {
        "id": "honey-uncrustable",
        "name": "honey uncrustable",
        "category": "other",
        "serving_label": "1 sandwich",
        "serving_g": 51,
        "calories": 210,
        "protein_g": 6,
        "carbs_g": 28,
        "fat_g": 9,
        "fiber_g": 2,
        "sugar_g": 8,
        "sodium_mg": 270,
        "in_stock": True,
        "qty": 4,
    }
    row.update(extra)
    return row


def _pantry(*rows) -> dict:
    return {"ingredients": list(rows), "updated_at": "2026-09-01"}


class HoneyUncrustable2Pack(unittest.TestCase):
    def test_json_seed_does_not_carry_the_item(self):
        for path in (
            REPO_ROOT / "fitness" / "nutrition" / "inventory.json",
            ROOT / "fitness" / "nutrition" / "inventory.json",
        ):
            blob = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("uncrustable", blob, path)
        inv, src = load_workspace_inventory()
        self.assertEqual(src, SOT_FILE)
        names = [str(i.get("name") or "").lower() for i in inv.get("ingredients") or []]
        self.assertTrue(all("uncrustable" not in n for n in names))

    def test_doubles_nutrients_and_serving_g_this_row_only(self):
        oats = {
            "id": "oats",
            "name": "Oats",
            "calories": 150,
            "protein_g": 5,
            "carbs_g": 27,
            "fat_g": 3,
            "serving_g": 40,
            "qty": 2,
        }
        before = _pantry(_uncrustable(), oats)
        after, changed = apply_honey_uncrustable_2pack(before)
        self.assertTrue(changed)
        honey = next(i for i in after["ingredients"] if i["id"] == "honey-uncrustable")
        oats_after = next(i for i in after["ingredients"] if i["id"] == "oats")
        self.assertEqual(honey["calories"], 420)
        self.assertEqual(honey["protein_g"], 12)
        self.assertEqual(honey["carbs_g"], 56)
        self.assertEqual(honey["fat_g"], 18)
        self.assertEqual(honey["fiber_g"], 4)
        self.assertEqual(honey["sugar_g"], 16)
        self.assertEqual(honey["sodium_mg"], 540)
        self.assertEqual(honey["serving_g"], 102)
        self.assertEqual(honey["serving_label"], UNCRUSTABLE_2PACK_LABEL)
        self.assertEqual(honey["qty"], 4)
        self.assertTrue(honey["in_stock"])
        self.assertEqual(oats_after, oats)
        self.assertEqual(before["ingredients"][0]["calories"], 210)

    def test_idempotent_when_label_already_2pack(self):
        row = _uncrustable(serving_label=UNCRUSTABLE_2PACK_LABEL, calories=420)
        after, changed = apply_honey_uncrustable_2pack(_pantry(row))
        self.assertFalse(changed)
        honey = after["ingredients"][0]
        self.assertEqual(honey["calories"], 420)
        self.assertEqual(honey["serving_label"], UNCRUSTABLE_2PACK_LABEL)

    def test_missing_item_is_not_invented(self):
        inv = _pantry({"id": "oats", "name": "Oats", "calories": 150})
        after, changed = apply_honey_uncrustable_2pack(inv)
        self.assertFalse(changed)
        self.assertEqual(after["ingredients"], inv["ingredients"])

    def test_matches_display_name_case_insensitively(self):
        row = _uncrustable(id="snack-1", name="Honey Uncrustable")
        after, changed = apply_honey_uncrustable_2pack(_pantry(row))
        self.assertTrue(changed)
        self.assertEqual(after["ingredients"][0]["calories"], 420)

    def test_turso_load_persists_once(self):
        stored = _pantry(_uncrustable())
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
        honey = next(i for i in inv["ingredients"] if i["id"] == "honey-uncrustable")
        self.assertEqual(honey["calories"], 420)
        self.assertEqual(honey["serving_label"], UNCRUSTABLE_2PACK_LABEL)

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_get_inventory",
            side_effect=lambda uid: deepcopy(stored),
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_put_inventory",
            side_effect=put,
        ):
            inv2, _src = load_preview_inventory("chris-sub")
        self.assertEqual(len(puts), 1)
        self.assertEqual(inv2["ingredients"][0]["calories"], 420)

    def test_turso_dark_does_not_add_item_to_file_seed(self):
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False):
            inv, src = load_preview_inventory("chris-sub")
        self.assertEqual(src, SOT_FILE)
        names = json.dumps(inv).lower()
        self.assertNotIn("uncrustable", names)


if __name__ == "__main__":
    unittest.main()
