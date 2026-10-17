"""Assisted lifts: effective load is bodyweight minus the pin (#1156).

The logged number stays the machine pin. Tonnage, e1RM, and history use the
closest weigh-in in either direction. Progression reverses that pin.
"""

from __future__ import annotations

import unittest

from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.analytics import dashboard_payload, strength_trend, volume_by_week
from rt_dashboard.assisted_load import (
    apply_assisted_effective_loads,
    catalog_is_assisted,
    nearest_weighin,
)
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.pr_detect import apply_auto_prs
from rt_dashboard.workout_planner import generate_workout_plan, prescribe
from rt_dashboard.workout_repo import WorkoutRepository


def _assisted(**extra):
    row = {
        "id": "assisted-pullups",
        "name": "Assisted Pullups",
        "movement": "compound",
        "equipment": ["assisted_pullup"],
        "assisted": True,
        "primary_muscles": ["lats"],
        "session_types": ["pull"],
        "default_sets": 2,
        "default_reps": 8,
        "rep_range": [6, 12],
        "priority": 10,
        "available": True,
    }
    row.update(extra)
    return row


def _press():
    return {
        "name": "DB Flat Press",
        "movement": "compound",
        "equipment": ["dumbbells"],
        "primary_muscles": ["chest"],
        "session_types": ["push"],
        "default_sets": 2,
        "default_reps": 8,
        "rep_range": [8, 12],
    }


def _last(weight, reps, date="2026-10-12"):
    return {
        "weight_lbs": weight,
        "first_weight_lbs": weight,
        "sets": 1,
        "reps": reps,
        "first_reps": reps,
        "date": date,
    }


def _session(date, name, weight, reps, *, sets=1):
    return Session(
        date=date,
        session_type="pull",
        exercises=[
            ExerciseEntry(
                name=name,
                sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
            )
        ],
    )


def _weigh(date, lbs):
    return {"date": date, "weight_lbs": lbs}


CATALOG = {"exercises": [_assisted()]}


