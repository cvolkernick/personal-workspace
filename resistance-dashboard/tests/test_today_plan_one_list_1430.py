"""#1430: Today (/api/dashboard), the agent feed and the quest board show one list.

Before the first set, ``prefer_rendered_session`` shows the local planner.
After it, the saved row. Today kept rendering the local planner, so the quest
board and agent feed flipped to a list the UI never showed once a set was
logged. The dashboard now reads the same chosen plan, and the shown local list
is pinned as the day's row so it stays fixed after the first logged set.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.agent_today import export_agent_today

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-11-25"


def _row(name, sets, reps, load):
    return {
        "name": name,
        "sets": sets,
        "reps": reps,
        "weight_lbs": load,
        "load": load,
        "prescription": {"sets": sets, "reps": reps, "weight_lbs": load},
    }


LOCAL = [
    _row("DB Flat Press", 4, 6, 50.0),
    _row("DB Incline Press", 2, 8, 45.0),
    _row("DB Shoulder Press", 2, 5, 35.0),
    _row("Tricep Pushdowns", 2, 12, 40.0),
    _row("Lateral Raises", 2, 9, 15.0),
]
SAVED_GROK = {
    "session_type": "push",
    "is_rest_day": False,
    "source": "grok",
    "message": "Push session.",
    "exercises": [
        _row("DB Incline Press", 3, 8, 45.0),
        _row("DB Shoulder Press", 2, 5, 35.0),
        _row("Smith Flat Bench", 3, 5, 75.0),
        _row("Lateral Raises", 3, 9, 15.0),
        _row("Tricep Pushdowns", 3, 12, 40.0),
    ],
}
EXPECTED = [(r["name"], r["sets"], r["reps"], r["weight_lbs"]) for r in LOCAL]


def _payload(store_rows, sessions):
    plan = {
        "date": DAY,
        "session_type": "push",
        "is_rest_day": False,
        "message": "Suggested PUSH session.",
        "exercises": copy.deepcopy(store_rows),
    }
    return {
        "meta": {"local_today": DAY},
        "coach": {"today": {"date": DAY, "workout": {"session_type": "push", "exercises": copy.deepcopy(store_rows)}}},
        "workout_store": {"plan": plan, "goals": {"rotation": ["push", "pull", "legs"]}},
        "sessions": sessions,
        "workout": {},
    }


def _sig(rows):
    out = []
    for ex in rows:
        rx = ex.get("prescription") if isinstance(ex.get("prescription"), dict) else {}
        sets = ex.get("sets") if ex.get("sets") is not None else rx.get("sets")
        reps = ex.get("reps") if ex.get("reps") is not None else rx.get("reps")
        load = ex.get("weight_lbs") if ex.get("weight_lbs") is not None else rx.get("weight_lbs")
        out.append((ex["name"], int(sets), int(reps), float(load)))
    return out


class TodayPlanOneListTests(unittest.TestCase):
    def setUp(self):
        self.store = {DAY: copy.deepcopy(SAVED_GROK)}
        self.saves = []

    def _save(self, user_id, day, plan):
        self.saves.append((user_id, day))
        self.store[day] = copy.deepcopy(plan)
        return {"ok": True, "store": "test"}

    def _fill(self, *_a, **kw):
        # fill_stamped_workout(read_only) returns the day's saved row.
        return copy.deepcopy(self.store[kw["day"]])

    def _surfaces(self, payload, **kw):
        from server import _overlay_agent_workout

        with mock.patch("rt_dashboard.agent_plan.stamp_and_fill_workout", side_effect=self._fill), \
             mock.patch("rt_dashboard.agent_plan.save_last_good_workout_plan", side_effect=self._save):
            served = _overlay_agent_workout(copy.deepcopy(payload), "user-1", **kw)
        dashboard = _sig(served["workout_store"]["plan"]["exercises"])
        quest = _sig(served["coach"]["today"]["workout"]["exercises"])  # ensure_daily_tasks input
        feed = _sig(export_agent_today(served)["today"]["workout"]["plan_exercises"])
        return dashboard, quest, feed

    def test_three_surfaces_match_before_and_after_one_logged_set(self):
        before = self._surfaces(_payload(LOCAL, []), read_only=True, pin=True)
        for surface in before:
            self.assertEqual(surface, EXPECTED)
        # The shown local list is now the saved row (once).
        self.assertEqual(self.saves, [("user-1", DAY)])
        self.assertEqual(_sig(self.store[DAY]["exercises"]), EXPECTED)
        self.assertEqual(self.store[DAY]["source"], "local")

        logged = [{
            "date": DAY,
            "session_type": "push",
            "exercises": [{"name": "DB Flat Press", "sets": [{"weight_lbs": 50, "sets": 1, "reps": 6}]}],
        }]
        # After a set the local planner offers the remainder; the list must not change.
        after = self._surfaces(_payload(LOCAL[1:], logged), read_only=True, pin=True)
        for surface in after:
            self.assertEqual(surface, EXPECTED)
        self.assertEqual(len(self.saves), 1)

    def test_unpinned_saved_row_would_flip_after_a_logged_set(self):
        # Documents the bug: without pinning, the feed changes after one set.
        before = self._surfaces(_payload(LOCAL, []), read_only=True, pin=False)
        self.assertEqual(before[2], EXPECTED)
        logged = [{
            "date": DAY,
            "session_type": "push",
            "exercises": [{"name": "DB Flat Press", "sets": [{"weight_lbs": 50, "sets": 1, "reps": 6}]}],
        }]
        after = self._surfaces(_payload(LOCAL[1:], logged), read_only=True, pin=False)
        self.assertNotEqual(after[2], EXPECTED)
        self.assertEqual(self.saves, [])

    def test_agent_token_read_only_default_never_pins(self):
        self._surfaces(_payload(LOCAL, []), read_only=True)
        self.assertEqual(self.saves, [])

    def test_matching_saved_row_is_not_rewritten(self):
        self.store[DAY] = {**SAVED_GROK, "exercises": copy.deepcopy(LOCAL), "source": "local"}
        self._surfaces(_payload(LOCAL, []), read_only=True, pin=True)
        self.assertEqual(self.saves, [])

    def test_dashboard_route_overlays_the_chosen_plan(self):
        text = (ROOT / "server.py").read_text(encoding="utf-8")
        i = text.find('if parsed.path == "/api/dashboard":')
        self.assertGreater(i, 0)
        block = text[i : i + 1600]
        self.assertIn("_overlay_agent_workout(", block)
        self.assertIn("read_only=True", block)
        self.assertIn("pin=True", block)


if __name__ == "__main__":
    unittest.main()
