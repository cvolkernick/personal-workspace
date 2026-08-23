"""Today hub no longer shows Log this plan; quest auto-log and Log tab stay.

Quest complete already upserts lifts via attach_lift_quest_log (#266 / #260).
The Today hub prefiller was leftover chrome. Manual override stays on Log.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
COACH = (ROOT / "rt_dashboard" / "coach.py").read_text(encoding="utf-8")
UTIL = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


class LogThisPlanRemoved(unittest.TestCase):
    def test_today_hub_has_no_log_this_plan_control(self):
        hub = HTML[HTML.find('id="today-hub"') : HTML.find('id="today-actions"')]
        self.assertIn('id="today-hub"', HTML)
        self.assertNotIn('id="btn-log-plan"', HTML)
        self.assertNotIn("Log this plan", HTML)
        self.assertNotIn("btn-log-plan", hub)
        self.assertIn('id="btn-generate-workout"', hub)
        self.assertIn("Refresh plan", hub)
        self.assertIn('id="btn-generate-meal"', hub)
        self.assertIn("Generate meal", hub)

    def test_dead_prefill_helper_removed(self):
        self.assertNotIn("function logPlanToForm", JS)
        self.assertNotIn("btn-log-plan", JS)
        self.assertIn("function prefillsFromWorkoutPlan", JS)
        self.assertIn("async function submitWorkout", JS)
        self.assertIn('id="btn-focus-log"', HTML)
        self.assertIn('$("btn-focus-log").addEventListener("click"', JS)
        self.assertIn('fetch("/api/workouts"', JS)

    def test_log_tab_copy_does_not_point_at_removed_button(self):
        log = HTML[HTML.find('id="log-card"') : HTML.find('id="history-card"')]
        self.assertIn("log-form", log)
        self.assertNotIn("Log this plan", log)
        self.assertIn("auto-log", log)
        self.assertIn("Log tab", log)

    def test_quest_complete_still_attaches_lift_log(self):
        complete = UTIL.split("def daily_tasks_complete_body", 1)[1].split(
            "def inventory_write", 1
        )[0]
        self.assertIn("attach_lift_quest_log", complete)
        self.assertIn("complete_leaf", complete)

    def test_coach_brief_does_not_name_removed_button(self):
        self.assertNotIn("Log this plan", COACH)
        self.assertIn("auto-log", COACH)
        self.assertIn("Log tab", COACH)


class HobbyAndIgnoreLock(unittest.TestCase):
    def test_hobby_function_count_stays_at_12(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])

    def test_ignore_build_unchanged(self):
        self.assertIn(
            '"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"',
            VERCEL,
        )


if __name__ == "__main__":
    unittest.main()
