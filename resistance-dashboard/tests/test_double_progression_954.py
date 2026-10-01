"""Double-progression load selection for the next session (#954)."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    generate_workout_plan,
    last_performance,
    prescribe,
)


def _compound(**extra):
    row = {
        "name": "DB Flat Press",
        "movement": "compound",
        "equipment": ["dumbbells"],
        "primary_muscles": ["chest"],
        "session_types": ["push"],
        "default_sets": 2,
        "default_reps": 8,
        "rep_range": [8, 12],
    }
    row.update(extra)
    return row


def _isolation(**extra):
    row = {
        "name": "DB Curls",
        "movement": "isolation",
        "equipment": ["dumbbells"],
        "primary_muscles": ["biceps"],
        "session_types": ["pull"],
        "default_sets": 2,
        "default_reps": 12,
        "rep_range": [8, 12],
    }
    row.update(extra)
    return row


def _last(weight, reps, date="2026-09-29"):
    return {"weight_lbs": weight, "sets": 2, "reps": reps, "date": date}


class DoubleProgression954(unittest.TestCase):
    def test_compound_top_of_band_adds_one_step(self):
        rx = prescribe(_compound(), _last(100, 9), recovery_score=80)
        self.assertEqual(rx["progression_reason"], "add_load")
        self.assertEqual(rx["weight_lbs"], 105.0)
        self.assertEqual(rx["load"], 105.0)
        self.assertEqual(rx["target_reps"], 5)
        self.assertEqual(rx["rep_range_label"], "5-9")

    def test_compound_inside_band_holds_and_adds_a_rep(self):
        rx = prescribe(_compound(), _last(100, 7), recovery_score=80)
        self.assertEqual(rx["progression_reason"], "hold_plus1")
        self.assertEqual(rx["weight_lbs"], 100.0)
        self.assertEqual(rx["target_reps"], 8)

    def test_compound_below_band_drops_one_step(self):
        rx = prescribe(_compound(), _last(100, 3), recovery_score=80)
        self.assertEqual(rx["progression_reason"], "drop_load")
        self.assertEqual(rx["weight_lbs"], 95.0)
        self.assertEqual(rx["target_reps"], 5)

    def test_isolation_16_adds_and_12_holds(self):
        added = prescribe(_isolation(), _last(20, 16), recovery_score=80)
        self.assertEqual(added["progression_reason"], "add_load")
        self.assertEqual(added["weight_lbs"], 25.0)
        self.assertEqual(added["target_reps"], 8)
        self.assertEqual(added["rep_range_label"], "8-15")
        held = prescribe(_isolation(), _last(20, 12), recovery_score=80)
        self.assertEqual(held["progression_reason"], "hold_plus1")
        self.assertEqual(held["weight_lbs"], 20.0)
        self.assertEqual(held["target_reps"], 13)

    def test_isolation_at_band_top_adds_load(self):
        """15 is the top of 8–15, same fencepost as compound 9."""
        rx = prescribe(_isolation(), _last(20, 15), recovery_score=80)
        self.assertEqual(rx["progression_reason"], "add_load")
        self.assertEqual(rx["target_reps"], 8)

    def test_later_sets_below_the_band_do_not_drop_the_load(self):
        session = Session(
            date="2026-09-29",
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="DB Flat Press",
                    sets=[
                        SetEntry(weight_lbs=100, sets=1, reps=7),
                        SetEntry(weight_lbs=100, sets=1, reps=3),
                    ],
                )
            ],
        )
        last = last_performance([session], "DB Flat Press")
        self.assertEqual(last["reps"], 7)
        self.assertEqual(last["first_reps"], 7)
        self.assertEqual(last["top_reps"], 7)
        rx = prescribe(_compound(), last, recovery_score=80)
        self.assertEqual(rx["progression_reason"], "hold_plus1")
        self.assertEqual(rx["weight_lbs"], 100.0)
        self.assertEqual(rx["target_reps"], 8)

    def test_barbell_lower_steps_10_and_smith_upper_steps_5(self):
        rdl = _compound(
            name="RDL",
            equipment=[],
            equipment_any=["barbell", "dumbbells"],
            primary_muscles=["hamstrings", "glutes"],
            session_types=["legs"],
        )
        smith = _compound(
            name="Smith Flat Bench",
            equipment=["smith_machine"],
            primary_muscles=["chest"],
        )
        self.assertEqual(
            prescribe(rdl, _last(100, 9), recovery_score=80)["weight_lbs"], 110.0
        )
        self.assertEqual(
            prescribe(smith, _last(100, 9), recovery_score=80)["weight_lbs"], 105.0
        )

    def test_add_load_stops_at_equipment_max(self):
        at_max = prescribe(
            _compound(), _last(50, 9), recovery_score=80, load_cap=50
        )
        self.assertEqual(at_max["weight_lbs"], 50.0)
        self.assertEqual(at_max["progression_reason"], "hold_plus1")
        self.assertEqual(at_max["target_reps"], 9)
        up_to_max = prescribe(
            _compound(), _last(45, 9), recovery_score=80, load_cap=50
        )
        self.assertEqual(up_to_max["weight_lbs"], 50.0)
        self.assertEqual(up_to_max["progression_reason"], "add_load")
        self.assertEqual(up_to_max["target_reps"], 5)

    def test_fitbench_library_cap_is_30(self):
        ex = _compound(
            name="FITBENCH Press",
            equipment=["fitbench"],
            primary_muscles=["chest"],
        )
        equipment = {
            "items": [
                {
                    "id": "fitbench",
                    "name": "FITBENCH",
                    "tag": "fitbench",
                    "max_weight_lbs": None,
                }
            ]
        }
        rx = prescribe(ex, _last(30, 9), recovery_score=80, equipment=equipment)
        self.assertEqual(rx["weight_lbs"], 30.0)
        self.assertEqual(rx["progression_reason"], "hold_plus1")
        self.assertLessEqual(rx["load"], 30.0)

    def test_deload_flag_and_low_recovery_do_not_add_load(self):
        flagged = prescribe(
            _compound(), _last(100, 9), recovery_score=80, deload=True
        )
        self.assertEqual(flagged["progression_reason"], "deload_override")
        self.assertEqual(flagged["weight_lbs"], 90.0)
        low = prescribe(_compound(), _last(100, 9), recovery_score=39)
        self.assertEqual(low["progression_reason"], "deload_override")
        self.assertLessEqual(low["weight_lbs"], 100.0)
        self.assertNotEqual(low["progression_reason"], "add_load")

    def test_no_history_is_a_seed(self):
        rx = prescribe(_compound(), None, default_hard_sets=2, recovery_score=80)
        self.assertEqual(rx["progression_reason"], "seed")
        self.assertIsNone(rx["weight_lbs"])
        self.assertIsNone(rx["load"])
        self.assertEqual(rx["sets"], 2)
        self.assertEqual(rx["rep_range_label"], "5-9")

    def test_pull_log_from_2026_09_29(self):
        """Pulldowns 100×10 add a pin. DB Curls 27.5×6 drop a 2.5 lb step."""
        catalog = {
            "exercises": [
                {
                    "id": "pulldowns",
                    "name": "Pulldowns",
                    "session_types": ["pull"],
                    "primary_muscles": ["lats"],
                    "movement": "compound",
                    "equipment": ["cable", "lat_pulldown"],
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [8, 12],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "db-curls",
                    "name": "DB Curls",
                    "session_types": ["pull"],
                    "primary_muscles": ["biceps"],
                    "movement": "isolation",
                    "equipment": ["dumbbells"],
                    "default_sets": 2,
                    "default_reps": 12,
                    "rep_range": [8, 12],
                    "priority": 6,
                    "available": True,
                },
            ]
        }
        session = Session(
            date="2026-09-29",
            session_type="pull",
            exercises=[
                ExerciseEntry(
                    name="Pulldowns",
                    sets=[SetEntry(weight_lbs=100, sets=3, reps=10)],
                ),
                ExerciseEntry(
                    name="DB Curls",
                    sets=[SetEntry(weight_lbs=27.5, sets=3, reps=6)],
                ),
            ],
        )
        plan = generate_workout_plan(
            catalog,
            {
                "rotation": ["push", "pull", "legs"],
                "exercises_per_session": 5,
                "default_hard_sets": 2,
                # Keep the curl on the plan. Auto-focus would pin biceps at
                # the maintenance max after this log and drop the isolation.
                "auto_focus_muscles": False,
                "focus_muscles": ["lats"],
                "maintenance_sets_per_muscle_week": 20,
                "sets_per_muscle_week_max": 20,
                "sets_per_muscle_week_priority_max": 20,
            },
            [session],
            recovery_score=80,
            session_type="pull",
            as_of="2026-09-30",
        )
        by_name = {e["name"]: e for e in plan["exercises"]}
        pulldown = by_name["Pulldowns"]
        curls = by_name["DB Curls"]
        self.assertEqual(pulldown["progression_reason"], "add_load")
        self.assertEqual(pulldown["load"], 105.0)
        self.assertEqual(pulldown["target_reps"], 5)
        self.assertEqual(pulldown["rep_range"], "5-9")
        self.assertEqual(curls["progression_reason"], "drop_load")
        self.assertEqual(curls["load"], 25.0)
        self.assertEqual(curls["target_reps"], 8)
        self.assertEqual(curls["rep_range"], "8-15")

    def test_same_day_close_plan_stays_non_empty(self):
        """#951: a closed day still rolls a non-empty next-letter plan."""
        et = ZoneInfo("America/New_York")
        catalog = {
            "exercises": [
                {
                    "id": "db-flat-press",
                    "name": "DB Flat Press",
                    "session_types": ["push"],
                    "primary_muscles": ["chest"],
                    "movement": "compound",
                    "equipment": ["dumbbells"],
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [8, 12],
                    "priority": 10,
                    "available": True,
                },
                {
                    "id": "rdl",
                    "name": "RDL",
                    "session_types": ["legs"],
                    "primary_muscles": ["hamstrings"],
                    "movement": "compound",
                    "equipment_any": ["barbell"],
                    "default_sets": 2,
                    "default_reps": 8,
                    "rep_range": [6, 10],
                    "priority": 10,
                    "available": True,
                },
            ]
        }
        legs = Session(
            date="2026-09-09",
            session_type="legs",
            closed_at="2026-09-09T00:30:00-04:00",
            exercises=[
                ExerciseEntry(
                    name="RDL",
                    sets=[SetEntry(weight_lbs=100, sets=2, reps=8)],
                )
            ],
        )
        plan = generate_workout_plan(
            catalog,
            {"rotation": ["push", "pull", "legs"], "exercises_per_session": 4},
            [legs],
            recovery_score=80,
            as_of="2026-09-09",
            last_wake_at="2026-09-08T07:00:00-04:00",
            now=datetime(2026, 9, 9, 0, 45, tzinfo=et),
        )
        self.assertTrue(plan["exercises"])
        self.assertIsNone(plan.get("generate_error"))
        self.assertEqual(plan["next_session_type"], "push")
        self.assertTrue(any(e["name"] == "DB Flat Press" for e in plan["exercises"]))
        for ex in plan["exercises"]:
            self.assertIn(
                ex["progression_reason"],
                {"hold_plus1", "add_load", "drop_load", "seed", "deload_override"},
            )
            self.assertTrue(ex["rep_range"])
            self.assertIsNotNone(ex["target_reps"])

    def test_agent_today_exposes_progression_fields(self):
        plan = {
            "session_type": "pull",
            "is_rest_day": False,
            "exercises": [
                {
                    "name": "Pulldowns",
                    "load": 105,
                    "rep_range": "5-9",
                    "target_reps": 5,
                    "progression_reason": "add_load",
                    "prescription": {
                        "sets": 2,
                        "reps": 5,
                        "weight_lbs": 105,
                    },
                }
            ],
        }
        body = export_agent_today(
            {
                "workout_store": {"plan": plan},
                "sessions": [],
                "coach": {"today": {"date": "2026-09-30"}},
            }
        )
        row = body["today"]["workout"]["plan_exercises"][0]
        self.assertEqual(row["load"], 105)
        self.assertEqual(row["rep_range"], "5-9")
        self.assertEqual(row["target_reps"], 5)
        self.assertEqual(row["progression_reason"], "add_load")
        self.assertEqual(row["reps"], 5)
        self.assertEqual(row["weight_lbs"], 105)


if __name__ == "__main__":
    unittest.main()
