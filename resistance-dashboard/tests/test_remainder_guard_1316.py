"""#1316 follow-up: an unstarted saved row is not a remainder (pin check
applies); a started one keeps its remainder logic; the parity/smoke guard
covers nutrition_meal_plan. No live SuperGrok, no Turso."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from rt_dashboard import meal_plan_store as mps
from rt_dashboard.agent_plan import ensure_today_grok_plan, fill_stamped_workout, saved_plan_problem
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.training_day import frozen_plan_remainder, remainder_public, remainder_started
from rt_dashboard.workout_plan_store import (
    clear_memory_workout_plans,
    load_last_good_workout_plan,
    save_last_good_workout_plan,
)
from rt_dashboard.workout_store import load_workspace_catalog, load_workspace_goals

CATALOG, _ = load_workspace_catalog()
GOALS, _ = load_workspace_goals()
DAY = "2026-10-21"
UID = "u-rem-1316"

# The live 10/21 Pull row on prism (saved before the pin existed).
LIVE_1021 = [("Pulldowns", 112.5, 3, 5), ("Seated Cable Row", 115.0, 3, 8),
             ("Smith Barbell Row", 100.0, 2, 5), ("DB Curls", 25.0, 3, 9), ("Face Pulls", 20.0, 3, 14)]
OBJECTIVE = {
    "frozen": True, "session_type": "pull", "target_n": 5,
    "leaves": [{"slug": "ex-" + n.lower().replace(" ", "-"), "title": f"{n} ({w} lb {s}×{r})",
                "sets": s, "reps": r, "weight_lbs": w, "volume": w * s * r} for n, w, s, r in LIVE_1021],
}


def _row(remainder):
    return {
        "session_type": "pull", "is_rest_day": False, "empty": False, "source": "grok",
        "exercises": [{"name": n, "prescription": {"sets": s, "reps": r, "weight_lbs": w}}
                      for n, w, s, r in LIVE_1021],
        "remainder": remainder,
        "context": {"remainder": remainder},
    }


def _legacy_unstarted():
    """Exactly the live shape: remainder copied from the frozen target, no
    logged_sets field (saved before this change), every lift at planned."""
    rem = remainder_public(frozen_plan_remainder(OBJECTIVE, []))
    rem.pop("logged_sets"); rem.pop("target_lifts")
    return rem


def _grok_with_pin():
    return {"ok": True, "model": "grok-test", "meal": {"items": [], "empty": True},
            "workout": {"session_type": "pull", "is_rest_day": False, "empty": False,
                        "source": "grok", "message": "stub",
                        "exercises": [{"name": n, "prescription": {"sets": 3, "reps": 8, "weight_lbs": 50}}
                                      for n in ("Assisted Pullups", "Seated Cable Row", "Face Pulls")]}}


class RemainderStartedTests(unittest.TestCase):
    def test_unstarted_target_is_not_started(self):
        self.assertFalse(remainder_started(remainder_public(frozen_plan_remainder(OBJECTIVE, []))))
        self.assertFalse(remainder_started(_legacy_unstarted()))

    def test_logged_sets_start_it(self):
        sess = [Session(date=DAY, session_type="pull",
                        exercises=[ExerciseEntry(name="Pulldowns", sets=[SetEntry(112.5, 2, 5)])])]
        rem = remainder_public(frozen_plan_remainder(OBJECTIVE, sess))
        self.assertEqual(rem["logged_sets"], 2)
        self.assertTrue(remainder_started(rem))

    def test_finished_lift_dropping_out_starts_it(self):
        sess = [Session(date=DAY, session_type="pull",
                        exercises=[ExerciseEntry(name="Face Pulls", sets=[SetEntry(20.0, 3, 14)])])]
        rem = remainder_public(frozen_plan_remainder(OBJECTIVE, sess))
        rem.pop("logged_sets")  # even without the counter, the dropped lift shows it
        self.assertEqual(len(rem["lifts"]), 4)
        self.assertTrue(remainder_started(rem))


class _Base(unittest.TestCase):
    def setUp(self):
        clear_memory_workout_plans()
        self.p = mock.patch("rt_dashboard.agent_plan._library_for_plan", return_value=(CATALOG, None))
        self.p.start()
        self.ctx = {"day": DAY, "catalog": CATALOG, "goals": GOALS, "sessions": [], "recovery": {},
                    "stamped": {"session_type": "pull", "is_rest_day": False, "exercises": [],
                                "next_session_type": "pull"}}

    def tearDown(self):
        self.p.stop()
        clear_memory_workout_plans()


class UnstartedRowRegeneratedTests(_Base):
    def test_a_unstarted_saved_pull_row_without_pin_is_regenerated(self):
        save_last_good_workout_plan(UID, DAY, _row(_legacy_unstarted()))
        self.assertIn("assisted-pullups",
                      saved_plan_problem(load_last_good_workout_plan(UID, DAY), "pull", ["assisted-pullups"]))
        with mock.patch("rt_dashboard.agent_plan.generate_grok_plans", return_value=_grok_with_pin()) as gen:
            out = ensure_today_grok_plan(UID, day=DAY, context=dict(self.ctx))
        gen.assert_called_once()
        self.assertTrue(out["ok"])
        self.assertEqual(out["workout"]["exercises"][0]["name"], "Assisted Pullups")
        self.assertEqual(load_last_good_workout_plan(UID, DAY)["exercises"][0]["name"], "Assisted Pullups")

    def test_a_token_feed_does_not_serve_unstarted_row(self):
        save_last_good_workout_plan(UID, DAY, _row(_legacy_unstarted()))
        slot = {"session_type": "pull", "is_rest_day": False, "exercises": []}
        out = fill_stamped_workout(UID, day=DAY, workout=slot, goals=GOALS, read_only=True)
        self.assertEqual(out.get("exercises"), [])  # the stamp, not the stale row


class StartedRowKeptTests(_Base):
    def _started(self, lift="Pulldowns", sets=2):
        sess = [Session(date=DAY, session_type="pull",
                        exercises=[ExerciseEntry(name=lift, sets=[SetEntry(112.5, sets, 5)])])]
        return remainder_public(frozen_plan_remainder(OBJECTIVE, sess))

    def test_b_started_row_keeps_remainder_logic(self):
        save_last_good_workout_plan(UID, DAY, _row(self._started()))
        with mock.patch("rt_dashboard.agent_plan.generate_grok_plans") as gen:
            out = ensure_today_grok_plan(UID, day=DAY, context=dict(self.ctx))
        gen.assert_not_called()
        self.assertEqual(out["skipped"], "already_generated")
        self.assertEqual(out["workout"]["exercises"][0]["name"], "Pulldowns")

    def test_deploy_mirror_chris_logged_sets_on_todays_pull_row(self):
        """Later deploy: if Chris has logged sets on today's Pull row (the live
        10/21 row, no Assisted Pullups), the started-row rule leaves it alone on
        every path: SuperGrok session path, token feed, and validity check."""
        rem = self._started("Pulldowns", 1)
        self.assertTrue(remainder_started(rem))
        save_last_good_workout_plan(UID, DAY, _row(rem))
        before = load_last_good_workout_plan(UID, DAY)
        self.assertIsNone(saved_plan_problem(before, "pull", ["assisted-pullups"]))
        with mock.patch("rt_dashboard.agent_plan.generate_grok_plans") as gen:
            out = ensure_today_grok_plan(UID, day=DAY, context=dict(self.ctx))
        gen.assert_not_called()
        self.assertEqual([e["name"] for e in out["workout"]["exercises"]], [n for n, *_ in LIVE_1021])
        slot = {"session_type": "pull", "is_rest_day": False, "exercises": []}
        fed = fill_stamped_workout(UID, day=DAY, workout=slot, goals=GOALS, read_only=True)
        self.assertEqual([e["name"] for e in fed["exercises"]], [n for n, *_ in LIVE_1021])
        self.assertEqual(load_last_good_workout_plan(UID, DAY), before)


MEAL = {"items": [{"name": "Greek yogurt", "portion_g": 250}], "meals": [], "message": "m",
        "generated_at": "2026-10-21T19:19:09Z", "nutrition_day": {"window_end": "15:19"},
        "consumed": {"calories": 710}, "notes": {"veg_slot_filled": True}}


class MealGuardTests(unittest.TestCase):
    def _save(self, plan, prior, day=DAY, guard=True):
        env = {"FITDASH_PARITY": "1"} if guard else {}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True), \
             mock.patch("rt_dashboard.workout_plan_store.current_training_day", return_value=DAY), \
             mock.patch.object(mps, "_turso_get_meal_plan", return_value=prior), \
             mock.patch.object(mps, "_turso_put_meal_plan") as put:
            if not guard:
                for k in ("FITDASH_PARITY", "FITDASH_SMOKE", "FITDASH_PLAN_WRITE_GUARD"):
                    os.environ.pop(k, None)
            out = mps.save_last_good_meal_plan("u", day, plan)
        return out, put

    def test_c_guarded_run_writes_nothing_when_only_refresh_fields_changed(self):
        fresh = {**MEAL, "generated_at": "2026-10-21T19:32:50Z", "nutrition_day": {"window_end": "15:32"},
                 "consumed": {"calories": 710, "date": DAY}, "notes": {"veg_slot_filled": True, "x": 1}}
        out, put = self._save(fresh, dict(MEAL))
        put.assert_not_called()
        self.assertTrue(out["ok"])
        self.assertEqual(out["store"], "guard_unchanged")

    def test_guarded_run_refuses_other_days(self):
        out, put = self._save(dict(MEAL), None, day="2026-10-20")
        put.assert_not_called()
        self.assertEqual(out["store"], "guard_refused")

    def test_guarded_run_writes_real_change_on_current_day(self):
        changed = {**MEAL, "items": [{"name": "Chicken", "portion_g": 200}]}
        _out, put = self._save(changed, dict(MEAL))
        put.assert_called_once()

    def test_unguarded_save_unchanged_behavior(self):
        _out, put = self._save(dict(MEAL), dict(MEAL), guard=False)
        put.assert_called_once()


if __name__ == "__main__":
    unittest.main()
