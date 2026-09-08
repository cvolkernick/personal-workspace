"""#526 Slice B: Add writes a live universe row (not overlay-only)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import available_body, exercise_write
from rt_dashboard.custom_movements import (
    add_library_movement,
    merge_custom_universe,
    missing_inventory_tags,
    require_equipment_access,
    resolve_universe_id,
    upsert_custom_exercise,
)
from rt_dashboard.library_store import apply_library_overlay
from rt_dashboard.workout_planner import normalize_exercise
from rt_dashboard.workout_store import load_workspace_catalog

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _eq(*tags):
    items = []
    for tag in tags:
        items.append(
            {
                "id": tag,
                "name": tag.replace("_", " ").title(),
                "tag": tag,
                "max_weight_lbs": 50 if tag == "dumbbells" else None,
                "source": "owned" if tag == "dumbbells" else "gym",
            }
        )
    return {"items": items}


NORDIC = {
    "name": "Nordic Curl",
    "session_types": ["legs"],
    "primary_muscles": ["hamstrings"],
    "movement": "isolation",
    "equipment": ["machine"],
    "available": True,
}


class MergeUniverse(unittest.TestCase):
    def test_custom_row_enters_before_overlay(self):
        catalog, _ = load_workspace_catalog()
        custom = upsert_custom_exercise({"exercises": []}, NORDIC)
        merged = merge_custom_universe(catalog, custom)
        ids = {e["id"] for e in merged["exercises"] if isinstance(e, dict)}
        self.assertIn("nordic-curl", ids)
        file_ids = {e["id"] for e in catalog["exercises"] if isinstance(e, dict)}
        self.assertNotIn("nordic-curl", file_ids)
        overlay_only = apply_library_overlay(
            catalog, {"enabled": ["nordic-curl"], "disabled": []}
        )
        overlay_ids = {e["id"] for e in overlay_only["exercises"] if isinstance(e, dict)}
        self.assertNotIn("nordic-curl", overlay_ids)
        stamped = apply_library_overlay(merged, {"enabled": ["nordic-curl"], "disabled": []})
        by = {e["id"]: e for e in stamped["exercises"]}
        self.assertTrue(by["nordic-curl"]["available"])
        self.assertEqual(by["nordic-curl"].get("universe"), "custom")

    def test_seed_lying_leg_curl_stays_in_file_universe(self):
        catalog, _ = load_workspace_catalog()
        by = {e["id"]: e for e in catalog["exercises"] if isinstance(e, dict)}
        self.assertIn("lying-leg-curls", by)
        self.assertTrue(by["lying-leg-curls"]["available"])
        self.assertIn("seated-leg-curls", by)
        self.assertTrue(by["seated-leg-curls"]["available"])
        self.assertEqual(
            resolve_universe_id({"name": "Laying Leg Curl"}, catalog),
            "lying-leg-curls",
        )
        self.assertNotEqual(
            resolve_universe_id({"name": "Nordic Curl"}, catalog),
            "seated-leg-curls",
        )

    def test_missing_equipment_tag_is_rejected(self):
        ex = normalize_exercise(NORDIC)
        missing = missing_inventory_tags(ex, _eq("dumbbells"))
        self.assertEqual(missing, ["machine"])
        with self.assertRaises(ValueError) as ctx:
            require_equipment_access(ex, _eq("dumbbells"))
        self.assertIn("machine", str(ctx.exception))
        require_equipment_access(ex, _eq("machine"))


class PersistAdd(unittest.TestCase):
    def _headers(self):
        token = make_session(
            {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
        )
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def _turso_pair(self):
        store = {}

        def get(uid):
            return store.get(uid)

        def put(uid, payload):
            store[uid] = payload

        return store, get, put

    def test_add_persists_custom_universe_row_not_overlay_only(self):
        custom_store, custom_get, custom_put = self._turso_pair()
        overlay_store, overlay_get, overlay_put = self._turso_pair()
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.custom_movements._turso_get", side_effect=custom_get
            ), mock.patch(
                "rt_dashboard.custom_movements._turso_put", side_effect=custom_put
            ), mock.patch(
                "rt_dashboard.library_store._turso_get", side_effect=overlay_get
            ), mock.patch(
                "rt_dashboard.library_store._turso_put", side_effect=overlay_put
            ), mock.patch(
                "rt_dashboard.equipment_store.load_preview_equipment",
                return_value=(_eq("machine", "dumbbells"), "turso"),
            ), mock.patch(
                "rt_dashboard.workout_plan_store.save_last_good_workout_plan"
            ) as save_plan, mock.patch(
                "rt_dashboard.workout_planner.generate_workout_plan"
            ) as gen_plan:
                status, body = exercise_write(self._headers(), dict(NORDIC))
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["write"]["universe"], "custom")
        custom_ids = {
            e["id"] for e in (body["custom"].get("exercises") or []) if isinstance(e, dict)
        }
        self.assertIn("nordic-curl", custom_ids)
        catalog_ids = {
            e["id"] for e in (body["catalog"].get("exercises") or []) if isinstance(e, dict)
        }
        self.assertIn("nordic-curl", catalog_ids)
        by = {e["id"]: e for e in body["catalog"]["exercises"]}
        self.assertTrue(by["nordic-curl"]["available"])
        self.assertEqual(by["nordic-curl"]["name"], "Nordic Curl")
        self.assertIn("nordic-curl", body["library"]["enabled"])
        persisted = custom_store.get("sub-1") or {}
        persisted_ids = {
            e["id"]
            for e in (persisted.get("exercises") or [])
            if isinstance(e, dict)
        }
        self.assertIn("nordic-curl", persisted_ids)
        self.assertIsNone(body.get("plan"))
        self.assertIsNone(body.get("workout"))
        save_plan.assert_not_called()
        gen_plan.assert_not_called()
        names = [
            e["name"]
            for e in body["catalog"]["exercises"]
            if isinstance(e, dict) and e.get("available")
        ]
        self.assertIn("Nordic Curl", names)
        self.assertIn("Lying Leg Curl", names)
        self.assertIn("Seated Leg Curls", names)

    def test_add_rejects_missing_equipment_tag(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.equipment_store.load_preview_equipment",
                return_value=(_eq("dumbbells"), "turso"),
            ):
                status, body = exercise_write(self._headers(), dict(NORDIC))
        self.assertEqual(status, 400, body)
        self.assertIn("machine", str(body.get("error") or ""))
        self.assertIn("inventory", str(body.get("error") or "").lower())

    def test_available_body_lists_custom_id_after_persist(self):
        custom_store, custom_get, custom_put = self._turso_pair()
        overlay_store, overlay_get, overlay_put = self._turso_pair()
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.custom_movements._turso_get", side_effect=custom_get
            ), mock.patch(
                "rt_dashboard.custom_movements._turso_put", side_effect=custom_put
            ), mock.patch(
                "rt_dashboard.library_store._turso_get", side_effect=overlay_get
            ), mock.patch(
                "rt_dashboard.library_store._turso_put", side_effect=overlay_put
            ), mock.patch(
                "rt_dashboard.equipment_store.load_preview_equipment",
                return_value=(_eq("machine"), "turso"),
            ):
                add_library_movement("sub-1", dict(NORDIC))
                status, body = available_body(self._headers())
        self.assertEqual(status, 200, body)
        self.assertIn("Nordic Curl", body["names"])
        by = {e["id"]: e for e in body["catalog"]["exercises"]}
        self.assertTrue(by["nordic-curl"]["available"])


class UiAddForm(unittest.TestCase):
    def test_library_has_add_form(self):
        self.assertIn('id="library-add-form"', HTML)
        self.assertIn('id="lib-add-name"', HTML)
        self.assertIn('id="lib-add-session"', HTML)
        self.assertIn('id="lib-add-muscle"', HTML)
        self.assertIn('id="lib-add-movement"', HTML)
        self.assertIn('id="lib-add-equipment"', HTML)
        self.assertIn("Add movement", HTML)
        catalog = HTML[
            HTML.find('id="exercise-catalog-card"') : HTML.find('id="log-card"')
        ]
        self.assertIn("live custom row", catalog)

    def test_js_posts_exercise_and_refreshes_log_select(self):
        self.assertIn("async function submitLibraryAdd", JS)
        self.assertIn('fetch("/api/workout/exercise"', JS)
        submit = JS.split("async function submitLibraryAdd", 1)[1].split(
            "async function applyLibraryMembership", 1
        )[0]
        self.assertIn("state.workout_store.catalog = data.catalog", submit)
        self.assertIn("renderExerciseCatalog(state.workout_store)", submit)
        self.assertNotIn("/api/workout-plan/generate", submit)
        catalog = JS.split("function renderExerciseCatalog", 1)[1].split(
            "function renderLibrarySuggestions", 1
        )[0]
        self.assertIn("refreshExerciseNameSelects()", catalog)
        self.assertIn("<select class=\"ex-name\" required aria-label=\"Exercise\">", JS)
        self.assertNotIn('<input type="text" class="ex-name"', JS)

    def test_log_copy_points_at_real_add(self):
        log = HTML[HTML.find('id="log-card"') : HTML.find('id="history-card"')]
        self.assertIn("More → Exercise library", log)
        self.assertNotIn("Add a movement there if it is missing", log)


if __name__ == "__main__":
    unittest.main()
