"""#1304: degenerate, stub or mislabelled saved plans are never served."""
import unittest

from rt_dashboard.agent_plan import saved_plan_problem
from rt_dashboard.training_day import remainder_is_history


def _ex(name, sets=3):
    return {"name": name, "sets": sets, "reps": "8-10"}


PULL = [_ex("Barbell Row"), _ex("Lat Pulldown"), _ex("Dumbbell Curl"), _ex("Face Pull")]
PUSH = [_ex("Bench Press"), _ex("Overhead Press"), _ex("Triceps Pushdown")]


class SavedPlanProblemTests(unittest.TestCase):
    def test_full_pull_plan_ok(self):
        self.assertIsNone(saved_plan_problem({"session_type": "pull", "exercises": PULL}, "pull"))

    def test_closed_day_stub_rejected(self):
        stub = {"session_type": "push", "already_trained_today": True,
                "exercises": [_ex("Barbell Row", 1), _ex("Lat Pulldown", 1)]}
        self.assertIn("stub", saved_plan_problem(stub))

    def test_mislabelled_rejected(self):
        self.assertIn("mislabelled", saved_plan_problem({"session_type": "push", "exercises": PULL}))

    def test_degenerate_rejected(self):
        p = {"session_type": "pull", "exercises": [_ex("Barbell Row", 1), _ex("Lat Pulldown", 1)]}
        self.assertIn("degenerate", saved_plan_problem(p, "pull"))

    def test_remainder_not_degenerate(self):
        p = {"session_type": "pull", "remainder": {"done": False},
             "exercises": [_ex("Barbell Row", 1)]}
        self.assertIsNone(saved_plan_problem(p, "pull"))

    def test_other_letter_rejected(self):
        self.assertIn("other letter", saved_plan_problem({"session_type": "push", "exercises": PUSH}, "pull"))


class RemainderIsHistoryTests(unittest.TestCase):
    def test_closed_yesterday_is_history(self):
        w = [{"closed_at": "2026-10-20T22:00:00-04:00"}]
        self.assertTrue(remainder_is_history(w, "2026-10-21", {"done": False}))

    def test_closed_today_not_history(self):
        w = [{"closed_at": "2026-10-21T02:40:00-04:00"}]
        self.assertFalse(remainder_is_history(w, "2026-10-21", {"done": False}))

    def test_open_session_not_history(self):
        self.assertFalse(remainder_is_history([{"date": "2026-10-20"}], "2026-10-21", {"done": False}))


if __name__ == "__main__":
    unittest.main()


class OverlayAndHeaderTests(unittest.TestCase):
    def test_overlay_clears_stale_closed_flags(self):
        from rt_dashboard.agent_plan import overlay_workout_on_payload

        payload = {"workout": {"session_type": "push", "already_trained_today": True,
                               "session_closed_today": True}}
        out = overlay_workout_on_payload(payload, {"session_type": "pull", "exercises": PULL})
        wo = out["coach"]["today"]["workout"]
        self.assertEqual(wo["session_type"], "pull")
        self.assertFalse(wo.get("already_trained_today"))
        self.assertFalse(wo.get("session_closed_today"))

    def test_stale_already_trained_header_retitled(self):
        from rt_dashboard.daily_plan_tasks import (
            PlannedGroup, PlannedItem, TRAIN_SESSION_SLUG, _retitle_train_session_header)

        items = [PlannedItem(group="training", slug=TRAIN_SESSION_SLUG,
                             title="Already trained today (PUSH). Next session: PULL.")]
        items += [PlannedItem(group="training", slug="ex-" + e["name"].lower().replace(" ", "-"),
                              title=e["name"]) for e in PULL]
        g = PlannedGroup(group="training", title="Training", emoji="x", items=items)
        _retitle_train_session_header(g, {"session_type": "pull", "exercises": PULL})
        self.assertEqual(items[0].title, "Complete today's PULL session (4 lifts)")
