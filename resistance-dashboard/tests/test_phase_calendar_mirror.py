"""App phase calendar mirrors the repo-root steps schedule (#1364 follow-up).

The coach card reads the bundled copy under resistance-dashboard/. The
repo-root file can move a step date without that copy, so the two
tracks.steps.schedule lists must stay equal.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from rt_dashboard.coach_steps import clear_rules_cache
from rt_dashboard.coach_weekly import current_calendar_step

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
REL = Path("fitness") / "coach" / "phase_calendar_goal3b.json"


def _steps_schedule(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["tracks"]["steps"]["schedule"]


class PhaseCalendarMirror(unittest.TestCase):
    def tearDown(self):
        clear_rules_cache()

    def test_steps_schedules_match(self):
        self.assertEqual(_steps_schedule(REPO / REL), _steps_schedule(APP / REL))

    def test_current_calendar_step_is_8000_on_2026_11_03(self):
        clear_rules_cache()
        self.assertEqual(current_calendar_step("2026-11-03"), 8000)
