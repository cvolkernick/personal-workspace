"""Catalog guard: Smith flat bench alias, plan resolution, starting weight (spec 2026-10-27)."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from rt_dashboard.catalog_guard import (
    apply_catalog_guard,
    guard_plan,
    load_guard_config,
    resolve_catalog_id,
    starting_weight,
)
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    NAME_ALIASES,
    clamp_llm_plan_loads,
    generate_workout_plan,
    load_step_lbs,
    match_catalog_id,
)

ROOT = Path(__file__).resolve().parents[1]
FIND = "Find a working weight: RPE 7 (about 3 reps left)"


def _row(eid, name, **kw):
    row = {
        "id": eid,
        "name": name,
        "session_types": ["push"],
        "primary_muscles": ["chest"],
        "secondary_muscles": ["triceps"],
        "movement": "compound",
        "equipment": ["smith_machine"],
        "default_sets": 3,
        "default_reps": 8,
        "rep_range": [6, 10],
        "priority": 8,
        "available": True,
    }
    row.update(kw)
    return row


def _catalog(*, flat=True, incline=True, barbell=True, legacy=False, extra=()):
    rows = []
    if legacy:
        rows.append(_row("smith-bench", "Smith Bench"))
    if flat:
        rows.append(_row("smith-flat-bench", "Smith Flat Bench"))
    if incline:
        rows.append(_row("smith-incline-bench", "Smith Incline Bench", bar_weight_lbs=15, load_convention="plates_plus_bar"))
    if barbell:
        rows.append(_row("barbell-flat-bench-press", "Barbell Flat Bench Press", equipment=["barbell", "bench"]))
    rows.append(_row("db-shoulder-press", "DB Shoulder Press", primary_muscles=["shoulders"], equipment=["dumbbells"]))
    rows.extend(extra)
    return {"exercises": rows}


def _sess(date, name, weight, reps, sets=1, st="push"):
    return Session(
        date=date,
        session_type=st,
        exercises=[ExerciseEntry(name=name, sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)])],
    )


EQUIP = {
    "items": [
        {"id": "smith_machine", "name": "Smith machine", "tag": "smith_machine", "max_weight_lbs": None},
        {"id": "dumbbells", "name": "Dumbbells", "tag": "dumbbells", "max_weight_lbs": 50.0},
        {"id": "barbell", "name": "Barbell", "tag": "barbell", "max_weight_lbs": None},
        {"id": "bench", "name": "Flat bench", "tag": "bench", "max_weight_lbs": None},
    ]
}


class GuardData(unittest.TestCase):
    def test_relations_are_data_in_the_bundled_file(self):
        cfg = json.loads((ROOT / "fitness/exercises/catalog_guard.json").read_text())
        rel = cfg["related_lifts"]
        self.assertEqual(rel["smith-flat-bench"][0], {"related_lift": "smith-incline-bench", "factor": 1.10})
        self.assertEqual(rel["smith-flat-bench"][1], {"related_lift": "barbell-flat-bench-press", "factor": 0.90})
        self.assertEqual(rel["smith-incline-bench"][0], {"related_lift": "smith-flat-bench", "factor": 0.90})
        self.assertEqual(cfg["generic_fallback"]["factor"], 0.90)
        self.assertEqual(cfg["starting_weight"]["no_history_text"], FIND)
        self.assertEqual(load_guard_config()["schema"], "fitdash.catalog_guard.v1")

    def test_guard_module_has_no_per_lift_branches(self):
        src = (ROOT / "rt_dashboard/catalog_guard.py").read_text()
        for needle in ("smith-flat-bench", "smith-incline-bench", "1.10", "barbell-flat-bench"):
            self.assertNotIn(needle, src)


class CatalogEntry(unittest.TestCase):
    def test_alias_smith_bench_resolves(self):
        cat = apply_catalog_guard(_catalog())
        for name in ("Smith bench", "smith BENCH", "Smith flat bench press", "Smith Flat Bench"):
            self.assertEqual(resolve_catalog_id(name, cat), "smith-flat-bench", name)
        by_id = {r["id"]: r for r in cat["exercises"]}
        self.assertEqual(match_catalog_id("Smith flat bench press", by_id), "smith-flat-bench")
        self.assertEqual(NAME_ALIASES["smith bench"], "smith-flat-bench")

    def test_flat_copies_incline_equipment_and_bar_fields_with_step_and_band(self):
        cat = apply_catalog_guard(_catalog())
        by_id = {r["id"]: r for r in cat["exercises"]}
        flat, inc = by_id["smith-flat-bench"], by_id["smith-incline-bench"]
        self.assertEqual(flat["equipment"], inc["equipment"])
        self.assertEqual(flat["bar_weight_lbs"], inc["bar_weight_lbs"])
        self.assertEqual(flat["load_convention"], inc["load_convention"])
        self.assertEqual(flat["load_step_lbs"], 5)
        self.assertEqual(flat["rep_range"], [5, 9])
        self.assertEqual(flat["movement_pattern"], "horizontal_press")
        self.assertEqual(load_step_lbs(flat, 65.0, EQUIP), 5.0)

    def test_missing_flat_is_not_injected_by_default(self):
        # Every live catalog already ships smith-flat-bench (or the legacy id,
        # which folds). Injecting would change plans for catalogs without it.
        cat = apply_catalog_guard(_catalog(flat=False))
        self.assertNotIn("smith-flat-bench", [r["id"] for r in cat["exercises"]])

    def test_missing_flat_is_injected_when_data_says_so(self):
        cfg = deepcopy(load_guard_config())
        cfg["exercises"][0]["inject_if_missing"] = True
        cat = apply_catalog_guard(_catalog(flat=False), cfg)
        by_id = {r["id"]: r for r in cat["exercises"]}
        self.assertIn("smith-flat-bench", by_id)
        self.assertEqual(by_id["smith-flat-bench"]["equipment"], ["smith_machine"])
        self.assertEqual(resolve_catalog_id("Smith bench", cat), "smith-flat-bench")
        self.assertNotIn("inject_if_missing", by_id["smith-flat-bench"])

    def test_legacy_smith_bench_id_folds_into_flat(self):
        cat = apply_catalog_guard(_catalog(flat=False, legacy=True))
        ids = [r["id"] for r in cat["exercises"]]
        self.assertNotIn("smith-bench", ids)
        self.assertEqual(ids.count("smith-flat-bench"), 1)
        both = apply_catalog_guard(_catalog(flat=True, legacy=True))
        self.assertEqual([r["id"] for r in both["exercises"]].count("smith-flat-bench"), 1)
        self.assertNotIn("smith-bench", [r["id"] for r in both["exercises"]])

    def test_idempotent_and_input_not_mutated(self):
        raw = _catalog()
        before = deepcopy(raw)
        once = apply_catalog_guard(raw)
        self.assertEqual(raw, before)
        self.assertEqual(apply_catalog_guard(once), once)


class StartingWeight(unittest.TestCase):
    def setUp(self):
        self.cat = apply_catalog_guard(_catalog())
        self.by_id = {r["id"]: r for r in self.cat["exercises"]}

    def test_flat_from_incline_x110_rounded_down(self):
        s = [_sess("2026-10-05", "Smith Incline Bench", 60.0, 7)]
        out = starting_weight(self.by_id["smith-flat-bench"], self.cat, s)
        self.assertEqual(out["weight_lbs"], 65.0)  # 66 -> 65
        self.assertEqual(out["source_lift"], "Smith Incline Bench")
        self.assertEqual(out["target_reps"], 7)
        self.assertEqual(out["label"], "Starting estimate from Smith Incline Bench")
        self.assertIn("RPE 7-8", out["cue"])

    def test_flat_from_barbell_x090_when_no_incline_history(self):
        s = [_sess("2026-10-17", "Barbell Flat Bench Press", 135.0, 6)]
        out = starting_weight(self.by_id["smith-flat-bench"], self.cat, s)
        self.assertEqual(out["weight_lbs"], 120.0)  # 121.5 -> 120
        self.assertEqual(out["source_lift"], "Barbell Flat Bench Press")

    def test_first_working_set_is_the_basis(self):
        s = [Session(date="2026-10-05", session_type="push", exercises=[ExerciseEntry(
            name="Smith Incline Bench",
            sets=[SetEntry(weight_lbs=50.0, sets=1, reps=8), SetEntry(weight_lbs=70.0, sets=1, reps=5)])])]
        out = starting_weight(self.by_id["smith-flat-bench"], self.cat, s)
        self.assertEqual(out["weight_lbs"], 55.0)  # 50 x 1.1 = 55, not the 70 top set

    def test_incline_from_flat_x090(self):
        s = [_sess("2026-10-11", "Smith Flat Bench", 65.0, 8)]
        out = starting_weight(self.by_id["smith-incline-bench"], self.cat, s)
        self.assertEqual(out["weight_lbs"], 55.0)  # 58.5 -> 55

    def test_generic_same_pattern_same_equipment_x090(self):
        extra = (_row("smith-decline-bench", "Smith Decline Bench", movement_pattern="horizontal_press"),)
        cat = apply_catalog_guard(_catalog(extra=extra))
        by_id = {r["id"]: r for r in cat["exercises"]}
        s = [_sess("2026-10-11", "Smith Flat Bench", 100.0, 8)]
        out = starting_weight(by_id["smith-decline-bench"], cat, s)
        self.assertEqual(out["weight_lbs"], 90.0)
        self.assertEqual(out["kind"], "generic")

    def test_no_related_history_gives_find_weight_text(self):
        out = starting_weight(self.by_id["smith-flat-bench"], self.cat, [])
        self.assertIsNone(out["weight_lbs"])
        self.assertEqual(out["text"], FIND)
        self.assertEqual(out["target_reps"], 7)

    def test_factor_comes_from_data(self):
        cfg = deepcopy(load_guard_config())
        cfg["related_lifts"]["smith-flat-bench"][0]["factor"] = 1.0
        cat = apply_catalog_guard(_catalog(), cfg)
        flat = {r["id"]: r for r in cat["exercises"]}["smith-flat-bench"]
        out = starting_weight(flat, cat, [_sess("2026-10-05", "Smith Incline Bench", 60.0, 7)], config=cfg)
        self.assertEqual(out["weight_lbs"], 60.0)


class PlanGuard(unittest.TestCase):
    def _plan(self, *rows):
        return {"session_type": "push", "message": "m", "exercises": [dict(r) for r in rows]}

    def test_alias_row_resolves_and_gets_a_derived_weight(self):
        s = [_sess("2026-10-05", "Smith Incline Bench", 60.0, 7)]
        plan = self._plan({"name": "Smith bench", "sets": 2, "reps": 8, "weight_lbs": None,
                           "prescription": {"sets": 2, "reps": 8, "weight_lbs": None}})
        out = guard_plan(plan, _catalog(), s, as_of="2026-10-27")
        row = out["exercises"][0]
        self.assertEqual(row["catalog_id"], "smith-flat-bench")
        self.assertEqual(row["name"], "Smith Flat Bench")
        self.assertEqual(row["planned_as"], "Smith bench")
        self.assertEqual(row["weight_lbs"], 65.0)
        self.assertEqual(row["prescription"]["weight_lbs"], 65.0)
        self.assertEqual(row["reps"], 7)
        self.assertEqual(row["load_source"], "related_estimate")
        self.assertIn("Starting estimate from Smith Incline Bench", row["load_reason"])

    def test_no_related_history_shows_text_never_blank(self):
        plan = self._plan({"name": "Smith bench", "sets": 2, "reps": 8})
        row = guard_plan(plan, _catalog(), [], as_of="2026-10-27")["exercises"][0]
        self.assertIsNone(row["weight_lbs"])
        self.assertEqual(row["weight_text"], FIND)
        self.assertEqual(row["prescription"]["weight_text"], FIND)

    def test_unknown_name_substitutes_same_pattern_with_warning(self):
        s = [_sess("2026-10-11", "Smith Flat Bench", 65.0, 8)]
        plan = self._plan({"name": "Smith Wide Grip Bench", "sets": 2, "reps": 8})
        with self.assertLogs("fitdash.catalog_guard", level="WARNING") as logs:
            out = guard_plan(plan, _catalog(), s, equipment=EQUIP, as_of="2026-10-27")
        row = out["exercises"][0]
        self.assertTrue(row["substituted"])
        self.assertEqual(row["original_name"], "Smith Wide Grip Bench")
        self.assertEqual(row["catalog_id"], "smith-flat-bench")
        self.assertEqual(row["weight_lbs"], 65.0)
        self.assertTrue(any("Smith Wide Grip Bench" in m for m in logs.output))
        self.assertIn("substituted", out["message"])

    def test_unknown_with_no_match_is_dropped_and_flagged(self):
        plan = self._plan({"name": "Sled Push", "sets": 3, "reps": 20},
                          {"name": "Smith Flat Bench", "sets": 2, "reps": 8, "weight_lbs": 65.0,
                           "prescription": {"weight_lbs": 65.0, "sets": 2, "reps": 8}})
        with self.assertLogs("fitdash.catalog_guard", level="WARNING"):
            out = guard_plan(plan, _catalog(), [], as_of="2026-10-27")
        names = [r["name"] for r in out["exercises"]]
        self.assertEqual(names, ["Smith Flat Bench"])
        self.assertIn("removed: Sled Push (not in equipment library)", out["message"])
        self.assertEqual(out["catalog_guard"]["removed"][0]["name"], "Sled Push")

    def test_every_served_row_has_a_catalog_id(self):
        plan = self._plan({"name": "Smith bench"}, {"name": "DB Shoulder Press", "weight_lbs": 30.0},
                          {"name": "Mystery Lift"}, {"name": "Smith Decline Thing"})
        out = guard_plan(plan, _catalog(), [], as_of="2026-10-27")
        for row in out["exercises"]:
            self.assertTrue(row.get("catalog_id"), row)
            self.assertTrue(row.get("weight_lbs") is not None or row.get("weight_text"), row)

    def test_existing_lift_with_history_is_unchanged(self):
        row = {"id": "smith-flat-bench", "name": "Smith Flat Bench", "sets": 2, "reps": 9, "weight_lbs": 65.0,
               "load": 65.0, "prescription": {"weight_lbs": 65.0, "sets": 2, "reps": 9}, "rationale": "r"}
        out = guard_plan(self._plan(row), _catalog(), [_sess("2026-10-11", "Smith Flat Bench", 65.0, 8)],
                         as_of="2026-10-27")
        got = dict(out["exercises"][0])
        self.assertEqual(got.pop("catalog_id"), "smith-flat-bench")
        self.assertEqual(got, row)

    def test_rows_logged_today_are_never_touched(self):
        row = {"name": "Smith bench", "sets": 2, "reps": 8, "weight_lbs": None}
        s = [_sess("2026-10-27", "Smith bench", 45.0, 8)]
        out = guard_plan(self._plan(row), _catalog(), s, as_of="2026-10-27")
        self.assertEqual(out["exercises"][0], row)
        self.assertNotIn("catalog_id", out["exercises"][0])

    def test_guard_never_mutates_the_input_plan(self):
        plan = self._plan({"name": "Smith bench", "sets": 2, "reps": 8})
        before = deepcopy(plan)
        guard_plan(plan, _catalog(), [], as_of="2026-10-27")
        self.assertEqual(plan, before)


class PlannerIntegration(unittest.TestCase):
    GOALS = {"split": "ppl", "rotation": ["push", "pull", "legs"], "exercises_per_session": 5}

    def test_stale_fallback_catalog_smith_bench_gets_history_weight(self):
        """Root cause 10/27: stale catalog id smith-bench, logs renamed to Smith Flat Bench."""
        cat = _catalog(flat=False, incline=False, barbell=False, legacy=True)
        s = [_sess("2026-10-11", "Smith Flat Bench", 65.0, 8), _sess("2026-10-26", "Leg Press", 190.0, 8, st="legs")]
        plan = generate_workout_plan(cat, self.GOALS, s, session_type="push", as_of="2026-10-27", equipment=EQUIP)
        rows = {r["name"]: r for r in plan["exercises"]}
        self.assertNotIn("Smith Bench", rows)
        flat = rows["Smith Flat Bench"]
        self.assertEqual(flat["catalog_id"], "smith-flat-bench")
        self.assertEqual(flat["prescription"]["weight_lbs"], 65.0)
        for r in plan["exercises"]:
            self.assertTrue(r.get("catalog_id"))
            rx = r.get("prescription") or {}
            self.assertTrue(rx.get("weight_lbs") is not None or rx.get("weight_text"), r["name"])

    def test_no_history_planner_row_uses_related_lift(self):
        cat = _catalog(barbell=False)
        cat["exercises"] = [r for r in cat["exercises"] if r["id"] != "db-shoulder-press"]
        s = [_sess("2026-10-05", "Smith Incline Bench", 60.0, 7)]
        plan = generate_workout_plan(cat, self.GOALS, s, session_type="push", as_of="2026-10-27", equipment=EQUIP)
        flat = [r for r in plan["exercises"] if r.get("catalog_id") == "smith-flat-bench"]
        self.assertTrue(flat)
        self.assertEqual(flat[0]["prescription"]["weight_lbs"], 65.0)
        self.assertEqual(flat[0]["prescription"]["load_source"], "related_estimate")

    def test_llm_plan_alias_and_unknown(self):
        wo = {"session_type": "push", "exercises": [
            {"name": "Smith bench", "sets": 2, "reps": 8},
            {"name": "Underwater Basket Press", "sets": 2, "reps": 8},
        ]}
        s = [_sess("2026-10-11", "Smith Flat Bench", 65.0, 8), _sess("2026-10-26", "Leg Press", 190.0, 8, st="legs")]
        with self.assertLogs("fitdash.catalog_guard", level="WARNING"):
            out = clamp_llm_plan_loads(wo, s, _catalog(), as_of="2026-10-27", equipment=EQUIP)
        names = [r["name"] for r in out["exercises"]]
        self.assertEqual(names[0], "Smith Flat Bench")
        self.assertEqual(out["exercises"][0]["weight_lbs"], 65.0)
        for r in out["exercises"]:
            self.assertTrue(r.get("catalog_id"))


class TodayLeafTitle(unittest.TestCase):
    def test_find_weight_text_in_leaf_title(self):
        from rt_dashboard.daily_plan_tasks import plan_from_today_board

        today = {"date": "2026-10-27", "workout": {"exercises": [
            {"name": "Smith Flat Bench", "sets": 2, "reps": 7, "weight_lbs": None, "weight_text": FIND},
            {"name": "DB Shoulder Press", "sets": 2, "reps": 6, "weight_lbs": 30.0},
        ]}}
        groups = plan_from_today_board(today, day="2026-10-27")
        titles = [i.title for g in groups for i in g.items if i.slug.startswith("ex-")]
        self.assertIn(f"Smith Flat Bench (2×7 · {FIND})", titles)
        self.assertIn("DB Shoulder Press (30.0 lb 2×6)", titles)


if __name__ == "__main__":
    unittest.main()
