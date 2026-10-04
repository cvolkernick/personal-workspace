"""LLM plan loads follow double progression (#1062)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.grok_planner import generate_grok_plans
from rt_dashboard.workout_plan_store import flatten_plan_exercises
from rt_dashboard.workout_planner import clamp_llm_plan_loads


def _rdl():
    return {
        "id": "rdl",
        "name": "RDL",
        "movement": "compound",
        "equipment": [],
        "equipment_any": ["barbell", "dumbbells"],
        "primary_muscles": ["hamstrings", "glutes"],
        "session_types": ["legs"],
        "default_sets": 2,
        "default_reps": 8,
        "available": True,
    }


def _leg_press():
    return {
        "id": "leg-press",
        "name": "Leg Press",
        "movement": "compound",
        "equipment": ["leg_press"],
        "primary_muscles": ["quads"],
        "secondary_muscles": ["glutes", "hamstrings"],
        "session_types": ["legs"],
        "default_sets": 2,
        "default_reps": 8,
        "available": True,
    }


def _curl():
    return {
        "id": "db-curls",
        "name": "DB Curls",
        "movement": "isolation",
        "equipment": ["dumbbells"],
        "primary_muscles": ["biceps"],
        "session_types": ["pull"],
        "default_sets": 2,
        "default_reps": 12,
        "available": True,
    }


def _press():
    return {
        "id": "db-flat-press",
        "name": "DB Flat Press",
        "movement": "compound",
        "equipment": ["dumbbells"],
        "primary_muscles": ["chest"],
        "session_types": ["push"],
        "default_sets": 2,
        "default_reps": 8,
        "available": True,
    }


def _log(name, weight, reps, date="2026-09-30", **extra):
    row = {
        "date": date,
        "session_type": "legs",
        "exercises": [
            {
                "name": name,
                "sets": [
                    {"weight_lbs": weight, "sets": 1, "reps": reps},
                    {"weight_lbs": weight, "sets": 1, "reps": max(1, reps - 2)},
                ],
            }
        ],
    }
    row.update(extra)
    return row


def _plan(name, weight, reps, sets=3):
    return {
        "session_type": "legs",
        "is_rest_day": False,
        "exercises": [
            {
                "name": name,
                "prescription": {"sets": sets, "reps": reps, "weight_lbs": weight},
            }
        ],
    }


def _clamp(workout, sessions, exercises, **kwargs):
    kwargs.setdefault("recovery", {"score": 80})
    kwargs.setdefault("as_of", "2026-10-01")
    kwargs.setdefault("goals", {"default_hard_sets": 2, "rotation": ["push", "pull", "legs"]})
    return clamp_llm_plan_loads(
        workout,
        sessions,
        {"exercises": exercises},
        **kwargs,
    )


class LlmLoadClamp1062(unittest.TestCase):
    def test_rdl_135_against_45x6_becomes_45x7(self):
        out = _clamp(_plan("RDL", 135, 8, sets=4), [_log("RDL", 45, 6)], [_rdl()])
        row = out["exercises"][0]
        self.assertEqual(row["weight_lbs"], 45.0)
        self.assertEqual(row["load"], 45.0)
        self.assertEqual(row["reps"], 7)
        self.assertEqual(row["prescription"]["weight_lbs"], 45.0)
        self.assertEqual(row["prescription"]["reps"], 7)
        self.assertEqual(row["sets"], 4)
        self.assertEqual(row["prescription"]["sets"], 4)
        self.assertEqual(row["load_source"], "sop")
        self.assertEqual(row["load_reason"], "45×6 last first set → hold 45, aim 7")
        self.assertEqual(row["progression_reason"], "hold_plus1")

    def test_leg_press_225_against_180x11_takes_one_step_at_5(self):
        out = _clamp(
            _plan("Leg Press", 225, 8),
            [_log("Leg Press", 180, 11)],
            [_leg_press()],
        )
        row = out["exercises"][0]
        self.assertEqual(row["weight_lbs"], 185.0)
        self.assertEqual(row["reps"], 5)
        self.assertEqual(row["load_source"], "sop")
        self.assertEqual(
            row["load_reason"], "180×11 last first set → add to 185, aim 5"
        )
        self.assertNotEqual(row["weight_lbs"], 225)

    def test_four_reps_drops_one_step(self):
        out = _clamp(
            _plan("DB Flat Press", 135, 8),
            [_log("DB Flat Press", 100, 4, session_type="push")],
            [_press()],
        )
        row = out["exercises"][0]
        self.assertEqual(row["weight_lbs"], 95.0)
        self.assertEqual(row["reps"], 5)
        self.assertEqual(row["load_source"], "sop")
        self.assertEqual(
            row["load_reason"], "100×4 last first set → drop to 95, aim 5"
        )

    def test_isolation_band_holds_inside_8_to_15(self):
        out = _clamp(
            _plan("DB Curls", 40, 8),
            [_log("DB Curls", 20, 12, session_type="pull")],
            [_curl()],
        )
        row = out["exercises"][0]
        self.assertEqual(row["weight_lbs"], 20.0)
        self.assertEqual(row["reps"], 13)
        self.assertEqual(row["rep_range"], "8-15")
        self.assertEqual(row["load_source"], "sop")
        self.assertEqual(row["load_reason"], "20×12 last first set → hold 20, aim 13")

    def test_deload_override_beats_the_llm_and_the_sop_step(self):
        out = _clamp(
            _plan("RDL", 135, 8),
            [_log("RDL", 45, 6)],
            [_rdl()],
            goals={"deload": True, "default_hard_sets": 2},
            recovery={"score": 80},
        )
        row = out["exercises"][0]
        self.assertEqual(row["load_source"], "deload")
        self.assertEqual(row["progression_reason"], "deload_override")
        self.assertEqual(row["weight_lbs"], 40.5)
        self.assertNotEqual(row["weight_lbs"], 135)
        self.assertNotEqual(row["reps"], 8)
        self.assertIn("deload", row["load_reason"])

    def test_deload_log_is_not_the_baseline(self):
        sessions = [
            _log("RDL", 135, 8, date="2026-10-03", notes="Restore deload"),
            _log("RDL", 45, 6, date="2026-09-30"),
        ]
        out = _clamp(_plan("RDL", 225, 8), sessions, [_rdl()], as_of="2026-10-04")
        row = out["exercises"][0]
        self.assertEqual(row["load_source"], "sop")
        self.assertEqual(row["weight_lbs"], 45.0)
        self.assertEqual(row["reps"], 7)

    def test_new_lift_is_capped_llm_new(self):
        out = _clamp(_plan("RDL", 135, 8, sets=3), [], [_rdl()])
        row = out["exercises"][0]
        self.assertEqual(row["load_source"], "llm_new")
        self.assertEqual(row["weight_lbs"], 45.0)
        self.assertEqual(row["reps"], 8)
        self.assertEqual(row["sets"], 3)
        self.assertEqual(row["load_reason"], "no history → capped at 45, aim 8")
        self.assertNotIn("progression_reason", row)

    def test_new_lift_under_the_cap_keeps_the_model_load(self):
        out = _clamp(_plan("DB Curls", 10, 12), [], [_curl()])
        row = out["exercises"][0]
        self.assertEqual(row["load_source"], "llm_new")
        self.assertEqual(row["weight_lbs"], 10.0)
        self.assertEqual(row["reps"], 12)
        self.assertIn("kept LLM 10", row["load_reason"])

    def test_agent_today_exposes_load_source_and_reason(self):
        clamped = _clamp(_plan("RDL", 135, 8), [_log("RDL", 45, 6)], [_rdl()])
        flat = flatten_plan_exercises(clamped["exercises"])
        self.assertEqual(flat[0]["load_source"], "sop")
        body = export_agent_today(
            {
                "workout_store": {
                    "plan": {
                        "session_type": "legs",
                        "is_rest_day": False,
                        "exercises": flat,
                    }
                },
                "sessions": [],
                "coach": {"today": {"date": "2026-10-01"}},
            }
        )
        row = body["today"]["workout"]["plan_exercises"][0]
        self.assertEqual(row["load_source"], "sop")
        self.assertEqual(row["load_reason"], "45×6 last first set → hold 45, aim 7")
        self.assertEqual(row["weight_lbs"], 45.0)
        self.assertEqual(row["reps"], 7)

    def test_generate_grok_plans_clamps_before_persist(self):
        def fake_chat(messages, **kwargs):
            return {
                "answer": json.dumps(
                    {
                        "meal": {"message": "ok", "items": [], "meals": []},
                        "workout": {
                            "session_type": "legs",
                            "is_rest_day": False,
                            "message": "too heavy",
                            "exercises": [
                                {
                                    "name": "RDL",
                                    "prescription": {
                                        "sets": 3,
                                        "reps": 8,
                                        "weight_lbs": 135,
                                    },
                                }
                            ],
                        },
                    }
                ),
                "model": "grok-test",
                "auth_source": "supergrok_session",
            }

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={"token": "t", "source": "test", "expired": False},
        ), mock.patch(
            "rt_dashboard.grok_ask.chat_completions",
            side_effect=fake_chat,
        ):
            out = generate_grok_plans(
                "user-1",
                recovery={"score": 80, "label": "Ready"},
                goals={
                    "rotation": ["push", "pull", "legs"],
                    "default_hard_sets": 2,
                },
                catalog={"exercises": [_rdl()]},
                sessions=[_log("RDL", 45, 6)],
                next_session_type="legs",
                as_of="2026-10-01",
            )
        self.assertTrue(out["ok"])
        row = out["workout"]["exercises"][0]
        self.assertEqual(row["load_source"], "sop")
        self.assertEqual(row["weight_lbs"], 45.0)
        self.assertEqual(row["reps"], 7)
        self.assertEqual(row["prescription"]["sets"], 3)


if __name__ == "__main__":
    unittest.main()
