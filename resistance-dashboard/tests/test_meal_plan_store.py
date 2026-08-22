"""Last-good meal_plan persist: user+local_today key, honest empty, no invented food."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body
from rt_dashboard.meal_plan_store import (
    MSG_NO_IN_STOCK,
    MSG_PANTRY_UNAVAILABLE,
    apply_honest_empty_copy,
    is_good_meal_plan,
    load_last_good_meal_plan,
    persist_key,
    resolve_dashboard_meal_plan,
    save_last_good_meal_plan,
)
from rt_dashboard.models import HealthSnapshot
from rt_dashboard.nutrition_planner import generate_meal_plan

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SNAPSHOT = (ROOT / "static" / "meal-snapshot.js").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")

STOCKED = {
    "ingredients": [
        {
            "id": "chicken",
            "name": "Chicken",
            "calories": 280,
            "protein_g": 52,
            "carbs_g": 0,
            "fat_g": 6,
            "in_stock": True,
            "serving_label": "6 oz",
        }
    ]
}
OOS = {
    "ingredients": [
        {
            "id": "chicken",
            "name": "Chicken",
            "calories": 280,
            "protein_g": 52,
            "carbs_g": 0,
            "fat_g": 6,
            "in_stock": False,
        }
    ]
}
GOOD = {
    "meals": [
        {
            "label": "Next meal",
            "items": [{"id": "chicken", "name": "Chicken", "calories": 280, "protein_g": 52}],
        }
    ],
    "items": [{"id": "chicken", "name": "Chicken", "calories": 280, "protein_g": 52}],
    "stocked_count": 1,
    "in_stock_only": True,
    "message": "Plan from 1 in-stock ingredient only (out-of-stock excluded).",
    "remaining_before_plan": {"calories": 800, "protein_g": 80, "carbs_g": 100, "fat_g": 30},
}
EMPTY_STOCKED = {
    "meals": [],
    "items": [],
    "stocked_count": 1,
    "pantry_dark": False,
    "in_stock_only": True,
    "message": "Could not fit more servings without exceeding soft calorie ceiling (in-stock only).",
    "remaining_before_plan": {"calories": 800, "protein_g": 80, "carbs_g": 100, "fat_g": 30},
}
EMPTY_FULL = {
    "meals": [],
    "items": [],
    "stocked_count": 1,
    "pantry_dark": False,
    "in_stock_only": True,
    "message": "Day essentially complete — only ~40 kcal and 8g protein left under target; no extra servings planned.",
    "remaining_before_plan": {"calories": 40, "protein_g": 8, "carbs_g": 10, "fat_g": 2},
}


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class HonestEmptyCopy(unittest.TestCase):
    def test_planner_pantry_dark_vs_no_stock(self):
        dark = generate_meal_plan(
            {"ingredients": []},
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        self.assertTrue(dark.get("pantry_dark"))
        self.assertEqual(dark["stocked_count"], 0)
        self.assertEqual(dark["message"], MSG_PANTRY_UNAVAILABLE)
        self.assertEqual(dark["items"], [])
        self.assertEqual(dark["meals"], [])

        oos = generate_meal_plan(
            OOS,
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        self.assertFalse(oos.get("pantry_dark"))
        self.assertEqual(oos["stocked_count"], 0)
        self.assertEqual(oos["message"], MSG_NO_IN_STOCK)
        self.assertEqual(oos["items"], [])

    def test_apply_honest_empty_never_invents(self):
        out = apply_honest_empty_copy({"items": [], "meals": []}, {"ingredients": []})
        self.assertEqual(out["message"], MSG_PANTRY_UNAVAILABLE)
        out = apply_honest_empty_copy(
            {"items": [], "meals": [], "stocked_count": 0}, OOS
        )
        self.assertEqual(out["message"], MSG_NO_IN_STOCK)


class PersistKeyAndTurso(unittest.TestCase):
    def test_key_is_user_and_civil_day(self):
        key = persist_key("sub-1", "2026-08-22T15:04:05-04:00")
        self.assertEqual(key, {"user_id": "sub-1", "local_today": "2026-08-22"})

    def test_save_fails_honest_without_turso(self):
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False):
            out = save_last_good_meal_plan("sub-1", "2026-08-22", GOOD)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "turso env missing")
        self.assertEqual(out["store"], "turso")
        self.assertEqual(out["key"]["user_id"], "sub-1")
        self.assertEqual(out["key"]["local_today"], "2026-08-22")

    def test_save_skips_empty_plan(self):
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True):
            out = save_last_good_meal_plan("sub-1", "2026-08-22", EMPTY_STOCKED)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "not a good meal_plan")

    def test_save_and_load_same_user_day_only(self):
        rows = {}

        def put(uid, day, plan):
            rows[(uid, day)] = dict(plan)

        def get(uid, day):
            return rows.get((uid, day))

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.meal_plan_store._turso_put_meal_plan", side_effect=put
        ), mock.patch(
            "rt_dashboard.meal_plan_store._turso_get_meal_plan", side_effect=get
        ):
            saved = save_last_good_meal_plan("sub-1", "2026-08-22", GOOD)
            self.assertTrue(saved["ok"])
            same = load_last_good_meal_plan("sub-1", "2026-08-22")
            self.assertTrue(is_good_meal_plan(same))
            self.assertEqual(same["items"][0]["name"], "Chicken")
            self.assertIsNone(load_last_good_meal_plan("sub-1", "2026-08-21"))
            self.assertIsNone(load_last_good_meal_plan("other-user", "2026-08-22"))


class ResolveLastGood(unittest.TestCase):
    def test_good_generate_persists_and_keeps_items(self):
        puts = []

        def put(uid, day, plan):
            puts.append((uid, day, plan))

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.meal_plan_store._turso_put_meal_plan", side_effect=put
        ), mock.patch(
            "rt_dashboard.meal_plan_store._turso_get_meal_plan",
            side_effect=lambda uid, day: GOOD,
        ):
            out = resolve_dashboard_meal_plan("sub-1", "2026-08-22", GOOD, STOCKED)
        self.assertEqual(out["source"], "generate")
        self.assertTrue(out["persist"]["ok"])
        self.assertEqual(puts[0][0], "sub-1")
        self.assertEqual(puts[0][1], "2026-08-22")
        self.assertEqual(out["items"][0]["name"], "Chicken")

    def test_restore_last_good_when_generate_empty_but_stocked(self):
        with mock.patch(
            "rt_dashboard.meal_plan_store.load_last_good_meal_plan",
            return_value=GOOD,
        ), mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ):
            out = resolve_dashboard_meal_plan(
                "sub-1", "2026-08-22", EMPTY_STOCKED, STOCKED
            )
        self.assertEqual(out["source"], "last_good")
        self.assertEqual(out["items"][0]["name"], "Chicken")
        self.assertEqual(out["persist_key"]["user_id"], "sub-1")
        self.assertEqual(out["persist_key"]["local_today"], "2026-08-22")

    def test_does_not_restore_when_pantry_dark(self):
        with mock.patch(
            "rt_dashboard.meal_plan_store.load_last_good_meal_plan",
            return_value=GOOD,
        ):
            out = resolve_dashboard_meal_plan(
                "sub-1", "2026-08-22", EMPTY_STOCKED, {"ingredients": []}
            )
        self.assertNotEqual(out.get("source"), "last_good")
        self.assertEqual(out["message"], MSG_PANTRY_UNAVAILABLE)
        self.assertFalse(is_good_meal_plan(out))

    def test_does_not_restore_when_stocked_count_zero(self):
        empty_oos = {**EMPTY_STOCKED, "stocked_count": 0}
        with mock.patch(
            "rt_dashboard.meal_plan_store.load_last_good_meal_plan",
            return_value=GOOD,
        ):
            out = resolve_dashboard_meal_plan("sub-1", "2026-08-22", empty_oos, OOS)
        self.assertEqual(out["message"], MSG_NO_IN_STOCK)
        self.assertFalse(is_good_meal_plan(out))

    def test_keeps_remaining_macros_full_message(self):
        with mock.patch(
            "rt_dashboard.meal_plan_store.load_last_good_meal_plan",
            return_value=GOOD,
        ):
            out = resolve_dashboard_meal_plan("sub-1", "2026-08-22", EMPTY_FULL, STOCKED)
        self.assertEqual(out["source"], "generate")
        self.assertIn("Day essentially complete", out["message"])
        self.assertFalse(is_good_meal_plan(out))

    def test_clamp_drops_invented_restore(self):
        invented = {
            "items": [{"name": "Unicorn steak", "calories": 900}],
            "meals": [{"label": "Next meal", "items": [{"name": "Unicorn steak"}]}],
            "stocked_count": 1,
        }
        with mock.patch(
            "rt_dashboard.meal_plan_store.load_last_good_meal_plan",
            return_value=invented,
        ):
            out = resolve_dashboard_meal_plan(
                "sub-1", "2026-08-22", EMPTY_STOCKED, STOCKED
            )
        self.assertFalse(is_good_meal_plan(out))
        self.assertEqual(out["source"], "generate")


class DashboardPersistKey(unittest.TestCase):
    def test_signed_in_get_exposes_user_day_key(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                status, body = dashboard_body(_headers(), "tz=America/New_York")
        self.assertEqual(status, 200)
        meal = (body.get("nutrition_store") or {}).get("meal_plan") or {}
        key = meal.get("persist_key") or (meal.get("persist") or {}).get("key") or {}
        self.assertEqual(key.get("user_id"), "sub-1")
        self.assertEqual(key.get("local_today"), body["meta"]["local_today"])
        self.assertTrue(is_good_meal_plan(meal) or meal.get("pantry_dark") or meal.get("stocked_count") == 0)
        persist = meal.get("persist") or {}
        if is_good_meal_plan(meal):
            self.assertFalse(persist.get("ok"))
            self.assertEqual(persist.get("error"), "turso env missing")

    def test_empty_pantry_dashboard_is_pantry_unavailable(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.inventory_store.load_preview_inventory",
                return_value=({"ingredients": []}, "test-empty"),
            ), mock.patch(
                "rt_dashboard.meal_plan_store.load_last_good_meal_plan",
                return_value=GOOD,
            ):
                status, body = dashboard_body(_headers())
        self.assertEqual(status, 200)
        meal = (body.get("nutrition_store") or {}).get("meal_plan") or {}
        self.assertEqual(meal.get("message"), MSG_PANTRY_UNAVAILABLE)
        self.assertEqual(meal.get("items"), [])
        today = ((body.get("coach") or {}).get("today") or {}).get("meal") or {}
        self.assertEqual(today.get("message"), MSG_PANTRY_UNAVAILABLE)
        self.assertEqual(today.get("empty_reason"), "pantry_unavailable")


class OverlayAndHobbyLock(unittest.TestCase):
    def test_generate_meal_control_and_honest_copy(self):
        self.assertIn('id="btn-generate-meal"', HTML)
        self.assertIn("Generate meal", HTML)
        self.assertIn("window.generateMealPlan = generatePlan", JS)
        self.assertIn("Pantry unavailable", JS)
        self.assertIn("No in-stock items", JS)
        self.assertNotIn("check in-stock inventory", JS)
        self.assertNotIn("restock staples below, then refresh", JS)
        self.assertIn("btn-generate-meal", SNAPSHOT)
        self.assertIn("/api/meal-plan/generate", SNAPSHOT)
        self.assertIn("Pantry unavailable", SNAPSHOT)
        self.assertIn("No in-stock items", SNAPSHOT)

    def test_app_js_stays_at_least_180kb(self):
        self.assertGreaterEqual((ROOT / "static" / "app.js").stat().st_size, 180_000)

    def test_no_new_serverless_function(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])
        self.assertFalse((ROOT / "api" / "meal-plan.py").exists())
        self.assertFalse((ROOT / "api" / "meal_plan.py").exists())

    def test_ignore_build_unchanged(self):
        self.assertIn(
            '"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"',
            VERCEL,
        )


if __name__ == "__main__":
    unittest.main()
