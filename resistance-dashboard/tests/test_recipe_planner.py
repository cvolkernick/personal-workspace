"""Recipe-day planner (#1288). Library dishes, not a loose ingredient filler."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from rt_dashboard.agent_today import _meal_slots
from rt_dashboard.meal_calendar import meal_display_name
from rt_dashboard.nutrition_planner import (
    _eating_window_bounds,
    _meal_tz,
    _resolve_eat_times,
    generate_meal_plan,
    normalize_targets,
    suggest_inventory_staples,
)
from rt_dashboard.recipe_planner import generate_recipe_plan
from rt_dashboard.recipe_store import (
    load_nourish_doc,
    load_nourish_recipes,
    normalize_recipe,
    seed_nourish_recipes,
)
from rt_dashboard.restock_cart import restock_list

ET = ZoneInfo("America/New_York")
MORNING = datetime(2026, 8, 22, 8, 0, tzinfo=ET)
TARGETS = {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55}
CONSUMED = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}


def _pantry(stamp="2026-08-22T12:00:00Z", **overrides):
    foods = load_nourish_doc()["foods"]
    ingredients = []
    for food in foods:
        row = dict(food)
        row["in_stock"] = True
        row["updated_at"] = stamp
        if row["id"] in overrides:
            row.update(overrides[row["id"]])
        ingredients.append(row)
    return {"ingredients": ingredients}


def _recipes(inventory):
    return load_nourish_recipes(inventory)


def _gym(day, hour, end_hour, end_minute=0):
    return {
        "start": day.replace(hour=hour, minute=0, second=0, microsecond=0),
        "end": day.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0),
    }


def _clock(meal):
    return datetime.fromisoformat(meal["eat_at"]).astimezone(ET)


class RecipeDayPlans(unittest.TestCase):
    def setUp(self):
        self.inv = _pantry()
        self.recipes = _recipes(self.inv)

    def _plan(self, **kwargs):
        args = dict(
            now=MORNING,
            tz_name="America/New_York",
        )
        args.update(kwargs)
        return generate_recipe_plan(self.inv, TARGETS, CONSUMED, self.recipes, **args)

    def test_training_and_rest_hit_targets(self):
        for label, window in (
            ("training", _gym(MORNING, 12, 13, 30)),
            ("rest", None),
        ):
            plan = self._plan(workout_window=window)
            self.assertIsNotNone(plan, label)
            totals = plan["planned_totals"]
            self.assertEqual(plan["mode"], "recipe")
            self.assertGreaterEqual(totals["calories"], 2100 * 0.95, label)
            self.assertLessEqual(totals["calories"], 2100 * 1.05, label)
            self.assertGreaterEqual(totals["protein_g"], 200, label)
            first = plan["meals"][0]
            self.assertGreaterEqual(first["totals"]["protein_g"], 50, label)
            self.assertLessEqual(first["totals"]["protein_g"], 60, label)
            self.assertTrue(all(meal.get("recipe_id") for meal in plan["meals"]))
            if window:
                pre = next(meal for meal in plan["meals"] if meal["slot_role"] == "pre-workout")
                post = next(meal for meal in plan["meals"] if meal["slot_role"] == "post-workout")
                self.assertGreaterEqual(pre["totals"]["carbs_g"], 40)
                self.assertLessEqual(pre["totals"]["carbs_g"], 60)
                self.assertLessEqual(pre["totals"]["fat_g"], 10)
                self.assertLessEqual(pre["totals"].get("fiber_g") or 0, 6)
                others = [meal["totals"]["carbs_g"] for meal in plan["meals"] if meal is not post]
                higher = [value for value in others if value > post["totals"]["carbs_g"] + 0.05]
                self.assertEqual(len(higher), 1)

    def test_seven_day_variety(self):
        for training in (True, False):
            history = []
            last_use = {}
            for offset in range(7):
                day = MORNING + timedelta(days=offset)
                window = _gym(day, 12, 13, 30) if training else None
                plan = generate_recipe_plan(
                    self.inv,
                    TARGETS,
                    CONSUMED,
                    self.recipes,
                    now=day,
                    tz_name="America/New_York",
                    workout_window=window,
                    recipe_history=history,
                )
                self.assertIsNotNone(plan, (training, offset))
                totals = plan["planned_totals"]
                self.assertGreaterEqual(totals["protein_g"], 200)
                self.assertGreaterEqual(totals["calories"], 2100 * 0.95)
                proteins = []
                cuisines = []
                for meal in plan["meals"]:
                    rid = meal["recipe_id"]
                    prev = last_use.get(rid)
                    if prev is not None:
                        self.assertGreater((day.date() - prev).days, 2, rid)
                    last_use[rid] = day.date()
                    proteins.append(meal["primary_protein"])
                    cuisines.append(meal["cuisine"].casefold())
                    history.append(
                        {
                            "date": day.date().isoformat(),
                            "recipe_id": rid,
                            "primary_protein": meal["primary_protein"],
                            "cuisine": meal["cuisine"],
                        }
                    )
                for source in set(proteins):
                    self.assertLessEqual(proteins.count(source), 2)
                for left, right in zip(proteins, proteins[1:]):
                    self.assertNotEqual(left, right)
                for left, right in zip(cuisines, cuisines[1:]):
                    self.assertNotEqual(left, right)

    def test_eggs_stay_paired_and_salsa_is_pico_or_rotel(self):
        with self.assertRaises(ValueError):
            normalize_recipe(
                {
                    "name": "Whites only",
                    "ingredients": [{"ingredient_id": "egg-whites", "grams": 100}],
                    "instructions": ["Cook."],
                }
            )
        with self.assertRaises(ValueError):
            normalize_recipe(
                {
                    "name": "Jar",
                    "ingredients": [{"ingredient_id": "jar-salsa", "grams": 30}],
                    "instructions": ["Spoon it on."],
                }
            )
        ok = normalize_recipe(
            {
                "name": "Pico eggs",
                "ingredients": [
                    {"ingredient_id": "eggs-whole", "grams": 50},
                    {"ingredient_id": "egg-whites", "grams": 80},
                    {"ingredient_id": "pico-de-gallo", "grams": 40},
                ],
                "instructions": ["Scramble."],
            }
        )
        self.assertEqual(ok["source"], "user")
        plan = self._plan(workout_window=_gym(MORNING, 12, 13, 30))
        for meal in plan["meals"]:
            ids = {item["id"] for item in meal["items"]}
            self.assertFalse(ids & {"salsa", "jar-salsa"})
            if "eggs-whole" in ids or "egg-whites" in ids:
                self.assertIn("eggs-whole", ids)
                self.assertIn("egg-whites", ids)

    def test_out_of_stock_is_not_planned_and_names_the_recipe(self):
        inv = _pantry(tilapia={"in_stock": False})
        recipes = _recipes(inv)
        plan = generate_recipe_plan(
            inv,
            TARGETS,
            CONSUMED,
            recipes,
            now=MORNING,
            tz_name="America/New_York",
            workout_window=_gym(MORNING, 12, 13, 30),
        )
        self.assertIsNotNone(plan)
        blob = " ".join(meal["recipe_id"] + " " + meal["recipe_name"] for meal in plan["meals"])
        self.assertNotIn("tilapia", blob.lower())
        reasons = " ".join(row["reason"] for row in plan["restock_suggestions"])
        self.assertIn("Lemon-Garlic Tilapia", reasons)
        self.assertIn("Tilapia", reasons)

    def test_stale_pantry_item_is_not_planned(self):
        old = (MORNING - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        inv = _pantry(tilapia={"updated_at": old, "in_stock": True})
        recipes = _recipes(inv)
        plan = generate_recipe_plan(
            inv, TARGETS, CONSUMED, recipes, now=MORNING, tz_name="America/New_York"
        )
        self.assertIsNotNone(plan)
        used = {item["id"] for meal in plan["meals"] for item in meal["items"]}
        self.assertNotIn("tilapia", used)
        prompts = " ".join(row["text"] + " " + row["name"] for row in plan["pantry_prompts"])
        self.assertIn("Still have this?", prompts)
        self.assertIn("Tilapia", prompts)

    def test_workout_clocks_follow_the_session(self):
        noon = self._plan(workout_window=_gym(MORNING, 12, 13, 30))
        pre = _clock(next(meal for meal in noon["meals"] if meal["slot_role"] == "pre-workout"))
        post = _clock(next(meal for meal in noon["meals"] if meal["slot_role"] == "post-workout"))
        self.assertGreaterEqual(pre, MORNING.replace(hour=9, minute=30))
        self.assertLessEqual(pre, MORNING.replace(hour=10, minute=30))
        self.assertLessEqual(post, MORNING.replace(hour=15, minute=30))
        self.assertIn("Pre-workout", noon["meals"][0]["label"] if noon["meals"][0]["slot_role"] == "pre-workout" else next(m["label"] for m in noon["meals"] if m["slot_role"] == "pre-workout"))
        evening = self._plan(workout_window=_gym(MORNING, 17, 18, 30))
        pre_e = _clock(next(meal for meal in evening["meals"] if meal["slot_role"] == "pre-workout"))
        post_e = _clock(next(meal for meal in evening["meals"] if meal["slot_role"] == "post-workout"))
        self.assertEqual(pre_e.hour, 15)
        self.assertEqual(post_e.hour, 19)
        self.assertGreater(pre_e, pre)

    def test_rest_day_eat_at_matches_the_engine(self):
        plan = self._plan()
        tz, name = _meal_tz("America/New_York")
        start, end = _eating_window_bounds(MORNING, tz, name)
        engine = _resolve_eat_times(
            len(plan["meals"]), now=MORNING, start=start, end=end, tz=tz
        )
        self.assertEqual(
            [meal["eat_at"] for meal in plan["meals"]],
            [moment.isoformat(timespec="seconds") for moment in engine],
        )
        self.assertEqual(
            [meal["label"] for meal in plan["meals"]],
            ["Next meal", "Later meal", "Evening", "Optional snack"][: len(plan["meals"])],
        )
        self.assertTrue(all(meal.get("slot_role") for meal in plan["meals"]))

    def test_dispatch_uses_recipes_and_falls_back_without_them(self):
        planned = generate_meal_plan(
            self.inv,
            TARGETS,
            CONSUMED,
            recipes=self.recipes,
            now=MORNING,
            tz_name="America/New_York",
            workout_window=_gym(MORNING, 12, 13, 30),
        )
        self.assertEqual(planned["mode"], "recipe")
        bare = {
            "ingredients": [
                {
                    "id": "chicken",
                    "name": "Chicken",
                    "serving_g": 170,
                    "calories": 280,
                    "protein_g": 52,
                    "carbs_g": 0,
                    "fat_g": 6,
                    "in_stock": True,
                    "updated_at": "2026-08-22T12:00:00Z",
                }
            ]
        }
        greedy = generate_meal_plan(bare, TARGETS, CONSUMED, now=MORNING, tz_name="America/New_York")
        empty = generate_meal_plan(
            bare, TARGETS, CONSUMED, recipes=[], now=MORNING, tz_name="America/New_York"
        )
        self.assertEqual(greedy["mode"], "ingredient_fallback")
        self.assertEqual(empty["mode"], "ingredient_fallback")
        self.assertEqual(greedy["meals"], empty["meals"])
        self.assertEqual(greedy["planned_totals"], empty["planned_totals"])
        self.assertFalse(any(meal.get("recipe_id") for meal in greedy["meals"]))

    def test_agent_today_and_calendar_name(self):
        plan = self._plan()
        slots = _meal_slots({"nutrition_store": {"meal_plan": plan}})
        self.assertEqual(slots[0]["recipe_id"], plan["meals"][0]["recipe_id"])
        self.assertEqual(slots[0]["slot_role"], plan["meals"][0]["slot_role"])
        self.assertTrue(slots[0]["steps"])
        self.assertEqual(meal_display_name(plan["meals"][0]), plan["meals"][0]["recipe_name"])

    def test_null_fat_uses_bodyweight_and_carb_shift_is_optional(self):
        filled = normalize_targets(
            {"calories": 2100, "protein_g": 210, "carbs_g": None, "fat_g": None, "bodyweight_lb": 180}
        )
        self.assertEqual(filled["fat_g"], 54.0)
        self.assertEqual(filled["carbs_g"], 193.5)
        untouched = normalize_targets({"calories": 2100, "protein_g": 210})
        self.assertEqual(untouched["fat_g"], 55)
        self.assertEqual(untouched["carbs_g"], 180)
        shifted = self._plan(workout_window=_gym(MORNING, 12, 13, 30))
        # shift is applied only when the field is present
        moved = generate_recipe_plan(
            self.inv,
            {**TARGETS, "carb_shift_g": 30},
            CONSUMED,
            self.recipes,
            now=MORNING,
            tz_name="America/New_York",
            workout_window=_gym(MORNING, 12, 13, 30),
        )
        self.assertIsNotNone(moved)
        self.assertEqual(moved["targets"]["carbs_g"], 210.0)
        self.assertAlmostEqual(moved["targets"]["fat_g"], 41.7, places=1)
        self.assertNotIn("carb_shift_g", shifted["targets"])

    def test_unlocks_prepend_staples_and_restock_list(self):
        hint = {
            "action": "restock",
            "id": "tilapia",
            "name": "Tilapia",
            "reason": "Tilapia → unlocks Lemon-Garlic Tilapia",
        }
        out = suggest_inventory_staples({"ingredients": []}, recipe_unlocks=[hint])
        self.assertEqual(out["suggestions"][0]["reason"], hint["reason"])
        listed = restock_list([], recipe_unlocks=[hint])
        self.assertIn("Lemon-Garlic Tilapia", listed["items"][0]["reason"])


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Store:
    def __init__(self, rows):
        self.rows = list(rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split()).upper()
        if text.startswith("CREATE"):
            return _Cursor([])
        if text.startswith("SELECT") and "AND ID = ?" in text:
            uid, rid = params
            hit = [row for row in self.rows if row["user_id"] == uid and row["id"] == rid]
            return _Cursor([{"payload": row["payload"]} for row in hit])
        if text.startswith("SELECT"):
            uid = params[0]
            hit = [row for row in self.rows if row["user_id"] == uid]
            return _Cursor([{"id": row["id"], "payload": row["payload"]} for row in hit])
        if "INSERT" in text:
            uid, rid, payload, updated = params
            self.rows = [row for row in self.rows if not (row["user_id"] == uid and row["id"] == rid)]
            self.rows.append(
                {"user_id": uid, "id": rid, "payload": payload, "updated_at": updated}
            )
            return _Cursor([])
        raise AssertionError(text)


class SeedIsIdempotent(unittest.TestCase):
    def test_existing_turso_row_is_not_overwritten(self):
        old = {
            "id": "herb-egg-bites",
            "name": "OLD",
            "yield_servings": 1,
            "ingredients": [{"ingredient_id": "eggs-whole", "grams_batch": 50}, {"ingredient_id": "egg-whites", "grams_batch": 80}],
            "instructions": ["Leave this name alone."],
            "source": "user",
        }
        store = _Store(
            [
                {
                    "user_id": "user-1",
                    "id": "herb-egg-bites",
                    "payload": json.dumps(old),
                    "updated_at": "2026-08-01T00:00:00Z",
                }
            ]
        )
        patches = [
            mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True),
            mock.patch("rt_dashboard.turso_http.connect", return_value=store),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        inv = {"ingredients": []}
        first = seed_nourish_recipes("user-1", inv)
        self.assertNotIn("herb-egg-bites", first["seeded"])
        self.assertTrue(first["seeded"])
        kept = json.loads(next(row["payload"] for row in store.rows if row["id"] == "herb-egg-bites"))
        self.assertEqual(kept["name"], "OLD")
        again = seed_nourish_recipes("user-1", inv)
        self.assertEqual(again["seeded"], [])
        kept_again = json.loads(next(row["payload"] for row in store.rows if row["id"] == "herb-egg-bites"))
        self.assertEqual(kept_again["name"], "OLD")


if __name__ == "__main__":
    unittest.main()
