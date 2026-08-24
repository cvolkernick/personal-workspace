"""#229: named pantry SoT (Turso vs inventory.json). No invent food. No Vercel secrets."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body
from api.workout._util import meal_plan_body
from rt_dashboard.grok_planner import generate_grok_plans
from rt_dashboard.inventory_store import (
    FALLBACK_TURSO_DARK,
    INVENTORY_PATH,
    NAMED_INVENTORY_SOTS,
    SOT_FILE,
    SOT_TURSO,
    canonicalize_inventory_source,
    inventory_source_fields,
    load_preview_inventory,
    persist_inventory,
)
from rt_dashboard.models import HealthSnapshot
from rt_dashboard.nutrition_planner import generate_meal_plan

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
DASHBOARD_PY = ROOT / "api" / "dashboard.py"
SERVER_PY = ROOT / "server.py"
VERCEL_JSON = ROOT / "vercel.json"
ENV_EXAMPLE = ROOT / ".env.example"


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class NamedSourceOfTruth(unittest.TestCase):
    def test_named_sots_are_turso_or_file(self):
        self.assertEqual(NAMED_INVENTORY_SOTS, (SOT_TURSO, SOT_FILE))
        self.assertEqual(SOT_FILE, "fitness/nutrition/inventory.json")
        self.assertEqual(SOT_FILE, INVENTORY_PATH)
        self.assertNotIn("unset", NAMED_INVENTORY_SOTS)
        self.assertNotIn("github", NAMED_INVENTORY_SOTS)
        self.assertNotIn("default", NAMED_INVENTORY_SOTS)

    def test_canonicalize_never_unset_or_github(self):
        self.assertEqual(canonicalize_inventory_source("turso"), SOT_TURSO)
        self.assertEqual(canonicalize_inventory_source("turso-default"), SOT_TURSO)
        self.assertEqual(canonicalize_inventory_source(INVENTORY_PATH), SOT_FILE)
        self.assertEqual(canonicalize_inventory_source("default"), SOT_FILE)
        self.assertEqual(canonicalize_inventory_source("github"), SOT_FILE)
        self.assertEqual(canonicalize_inventory_source("unset"), SOT_FILE)
        self.assertEqual(canonicalize_inventory_source(""), SOT_FILE)

    def test_fields_name_sot_and_honest_fallback(self):
        live = inventory_source_fields("turso")
        self.assertEqual(live["inventory"], SOT_TURSO)
        self.assertEqual(live["inventory_sot"], SOT_TURSO)
        self.assertIsNone(live["inventory_fallback"])
        dark = inventory_source_fields(INVENTORY_PATH)
        self.assertEqual(dark["inventory"], SOT_FILE)
        self.assertEqual(dark["inventory_sot"], SOT_FILE)
        self.assertEqual(dark["inventory_fallback"], FALLBACK_TURSO_DARK)
        self.assertEqual(inventory_source_fields("unset")["inventory_sot"], SOT_FILE)


class SameReadPath(unittest.TestCase):
    def test_public_and_pi_call_load_preview_inventory(self):
        dash = DASHBOARD_PY.read_text(encoding="utf-8")
        server = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn("load_preview_inventory", dash)
        self.assertIn("inventory_source_fields", dash)
        self.assertIn("load_preview_inventory", server)
        self.assertIn("inventory_source_fields", server)
        self.assertIn("persist_inventory", server)
        # Pi dashboard overlay must not keep GitHub/local as pantry SoT.
        overlay = server.split("Named pantry SoT", 1)[1].split("Cached remote layers", 1)[0]
        self.assertIn('nut["inventory"] = inv', overlay)
        self.assertIn("inventory_source_fields", overlay)

    def test_turso_live_is_named_turso(self):
        stored = {"ingredients": [{"id": "oats", "name": "Oats", "in_stock": True}]}
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_get_inventory",
            return_value=stored,
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_put_inventory",
        ) as put:
            inv, src = load_preview_inventory("sub-1")
        fields = inventory_source_fields(src)
        self.assertEqual(src, SOT_TURSO)
        self.assertEqual(fields["inventory_sot"], SOT_TURSO)
        self.assertIsNone(fields["inventory_fallback"])
        self.assertEqual(inv["ingredients"][0]["name"], "Oats")
        put.assert_not_called()

    def test_turso_dark_is_named_file_fallback(self):
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False):
            inv, src = load_preview_inventory("sub-1")
        fields = inventory_source_fields(src)
        self.assertEqual(src, SOT_FILE)
        self.assertEqual(fields["inventory_sot"], SOT_FILE)
        self.assertEqual(fields["inventory_fallback"], FALLBACK_TURSO_DARK)
        self.assertGreaterEqual(len(inv.get("ingredients") or []), 1)

    def test_persist_turso_does_not_write_file(self):
        puts = []

        def put(uid, inv):
            puts.append((uid, inv))

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_put_inventory",
            side_effect=put,
        ), mock.patch(
            "rt_dashboard.inventory_store._turso_get_inventory",
            side_effect=lambda uid: puts[-1][1] if puts else None,
        ), mock.patch(
            "rt_dashboard.nutrition_store.write_nutrition_file",
        ) as file_write:
            out = persist_inventory(
                {"ingredients": [{"id": "oats", "name": "Oats"}]},
                "sub-1",
                file_client=object(),
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], SOT_TURSO)
        self.assertEqual(len(puts), 1)
        file_write.assert_not_called()

    def test_persist_file_when_turso_dark(self):
        writes = []

        def fake_write(client, rel, data, message=""):
            writes.append((rel, data, message))
            return {"path": rel, "local": True, "github": False}

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=False
        ), mock.patch(
            "rt_dashboard.nutrition_store.write_nutrition_file",
            side_effect=fake_write,
        ):
            out = persist_inventory(
                {"ingredients": [{"id": "oats", "name": "Oats"}]},
                "sub-1",
                file_client=object(),
                message="nutrition: test",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], SOT_FILE)
        self.assertEqual(writes[0][0], INVENTORY_PATH)


class EmptyPantryBlocksMealInvent(unittest.TestCase):
    def test_planner_empty_inventory_has_no_food(self):
        plan = generate_meal_plan(
            {"ingredients": []},
            {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
            {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        )
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["meals"], [])
        self.assertTrue(plan.get("pantry_dark"))
        self.assertEqual(plan.get("message"), "Pantry unavailable")
        blob = json.dumps(plan).lower()
        self.assertNotIn("chicken", blob)
        self.assertNotIn("unicorn", blob)
        self.assertNotIn("supergrok", blob)

    def test_grok_empty_inventory_drops_invented_meal(self):
        def fake_chat(messages, **kwargs):
            return {
                "answer": json.dumps(
                    {
                        "meal": {
                            "message": "invented",
                            "items": [
                                {"name": "Unicorn steak", "calories": 900},
                                {"name": "Chicken breast", "calories": 280},
                            ],
                            "meals": [
                                {
                                    "label": "Next meal",
                                    "items": [{"name": "Unicorn steak"}],
                                }
                            ],
                        },
                        "workout": {
                            "session_type": "push",
                            "is_rest_day": False,
                            "message": "ok",
                            "exercises": [],
                        },
                    }
                ),
                "model": "grok-test",
                "auth_source": "supergrok_session",
            }

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={
                "token": "user-token-must-not-leak",
                "source": "supergrok_session",
                "expired": False,
            },
        ), mock.patch(
            "rt_dashboard.grok_ask.chat_completions",
            side_effect=fake_chat,
        ):
            out = generate_grok_plans("user-1", inventory={"ingredients": []})
        meal = out["meal"]
        self.assertEqual(meal["items"], [])
        self.assertEqual(meal["meals"], [])
        self.assertTrue(meal.get("pantry_dark"))
        self.assertEqual(meal.get("message"), "Pantry unavailable")
        blob = json.dumps(meal).lower()
        self.assertNotIn("unicorn", blob)
        self.assertNotIn("chicken", blob)
        self.assertNotIn("user-token-must-not-leak", json.dumps(out))

    def test_grok_missing_inventory_does_not_invent_from_logs(self):
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return {
                "answer": json.dumps(
                    {
                        "meal": {
                            "message": "from logs",
                            "items": [{"name": "Logged pizza", "calories": 800}],
                            "meals": [],
                        },
                        "workout": {
                            "session_type": "pull",
                            "is_rest_day": False,
                            "message": "ok",
                            "exercises": [],
                        },
                    }
                ),
                "model": "grok-test",
                "auth_source": "supergrok_session",
            }

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={
                "token": "t",
                "source": "supergrok_session",
                "expired": False,
            },
        ), mock.patch(
            "rt_dashboard.grok_ask.chat_completions",
            side_effect=fake_chat,
        ):
            out = generate_grok_plans(
                "user-1",
                food_logs_today=[{"name": "Logged pizza", "calories": 800}],
            )
        user = captured["messages"][1]["content"]
        self.assertIn("PANTRY IS EMPTY OR MISSING", user)
        self.assertNotIn("Meal ideas may use remaining macros", user)
        meal = out["meal"]
        self.assertEqual(meal["items"], [])
        self.assertTrue(meal.get("pantry_dark"))
        self.assertNotIn("pizza", json.dumps(meal).lower())

    def test_dashboard_empty_pantry_names_sot_and_blocks_plan(self):
        empty = {"updated_at": "2026-08-24", "ingredients": []}
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
                return_value=(empty, SOT_TURSO),
            ):
                status, body = dashboard_body(_headers())
                meal_status, meal_body = meal_plan_body(_headers())
        self.assertEqual(status, 200)
        nut = body["nutrition_store"]
        self.assertEqual(nut["sources"]["inventory_sot"], SOT_TURSO)
        self.assertIsNone(nut["sources"]["inventory_fallback"])
        meal = nut["meal_plan"]
        self.assertEqual(meal.get("items"), [])
        self.assertEqual(meal.get("meals"), [])
        self.assertTrue(meal.get("pantry_dark"))
        self.assertEqual(meal_status, 200)
        self.assertEqual(meal_body["sources"]["inventory_sot"], SOT_TURSO)
        self.assertEqual(meal_body["plan"]["items"], [])


class VercelPathSeesNoSecrets(unittest.TestCase):
    def test_vercel_json_has_no_tasks_or_pantry_secrets(self):
        raw = VERCEL_JSON.read_text(encoding="utf-8")
        cfg = json.loads(raw)
        self.assertNotIn("env", cfg)
        self.assertNotIn("GOOGLE_TASKS_", raw)
        self.assertNotIn("TURSO_AUTH_TOKEN", raw)
        self.assertNotIn("TURSO_DATABASE_URL", raw)
        self.assertNotIn("FITDASH_MASTER_KEY", raw)

    def test_env_example_does_not_export_tasks_or_turso_secrets(self):
        raw = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertNotIn("GOOGLE_TASKS_", raw)
        self.assertNotIn("TURSO_AUTH_TOKEN", raw)
        self.assertNotIn("TURSO_DATABASE_URL", raw)
        self.assertNotIn("1//", raw)

    def test_client_bundle_has_no_secrets(self):
        blob = ""
        for path in (
            ROOT / "static" / "app.js",
            ROOT / "static" / "index.html",
            ROOT / "static" / "meal-snapshot.js",
            ROOT / "static" / "supergrok.js",
        ):
            blob += path.read_text(encoding="utf-8")
        for needle in (
            "GOOGLE_TASKS_",
            "TURSO_AUTH_TOKEN",
            "TURSO_DATABASE_URL",
            "FITDASH_MASTER_KEY",
            "BEGIN RSA",
        ):
            self.assertNotIn(needle, blob)

    def test_dashboard_payload_does_not_leak_secrets(self):
        env = {
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "TURSO_AUTH_TOKEN": "turso-secret-must-not-leak",
            "TURSO_DATABASE_URL": "libsql://secret.example",
            "GOOGLE_TASKS_REFRESH_TOKEN": "1//tasks-secret",
            "GOOGLE_TASKS_CLIENT_SECRET": "gt-secret",
        }
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
                status, body = dashboard_body(_headers())
        self.assertEqual(status, 200)
        dumped = json.dumps(body)
        self.assertNotIn("turso-secret-must-not-leak", dumped)
        self.assertNotIn("libsql://secret.example", dumped)
        self.assertNotIn("1//tasks-secret", dumped)
        self.assertNotIn("gt-secret", dumped)
        self.assertNotIn("GOOGLE_TASKS_", dumped)
        self.assertNotIn("TURSO_AUTH_TOKEN", dumped)
        src = body["nutrition_store"]["sources"]
        self.assertIn(src["inventory_sot"], NAMED_INVENTORY_SOTS)


if __name__ == "__main__":
    unittest.main()
