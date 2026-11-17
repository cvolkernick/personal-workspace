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


if __name__ == "__main__":
    unittest.main()
