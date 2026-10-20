"""Compound lifts keep primary and secondary muscles (#1299).

Forearms stay a stored tag. They are not a 15th volume group and they are
not credited. Logged set rows are not rewritten.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import exercise_write
from rt_dashboard.custom_movements import (
    load_custom_movements,
    merge_custom_universe,
)
from rt_dashboard.deadlift_muscle_migrate import (
    deadlift_kind,
    retag_custom_movements,
)
from rt_dashboard.muscle_sets import (
    VOLUME_GROUPS,
    credit_proper_sets,
    volume_groups,
    week_group_credits,
)
from rt_dashboard.workout_store import load_workspace_catalog

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

BARBELL_PRIMARY = ["glutes", "hamstrings", "lower_back"]
BARBELL_SECONDARY = ["quads", "lats_upper_back", "traps", "forearms"]
BARBELL_VOLUME_SECONDARY = ["quads", "lats_upper_back", "traps"]
TRAP_PRIMARY = ["glutes", "hamstrings", "lower_back", "quads"]
TRAP_SECONDARY = ["lats_upper_back", "traps", "forearms"]
TRAP_VOLUME_SECONDARY = ["lats_upper_back", "traps"]


def _eq(*tags):
    return {
        "items": [
            {
                "id": tag,
                "name": tag.replace("_", " ").title(),
                "tag": tag,
                "source": "owned",
            }
            for tag in tags
        ]
    }


def _logged_deadlift():
    return {
        "date": "2026-10-19",
        "session_type": "legs",
        "exercises": [
            {
                "name": "Barbell Deadlift",
                "sets": [{"weight_lbs": 95, "sets": 1, "reps": 10}],
            }
        ],
    }


class CatalogDeadliftRows(unittest.TestCase):
    def _catalog(self):
        root = REPO / "fitness" / "exercises" / "catalog.json"
        bundled = ROOT / "fitness" / "exercises" / "catalog.json"
        self.assertEqual(root.read_text(encoding="utf-8"), bundled.read_text(encoding="utf-8"))
        return json.loads(root.read_text(encoding="utf-8"))

    def test_deadlift_rows_credit_primary_and_secondary(self):
        catalog = self._catalog()
        self.assertEqual(catalog["updated_at"], "2026-10-16")
        by_id = {row["id"]: row for row in catalog["exercises"]}
        self.assertEqual(len(by_id), 48)
        self.assertNotIn("forearms", VOLUME_GROUPS)
        barbell = by_id["barbell-deadlift"]
        trap = by_id["trap-bar-deadlift"]
        self.assertEqual(barbell["name"], "Barbell Deadlift")
        self.assertEqual(barbell["primary_muscles"], BARBELL_PRIMARY)
        self.assertEqual(barbell["secondary_muscles"], BARBELL_SECONDARY)
        self.assertEqual(barbell["volume_primary"], BARBELL_PRIMARY)
        self.assertEqual(barbell["volume_secondary"], BARBELL_VOLUME_SECONDARY)
        self.assertEqual(barbell["default_sets"], 3)
        self.assertTrue(barbell["available"])
        self.assertEqual(trap["primary_muscles"], TRAP_PRIMARY)
        self.assertEqual(trap["secondary_muscles"], TRAP_SECONDARY)
        self.assertEqual(trap["volume_primary"], TRAP_PRIMARY)
        self.assertEqual(trap["volume_secondary"], TRAP_VOLUME_SECONDARY)
        self.assertEqual(by_id["rdl"]["secondary_muscles"], ["lower_back"])
        self.assertEqual(by_id["rdl"]["volume_primary"], ["hamstrings", "glutes"])
        self.assertEqual(by_id["rdl"]["volume_secondary"], ["lower_back"])
        for row in (barbell, trap):
            primary, secondary = volume_groups(row)
            credits = credit_proper_sets(primary, secondary, 1)
            self.assertEqual(credits.keys() >= set(row["volume_primary"]), True)
            for group in row["volume_primary"]:
                self.assertEqual(credits[group], 1.0)
            for group in row["volume_secondary"]:
                self.assertEqual(credits[group], 0.5)
            self.assertNotIn("forearms", credits)
            self.assertNotIn("forearms", primary + secondary)

    def test_logged_barbell_deadlift_uses_the_new_tags(self):
        catalog, _src = load_workspace_catalog()
        session = _logged_deadlift()
        before = json.dumps(session)
        credits, proper = week_group_credits(
            [session], catalog, week_start="2026-10-19", as_of="2026-10-20"
        )
        self.assertEqual(json.dumps(session), before)
        self.assertEqual(proper, 1)
        for group in BARBELL_PRIMARY:
            self.assertEqual(credits[group], 1.0)
        for group in BARBELL_VOLUME_SECONDARY:
            self.assertEqual(credits[group], 0.5)
        self.assertEqual(credits.get("forearms", 0.0), 0.0)
        self.assertNotIn("forearms", credits)


class CustomDeadliftBackfill(unittest.TestCase):
    def test_glutes_only_custom_row_is_retagged_and_rerun_is_a_noop(self):
        custom = {
            "exercises": [
                {
                    "id": "custom-barbell-deadlift",
                    "name": "Barbell Deadlift",
                    "primary_muscles": ["glutes"],
                    "secondary_muscles": [],
                    "movement": "compound",
                },
                {
                    "id": "rdl",
                    "name": "RDL",
                    "primary_muscles": ["hamstrings", "glutes"],
                    "secondary_muscles": ["lower_back"],
                },
                {
                    "id": "custom-hex",
                    "name": "Hex Bar Deadlift",
                    "primary_muscles": ["glutes"],
                    "secondary_muscles": [],
                },
            ]
        }
        session = _logged_deadlift()
        before_session = json.dumps(session)
        updated, changed = retag_custom_movements(custom)
        self.assertTrue(changed)
        self.assertEqual(json.dumps(session), before_session)
        by_id = {row["id"]: row for row in updated["exercises"]}
        barbell = by_id["custom-barbell-deadlift"]
        self.assertEqual(barbell["name"], "Barbell Deadlift")
        self.assertEqual(barbell["primary_muscles"], BARBELL_PRIMARY)
        self.assertEqual(barbell["secondary_muscles"], BARBELL_SECONDARY)
        self.assertEqual(barbell["volume_secondary"], BARBELL_VOLUME_SECONDARY)
        self.assertNotIn("forearms", barbell["volume_secondary"])
        hex_row = by_id["custom-hex"]
        self.assertEqual(hex_row["name"], "Hex Bar Deadlift")
        self.assertEqual(hex_row["primary_muscles"], TRAP_PRIMARY)
        self.assertEqual(hex_row["volume_primary"], TRAP_PRIMARY)
        self.assertEqual(by_id["rdl"]["secondary_muscles"], ["lower_back"])
        again, changed_again = retag_custom_movements(updated)
        self.assertFalse(changed_again)
        self.assertIs(again, updated)

    def test_rdl_and_stiff_leg_names_are_not_deadlifts(self):
        self.assertIsNone(deadlift_kind("RDL"))
        self.assertIsNone(deadlift_kind("Romanian Deadlift"))
        self.assertIsNone(deadlift_kind("Stiff-Leg Deadlift"))
        self.assertEqual(deadlift_kind("Deadlift"), "conventional")
        self.assertEqual(deadlift_kind("Trap-Bar Deadlift"), "trap")
        payload = {
            "exercises": [
                {
                    "id": "rdl",
                    "name": "Romanian Deadlift",
                    "primary_muscles": ["hamstrings"],
                    "secondary_muscles": ["back"],
                }
            ]
        }
        updated, changed = retag_custom_movements(payload)
        self.assertFalse(changed)
        self.assertEqual(updated["exercises"][0]["secondary_muscles"], ["back"])

    def test_custom_name_wins_until_the_backfill(self):
        catalog, _src = load_workspace_catalog()
        stale = {
            "exercises": [
                {
                    "id": "custom-barbell-deadlift",
                    "name": "Barbell Deadlift",
                    "primary_muscles": ["glutes"],
                    "secondary_muscles": [],
                    "movement": "compound",
                    "available": True,
                }
            ]
        }
        merged = merge_custom_universe(catalog, stale)
        credits, _proper = week_group_credits(
            [_logged_deadlift()],
            merged,
            week_start="2026-10-19",
            as_of="2026-10-20",
        )
        self.assertEqual(credits["glutes"], 1.0)
        self.assertEqual(credits["hamstrings"], 0.0)
        updated, changed = retag_custom_movements(stale)
        self.assertTrue(changed)
        retagged = merge_custom_universe(catalog, updated)
        credits, proper = week_group_credits(
            [_logged_deadlift()],
            retagged,
            week_start="2026-10-19",
            as_of="2026-10-20",
        )
        self.assertEqual(proper, 1)
        self.assertEqual(credits["hamstrings"], 1.0)
        self.assertEqual(credits["quads"], 0.5)
        self.assertNotIn("forearms", credits)

    def test_load_puts_once_and_leaves_the_logged_set_alone(self):
        logged = _logged_deadlift()
        before = json.dumps(logged)
        store = {
            "sub-1": {
                "exercises": [
                    {
                        "id": "custom-barbell-deadlift",
                        "name": "Barbell Deadlift",
                        "primary_muscles": ["Glutes"],
                        "secondary_muscles": [],
                        "movement": "compound",
                    }
                ]
            }
        }
        puts = []

        def get(uid):
            return store.get(uid)

        def put(uid, payload):
            puts.append(json.dumps(payload, sort_keys=True))
            store[uid] = payload

        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True), mock.patch(
            "rt_dashboard.custom_movements._turso_get", side_effect=get
        ), mock.patch("rt_dashboard.custom_movements._turso_put", side_effect=put):
            loaded, src = load_custom_movements("sub-1")
            self.assertEqual(src, "turso")
            self.assertEqual(len(puts), 1)
            loaded_again, _src = load_custom_movements("sub-1")
        self.assertEqual(len(puts), 1)
        by_id = {row["id"]: row for row in loaded["exercises"]}
        self.assertEqual(by_id["custom-barbell-deadlift"]["primary_muscles"], BARBELL_PRIMARY)
        self.assertEqual(
            {row["id"]: row["primary_muscles"] for row in loaded_again["exercises"]},
            {row["id"]: row["primary_muscles"] for row in loaded["exercises"]},
        )
        self.assertEqual(json.dumps(logged), before)
        self.assertNotIn("sessions", store["sub-1"])


class ExerciseWriteMuscles(unittest.TestCase):
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

    def _patches(self, custom_get, custom_put, overlay_get, overlay_put):
        return (
            mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True),
            mock.patch("rt_dashboard.custom_movements._turso_get", side_effect=custom_get),
            mock.patch("rt_dashboard.custom_movements._turso_put", side_effect=custom_put),
            mock.patch("rt_dashboard.library_store._turso_get", side_effect=overlay_get),
            mock.patch("rt_dashboard.library_store._turso_put", side_effect=overlay_put),
            mock.patch(
                "rt_dashboard.equipment_store.load_preview_equipment",
                return_value=(_eq("barbell"), "turso"),
            ),
        )

    def test_post_persists_primary_secondary_and_derived_volume(self):
        custom_store, custom_get, custom_put = self._turso_pair()
        _overlay_store, overlay_get, overlay_put = self._turso_pair()
        body_in = {
            "name": "Barbell Deadlift",
            "session_types": ["legs"],
            "primary_muscles": ["glutes", "hamstrings", "lower_back"],
            "secondary_muscles": ["quads", "lats_upper_back", "traps", "forearms"],
            "volume_primary": ["chest"],
            "volume_secondary": ["forearms"],
            "movement": "compound",
            "equipment": ["barbell"],
        }
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        stacks = self._patches(custom_get, custom_put, overlay_get, overlay_put)
        with mock.patch.dict(os.environ, env, clear=True):
            with stacks[0], stacks[1], stacks[2], stacks[3], stacks[4], stacks[5]:
                status, body = exercise_write(self._headers(), body_in)
        self.assertEqual(status, 200, body)
        saved = body["exercise"]
        self.assertEqual(saved["primary_muscles"], BARBELL_PRIMARY)
        self.assertEqual(saved["secondary_muscles"], BARBELL_SECONDARY)
        self.assertEqual(saved["volume_primary"], BARBELL_PRIMARY)
        self.assertEqual(saved["volume_secondary"], BARBELL_VOLUME_SECONDARY)
        self.assertNotIn("forearms", saved["volume_primary"] + saved["volume_secondary"])
        persisted = custom_store["sub-1"]["exercises"]
        self.assertEqual(persisted[0]["volume_secondary"], BARBELL_VOLUME_SECONDARY)

    def test_edit_by_id_updates_muscles(self):
        custom_store, custom_get, custom_put = self._turso_pair()
        _overlay_store, overlay_get, overlay_put = self._turso_pair()
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        first = {
            "name": "Barbell Deadlift",
            "session_types": ["legs"],
            "primary_muscles": ["glutes"],
            "secondary_muscles": [],
            "movement": "compound",
            "equipment": ["barbell"],
        }
        with mock.patch.dict(os.environ, env, clear=True):
            stacks = self._patches(custom_get, custom_put, overlay_get, overlay_put)
            with stacks[0], stacks[1], stacks[2], stacks[3], stacks[4], stacks[5]:
                status, body = exercise_write(self._headers(), first)
                self.assertEqual(status, 200, body)
                eid = body["exercise"]["id"]
                status, body = exercise_write(
                    self._headers(),
                    {
                        "id": eid,
                        "name": "Barbell Deadlift",
                        "session_types": ["legs"],
                        "primary_muscles": BARBELL_PRIMARY,
                        "secondary_muscles": BARBELL_SECONDARY,
                        "movement": "compound",
                        "equipment": ["barbell"],
                    },
                )
        self.assertEqual(status, 200, body)
        rows = [row for row in custom_store["sub-1"]["exercises"] if row["name"] == "Barbell Deadlift"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], eid)
        self.assertEqual(rows[0]["primary_muscles"], BARBELL_PRIMARY)
        self.assertEqual(rows[0]["secondary_muscles"], BARBELL_SECONDARY)
        self.assertEqual(rows[0]["volume_secondary"], BARBELL_VOLUME_SECONDARY)

    def test_overlap_and_empty_primary_are_rejected(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        overlap = {
            "name": "Barbell Deadlift",
            "primary_muscles": ["glutes", "hamstrings"],
            "secondary_muscles": ["glutes", "quads"],
            "movement": "compound",
        }
        empty = {
            "name": "Barbell Deadlift",
            "primary_muscles": [],
            "secondary_muscles": ["quads"],
            "movement": "compound",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = exercise_write(self._headers(), overlap)
            self.assertEqual(status, 400, body)
            self.assertIn("overlap", str(body.get("error") or ""))
            status, body = exercise_write(self._headers(), empty)
            self.assertEqual(status, 400, body)
            self.assertIn("primary_muscles", str(body.get("error") or ""))


class LibraryForm(unittest.TestCase):
    def test_form_picks_many_primaries_and_secondaries(self):
        self.assertIn('id="lib-add-muscle"', HTML)
        self.assertIn('id="lib-add-secondary"', HTML)
        self.assertIn('id="lib-add-id"', HTML)
        self.assertIn('value="lower_back"', HTML)
        self.assertIn('value="forearms"', HTML)
        self.assertIn("Add movement", HTML)
        submit = JS.split("async function submitLibraryAdd", 1)[1].split(
            "async function applyLibraryMembership", 1
        )[0]
        self.assertIn("primary_muscles: primary", submit)
        self.assertIn("secondary_muscles: secondary", submit)
        self.assertIn("state.workout_store.catalog = data.catalog", submit)
        self.assertIn("renderExerciseCatalog(state.workout_store)", submit)
        self.assertNotIn("/api/workout-plan/generate", submit)
        catalog = JS.split("function renderExerciseCatalog", 1)[1].split(
            "function renderLibrarySuggestions", 1
        )[0]
        self.assertIn("secondary_muscles", catalog)
        self.assertIn('data-library-edit', catalog)
        self.assertIn('universe || "") === "custom"', catalog)


if __name__ == "__main__":
    unittest.main()
