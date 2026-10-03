"""#1024: main-lift load tracker on the existing Strength card.

Pinned lifts lead the menu. The first logged set is the point. Aliased
names share one series. Empty lifts stay empty.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard.agent_today import export_agent_today  # noqa: E402
from rt_dashboard.analytics import dashboard_payload, strength_trend  # noqa: E402
from rt_dashboard.models import ExerciseEntry, Session, SetEntry  # noqa: E402
from rt_dashboard.trends_export import export_trends_window  # noqa: E402
from rt_dashboard.workout_planner import normalize_goals  # noqa: E402
from rt_dashboard.workout_store import load_workspace_goals  # noqa: E402

MAIN = [
    "RDL",
    "Leg Press",
    "DB Incline Press",
    "DB Flat Press",
    "Seated Cable Row",
    "Pulldowns",
]
ALIASES = {
    "RDLs": "RDL",
    "Romanian Deadlift": "RDL",
    "Romanian Deadlifts": "RDL",
    "Lat Pulldown": "Pulldowns",
    "Lat Pulldowns": "Pulldowns",
    "Pull Downs": "Pulldowns",
    "Pulldown": "Pulldowns",
}


def _goals():
    return {"main_lifts": list(MAIN), "lift_aliases": dict(ALIASES)}


def _sessions():
    return [
        Session(
            date="2026-05-01",
            session_type="legs",
            exercises=[
                ExerciseEntry(
                    name="Romanian Deadlift",
                    sets=[
                        SetEntry(weight_lbs=40, sets=1, reps=8),
                        SetEntry(weight_lbs=45, sets=1, reps=6),
                    ],
                ),
                ExerciseEntry(
                    name="Leg Press",
                    sets=[SetEntry(weight_lbs=200, sets=3, reps=10)],
                ),
            ],
        ),
        Session(
            date="2026-05-08",
            session_type="legs",
            exercises=[
                ExerciseEntry(
                    name="RDLs",
                    sets=[SetEntry(weight_lbs=50, sets=1, reps=7)],
                ),
                ExerciseEntry(
                    name="Leg Press",
                    sets=[SetEntry(weight_lbs=210, sets=3, reps=8)],
                ),
            ],
        ),
        Session(
            date="2026-05-08",
            session_type="pull",
            exercises=[
                ExerciseEntry(
                    name="Lat Pulldowns",
                    sets=[SetEntry(weight_lbs=100, sets=3, reps=8)],
                ),
                ExerciseEntry(
                    name="Seated Cable Row",
                    sets=[SetEntry(weight_lbs=90, sets=1, reps=10)],
                ),
                ExerciseEntry(
                    name="DB Curls",
                    sets=[SetEntry(weight_lbs=25, sets=3, reps=12)],
                ),
            ],
        ),
        Session(
            date="2026-06-01",
            session_type="legs",
            exercises=[
                ExerciseEntry(
                    name="Romanian Deadlift",
                    sets=[SetEntry(weight_lbs=40, sets=1, reps=8)],
                ),
                ExerciseEntry(
                    name="RDL",
                    sets=[SetEntry(weight_lbs=50, sets=1, reps=5)],
                ),
            ],
        ),
    ]


class MainLiftSeries(unittest.TestCase):
    def test_alias_merge_first_set_and_pinned_order(self):
        payload = dashboard_payload(_sessions(), goals=_goals())
        self.assertEqual(payload["main_lifts"], MAIN)
        self.assertEqual(payload["top_exercises"][:6], MAIN)
        self.assertEqual(payload["top_exercises"][6], "DB Curls")
        self.assertNotIn("RDLs", payload["top_exercises"])
        self.assertNotIn("Romanian Deadlift", payload["top_exercises"])
        self.assertNotIn("Lat Pulldowns", payload["top_exercises"])

        rdl = payload["strength_trends"]["RDL"]
        self.assertEqual([p["date"] for p in rdl], ["2026-05-01", "2026-05-08", "2026-06-01"])
        self.assertEqual(rdl[0]["first_set"], {"weight_lbs": 40.0, "reps": 8})
        self.assertEqual(rdl[0]["top_set"], {"weight_lbs": 45.0, "reps": 6})
        self.assertEqual(rdl[0]["set_count"], 2)
        self.assertEqual(rdl[0]["lift_tonnage"], 40 * 8 + 45 * 6)
        self.assertEqual(rdl[1]["first_set"], {"weight_lbs": 50.0, "reps": 7})
        self.assertEqual(rdl[1]["exercise"], "RDL")
        # Same session, two spellings: one point. First logged set wins.
        self.assertEqual(rdl[2]["first_set"], {"weight_lbs": 40.0, "reps": 8})
        self.assertEqual(rdl[2]["top_set"], {"weight_lbs": 50.0, "reps": 5})
        self.assertEqual(rdl[2]["set_count"], 2)
        self.assertEqual(rdl[2]["lift_tonnage"], 40 * 8 + 50 * 5)

        pulldown = payload["strength_trends"]["Pulldowns"]
        self.assertEqual(len(pulldown), 1)
        self.assertEqual(pulldown[0]["first_set"], {"weight_lbs": 100.0, "reps": 8})
        self.assertEqual(pulldown[0]["set_count"], 3)
        self.assertEqual(pulldown[0]["lift_tonnage"], 100 * 3 * 8)
        self.assertEqual(payload["strength_trends"]["DB Incline Press"], [])
        self.assertEqual(payload["strength_trends"]["DB Flat Press"], [])

        # Daily volume rows stay date + volume. This card does not rewrite them.
        self.assertEqual(len(payload["volume_by_day"]), 90)
        self.assertEqual(set(payload["volume_by_day"][0]), {"date", "volume"})

    def test_direct_alias_query_matches_canonical_series(self):
        trend = strength_trend(_sessions(), "Romanian Deadlift", aliases=ALIASES)
        self.assertEqual(trend[0]["exercise"], "RDL")
        self.assertGreaterEqual(len(trend), 3)

    def test_dict_sessions_match_objects(self):
        objects = strength_trend(_sessions(), "RDL", aliases=ALIASES)
        dicts = strength_trend(
            [s.to_dict() for s in _sessions()], "RDL", aliases=ALIASES
        )
        self.assertEqual(objects, dicts)


class MainLiftConfig(unittest.TestCase):
    def test_goals_files_and_normalize_keep_pins(self):
        repo = json.loads((REPO / "fitness/exercises/goals.json").read_text(encoding="utf-8"))
        bundled = json.loads(
            (ROOT / "fitness/exercises/goals.json").read_text(encoding="utf-8")
        )
        self.assertEqual(repo, bundled)
        self.assertEqual(repo["main_lifts"], MAIN)
        self.assertEqual(repo["lift_aliases"]["Romanian Deadlift"], "RDL")
        self.assertEqual(repo["lift_aliases"]["RDLs"], "RDL")
        self.assertEqual(repo["lift_aliases"]["Lat Pulldowns"], "Pulldowns")
        self.assertEqual(repo["progression"], "double_progression")

        loaded, src = load_workspace_goals()
        self.assertEqual(src, "fitness/exercises/goals.json")
        self.assertEqual(loaded["main_lifts"], MAIN)
        self.assertEqual(loaded["lift_aliases"]["Romanian Deadlift"], "RDL")

        normalized = normalize_goals(
            {
                "split": "ppl",
                "main_lifts": ["RDL", " RDL ", "Leg Press"],
                "lift_aliases": {"RDLs": "RDL"},
                "not_a_goal": True,
            }
        )
        self.assertEqual(normalized["main_lifts"], ["RDL", "Leg Press"])
        self.assertEqual(normalized["lift_aliases"], {"RDLs": "RDL"})
        self.assertNotIn("not_a_goal", normalized)
        self.assertEqual(normalized["progression"], "double_progression")


class MainLiftExports(unittest.TestCase):
    def test_trends_window_keeps_calorie_rows_and_clips_lifts(self):
        body = export_trends_window(
            {},
            days=7,
            end="2026-05-08",
            tz_name="America/New_York",
            sessions=_sessions(),
            goals=_goals(),
        )
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["rows"]), 7)
        self.assertIsNone(body["rows"][0]["intake_kcal"])
        self.assertEqual(set(body["rows"][0]), {
            "date",
            "intake_kcal",
            "burned_kcal",
            "weight_lb",
            "logged",
        })
        rdl = body["main_lifts"]["RDL"]
        self.assertEqual([p["date"] for p in rdl], ["2026-05-08"])
        point = rdl[0]
        self.assertEqual(set(point["first_set"]), {"weight_lbs", "reps"})
        self.assertEqual(set(point["top_set"]), {"weight_lbs", "reps"})
        self.assertIn("lift_tonnage", point)
        self.assertIn("set_count", point)
        self.assertEqual(body["main_lifts"]["DB Incline Press"], [])
        self.assertNotIn("2026-05-01", [p["date"] for p in rdl])

    def test_agent_today_exposes_main_lift_fields(self):
        body = export_agent_today(
            {
                "coach": {"today": {"date": "2026-05-08"}},
                "sessions": [s.to_dict() for s in _sessions()],
                "workout_store": {"goals": _goals()},
            }
        )
        rdl = body["main_lifts"]["RDL"]
        self.assertEqual([p["date"] for p in rdl], ["2026-05-01", "2026-05-08"])
        self.assertEqual(rdl[-1]["first_set"], {"weight_lbs": 50.0, "reps": 7})
        self.assertIn("top_set", rdl[-1])
        self.assertIn("lift_tonnage", rdl[-1])
        self.assertIn("set_count", rdl[-1])
        self.assertEqual(body["main_lifts"]["DB Flat Press"], [])
        self.assertNotIn("main_lifts", body["today"])

    def test_agent_today_without_config_does_not_invent_lifts(self):
        body = export_agent_today({"coach": {"today": {"date": "2026-05-08"}}})
        self.assertEqual(body["main_lifts"], {})


class MainLiftCardSource(unittest.TestCase):
    def test_existing_card_plots_first_set_and_tonnage_axis(self):
        html = (ROOT / "static/index.html").read_text(encoding="utf-8")
        app = (ROOT / "static/app.js").read_text(encoding="utf-8")
        sw = (ROOT / "static/sw.js").read_text(encoding="utf-8")
        self.assertIn('id="chart-strength"', html)
        self.assertIn("<h2>Main lifts</h2>", html)
        self.assertIn("/app.js?v=guardrails-1026-1", html)
        self.assertIn('const CACHE = "fitdash-shell-v128"', sw)
        self.assertIn("/app.js?v=guardrails-1026-1", sw)
        self.assertIn('mainGroup.label = "Main lifts"', app)
        self.assertIn('yAxisID: "y1"', app)
        self.assertIn("First set", app)
        self.assertIn("Session tonnage", app)
        self.assertIn("Est. 1RM (Epley)", app)
        self.assertNotIn("best load", app)


if __name__ == "__main__":
    unittest.main()
