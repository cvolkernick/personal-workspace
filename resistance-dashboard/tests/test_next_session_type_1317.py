"""#1317: after a closed letter rolls to an open plan, next_session_type is
the session after that plan (Pull -> Legs), not the plan itself."""

import unittest

from rt_dashboard.agent_today import _workout_today
from rt_dashboard.agent_plan import ensure_today_grok_plan
from rt_dashboard.workout_plan_store import clear_memory_workout_plans
from unittest import mock

DAY = "2026-10-21"
PULL = [
    {"name": "Assisted Pullups", "sets": 2, "reps": 7, "weight_lbs": 70},
    {"name": "Seated Cable Row", "sets": 3, "reps": 8},
    {"name": "Face Pulls", "sets": 3, "reps": 14},
]
GOALS = {"rotation": ["push", "pull", "legs"]}


def _payload(next_st="pull"):
    slot = {"session_type": "pull", "is_rest_day": False, "exercises": PULL,
            "next_session_type": next_st}
    return {
        "workout": slot,
        "workout_store": {"goals": GOALS, "plan": dict(slot)},
        "sessions": [{
            "date": DAY, "session_type": "push", "closed_at": "2026-10-21T13:00:00Z",
            "exercises": [{"name": "DB Flat Press", "sets": [{"weight_lbs": 55, "sets": 3, "reps": 8}]}],
        }],
        "coach": {"today": {"date": DAY, "workout": dict(slot)}},
    }


class NextSessionTypeTests(unittest.TestCase):
    def test_rolled_open_pull_names_legs_next(self):
        p = _payload()
        out = _workout_today(p, p["coach"]["today"], DAY)
        self.assertEqual(out["session_type"], "pull")
        self.assertEqual(out["next_session_type"], "legs")
        self.assertFalse(out["session_closed_today"])

    def test_custom_rotation(self):
        p = _payload()
        p["workout_store"]["goals"] = {"rotation": ["pull", "push", "legs"]}
        out = _workout_today(p, p["coach"]["today"], DAY)
        self.assertEqual(out["next_session_type"], "push")

    def test_open_day_keeps_upcoming_letter_contract(self):
        p = _payload()
        p["sessions"] = []
        out = _workout_today(p, p["coach"]["today"], DAY)
        self.assertEqual(out["session_type"], "pull")
        self.assertEqual(out["next_session_type"], "pull")


class RolledStampTests(unittest.TestCase):
    def setUp(self):
        clear_memory_workout_plans()

    def tearDown(self):
        clear_memory_workout_plans()

    def test_rolled_stamp_next_is_after_rolled_letter(self):
        ctx = {"day": DAY, "goals": GOALS, "sessions": [], "recovery": {}, "catalog": {"exercises": []},
               "stamped": {"session_type": "push", "is_rest_day": False, "exercises": [],
                           "already_trained_today": True, "session_closed_today": True,
                           "next_session_type": "pull"}}
        seen = {}

        def gen(uid, **kw):
            seen.update(kw)
            return {"ok": False, "error": "stopped in test"}

        with mock.patch("rt_dashboard.agent_plan._deterministic_workout", return_value={}), \
             mock.patch("rt_dashboard.agent_plan.fallback_workout", return_value={}), \
             mock.patch("rt_dashboard.agent_plan.generate_grok_plans", side_effect=gen), \
             mock.patch("rt_dashboard.agent_plan.stamp_today_session",
                        side_effect=lambda w, *a, **k: {**w, "next_session_type": k.get("next_st_override")}):
            ensure_today_grok_plan("u-1317", day=DAY, context=ctx)
        self.assertEqual(seen.get("next_session_type"), "pull")  # today's letter for SuperGrok


if __name__ == "__main__":
    unittest.main()
