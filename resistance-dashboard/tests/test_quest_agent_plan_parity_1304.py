"""#1304: quest TRAINING rows equal /api/agent/today plan_exercises."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.agent_plan import overlay_workout_on_payload
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.daily_plan_tasks import plan_preview

DAY = "2026-10-20"
PLAN = [
    {"name": "Barbell Flat Bench Press", "sets": 2, "reps": 6, "weight_lbs": 95},
    {"name": "DB Incline Press", "sets": 3, "reps": 5, "weight_lbs": 45},
    {"name": "DB Shoulder Press", "sets": 2, "reps": 5, "weight_lbs": 35},
    {"name": "Lateral Raises", "sets": 3, "reps": 13, "weight_lbs": 10},
    {"name": "Tricep Pushdowns (Rope)", "sets": 3, "reps": 11, "weight_lbs": 42.5},
]
# What prism's quest cache held: local-planner remainder at 1 set each.
STALE = {
    "frozen": True, "target_n": 5, "session_type": "push",
    "leaves": [
        {"slug": "ex-db-flat-press", "title": "DB Flat Press (45.0 lb 1×5)", "sets": 1, "reps": 5, "weight_lbs": 45.0},
        {"slug": "ex-smith-shrugs", "title": "Smith Shrugs (100.0 lb 1×8)", "sets": 1, "reps": 8, "weight_lbs": 100.0},
        {"slug": "ex-lateral-raises", "title": "Lateral Raises (10.0 lb 1×13)", "sets": 1, "reps": 13, "weight_lbs": 10.0},
        {"slug": "ex-db-incline-press", "title": "DB Incline Press (45.0 lb 1×5)", "sets": 1, "reps": 5, "weight_lbs": 45.0},
        {"slug": "ex-db-shoulder-press", "title": "DB Shoulder Press (35.0 lb 1×5)", "sets": 1, "reps": 5, "weight_lbs": 35.0},
    ],
}
ROW = re.compile(r"^(?P<name>.+) \((?P<w>[\d.]+) lb (?P<s>\d+)×(?P<r>\d+)\)$")


def _payload():
    local = {"session_type": "push", "exercises": [
        {"name": "DB Flat Press", "sets": 1, "reps": 5, "weight_lbs": 45},
        {"name": "Smith Shrugs", "sets": 1, "reps": 8, "weight_lbs": 100},
    ]}
    return {
        "meta": {"local_today": DAY},
        "coach": {"today": {
            "date": DAY,
            "actions": [{"id": "train-session", "kind": "training", "priority": 1,
                         "text": "Complete today's PUSH session (5 lifts as prescribed)."}],
            "workout": local,
        }},
    }


def _quest_rows(payload, saved):
    cache = {DAY: {"list_id": None, "ids": {}, "local_completed": {"cardio|azm": True},
                   "training_objective": saved}}
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "daily_quest_cache.json").write_text(json.dumps(cache), encoding="utf-8")
        with mock.patch.dict("os.environ", {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp}):
            prev = plan_preview(payload["coach"]["today"], day=DAY)
    train = [g for g in prev["groups"] if g["group"] == "training"][0]
    rows = []
    for it in train["items"]:
        if str(it["slug"]).startswith("ex-"):
            m = ROW.match(it["title"])
            assert m, it["title"]
            rows.append((m["name"], int(m["s"]), int(m["r"]), float(m["w"])))
    return rows


def _agent_rows(payload):
    wo = export_agent_today(payload)["today"]["workout"]
    return [(e["name"], int(e["sets"]), int(e["reps"]), float(e["weight_lbs"]))
            for e in wo["plan_exercises"]]


class QuestAgentPlanParity(unittest.TestCase):
    def _overlaid(self):
        return overlay_workout_on_payload(
            _payload(), {"session_type": "push", "exercises": PLAN, "source": "grok"}
        )

    def test_quest_rows_equal_plan_exercises(self):
        data = self._overlaid()
        self.assertEqual(_quest_rows(data, None), _agent_rows(data))

    def test_stale_one_set_objective_replaced_by_agent_plan(self):
        data = self._overlaid()
        rows = _quest_rows(data, STALE)
        self.assertEqual(rows, _agent_rows(data))
        names = [r[0] for r in rows]
        self.assertNotIn("Smith Shrugs", names)
        self.assertNotIn("DB Flat Press", names)
        self.assertIn("Tricep Pushdowns (Rope)", names)

    def test_daily_tasks_handler_uses_agent_overlay(self):
        text = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
        i = text.find('parsed.path == "/api/daily-tasks"')
        self.assertIn("_overlay_agent_workout(", text[i : i + 1500])


if __name__ == "__main__":
    unittest.main()
