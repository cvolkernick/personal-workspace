"""One movement family per session, plus the Preacher Curl Machine catalog row."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.muscle_sets import build_muscle_set_report, credit_proper_sets, volume_groups
from rt_dashboard.workout_planner import (
    CATALOG_PATH,
    CURL_FAMILY,
    SHRUG_FAMILY,
    collapse_family_duplicates,
    generate_workout_plan,
    pattern_family,
    primary_hinge_key,
)
from rt_dashboard.workout_store import _workspace_file_candidates, load_workspace_catalog

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


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


def _ex(eid, name, movement, primary, priority, sessions, equipment=None):
    return {
        "id": eid,
        "name": name,
        "session_types": sessions,
        "primary_muscles": primary,
        "secondary_muscles": [],
        "movement": movement,
        "equipment": equipment or [],
        "default_sets": 2,
        "default_reps": 10,
        "rep_range": [8, 12],
        "priority": priority,
        "available": True,
    }


def _plan_row(eid, name, primary, sets, *, family_last=None, weight=100, reps=10, priority=5):
    return {
        "id": eid,
        "name": name,
        "primary_muscles": primary,
        "secondary_muscles": [],
        "movement": "isolation",
        "priority": priority,
        "prescription": {"sets": sets, "reps": reps, "weight_lbs": weight},
        "last": {"weight_lbs": weight, "reps": reps},
    }


class TestFamilyGuard(unittest.TestCase):
    def test_shrug_variants_are_one_family_and_curls_are_another(self):
        for eid, name in (
            ("barbell-shrugs", "Barbell Shrugs"),
            ("smith-shrugs", "Smith Shrugs"),
            ("db-shrugs", "DB Shrugs"),
            ("trap-bar-shrugs", "Trap-Bar Shrugs"),
            ("machine-shrugs", "Machine Shrugs"),
        ):
            self.assertEqual(
                pattern_family({"id": eid, "name": name, "movement": "isolation", "primary_muscles": ["traps"]}),
                SHRUG_FAMILY,
                eid,
            )
        self.assertEqual(
            pattern_family({"id": "preacher-curl-machine", "name": "Preacher Curl Machine", "movement": "isolation", "primary_muscles": ["biceps"]}),
            CURL_FAMILY,
        )
        self.assertEqual(
            pattern_family({"id": "seated-leg-curls", "name": "Seated Leg Curls", "movement": "isolation", "primary_muscles": ["hamstrings"]}),
            "hamstring_curl",
        )

    def test_duplicate_shrugs_keep_the_rotation_winner_and_move_sets_off_traps(self):
        rows = [
            _plan_row("smith-shrugs", "Smith Shrugs", ["traps"], 2, weight=80, priority=3),
            _plan_row("barbell-shrugs", "Barbell Shrugs", ["traps"], 2, weight=135, priority=4),
            _plan_row("db-curls", "DB Curls", ["biceps"], 2, weight=25, priority=6),
        ]
        out = collapse_family_duplicates(
            rows,
            bands={"biceps": {"min": 4, "max": 8}, "traps": {"min": 4, "max": 8}},
            done={"biceps": 0, "traps": 0},
            last_family_ids={SHRUG_FAMILY: "smith-shrugs"},
            session_cap=14,
            biceps_under=True,
        )
        ids = [row["id"] for row in out]
        self.assertIn("barbell-shrugs", ids)
        self.assertNotIn("smith-shrugs", ids)
        self.assertEqual(ids.count("barbell-shrugs"), 1)
        shrug = next(row for row in out if row["id"] == "barbell-shrugs")
        curl = next(row for row in out if row["id"] == "db-curls")
        self.assertEqual(shrug["prescription"]["sets"], 2)
        self.assertEqual(curl["prescription"]["sets"], 3)
        trap_rows = [row for row in out if row["primary_muscles"] == ["traps"]]
        self.assertEqual(len(trap_rows), 1)

    def test_curl_exception_and_strict_shrug_and_hinge(self):
        two_curls = [
            _plan_row("db-curls", "DB Curls", ["biceps"], 2),
            _plan_row("preacher-curl-machine", "Preacher Curl Machine", ["biceps"], 2, weight=40),
        ]
        allowed = collapse_family_duplicates(
            two_curls,
            bands={"biceps": {"min": 4}},
            done={"biceps": 0},
            session_cap=14,
            biceps_under=True,
        )
        self.assertEqual(len(allowed), 2)
        blocked = collapse_family_duplicates(
            two_curls,
            bands={"biceps": {"min": 4}},
            done={"biceps": 6},
            session_cap=14,
            biceps_under=False,
        )
        self.assertEqual(len(blocked), 1)
        over_cap = collapse_family_duplicates(
            two_curls,
            bands={"biceps": {"min": 4}},
            done={"biceps": 0},
            session_cap=3,
            biceps_under=True,
        )
        self.assertEqual(len(over_cap), 1)
        hinges = [
            {
                "id": "rdl",
                "name": "RDL",
                "movement": "compound",
                "primary_muscles": ["hamstrings", "glutes"],
                "prescription": {"sets": 3, "reps": 8, "weight_lbs": 95},
                "priority": 8,
            },
            {
                "id": "barbell-deadlift",
                "name": "Barbell Deadlift",
                "movement": "compound",
                "primary_muscles": ["hamstrings", "glutes"],
                "prescription": {"sets": 3, "reps": 5, "weight_lbs": 185},
                "priority": 7,
            },
        ]
        self.assertIsNotNone(primary_hinge_key(hinges[0]))
        self.assertIsNotNone(primary_hinge_key(hinges[1]))
        one_hinge = collapse_family_duplicates(hinges, session_cap=14, biceps_under=False)
        self.assertEqual(len(one_hinge), 1)

    def test_pull_day_does_not_schedule_barbell_and_smith_shrug(self):
        catalog = {
            "exercises": [
                _ex("pulldowns", "Pulldowns", "compound", ["lats"], 10, ["pull"]),
                _ex("seated-cable-row", "Seated Cable Row", "compound", ["mid_upper_back"], 9, ["pull"]),
                _ex("barbell-shrugs", "Barbell Shrugs", "isolation", ["traps"], 8, ["pull"], ["barbell"]),
                _ex("smith-shrugs", "Smith Shrugs", "isolation", ["traps"], 8, ["pull"], ["smith_machine"]),
                _ex("db-curls", "DB Curls", "isolation", ["biceps"], 6, ["pull"]),
                _ex("preacher-curl-machine", "Preacher Curl Machine", "isolation", ["biceps"], 6, ["pull"], ["machine"]),
                _ex("face-pulls", "Face Pulls", "isolation", ["rear_delts"], 5, ["pull"]),
            ]
        }
        sessions = [
            _session("2026-11-20", "pull", "Smith Shrugs", 100, 2, 10),
            _session("2026-11-21", "push", "DB Flat Press", 50, 2, 8),
        ]
        plan = generate_workout_plan(
            catalog,
            {
                "rotation": ["push", "pull", "legs"],
                "exercises_per_session": 6,
                "default_hard_sets": 2,
                "session_working_set_cap": 14,
                "prefer_compounds_first": True,
                "rest_if_recovery_below": 40,
            },
            sessions,
            recovery_score=80,
            session_type="pull",
            as_of="2026-11-22",
        )
        ids = [row["id"] for row in plan["exercises"]]
        shrugs = [i for i in ids if "shrug" in i]
        self.assertEqual(shrugs, ["barbell-shrugs"], ids)
        shrug = next(row for row in plan["exercises"] if row["id"] == "barbell-shrugs")
        self.assertEqual(shrug["prescription"]["sets"], 2)
        families = {}
        for row in plan["exercises"]:
            fam = pattern_family(row)
            if not fam:
                continue
            families[fam] = families.get(fam, 0) + 1
        self.assertEqual(families.get(SHRUG_FAMILY), 1)
        self.assertLessEqual(families.get(CURL_FAMILY, 0), 2)
        for fam, count in families.items():
            if fam == CURL_FAMILY:
                continue
            self.assertLessEqual(count, 1, fam)
        self.assertLessEqual(
            sum(int((row.get("prescription") or {}).get("sets") or 0) for row in plan["exercises"]),
            14,
        )

    def test_second_curl_only_while_biceps_are_under_target(self):
        catalog = {
            "exercises": [
                _ex("pulldowns", "Pulldowns", "compound", ["lats"], 10, ["pull"]),
                _ex("db-curls", "DB Curls", "isolation", ["biceps"], 9, ["pull"]),
                _ex("preacher-curl-machine", "Preacher Curl Machine", "isolation", ["biceps"], 8, ["pull"], ["machine"]),
                _ex("face-pulls", "Face Pulls", "isolation", ["rear_delts"], 4, ["pull"]),
            ]
        }
        goals = {
            "rotation": ["push", "pull", "legs"],
            "exercises_per_session": 4,
            "default_hard_sets": 2,
            "session_working_set_cap": 14,
            "prefer_compounds_first": True,
            "rest_if_recovery_below": 40,
        }
        under = generate_workout_plan(
            catalog,
            goals,
            [_session("2026-11-21", "push", "DB Flat Press", 50)],
            recovery_score=80,
            session_type="pull",
            as_of="2026-11-22",
        )
        under_curls = [row["id"] for row in under["exercises"] if pattern_family(row) == CURL_FAMILY]
        self.assertLessEqual(len(under_curls), 2, under_curls)
        self.assertGreaterEqual(len(under_curls), 1, [row["id"] for row in under["exercises"]])
        heavy = []
        for day, name in (
            ("2026-11-16", "DB Curls"),
            ("2026-11-18", "DB Curls"),
            ("2026-11-20", "Preacher Curl Machine"),
        ):
            heavy.append(_session(day, "pull", name, 30, 3, 12))
        heavy.append(_session("2026-11-21", "push", "DB Flat Press", 50))
        met = generate_workout_plan(
            catalog, goals, heavy, recovery_score=80, session_type="pull", as_of="2026-11-22"
        )
        met_curls = [row["id"] for row in met["exercises"] if pattern_family(row) == CURL_FAMILY]
        self.assertLessEqual(len(met_curls), 1, met_curls)


class TestPreacherCurlCatalog(unittest.TestCase):
    def test_row_is_in_every_catalog_the_app_loads_and_credits_biceps(self):
        paths = [
            REPO / "fitness" / "exercises" / "catalog.json",
            ROOT / "fitness" / "exercises" / "catalog.json",
        ]
        found = []
        for path in paths:
            self.assertTrue(path.is_file(), path)
            data = json.loads(path.read_text(encoding="utf-8"))
            row = next(ex for ex in data["exercises"] if ex.get("id") == "preacher-curl-machine")
            found.append(row)
            self.assertEqual(row["name"], "Preacher Curl Machine")
            self.assertEqual(row["movement"], "isolation")
            self.assertIn("pull", row["session_types"])
            self.assertEqual(row["primary_muscles"], ["biceps"])
            self.assertEqual(row["rep_range"], [8, 15])
            self.assertTrue(row["available"])
            self.assertEqual(row.get("pattern_family"), "curl")
        loaded, source = load_workspace_catalog()
        self.assertEqual(source, CATALOG_PATH)
        loaded_row = next(ex for ex in loaded["exercises"] if ex.get("id") == "preacher-curl-machine")
        self.assertEqual(loaded_row["name"], "Preacher Curl Machine")
        candidates = [p for p in _workspace_file_candidates(CATALOG_PATH) if p.is_file()]
        self.assertTrue(any(p.name == "catalog.json" for p in candidates))
        primary, secondary = volume_groups(found[0])
        credits = credit_proper_sets(primary, secondary, 2)
        self.assertEqual(credits.get("biceps"), 2.0)
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-11-22",
                        "session_type": "pull",
                        "exercises": [
                            {
                                "name": "Preacher Curl Machine",
                                "sets": [{"weight_lbs": 40, "sets": 2, "reps": 12}],
                            }
                        ],
                    }
                ],
                "workout_store": {"catalog": {"exercises": [found[0]]}, "goals": {}},
            },
            as_of="2026-11-22",
        )
        biceps = (report.get("muscle_sets") or {}).get("biceps") or {}
        self.assertGreaterEqual(float(biceps.get("proper_sets") or 0), 2.0)


if __name__ == "__main__":
    unittest.main()