class AssistedLoad1156(unittest.TestCase):
    def test_catalog_flag_and_equipment_tag(self):
        self.assertTrue(catalog_is_assisted(_assisted()))
        tagged = _assisted()
        tagged.pop("assisted")
        self.assertTrue(catalog_is_assisted(tagged))
        self.assertFalse(catalog_is_assisted(_press()))

    def test_top_of_band_reduces_assistance(self):
        rx = prescribe(
            _assisted(),
            _last(75, 9),
            recovery_score=80,
            as_of="2026-10-13",
            weighins=[_weigh("2026-10-13", 173)],
        )
        self.assertEqual(rx["progression_reason"], "reduce_assistance")
        self.assertEqual(rx["weight_lbs"], 70.0)
        self.assertEqual(rx["target_reps"], 5)
        self.assertIn("assistance reduced", rx["load_reason"])
        self.assertEqual(rx["assistance_lbs"], 70.0)
        self.assertEqual(rx["effective_load_lbs"], 103.0)
        self.assertEqual(rx["effective_load_source"], "nearest_weighin")
        self.assertEqual(rx["weighin_days_away"], 0)

    def test_below_band_adds_assistance_and_inside_holds(self):
        low = prescribe(_assisted(), _last(75, 4), recovery_score=80)
        self.assertEqual(low["progression_reason"], "add_assistance")
        self.assertEqual(low["weight_lbs"], 80.0)
        self.assertEqual(low["target_reps"], 5)
        mid = prescribe(_assisted(), _last(75, 6), recovery_score=80)
        self.assertEqual(mid["progression_reason"], "hold_plus1")
        self.assertEqual(mid["weight_lbs"], 75.0)
        self.assertEqual(mid["target_reps"], 7)
        self.assertNotIn("assistance reduced", mid.get("load_reason") or "")

    def test_floor_allows_zero_and_does_not_go_negative(self):
        dropped = prescribe(_assisted(), _last(5, 9), recovery_score=80)
        self.assertEqual(dropped["weight_lbs"], 0.0)
        self.assertEqual(dropped["target_reps"], 5)
        self.assertEqual(dropped["progression_reason"], "reduce_assistance")
        held = prescribe(_assisted(), _last(0, 9), recovery_score=80)
        self.assertEqual(held["weight_lbs"], 0.0)
        self.assertEqual(held["progression_reason"], "hold_plus1")
        self.assertNotIn("assistance reduced", held.get("load_reason") or "")

    def test_deload_adds_assistance_instead_of_cutting_the_pin(self):
        rx = prescribe(_assisted(), _last(75, 9), recovery_score=40)
        self.assertEqual(rx["progression_reason"], "deload_override")
        self.assertEqual(rx["weight_lbs"], 82.5)
        self.assertEqual(rx["target_reps"], 5)
        self.assertIn("more assistance", rx["rationale"])

    def test_other_lifts_keep_the_old_step(self):
        rx = prescribe(_press(), _last(100, 9), recovery_score=80)
        self.assertEqual(rx["progression_reason"], "add_load")
        self.assertEqual(rx["weight_lbs"], 105.0)
        self.assertEqual(rx["target_reps"], 5)
        self.assertNotIn("assistance_lbs", rx)
        self.assertNotIn("effective_load_lbs", rx)
        self.assertNotIn("effective_load_source", rx)
        self.assertNotIn("load_reason", rx)

    def test_logged_set_uses_bodyweight_minus_pin(self):
        session = _session("2026-10-12", "Assisted Pullups", 75, 6)
        session.exercises.append(
            ExerciseEntry(
                name="DB Flat Press",
                sets=[SetEntry(weight_lbs=100, sets=1, reps=5)],
            )
        )
        apply_assisted_effective_loads(
            [session], [_weigh("2026-10-12", 173)], catalog=CATALOG
        )
        assisted = session.exercises[0]
        press = session.exercises[1]
        self.assertEqual(assisted.sets[0].weight_lbs, 75)
        self.assertEqual(assisted.sets[0].volume, 588)
        self.assertEqual(assisted.volume, 588)
        self.assertAlmostEqual(assisted.best_e1rm, 98 * (1 + 6 / 30.0))
        self.assertEqual(press.sets[0].weight_lbs, 100)
        self.assertEqual(press.volume, 500)
        self.assertFalse(hasattr(press, "effective_load_source") and press.effective_load_source)
        apply_assisted_effective_loads(
            [session], [_weigh("2026-10-12", 173)], catalog=CATALOG
        )
        self.assertEqual(assisted.sets[0].weight_lbs, 75)
        self.assertEqual(assisted.volume, 588)

    def test_closest_weighin_has_no_window_and_ties_prefer_earlier(self):
        far = nearest_weighin("2026-09-01", [_weigh("2026-07-23", 170)])
        self.assertIsNotNone(far)
        self.assertEqual(far[0], 170.0)
        self.assertEqual(far[1], 40)
        tie = nearest_weighin(
            "2026-10-10",
            [_weigh("2026-10-19", 180), _weigh("2026-10-01", 170)],
        )
        self.assertEqual(tie[0], 170.0)
        self.assertEqual(tie[1], 9)
        session = _session("2026-09-01", "Assisted Pullups", 75, 6)
        apply_assisted_effective_loads(
            [session], [_weigh("2026-07-23", 173)], catalog=CATALOG
        )
        self.assertEqual(session.exercises[0].effective_load_source, "nearest_weighin")
        self.assertEqual(session.exercises[0].weighin_days_away, 40)
        self.assertEqual(session.exercises[0].sets[0].volume, 588)

    def test_no_weighins_leave_the_set_out_of_tonnage(self):
        session = _session("2026-10-12", "Assisted Pullups", 75, 6)
        session.exercises.append(
            ExerciseEntry(
                name="DB Flat Press",
                sets=[SetEntry(weight_lbs=100, sets=1, reps=5)],
            )
        )
        apply_assisted_effective_loads([session], [], catalog=CATALOG)
        assisted = session.exercises[0]
        self.assertEqual(assisted.effective_load_source, "none")
        self.assertEqual(assisted.volume, 0)
        self.assertEqual(assisted.best_e1rm, 0)
        self.assertEqual(session.exercises[1].volume, 500)
        self.assertEqual(session.volume, 500)
        payload = assisted.to_dict()
        self.assertEqual(payload["effective_load_source"], "none")
        self.assertNotIn("effective_load_lbs", payload)

    def test_history_week_tonnage_and_e1rm_use_effective_load(self):
        session = _session("2026-10-12", "Assisted Pullups", 75, 6)
        session.exercises.append(
            ExerciseEntry(
                name="DB Flat Press",
                sets=[SetEntry(weight_lbs=100, sets=1, reps=5)],
            )
        )
        apply_assisted_effective_loads(
            [session], [_weigh("2026-10-01", 173)], catalog=CATALOG
        )
        weeks = volume_by_week([session])
        self.assertEqual(weeks[0]["volume"], 1088)
        payload = dashboard_payload(
            [session],
            goals={"main_lifts": ["Assisted Pullups"], "rotation": ["pull"]},
        )
        self.assertEqual(payload["volume_by_week"][0]["volume"], 1088)
        logged = payload["sessions"][0]["exercises"][0]
        self.assertEqual(logged["weight_lbs"] if "weight_lbs" in logged else logged["sets"][0]["weight_lbs"], 75)
        self.assertEqual(logged["sets"][0]["weight_lbs"], 75)
        self.assertEqual(logged["effective_load_lbs"], 98.0)
        self.assertEqual(logged["effective_load_source"], "nearest_weighin")
        self.assertEqual(logged["weighin_days_away"], 11)
        self.assertEqual(logged["volume"], 588)
        trend = strength_trend([session], "Assisted Pullups")
        self.assertAlmostEqual(trend[0]["best_e1rm"], 98 * 1.2)
        self.assertEqual(trend[0]["first_set"]["weight_lbs"], 98)
        self.assertNotAlmostEqual(trend[0]["best_e1rm"], 75 * 1.2)

    def test_agent_today_slice_shows_the_fields(self):
        plan = generate_workout_plan(
            CATALOG,
            {
                "rotation": ["push", "pull", "legs"],
                "exercises_per_session": 5,
                "default_hard_sets": 2,
                "auto_focus_muscles": False,
                "focus_muscles": ["lats"],
                "maintenance_sets_per_muscle_week": 20,
                "sets_per_muscle_week_max": 20,
                "sets_per_muscle_week_priority_max": 20,
            },
            [_session("2026-10-12", "Assisted Pullups", 75, 9)],
            recovery_score=80,
            session_type="pull",
            as_of="2026-10-13",
            weighins=[_weigh("2026-10-13", 173)],
        )
        row = plan["exercises"][0]
        self.assertEqual(row["name"], "Assisted Pullups")
        self.assertEqual(row["load"], 70.0)
        self.assertIn("assistance reduced", row["load_reason"])
        self.assertEqual(row["prescription"]["assistance_lbs"], 70.0)
        self.assertEqual(row["prescription"]["effective_load_lbs"], 103.0)
        logged = _session("2026-10-12", "Assisted Pullups", 75, 6)
        apply_assisted_effective_loads(
            [logged], [_weigh("2026-10-12", 173)], catalog=CATALOG
        )
        exported = export_agent_today(
            {
                "meta": {"local_today": "2026-10-13"},
                "coach": {"today": {"date": "2026-10-13"}},
                "sessions": [logged.to_dict()],
                "workout_store": {
                    "plan": plan,
                    "goals": {"main_lifts": ["Assisted Pullups"]},
                },
            }
        )
        planned = exported["today"]["workout"]["plan_exercises"][0]
        self.assertEqual(planned["assistance_lbs"], 70.0)
        self.assertEqual(planned["effective_load_source"], "nearest_weighin")
        self.assertIn("assistance reduced", planned["load_reason"])
        shown = exported["week"]["logged_sessions"][0]["exercises"][0]
        self.assertEqual(shown["assistance_lbs"], 75.0)
        self.assertEqual(shown["effective_load_lbs"], 98.0)
        self.assertEqual(shown["volume"], 588)
        series = exported["main_lifts"]["Assisted Pullups"]
        self.assertAlmostEqual(series[0]["best_e1rm"], 98 * 1.2)

    def test_more_assistance_is_not_a_pr(self):
        history = _session("2026-10-01", "Assisted Pullups", 75, 6)
        heavier_pin = _session("2026-10-08", "Assisted Pullups", 80, 6)
        lighter_pin = _session("2026-10-08", "Assisted Pullups", 70, 6)
        samples = [_weigh("2026-10-08", 173)]
        apply_assisted_effective_loads(
            [history, heavier_pin, lighter_pin], samples, catalog=CATALOG
        )
        apply_auto_prs(heavier_pin, [history])
        apply_auto_prs(lighter_pin, [history])
        self.assertFalse(heavier_pin.exercises[0].is_pr)
        self.assertTrue(lighter_pin.exercises[0].is_pr)

    def test_storage_keeps_the_pin(self):
        import tempfile
        from pathlib import Path

        session = _session("2026-10-12", "Assisted Pullups", 75, 6)
        apply_assisted_effective_loads(
            [session], [_weigh("2026-10-12", 173)], catalog=CATALOG
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = WorkoutRepository(
            db_path=Path(tmp.name) / "fitdash.db", user_id="test-user"
        )
        repo.upsert_session(session)
        loaded = repo.list_sessions()[0]
        st = loaded.exercises[0].sets[0]
        self.assertEqual(st.weight_lbs, 75)
        self.assertFalse(getattr(st, "effective_load_source", None))
        self.assertEqual(loaded.exercises[0].volume, 450)
        self.assertNotIn("effective_load_source", loaded.exercises[0].to_dict())


if __name__ == "__main__":
    unittest.main()
