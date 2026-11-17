"""Main compounds rotate by recency, on the rule path and the AI path."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from rt_dashboard.grok_planner import generate_grok_plans
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    default_catalog,
    generate_workout_plan,
    primary_hinge_key,
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

STUB = {
    "push": [
        {"name": "DB Flat Press", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 50}},
        {"name": "DB Incline Press", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 40}},
        {"name": "DB Shoulder Press", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 30}},
        {"name": "Lateral Raises", "prescription": {"sets": 2, "reps": 12, "weight_lbs": 15}},
    ],
    "pull": [
        {"name": "Seated Cable Row", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 90}},
        {"name": "Pulldowns", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 100}},
        {"name": "DB Curls", "prescription": {"sets": 2, "reps": 10, "weight_lbs": 25}},
    ],
    "legs": [
        {"name": "Leg Press", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 360}},
        {"name": "RDL", "prescription": {"sets": 4, "reps": 6, "weight_lbs": 185}},
        {"name": "Seated Leg Curl", "prescription": {"sets": 2, "reps": 10, "weight_lbs": 90}},
    ],
}


def _sets(row):
    return int((row.get("prescription") or {}).get("sets") or 0)


def _compound_names(plan):
    names = []
    for row in plan.get("exercises") or []:
        if row.get("movement") == "compound" or primary_hinge_key(row):
            names.append(row["name"])
    return tuple(names)


def _as_session(day, letter, plan):
    return Session(
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


class CompoundRotation(unittest.TestCase):
    def setUp(self):
        self.catalog = default_catalog()

    def test_identical_llm_output_still_rotates_compounds(self):
        state = {"letter": "push"}

        def fake_chat(messages, **kwargs):
            # The model returns the same lifts every day of a given split.
            letter = state["letter"]
            return {
                "answer": json.dumps({
                    "meal": {"message": "ok", "items": [], "meals": []},
                    "workout": {
                        "session_type": letter,
                        "is_rest_day": False,
                        "message": "same",
                        "exercises": STUB[letter],
                    },
                }),
                "model": "grok-test",
                "auth_source": "test",
            }

        days = {
            "push": ["2026-11-11", "2026-11-14", "2026-11-17"],
            "pull": ["2026-11-12", "2026-11-15", "2026-11-18"],
            "legs": ["2026-11-11", "2026-11-14", "2026-11-17"],
        }
        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={"token": "t", "source": "test", "expired": False},
        ), mock.patch(
            "rt_dashboard.grok_ask.chat_completions",
            side_effect=fake_chat,
        ):
            for letter, stamps in days.items():
                history = []
                compounds = []
                state["letter"] = letter
                for day in stamps:
                    out = generate_grok_plans(
                        "compound-rotation",
                        recovery={"score": 80, "label": "Ready"},
                        goals=dict(RAMP),
                        catalog=self.catalog,
                        sessions=history,
                        next_session_type=letter,
                        as_of=day,
                    )
                    self.assertTrue(out["ok"], out)
                    plan = out["workout"]
                    compounds.append(_compound_names(plan))
                    history.append(_as_session(day, letter, plan))
                self.assertNotEqual(compounds[0], compounds[1], (letter, compounds))
                self.assertNotEqual(compounds[1], compounds[2], (letter, compounds))

    def test_rule_path_rotates_compounds(self):
        days = {
            "push": ["2026-11-11", "2026-11-14", "2026-11-17"],
            "pull": ["2026-11-12", "2026-11-15", "2026-11-18"],
            "legs": ["2026-11-11", "2026-11-14", "2026-11-17"],
        }
        anchor = Session(
            date="2026-11-10",
            session_type="pull",
            exercises=[
                ExerciseEntry(name="Pulldowns", sets=[SetEntry(weight_lbs=100, sets=2, reps=8)])
            ],
        )
        for letter, stamps in days.items():
            history = [] if letter == "pull" else [anchor]
            if letter == "pull":
                history = [
                    Session(
                        date="2026-11-10",
                        session_type="push",
                        exercises=[
                            ExerciseEntry(
                                name="DB Flat Press",
                                sets=[SetEntry(weight_lbs=50, sets=2, reps=8)],
                            )
                        ],
                    )
                ]
            prior = []
            compounds = []
            for day in stamps:
                plan = generate_workout_plan(
                    self.catalog,
                    dict(RAMP),
                    history,
                    recovery_score=80,
                    session_type=letter,
                    as_of=day,
                    prior_plans=list(prior),
                )
                compounds.append(_compound_names(plan))
                prior.append({
                    "date": day,
                    "session_type": letter,
                    "exercises": plan["exercises"],
                })
                history.append(_as_session(day, letter, plan))
            self.assertNotEqual(compounds[0], compounds[1], (letter, compounds))
            self.assertNotEqual(compounds[1], compounds[2], (letter, compounds))

    def test_legs_hinge_caps_at_three_and_only_the_squat_reaches_four(self):
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
        prior = []
        for day in ("2026-11-11", "2026-11-14", "2026-11-17", "2026-11-20"):
            plan = generate_workout_plan(
                self.catalog,
                dict(RAMP),
                history,
                recovery_score=80,
                session_type="legs",
                as_of=day,
                prior_plans=list(prior),
            )
            saw_squat = False
            for row in plan["exercises"]:
                sets = _sets(row)
                self.assertGreaterEqual(sets, 2, row["name"])
                self.assertLessEqual(sets, 4, row["name"])
                if primary_hinge_key(row):
                    self.assertLessEqual(sets, 3, row["name"])
                if row["id"] in ("hack-squat", "leg-press"):
                    saw_squat = True
                    self.assertLessEqual(sets, 4)
                elif row.get("movement") == "compound":
                    self.assertLessEqual(sets, 3, row["name"])
            self.assertTrue(saw_squat, [row["name"] for row in plan["exercises"]])
            prior.append({"date": day, "session_type": "legs", "exercises": plan["exercises"]})
            history.append(_as_session(day, "legs", plan))
        ids = []
        # Re-read the four plans' squat choice from history names.
        for session in history[1:]:
            names = [ex.name for ex in session.exercises]
            self.assertTrue("Hack Squat" in names or "Leg Press" in names)
            ids.append("Hack Squat" in names)
        for left, right in zip(ids, ids[1:]):
            self.assertTrue(left or right)

    def test_hack_stays_before_leg_press(self):
        from rt_dashboard.workout_planner import postshape_ai_workout

        out = postshape_ai_workout(
            {
                "source": "grok",
                "session_type": "legs",
                "is_rest_day": False,
                "exercises": [
                    {"name": "Leg Press", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 300}},
                    {"name": "Hack Squat", "prescription": {"sets": 2, "reps": 8, "weight_lbs": 180}},
                    {"name": "RDL", "prescription": {"sets": 2, "reps": 6, "weight_lbs": 135}},
                ],
            },
            self.catalog,
            [],
            goals=dict(RAMP),
            as_of="2026-11-18",
        )
        names = [row["name"] for row in out["exercises"]]
        self.assertLess(names.index("Hack Squat"), names.index("Leg Press"))
        for row in out["exercises"]:
            if primary_hinge_key(row):
                self.assertLessEqual(_sets(row), 3)

    def test_legs_open_with_a_squat_and_the_hinge_follows(self):
        history = [
            Session(
                date="2026-11-17",
                session_type="legs",
                exercises=[
                    ExerciseEntry(name="Hack Squat", sets=[SetEntry(weight_lbs=180, sets=3, reps=8)]),
                    ExerciseEntry(name="RDL", sets=[SetEntry(weight_lbs=135, sets=3, reps=6)]),
                ],
            )
        ]
        prior = []
        for day in ("2026-11-18", "2026-11-20", "2026-11-23", "2026-11-26"):
            plan = generate_workout_plan(
                self.catalog, dict(RAMP), history,
                recovery_score=80, session_type="legs", as_of=day, prior_plans=list(prior),
            )
            rows = plan["exercises"]
            self.assertIn(rows[0]["id"], ("hack-squat", "leg-press"), [r["name"] for r in rows])
            self.assertGreaterEqual(_sets(rows[0]), 3)
            self.assertLessEqual(_sets(rows[0]), 4)
            squat_at = [
                i for i, row in enumerate(rows)
                if row["id"] in ("hack-squat", "leg-press")
            ]
            hinge_at = [i for i, row in enumerate(rows) if primary_hinge_key(row)]
            self.assertEqual(len(hinge_at), 1, [r["name"] for r in rows])
            self.assertGreater(hinge_at[0], max(squat_at))
            self.assertLessEqual(_sets(rows[hinge_at[0]]), 3)
            self.assertIn("hack-squat", [row["id"] for row in rows])
            self.assertIn("leg-press", [row["id"] for row in rows])
            names = [row["name"] for row in rows]
            self.assertLess(names.index("Hack Squat"), names.index("Leg Press"))
            prior.append({"date": day, "session_type": "legs", "exercises": rows})
            history.append(_as_session(day, "legs", plan))

    def test_swap_does_not_drop_a_compound_pattern(self):
        from rt_dashboard.workout_planner import pattern_family, postshape_ai_workout

        def families(exercises):
            found = set()
            for row in exercises:
                if primary_hinge_key(row):
                    found.add("hinge")
                elif row.get("movement") == "compound":
                    fam = pattern_family(row)
                    if fam:
                        found.add(fam)
            return found

        raw = {
            "source": "grok",
            "session_type": "push",
            "is_rest_day": False,
            "exercises": STUB["push"],
        }
        first = postshape_ai_workout(raw, self.catalog, [], goals=dict(RAMP), as_of="2026-11-18")
        before = families(first["exercises"])
        second = postshape_ai_workout(
            raw,
            self.catalog,
            [_as_session("2026-11-18", "push", first)],
            goals=dict(RAMP),
            as_of="2026-11-21",
            prior_plans=[{"date": "2026-11-18", "session_type": "push", "exercises": first["exercises"]}],
        )
        after = families(second["exercises"])
        self.assertTrue(before <= after, (before, after))
        self.assertNotEqual(_compound_names(first), _compound_names(second))

    def test_push_has_no_shrugs_or_trap_work(self):
        plan = generate_workout_plan(
            self.catalog,
            dict(RAMP),
            [
                Session(
                    date="2026-11-17",
                    session_type="legs",
                    exercises=[
                        ExerciseEntry(name="Hack Squat", sets=[SetEntry(weight_lbs=180, sets=3, reps=8)])
                    ],
                )
            ],
            recovery_score=80,
            session_type="push",
            as_of="2026-11-18",
        )
        for row in plan["exercises"]:
            prim = row.get("primary_muscles") or []
            self.assertFalse(prim and all(m == "traps" for m in prim), row["name"])
            self.assertNotIn("shrug", row["name"].lower())


if __name__ == "__main__":
    unittest.main()
