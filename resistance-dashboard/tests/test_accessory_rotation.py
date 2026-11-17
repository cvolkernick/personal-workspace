"""Accessories rotate. No fixed push, pull, or legs template."""

from __future__ import annotations

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_plan_store import (
    clear_memory_workout_plans,
    save_last_good_workout_plan,
)
from rt_dashboard.workout_planner import (
    catalog_exercises,
    default_catalog,
    generate_workout_plan,
    postshape_ai_workout,
    rotation_candidates_for,
)

RAMP = {
    "rotation": ["push", "pull", "legs"],
    "exercises_per_session": 5,
    "prefer_compounds_first": True,
    "auto_focus_muscles": True,
    "focus_muscles": [],
    "default_hard_sets": 2,
    "sets_per_muscle_week_min": 4,
    "sets_per_muscle_week_max": 8,
    "sets_per_muscle_week_priority_max": 10,
    "maintenance_sets_per_muscle_week": 3,
    "session_working_set_cap": 14,
    "secondary_set_fraction": 0.5,
    "volume_sets_target": 10,
    "volume_ramp_increment": 2,
    "volume_ramp_started_on": "2026-11-09",
    "volume_ramp_equal": True,
    "pinned_lifts": {},
}

HINGES = {"rdl", "barbell-deadlift", "trap-bar-deadlift", "back-extension", "back-extension-45"}
ABS_IDS = {"ab-crunch-machine", "cable-crunch", "roman-chair-raise"}
LEGS_NEW = {"leg-extension", "machine-adduction", "machine-abduction", "back-extension-45"}


def _sets(row):
    return int((row.get("prescription") or {}).get("sets") or 0)


def _ids(plan):
    return [row["id"] for row in plan["exercises"]]


