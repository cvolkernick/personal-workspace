"""Muscle-set counts use a rolling 7 ET days (#1297).

The Monday–Sunday week stays on the cap, the planner tiebreak, and
``et_week_bounds``. Tonnage weeks are not part of this module.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rt_dashboard.agent_read import build_training
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.muscle_sets import (
    accessory_tiebreak,
    build_muscle_set_report,
    et_week_bounds,
    rolling_7d_bounds,
)

ROOT = Path(__file__).resolve().parents[1]

PRESS = {
    "id": "db-flat-press",
    "name": "DB Flat Press",
    "movement": "compound",
    "primary_muscles": ["chest"],
    "volume_primary": ["chest"],
    "volume_secondary": ["triceps", "front_delts"],
    "available": True,
}
LATERAL = {
    "id": "lateral-raises",
    "name": "Lateral Raises",
    "movement": "isolation",
    "primary_muscles": ["shoulders"],
    "volume_primary": ["side_delts"],
    "volume_secondary": [],
    "available": True,
}
CATALOG = {"exercises": [PRESS, LATERAL]}


def _press(sets, day):
    return {
        "date": day,
        "session_type": "push",
        "exercises": [
            {"name": "DB Flat Press", "sets": [{"weight_lbs": 200, "sets": sets, "reps": 8}]}
        ],
    }


def _payload(sessions):
    return {"sessions": sessions, "workout_store": {"catalog": CATALOG}}


class RollingWindow(unittest.TestCase):
    def test_sunday_counts_on_monday_and_eight_days_ago_drops(self):
        # Monday 2026-10-19. Window is 2026-10-13 through 2026-10-19.
        # Sunday 10-18 counts. 10-11 is eight days before Monday and drops.
        # 10-12 is the day before the window and drops too.
        payload = _payload(
            [
                _press(1, "2026-10-11"),
                _press(4, "2026-10-12"),
                _press(2, "2026-10-13"),
                _press(1, "2026-10-18"),
            ]
        )
        report = build_muscle_set_report(payload, as_of="2026-10-19")
        self.assertEqual(
            report["window"],
            {
                "type": "rolling_7d",
                "start": "2026-10-13",
                "end": "2026-10-19",
                "label": "rolling 7 days",
            },
        )
        # 2 on the 13th + 1 on Sunday. The 11th and 12th are outside.
        self.assertEqual(report["muscle_sets"]["chest"]["proper_sets"], 3.0)
        # Calendar week is Monday 10-19 through Sunday 10-25. Only a Monday
        # log would land there, so the plan total stays 0.
        self.assertEqual(report["week_start"], "2026-10-19")
        self.assertEqual(report["week_end"], "2026-10-25")
        self.assertEqual(report["budget"]["week_proper_sets"], 0)
        self.assertEqual(report["calendar_credits"]["chest"], 0.0)
        self.assertEqual(et_week_bounds("2026-10-19"), ("2026-10-19", "2026-10-25"))

    def test_window_start_still_counts(self):
        report = build_muscle_set_report(
            _payload([_press(1, "2026-10-13")]), as_of="2026-10-19"
        )
        self.assertEqual(rolling_7d_bounds("2026-10-19"), ("2026-10-13", "2026-10-19"))
        self.assertEqual(report["muscle_sets"]["chest"]["proper_sets"], 1.0)

    def test_et_midnight_uses_the_civil_date_not_the_utc_date(self):
        # 2026-10-19 03:59Z is Sunday 23:59 EDT. 04:01Z is Monday 00:01 EDT.
        sunday_night = "2026-10-19T03:59:00+00:00"
        monday_morning = "2026-10-19T04:01:00+00:00"
        self.assertEqual(rolling_7d_bounds(sunday_night), ("2026-10-12", "2026-10-18"))
        self.assertEqual(rolling_7d_bounds(monday_morning), ("2026-10-13", "2026-10-19"))
        self.assertEqual(et_week_bounds(sunday_night), ("2026-10-12", "2026-10-18"))
        self.assertEqual(et_week_bounds(monday_morning), ("2026-10-19", "2026-10-25"))

        # UTC date is already Monday. ET date is still Sunday 23:30.
        still_sunday = "2026-10-19T03:30:00Z"
        monday_set = "2026-10-19T04:30:00Z"
        payload = _payload([_press(1, still_sunday), _press(5, monday_set)])

        before = build_muscle_set_report(payload, as_of=sunday_night)
        self.assertEqual(before["window"]["end"], "2026-10-18")
        self.assertEqual(before["muscle_sets"]["chest"]["proper_sets"], 1.0)
        self.assertEqual(before["week_start"], "2026-10-12")

        after = build_muscle_set_report(payload, as_of=monday_morning)
        self.assertEqual(after["window"]["start"], "2026-10-13")
        self.assertEqual(after["window"]["end"], "2026-10-19")
        self.assertEqual(after["window"]["label"], "rolling 7 days")
        # Sunday's set still counts. Monday's set counts too.
        self.assertEqual(after["muscle_sets"]["chest"]["proper_sets"], 6.0)
        # Plan week flipped to Monday and does not include Sunday's set.
        self.assertEqual(after["week_start"], "2026-10-19")
        self.assertEqual(after["budget"]["week_proper_sets"], 5)

        # Same instants as datetime objects, not only strings.
        late = datetime(2026, 10, 19, 3, 59, tzinfo=timezone.utc)
        early = datetime(2026, 10, 19, 4, 1, tzinfo=timezone.utc)
        self.assertEqual(rolling_7d_bounds(late)[1], "2026-10-18")
        self.assertEqual(rolling_7d_bounds(early)[1], "2026-10-19")

    def test_agent_feed_names_the_rolling_window(self):
        payload = {
            "meta": {"local_today": "2026-10-19"},
            "coach": {"today": {"date": "2026-10-19"}},
            "sessions": [_press(1, "2026-10-18")],
            "workout_store": {"catalog": CATALOG},
        }
        body = export_agent_today(payload)
        window = body["week"]["muscle_set_window"]
        self.assertEqual(window["type"], "rolling_7d")
        self.assertEqual(window["start"], "2026-10-13")
        self.assertEqual(window["end"], "2026-10-19")
        self.assertEqual(window["label"], "rolling 7 days")
        self.assertIn("rolling 7 days", json.dumps(window))
        # The feed's calendar week is still Monday through today.
        self.assertEqual(body["week"]["start"], "2026-10-19")
        self.assertEqual(body["week"]["end"], "2026-10-19")
        self.assertEqual(body["week"]["muscle_sets"]["chest"]["proper_sets"], 1.0)
        self.assertEqual(body["week"]["muscle_set_budget"]["week_proper_sets"], 0)
        training, _as_of = build_training(body)
        self.assertEqual(training["muscle_sets"]["period"], "rolling 7 days")
        self.assertEqual(training["muscle_sets"]["window"]["type"], "rolling_7d")
        self.assertEqual(training["muscle_sets"]["window"]["start"], "2026-10-13")
        self.assertEqual(training["muscle_sets"]["window"]["end"], "2026-10-19")

    def test_planner_tiebreak_stays_on_the_calendar_week(self):
        # Eight side-delt sets on Sunday. Rolling count meets the target.
        # Monday's calendar week is empty, so the accessory is still behind.
        payload = _payload(
            [
                {
                    "date": "2026-10-18",
                    "session_type": "push",
                    "exercises": [
                        {
                            "name": "Lateral Raises",
                            "sets": [{"weight_lbs": 20, "sets": 8, "reps": 12}],
                        }
                    ],
                }
            ]
        )
        report = build_muscle_set_report(payload, as_of="2026-10-19")
        self.assertEqual(report["muscle_sets"]["side_delts"]["proper_sets"], 8.0)
        self.assertEqual(report["calendar_credits"]["side_delts"], 0.0)
        self.assertGreater(accessory_tiebreak(LATERAL, report, {}), 0)


class TileCopy(unittest.TestCase):
    def test_tile_label_says_rolling_7_days(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("rolling 7 days · proper sets", html)
        self.assertIn("rolling 7 days · ${dates}proper sets", app)
        self.assertNotIn("Monday–Sunday ET", html)
        self.assertNotIn("Monday–Sunday ET", app)


if __name__ == "__main__":
    unittest.main()
