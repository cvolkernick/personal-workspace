"""Workout plan from programmed library ∩ accessible equipment."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body
from api.workout._util import dispatch_client_route, equipment_write
from rt_dashboard.equipment_store import (
    EQUIPMENT_PATH,
    SEED_REVISION,
    add_equipment_item,
    load_preview_equipment,
    load_workspace_equipment,
    migrate_equipment_inventory,
    remove_equipment_item,
)
from rt_dashboard.grok_planner import generate_grok_plans
from rt_dashboard.models import (
    ExerciseEntry,
    HealthSnapshot,
    Session,
    SetEntry,
)
from rt_dashboard.workout_planner import (
    available_load_lbs,
    filter_catalog_by_equipment,
    clamp_workout_to_equipment,
    generate_workout_plan,
    movement_feasible,
    prescribe,
)
from rt_dashboard.workout_store import load_workspace_catalog

ROOT = Path(__file__).resolve().parents[1]
REPO_EQ = Path(__file__).resolve().parents[2] / EQUIPMENT_PATH
BUNDLE_EQ = ROOT / EQUIPMENT_PATH
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


def _session(date, st, name, weight, sets=2, reps=10):
    return Session(
        date=date,
        session_type=st,
        exercises=[
            ExerciseEntry(
                name=name,
                sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
            )
        ],
    )


def _eq(*rows):
    items = []
    for row in rows:
        tag, mx = row[0], row[1] if len(row) > 1 else None
        items.append(
            {
                "id": tag,
                "name": tag.replace("_", " ").title(),
                "tag": tag,
                "max_weight_lbs": mx,
            }
        )
    return {"items": items}


GYM_TAGS = {
    "bench",
    "incline_bench",
    "smith_machine",
    "cable",
    "lat_pulldown",
    "assisted_pullup",
    "machine",
    "leg_press",
    "barbell",
}


class SeedFile(unittest.TestCase):
    def test_seed_is_access_not_exercise_dump(self):
        raw = json.loads(REPO_EQ.read_text(encoding="utf-8"))
        tags = {i["tag"] for i in raw["items"]}
        self.assertIn("dumbbells", tags)
        self.assertTrue(GYM_TAGS <= tags)
        names = [i["name"] for i in raw["items"]]
        self.assertNotIn("DB Flat Press", names)
        self.assertNotIn("Seated Cable Row", names)
        db = next(i for i in raw["items"] if i["tag"] == "dumbbells")
        self.assertEqual(db["max_weight_lbs"], 50)
        self.assertEqual(db["source"], "owned")
        bench = next(i for i in raw["items"] if i["tag"] == "bench")
        self.assertEqual(bench["source"], "gym")
        self.assertEqual(raw.get("seed_revision"), SEED_REVISION)
        self.assertTrue(BUNDLE_EQ.is_file())
        self.assertEqual(BUNDLE_EQ.read_bytes(), REPO_EQ.read_bytes())

    def test_loader_reads_seed(self):
        inv, src = load_workspace_equipment()
        self.assertEqual(src, EQUIPMENT_PATH)
        tags = {i["tag"] for i in inv["items"]}
        self.assertIn("dumbbells", tags)
        self.assertTrue(GYM_TAGS <= tags)
        by = {i["tag"]: i for i in inv["items"]}
        self.assertEqual(by["dumbbells"]["source"], "owned")
        self.assertEqual(by["bench"]["source"], "gym")


class FeasibilityRules(unittest.TestCase):
    def setUp(self):
        self.catalog, _ = load_workspace_catalog()
        self.goals = {
            "rotation": ["push", "pull", "legs"],
            "exercises_per_session": 5,
            "default_hard_sets": 2,
            "rest_if_recovery_below": 40,
        }

    def test_flat_only_bench_skips_incline(self):
        equipment = _eq(("dumbbells", 50), ("bench", None))
        filtered = filter_catalog_by_equipment(self.catalog, equipment)
        names = {e["name"] for e in filtered["exercises"]}
        self.assertIn("DB Flat Press", names)
        self.assertNotIn("DB Incline Press", names)
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [_session("2026-08-20", "pull", "DB Curls", 30)],
            recovery_score=80,
            session_type="push",
            as_of="2026-08-21",
            equipment=equipment,
        )
        planned = [e["name"] for e in plan["exercises"]]
        self.assertIn("DB Flat Press", planned)
        self.assertNotIn("DB Incline Press", planned)
        self.assertNotIn("Smith Bench", planned)
        self.assertNotIn("Tricep Pushdowns", planned)

    def test_db_max_50_does_not_prescribe_70(self):
        equipment = _eq(("dumbbells", 50), ("bench", None))
        sessions = [_session("2026-08-20", "push", "DB Flat Press", 70, 2, 12)]
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            sessions,
            recovery_score=80,
            session_type="push",
            as_of="2026-08-21",
            equipment=equipment,
        )
        press = next(e for e in plan["exercises"] if e["name"] == "DB Flat Press")
        w = float((press.get("prescription") or {}).get("weight_lbs") or 0)
        self.assertLessEqual(w, 50)
        self.assertNotEqual(w, 70)
        self.assertNotEqual(w, 75)

    def test_empty_inventory_stamps_session_type_no_invented_gear(self):
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [_session("2026-08-20", "push", "DB Flat Press", 45)],
            recovery_score=80,
            as_of="2026-08-21",
            equipment={"items": []},
        )
        self.assertEqual(plan["session_type"], "pull")
        self.assertFalse(plan["is_rest_day"])
        self.assertEqual(plan["exercises"], [])
        blob = json.dumps(plan.get("exercises") or []).lower()
        self.assertNotIn("cable", blob)
        self.assertNotIn("smith", blob)
        self.assertNotIn("assisted", blob)
        self.assertIn("equipment", (plan.get("message") or "").lower())
        cont = (plan.get("context") or {}).get("training_continuity") or {}
        self.assertTrue(cont.get("phase"))
        self.assertIn("training_continuity", plan.get("context") or {})

    def test_never_invents_cable_smith_assisted(self):
        equipment = _eq(("dumbbells", 50), ("bench", None))
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [_session("2026-08-20", "push", "DB Flat Press", 45)],
            recovery_score=80,
            session_type="pull",
            as_of="2026-08-21",
            equipment=equipment,
        )
        names = [e["name"] for e in plan["exercises"]]
        for banned in (
            "Seated Cable Row",
            "Pulldowns",
            "Assisted Pullups",
            "Face Pulls",
            "Smith Shrugs",
            "Machine Row",
        ):
            self.assertNotIn(banned, names)
        self.assertTrue(any("Curl" in n for n in names), names)

    def test_barbell_plates_only_compatible(self):
        equipment = _eq(("barbell", 135))
        filtered = filter_catalog_by_equipment(self.catalog, equipment)
        names = {e["name"] for e in filtered["exercises"]}
        self.assertIn("RDL", names)
        self.assertNotIn("DB Flat Press", names)
        self.assertNotIn("Leg Press", names)
        self.assertNotIn("Smith Bench", names)

    def test_volume_stays_dean_t_two_hard_sets(self):
        equipment = _eq(("dumbbells", 50), ("bench", None))
        plan = generate_workout_plan(
            self.catalog,
            {**self.goals, "default_hard_sets": 2},
            [_session("2026-08-20", "pull", "DB Curls", 25)],
            recovery_score=80,
            session_type="push",
            as_of="2026-08-21",
            equipment=equipment,
        )
        self.assertTrue(plan["exercises"])
        sets = [
            int((e.get("prescription") or {}).get("sets") or 0)
            for e in plan["exercises"]
        ]
        self.assertTrue(sets)
        self.assertNotIn(3, sets)
        self.assertLessEqual(max(sets), 2)
        self.assertIn(2, sets)

    def test_rest_day_still_fills_slot(self):
        plan = generate_workout_plan(
            self.catalog,
            self.goals,
            [],
            recovery_score=20,
            recovery_label="Needs Rest",
            equipment={"items": []},
        )
        self.assertTrue(plan["is_rest_day"])
        self.assertEqual(plan["session_type"], "rest")
        self.assertEqual(plan["exercises"], [])
        self.assertTrue((plan.get("context") or {}).get("training_continuity"))

    def test_movement_requires_every_tag(self):
        press = {
            "name": "DB Flat Press",
            "equipment": ["dumbbells", "bench"],
        }
        self.assertTrue(movement_feasible(press, _eq(("dumbbells", 50), ("bench", None))))
        self.assertFalse(movement_feasible(press, _eq(("dumbbells", 50))))
        self.assertFalse(movement_feasible(press, {"items": []}))


class ProgressionCap(unittest.TestCase):
    def test_double_progression_will_not_exceed_owned_load(self):
        ex = {
            "name": "DB Flat Press",
            "equipment": ["dumbbells", "bench"],
            "default_sets": 3,
            "default_reps": 10,
            "rep_range": [8, 12],
        }
        rx = prescribe(
            ex,
            {"weight_lbs": 50, "sets": 2, "reps": 12, "date": "2026-08-20"},
            default_hard_sets=2,
        )
        self.assertEqual(rx["weight_lbs"], 55.0)
        cap = available_load_lbs(ex, _eq(("dumbbells", 50), ("bench", None)))
        self.assertEqual(cap, 50)
        from rt_dashboard.workout_planner import cap_weight_to_inventory

        w, _, capped = cap_weight_to_inventory(rx["weight_lbs"], ex, _eq(("dumbbells", 50)))
        self.assertTrue(capped)
        self.assertEqual(w, 50)


class TursoPersist(unittest.TestCase):
    def test_empty_turso_seeds_from_file(self):
        puts = []
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.equipment_store._turso_get_equipment", return_value=None
        ), mock.patch(
            "rt_dashboard.equipment_store._turso_put_equipment",
            side_effect=lambda uid, inv: puts.append((uid, inv)),
        ):
            inv, src = load_preview_equipment("sub-1")
        self.assertEqual(src, "turso")
        tags = {i["tag"] for i in inv["items"]}
        self.assertIn("dumbbells", tags)
        self.assertTrue(GYM_TAGS <= tags)
        self.assertEqual(len(puts), 1)

    def test_stored_empty_row_is_sot_not_reseeded(self):
        puts = []
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.equipment_store._turso_get_equipment",
            return_value={"items": []},
        ), mock.patch(
            "rt_dashboard.equipment_store._turso_put_equipment",
            side_effect=lambda uid, inv: puts.append((uid, inv)),
        ):
            inv, src = load_preview_equipment("sub-1")
        self.assertEqual(src, "turso")
        self.assertEqual(inv["items"], [])
        self.assertEqual(puts, [])


class EquipmentWrites(unittest.TestCase):
    def _headers(self):
        token = make_session(
            {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
        )
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def test_cookie_less_is_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = equipment_write({}, "eq_add", {"name": "X", "tag": "barbell"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("equipment", body)

    def test_add_persists_to_turso(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        store = {}

        def get(uid):
            return store.get(uid) or {"items": []}

        def put(uid, inv):
            store[uid] = inv

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.equipment_store._turso_get_equipment", side_effect=get
            ), mock.patch(
                "rt_dashboard.equipment_store._turso_put_equipment", side_effect=put
            ):
                status, body = equipment_write(
                    self._headers(),
                    "eq_add",
                    {"name": "Barbell", "tag": "barbell", "max_weight_lbs": 135},
                )
        self.assertEqual(status, 200)
        tags = {i["tag"] for i in body["equipment"]["items"]}
        self.assertIn("barbell", tags)
        self.assertEqual(body["write"]["source"], "turso")

    def test_update_unknown_id_is_honest(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        store = {"sub-1": {"items": [{"id": "dumbbells", "name": "Dumbbells", "tag": "dumbbells", "max_weight_lbs": 50}]}}
        puts = []
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.equipment_store._turso_get_equipment",
                side_effect=lambda uid: store.get(uid),
            ), mock.patch(
                "rt_dashboard.equipment_store._turso_put_equipment",
                side_effect=lambda uid, inv: puts.append(inv),
            ):
                status, body = equipment_write(
                    self._headers(),
                    "eq_update",
                    {"id": "unicorn-rack", "name": "Unicorn rack", "tag": "smith_machine"},
                )
        self.assertEqual(status, 400)
        self.assertIn("not found", str(body.get("error") or "").lower())
        for inv in puts:
            tags = {i.get("tag") for i in (inv.get("items") or []) if isinstance(i, dict)}
            self.assertNotIn("unicorn-rack", tags)
            self.assertNotIn("smith_machine", {i.get("id") for i in (inv.get("items") or []) if isinstance(i, dict) and i.get("id") == "unicorn-rack"})

    def test_dispatch_equipment_paths(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for path in ("/api/equipment/add", "/api/equipment/update", "/api/equipment/remove"):
                status, body = dispatch_client_route({}, "", "POST", payload={}, path=path)
                self.assertEqual(status, 401, path)
                self.assertEqual(body["error"], "auth_required")


class DashboardCompose(unittest.TestCase):
    def test_signed_in_plan_uses_seed_gear(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        session = Session(
            date="2026-08-17",
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="DB Flat Press",
                    sets=[SetEntry(weight_lbs=45, sets=2, reps=10)],
                )
            ],
        )
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([session], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ):
                status, body = dashboard_body(headers)
        self.assertEqual(status, 200)
        wo = body["workout_store"]
        self.assertIn("equipment", wo)
        self.assertIn(wo["sources"]["equipment"], (EQUIPMENT_PATH, "turso"))
        tags = {i["tag"] for i in (wo["equipment"] or {}).get("items") or []}
        self.assertIn("dumbbells", tags)
        self.assertIn("cable", tags)
        plan = wo["plan"]
        self.assertEqual(plan.get("session_type"), "pull")
        names = [e.get("name") for e in plan.get("exercises") or []]
        self.assertTrue(names)
        self.assertNotIn("DB Floor Press", names)
        self.assertTrue((plan.get("context") or {}).get("training_continuity"))
        adds = {
            s["id"]
            for s in ((wo.get("library_suggestions") or {}).get("suggestions") or [])
        }
        self.assertIn("db-floor-press", adds)
        self.assertIn("db-row", adds)

    def test_empty_equipment_dashboard_keeps_session_type(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        session = Session(
            date="2026-08-17",
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="DB Flat Press",
                    sets=[SetEntry(weight_lbs=45, sets=2, reps=10)],
                )
            ],
        )
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([session], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.equipment_store.load_preview_equipment",
                return_value=({"items": []}, "turso"),
            ):
                status, body = dashboard_body(headers)
        self.assertEqual(status, 200)
        plan = body["workout_store"]["plan"]
        self.assertEqual(plan.get("session_type"), "pull")
        self.assertEqual(plan.get("exercises") or [], [])
        names = [e.get("name") for e in plan.get("exercises") or []]
        self.assertNotIn("Smith Bench", names)
        self.assertNotIn("Assisted Pullups", names)


class GrokClamp(unittest.TestCase):
    def test_clamp_drops_invented_cable_and_caps_db(self):
        catalog, _ = load_workspace_catalog()
        equipment = _eq(("dumbbells", 50), ("bench", None))
        workout = {
            "session_type": "push",
            "is_rest_day": False,
            "exercises": [
                {
                    "name": "DB Flat Press",
                    "prescription": {"weight_lbs": 70, "sets": 2, "reps": 8},
                },
                {
                    "name": "Tricep Pushdowns",
                    "prescription": {"weight_lbs": 45, "sets": 2, "reps": 12},
                },
                {
                    "name": "Unicorn Fly",
                    "prescription": {"weight_lbs": 20, "sets": 2, "reps": 12},
                },
            ],
        }
        out = clamp_workout_to_equipment(workout, catalog, equipment)
        names = [e["name"] for e in out["exercises"]]
        self.assertEqual(names, ["DB Flat Press"])
        self.assertLessEqual(out["exercises"][0]["prescription"]["weight_lbs"], 50)

    def test_grok_generate_clamps_equipment(self):
        catalog, _ = load_workspace_catalog()
        equipment = _eq(("dumbbells", 50))

        def fake_chat(messages, **kwargs):
            return {
                "answer": json.dumps(
                    {
                        "meal": {"message": "ok", "items": [], "meals": []},
                        "workout": {
                            "session_type": "pull",
                            "is_rest_day": False,
                            "message": "ok",
                            "exercises": [
                                {
                                    "name": "Assisted Pullups",
                                    "prescription": {"sets": 2, "reps": 8, "weight_lbs": 80},
                                },
                                {
                                    "name": "DB Curls",
                                    "prescription": {"sets": 2, "reps": 10, "weight_lbs": 70},
                                },
                            ],
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
            out = generate_grok_plans(
                "user-1",
                catalog=catalog,
                equipment=equipment,
                goals={"default_hard_sets": 2, "rotation": ["push", "pull", "legs"]},
                next_session_type="pull",
            )
        names = [e["name"] for e in out["workout"]["exercises"]]
        self.assertNotIn("Assisted Pullups", names)
        self.assertIn("DB Curls", names)
        self.assertLessEqual(out["workout"]["exercises"][0]["prescription"]["weight_lbs"], 50)


class UiIsGearNotExercises(unittest.TestCase):
    def test_html_adds_gear_not_exercises(self):
        self.assertIn("Equipment inventory", HTML)
        self.assertIn("Add / update gear", HTML)
        self.assertNotIn("Add / update exercise", HTML)
        self.assertNotIn('id="exercise-form"', HTML)
        self.assertIn('id="equipment-form"', HTML)
        self.assertIn("Programmed movements Today reads", HTML)
        self.assertIn("library-suggestions", HTML)
        self.assertIn("eq-source", HTML)

    def test_js_posts_equipment_not_catalog_add(self):
        self.assertIn('fetch("/api/equipment/add"', JS)
        self.assertIn('fetch("/api/equipment/remove"', JS)
        self.assertIn("async function submitEquipmentInventory", JS)
        self.assertIn('fetch("/api/workout/exercise/available"', JS)
        self.assertNotIn("/api/workout/exercise\"", JS.replace("/api/workout/exercise/available", ""))
        self.assertNotIn("Add / update exercise", JS)

    def test_vercel_rewrites_and_bundle(self):
        self.assertIn("/api/equipment/add", VERCEL)
        self.assertIn("fitness/exercises/equipment.json", VERCEL)
        self.assertNotIn("api/equipment.py", VERCEL)
        self.assertFalse((ROOT / "api" / "equipment.py").exists())

    def test_hobby_function_count_stays_at_12(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])


class MigrateV2(unittest.TestCase):
    def test_old_seed_gains_gym_and_drops_home_bench(self):
        old = {
            "items": [
                {
                    "id": "dumbbells",
                    "name": "Dumbbells",
                    "tag": "dumbbells",
                    "max_weight_lbs": 50,
                    "source": "owned",
                },
                {
                    "id": "bench",
                    "name": "Flat bench",
                    "tag": "bench",
                    "source": "owned",
                },
            ]
        }
        out = migrate_equipment_inventory(old)
        by = {i["tag"]: i for i in out["items"]}
        self.assertEqual(out["seed_revision"], SEED_REVISION)
        self.assertEqual(by["dumbbells"]["source"], "owned")
        self.assertEqual(by["dumbbells"]["max_weight_lbs"], 50)
        self.assertEqual(by["bench"]["source"], "gym")
        self.assertTrue(GYM_TAGS <= set(by))

    def test_empty_row_is_not_reseeded(self):
        out = migrate_equipment_inventory({"items": []})
        self.assertEqual(out["items"], [])

    def test_already_migrated_keeps_user_deletes(self):
        inv = {
            "seed_revision": SEED_REVISION,
            "items": [
                {
                    "id": "dumbbells",
                    "name": "Dumbbells",
                    "tag": "dumbbells",
                    "max_weight_lbs": 50,
                    "source": "owned",
                }
            ],
        }
        out = migrate_equipment_inventory(inv)
        self.assertEqual({i["tag"] for i in out["items"]}, {"dumbbells"})

    def test_live_turso_old_seed_is_written_back(self):
        puts = []
        old = {
            "items": [
                {
                    "id": "dumbbells",
                    "name": "Dumbbells",
                    "tag": "dumbbells",
                    "max_weight_lbs": 50,
                    "source": "owned",
                },
                {
                    "id": "bench",
                    "name": "Flat bench",
                    "tag": "bench",
                    "source": "owned",
                },
            ]
        }
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.equipment_store._turso_get_equipment", return_value=old
        ), mock.patch(
            "rt_dashboard.equipment_store._turso_put_equipment",
            side_effect=lambda uid, inv: puts.append(inv),
        ):
            inv, src = load_preview_equipment("sub-1")
        self.assertEqual(src, "turso")
        self.assertEqual(len(puts), 1)
        self.assertEqual(inv["seed_revision"], SEED_REVISION)
        self.assertIn("cable", {i["tag"] for i in inv["items"]})


class Mutators(unittest.TestCase):
    def test_add_update_remove(self):
        inv = {"items": []}
        inv = add_equipment_item(
            inv, {"name": "Dumbbells", "tag": "dumbbells", "max_weight_lbs": 40}
        )
        self.assertEqual(inv["items"][0]["max_weight_lbs"], 40)
        inv = add_equipment_item(
            inv, {"name": "Dumbbells", "tag": "dumbbells", "max_weight_lbs": 50}
        )
        self.assertEqual(len(inv["items"]), 1)
        self.assertEqual(inv["items"][0]["max_weight_lbs"], 50)
        inv = remove_equipment_item(inv, equipment_id="dumbbells")
        self.assertEqual(inv["items"], [])


if __name__ == "__main__":
    unittest.main()