class AccessoryRotation(unittest.TestCase):
    def setUp(self):
        self.catalog = default_catalog()
        clear_memory_workout_plans()

    def tearDown(self):
        clear_memory_workout_plans()

    def _candidates(self, letter):
        pool = [
            ex for ex in catalog_exercises(self.catalog, available_only=True)
            if letter in (ex.get("session_types") or [])
        ]
        return rotation_candidates_for(letter, pool)

    def _three(self, letter, days):
        prior = []
        anchor = "pull" if letter != "pull" else "push"
        history = [
            Session(
                date="2026-11-10",
                session_type=anchor,
                exercises=[
                    ExerciseEntry(
                        name="Pulldowns",
                        sets=[SetEntry(weight_lbs=100, sets=2, reps=8)],
                    )
                ],
            )
        ]
        plans = []
        for day in days:
            plan = generate_workout_plan(
                self.catalog,
                dict(RAMP),
                history,
                recovery_score=80,
                recovery_label="Ready",
                session_type=letter,
                as_of=day,
                prior_plans=list(prior),
            )
            plans.append(plan)
            prior.append({
                "date": day,
                "session_type": letter,
                "exercises": plan["exercises"],
            })
            history.append(
                Session(
                    date=day,
                    session_type=letter,
                    exercises=[
                        ExerciseEntry(
                            name=row["name"],
                            sets=[SetEntry(weight_lbs=100, sets=_sets(row), reps=8)],
                        )
                        for row in plan["exercises"]
                    ],
                )
            )
        return plans

    def _assert_window(self, letter, days):
        plans = self._three(letter, days)
        shapes = [_ids(plan) for plan in plans]
        self.assertNotEqual(shapes[0], shapes[1], shapes)
        self.assertNotEqual(shapes[1], shapes[2], shapes)
        self.assertNotEqual(set(shapes[0]), set(shapes[1]), shapes)
        union = set().union(*(set(shape) for shape in shapes))
        for cand in self._candidates(letter):
            self.assertIn(cand["id"], union, (letter, cand["id"], shapes))
        for plan in plans:
            self.assertLessEqual(sum(_sets(row) for row in plan["exercises"]), 14)
            hinges = [eid for eid in _ids(plan) if eid in HINGES]
            self.assertLessEqual(len(hinges), 1, _ids(plan))
        return plans

    def test_three_legs_days_rotate_every_new_accessory(self):
        plans = self._assert_window("legs", ["2026-11-11", "2026-11-14", "2026-11-17"])
        union = set().union(*(_ids(plan) for plan in plans))
        self.assertTrue(LEGS_NEW <= union, union)

    def test_three_push_days_rotate_the_accessory_pool(self):
        self._assert_window("push", ["2026-11-11", "2026-11-14", "2026-11-17"])

    def test_three_pull_days_rotate_the_accessory_pool(self):
        self._assert_window("pull", ["2026-11-12", "2026-11-15", "2026-11-18"])

    def test_stored_plan_counts_as_recency(self):
        user = "rotation-user"
        save_last_good_workout_plan(
            user,
            "2026-11-11",
            {
                "session_type": "legs",
                "is_rest_day": False,
                "exercises": [
                    {"id": "leg-extension", "name": "Leg Extension Machine", "sets": 2},
                    {"id": "machine-adduction", "name": "Machine Hip Adduction", "sets": 2},
                ],
            },
        )
        plan = generate_workout_plan(
            self.catalog,
            dict(RAMP),
            [
                Session(
                    date="2026-11-13",
                    session_type="pull",
                    exercises=[
                        ExerciseEntry(
                            name="Pulldowns",
                            sets=[SetEntry(weight_lbs=100, sets=2, reps=8)],
                        )
                    ],
                )
            ],
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-14",
            user_id=user,
        )
        ids = set(_ids(plan))
        # Those two were the most recent. The other legs accessories come first.
        self.assertNotIn("leg-extension", ids)
        self.assertNotIn("machine-adduction", ids)
        self.assertTrue(ids & {"machine-abduction", "back-extension-45"})

    def test_logged_history_counts_as_recency(self):
        history = [
            Session(
                date="2026-11-11",
                session_type="legs",
                exercises=[
                    ExerciseEntry(
                        name="Leg Extension Machine",
                        sets=[SetEntry(weight_lbs=50, sets=2, reps=12)],
                    ),
                    ExerciseEntry(
                        name="Machine Hip Adduction",
                        sets=[SetEntry(weight_lbs=50, sets=2, reps=12)],
                    ),
                ],
            )
        ]
        plan = generate_workout_plan(
            self.catalog,
            dict(RAMP),
            history,
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-14",
        )
        ids = set(_ids(plan))
        self.assertNotIn("leg-extension", ids)
        self.assertNotIn("machine-adduction", ids)
        self.assertTrue(ids & {"machine-abduction", "back-extension-45"})

    def test_guards_cap_one_hinge_abs_and_hack_order(self):
        plans = self._three("legs", ["2026-11-11", "2026-11-14", "2026-11-17"])
        abs_rows = []
        for plan in plans:
            hinges = [row for row in plan["exercises"] if row["id"] in HINGES]
            self.assertLessEqual(len(hinges), 1)
            names = [row["id"] for row in hinges]
            self.assertFalse(
                "barbell-deadlift" in names and any(row["id"].startswith("back-extension") for row in hinges)
            )
            for row in plan["exercises"]:
                if row["id"] in ABS_IDS:
                    abs_rows.append(row)
                    self.assertEqual(_sets(row), 2)
        self.assertGreaterEqual(len(abs_rows), 1)
        self.assertLessEqual(len({row["id"] for row in abs_rows}), 3)

        workout = {
            "source": "grok",
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [
                {"name": "Leg Press", "prescription": {"sets": 3, "reps": 8, "weight_lbs": 360}},
                {"name": "Hack Squat", "prescription": {"sets": 3, "reps": 8, "weight_lbs": 180}},
                {"name": "RDL", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 185}},
                {"name": "Barbell Deadlift", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 225}},
                {"name": "Back Extension Machine", "prescription": {"sets": 2, "reps": 10, "weight_lbs": 70}},
                {"name": "Calf Raises", "prescription": {"sets": 3, "reps": 12, "weight_lbs": 100}},
            ],
        }
        out = postshape_ai_workout(
            workout,
            self.catalog,
            [],
            goals={"default_hard_sets": 2, "session_working_set_cap": 14},
            as_of="2026-11-18",
        )
        ids = _ids(out)
        self.assertLessEqual(sum(_sets(row) for row in out["exercises"]), 14)
        hinges = [eid for eid in ids if eid in HINGES]
        self.assertEqual(len(hinges), 1, ids)
        self.assertLess(ids.index("hack-squat"), ids.index("leg-press"))
        self.assertEqual(out["source"], "grok")

    def test_ai_input_uses_the_same_rotation(self):
        def raw(day_note):
            return {
                "source": "grok",
                "session_type": "legs",
                "is_rest_day": False,
                "exercises": [
                    {"name": "Leg Press", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 200}},
                    {"name": "RDL", "prescription": {"sets": 2, "reps": 6, "weight_lbs": 135}},
                ],
                "message": day_note,
            }

        first = postshape_ai_workout(
            raw("one"),
            self.catalog,
            [],
            goals=dict(RAMP),
            as_of="2026-11-11",
        )
        second = postshape_ai_workout(
            raw("two"),
            self.catalog,
            [],
            goals=dict(RAMP),
            as_of="2026-11-14",
            prior_plans=[{
                "date": "2026-11-11",
                "session_type": "legs",
                "exercises": first["exercises"],
            }],
        )
        self.assertNotEqual(_ids(first), _ids(second))
        for plan in (first, second):
            self.assertLessEqual(sum(_sets(row) for row in plan["exercises"]), 14)
            hinges = [eid for eid in _ids(plan) if eid in HINGES]
            self.assertLessEqual(len(hinges), 1, _ids(plan))

    def test_abs_twice_a_week_at_two_sets_and_rotates(self):
        history = []
        goals = dict(RAMP)
        days = [
            ("2026-11-09", "push"),
            ("2026-11-10", "pull"),
            ("2026-11-11", "legs"),
            ("2026-11-12", "push"),
        ]
        abs_seen = []
        for day, letter in days:
            plan = generate_workout_plan(
                self.catalog,
                goals,
                history,
                recovery_score=80,
                session_type=letter,
                as_of=day,
            )
            self.assertLessEqual(sum(_sets(row) for row in plan["exercises"]), 14)
            rows = [row for row in plan["exercises"] if row["id"] in ABS_IDS]
            self.assertLessEqual(len(rows), 1)
            if rows:
                self.assertEqual(_sets(rows[0]), 2)
                abs_seen.append((letter, rows[0]["id"]))
            history.append(
                Session(
                    date=day,
                    session_type=letter,
                    exercises=[
                        ExerciseEntry(
                            name=row["name"],
                            sets=[SetEntry(weight_lbs=100, sets=_sets(row), reps=8)],
                        )
                        for row in plan["exercises"]
                    ],
                )
            )
        letters = [letter for letter, _eid in abs_seen]
        self.assertEqual(letters, ["push", "legs"])
        self.assertNotEqual(abs_seen[0][1], abs_seen[1][1])

    def _total(self, plan):
        return sum(_sets(row) for row in plan["exercises"])

    def test_full_log_does_not_thin_the_next_session_below_ten(self):
        letters = {
            "legs": ["2026-11-11", "2026-11-14", "2026-11-17"],
            "push": ["2026-11-11", "2026-11-14", "2026-11-17"],
            "pull": ["2026-11-12", "2026-11-15", "2026-11-18"],
        }
        for letter, days in letters.items():
            plans = self._three(letter, days)
            # Day 1 is logged in full before day 2 is built.
            self.assertGreaterEqual(self._total(plans[1]), 10, (letter, _ids(plans[1])))
            for plan in plans:
                self.assertLessEqual(self._total(plan), 14)
                hinges = [eid for eid in _ids(plan) if eid in HINGES]
                self.assertLessEqual(len(hinges), 1, _ids(plan))

    def test_deload_and_low_recovery_skip_the_set_floor(self):
        history = [
            Session(
                date="2026-11-10",
                session_type="pull",
                exercises=[
                    ExerciseEntry(
                        name="Pulldowns",
                        sets=[SetEntry(weight_lbs=100, sets=2, reps=8)],
                    )
                ],
            )
        ]
        first = generate_workout_plan(
            self.catalog,
            dict(RAMP),
            history,
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-11",
        )
        logged = history + [
            Session(
                date="2026-11-11",
                session_type="legs",
                exercises=[
                    ExerciseEntry(
                        name=row["name"],
                        sets=[SetEntry(weight_lbs=100, sets=_sets(row), reps=8)],
                    )
                    for row in first["exercises"]
                ],
            )
        ]
        normal = generate_workout_plan(
            self.catalog, dict(RAMP), logged,
            recovery_score=80, session_type="legs", as_of="2026-11-14",
        )
        deload = generate_workout_plan(
            self.catalog, dict(RAMP), logged,
            recovery_score=80, deload=True, session_type="legs", as_of="2026-11-14",
        )
        low = generate_workout_plan(
            self.catalog, dict(RAMP), logged,
            recovery_score=30, session_type="legs", as_of="2026-11-14",
        )
        self.assertGreaterEqual(self._total(normal), 10)
        self.assertLess(self._total(deload), 10, _ids(deload))
        self.assertLess(self._total(low), 10, _ids(low))

    def test_ab_crunch_machine_is_the_weekly_primary(self):
        by_id = {row["id"]: row for row in self.catalog["exercises"]}
        self.assertEqual(by_id["ab-crunch-machine"]["name"], "Ab Crunch Machine")
        history = []
        weeks = [
            [
                ("2026-11-09", "push"),
                ("2026-11-10", "pull"),
                ("2026-11-11", "legs"),
                ("2026-11-12", "push"),
            ],
            [
                ("2026-11-16", "push"),
                ("2026-11-17", "pull"),
                ("2026-11-18", "legs"),
                ("2026-11-19", "push"),
            ],
        ]
        alternates = []
        for week in weeks:
            abs_names = []
            for day, letter in week:
                plan = generate_workout_plan(
                    self.catalog,
                    dict(RAMP),
                    history,
                    recovery_score=80,
                    session_type=letter,
                    as_of=day,
                )
                rows = [row for row in plan["exercises"] if row["id"] in ABS_IDS]
                self.assertLessEqual(len(rows), 1)
                if rows:
                    self.assertEqual(_sets(rows[0]), 2)
                    abs_names.append(rows[0]["name"])
                history.append(
                    Session(
                        date=day,
                        session_type=letter,
                        exercises=[
                            ExerciseEntry(
                                name=row["name"],
                                sets=[SetEntry(weight_lbs=100, sets=_sets(row), reps=8)],
                            )
                            for row in plan["exercises"]
                        ],
                    )
                )
            self.assertEqual(len(abs_names), 2, abs_names)
            self.assertIn("Ab Crunch Machine", abs_names)
            other = [name for name in abs_names if name != "Ab Crunch Machine"]
            self.assertEqual(len(other), 1, abs_names)
            self.assertIn(other[0], {"Cable Crunch", "Roman Chair Knee Raise"})
            alternates.append(other[0])
        self.assertEqual(len(set(alternates)), 2, alternates)

    def test_accessory_caps_at_three_and_only_the_main_compound_at_four(self):
        from rt_dashboard.workout_planner import (
            catalog_exercises,
            shape_session_coverage,
            training_continuity,
        )

        pool = [
            ex for ex in catalog_exercises(self.catalog, available_only=True)
            if "push" in (ex.get("session_types") or [])
        ]
        by_id = {ex["id"]: ex for ex in pool}

        def row(eid, sets):
            ex = by_id[eid]
            return {
                "id": eid,
                "name": ex["name"],
                "movement": ex["movement"],
                "priority": ex.get("priority") or 0,
                "primary_muscles": list(ex.get("primary_muscles") or []),
                "secondary_muscles": list(ex.get("secondary_muscles") or []),
                "prescription": {"sets": sets, "reps": 8, "weight_lbs": 50},
            }

        shaped = shape_session_coverage(
            [
                row("db-flat-press", 6),
                row("smith-flat-bench", 6),
                row("lateral-raises", 5),
            ],
            st="push",
            pool=pool,
            pool_by_id=by_id,
            pin_ids=[],
            session_cap=14,
            default_hard=2,
            sessions=[],
            by_id=by_id,
            catalog=self.catalog,
            day="2026-11-18",
            sec_frac=0.5,
            already_logged=lambda _row: False,
            recovery_score=80,
            continuity=training_continuity(1),
            recovery_rhr_under=False,
            equipment=None,
            deload=False,
            weighins=None,
            today_logged_ids=[],
            today_logged_names=[],
            keep_written=True,
        )
        counts = {row["id"]: _sets(row) for row in shaped}
        self.assertIn("lateral-raises", counts)
        self.assertLessEqual(counts["lateral-raises"], 3)
        self.assertLessEqual(counts["smith-flat-bench"], 3)
        self.assertLessEqual(counts["db-flat-press"], 4)
        fours = [eid for eid, sets in counts.items() if sets == 4]
        self.assertLessEqual(len(fours), 1)
        for eid, sets in counts.items():
            self.assertLessEqual(sets, 4)
            if sets > 3:
                self.assertEqual(by_id[eid]["movement"], "compound")
        self.assertLessEqual(sum(counts.values()), 14)

    def test_btn_press_is_excluded_without_a_recent_log(self):
        plan = generate_workout_plan(
            self.catalog,
            dict(RAMP),
            [
                Session(
                    date="2026-11-10",
                    session_type="pull",
                    exercises=[
                        ExerciseEntry(
                            name="Pulldowns",
                            sets=[SetEntry(weight_lbs=100, sets=2, reps=8)],
                        )
                    ],
                )
            ],
            recovery_score=80,
            session_type="push",
            as_of="2026-11-11",
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertNotIn("Behind-the-Neck Smith Press", names)
        self.assertIn("DB Shoulder Press", names)

    def test_btn_press_returns_after_a_recent_log(self):
        plan = generate_workout_plan(
            self.catalog,
            dict(RAMP),
            [
                Session(
                    date="2026-11-01",
                    session_type="push",
                    exercises=[
                        ExerciseEntry(
                            name="Behind-the-Neck Smith Press",
                            sets=[SetEntry(weight_lbs=40, sets=2, reps=10)],
                        )
                    ],
                ),
                Session(
                    date="2026-11-10",
                    session_type="pull",
                    exercises=[
                        ExerciseEntry(
                            name="Pulldowns",
                            sets=[SetEntry(weight_lbs=100, sets=2, reps=8)],
                        )
                    ],
                ),
            ],
            recovery_score=80,
            session_type="push",
            as_of="2026-11-11",
        )
        names = [row["name"] for row in plan["exercises"]]
        self.assertIn("Behind-the-Neck Smith Press", names)


if __name__ == "__main__":
    unittest.main()
