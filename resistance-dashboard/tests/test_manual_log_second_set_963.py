"""A second manual log of the same exercise appends a set (#963)."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import seed_exercise
from rt_dashboard.workout_log import (
    merge_log_with_history,
    merge_same_day_session,
    parse_log_body,
)

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

DAY = "2026-10-01"


def _log(exercises, *, session_type="push", date=DAY):
    return parse_log_body(
        {
            "session_type": session_type,
            "date": date,
            "exercises": exercises,
        }
    )


def _bench(weight, sets=1, reps=8):
    return {
        "name": "Barbell Flat Bench Press",
        "sets": [{"weight_lbs": weight, "sets": sets, "reps": reps}],
    }


class SecondSetAppends(unittest.TestCase):
    def test_identical_second_set_keeps_the_first(self):
        first = _log([_bench(85)])
        second = _log([_bench(85)])
        merged = merge_log_with_history(second, [first])
        self.assertEqual(len(merged.exercises), 1)
        sets = merged.exercises[0].sets
        self.assertEqual(len(sets), 2)
        self.assertEqual((sets[0].weight_lbs, sets[0].sets, sets[0].reps), (85, 1, 8))
        self.assertEqual((sets[1].weight_lbs, sets[1].sets, sets[1].reps), (85, 1, 8))
        self.assertEqual(merged.exercises[0].name, "Barbell Flat Bench Press")

    def test_different_weight_on_set_two_persists_both(self):
        first = _log([_bench(85)])
        second = _log(
            [
                {
                    "name": "barbell   flat bench press",
                    "sets": [{"weight_lbs": 90, "sets": 1, "reps": 6}],
                }
            ]
        )
        merged = merge_log_with_history(second, [first])
        sets = merged.exercises[0].sets
        self.assertEqual(len(sets), 2)
        self.assertEqual(sets[0].weight_lbs, 85)
        self.assertEqual(sets[0].reps, 8)
        self.assertEqual(sets[1].weight_lbs, 90)
        self.assertEqual(sets[1].reps, 6)
        self.assertEqual(merged.exercises[0].name, "Barbell Flat Bench Press")

    def test_nth_set_appends(self):
        first = _log([_bench(85)])
        second = merge_log_with_history(_log([_bench(90)]), [first])
        third = merge_log_with_history(_log([_bench(95, reps=5)]), [second])
        sets = third.exercises[0].sets
        self.assertEqual(
            [(s.weight_lbs, s.reps) for s in sets],
            [(85, 8), (90, 8), (95, 5)],
        )

    def test_other_exercise_stays_untouched(self):
        first = _log(
            [
                _bench(85),
                {
                    "name": "DB Row",
                    "sets": [{"weight_lbs": 50, "sets": 3, "reps": 10}],
                },
            ]
        )
        merged = merge_log_with_history(_log([_bench(90)]), [first])
        self.assertEqual(
            [e.name for e in merged.exercises],
            ["Barbell Flat Bench Press", "DB Row"],
        )
        row = merged.exercises[1]
        self.assertEqual(len(row.sets), 1)
        self.assertEqual((row.sets[0].weight_lbs, row.sets[0].sets, row.sets[0].reps), (50, 3, 10))
        self.assertEqual(len(merged.exercises[0].sets), 2)

    def test_set_index_updates_only_that_set(self):
        first = _log(
            [
                {
                    "name": "Barbell Flat Bench Press",
                    "sets": [
                        {"weight_lbs": 85, "sets": 1, "reps": 8},
                        {"weight_lbs": 90, "sets": 1, "reps": 6},
                    ],
                }
            ]
        )
        edit = _log(
            [
                {
                    "name": "Barbell Flat Bench Press",
                    "sets": [
                        {
                            "weight_lbs": 95,
                            "sets": 1,
                            "reps": 5,
                            "set_index": 1,
                        }
                    ],
                }
            ]
        )
        merged = merge_log_with_history(edit, [first])
        sets = merged.exercises[0].sets
        self.assertEqual(len(sets), 2)
        self.assertEqual((sets[0].weight_lbs, sets[0].reps), (85, 8))
        self.assertEqual((sets[1].weight_lbs, sets[1].reps), (95, 5))
        self.assertFalse(hasattr(sets[1], "set_index"))

    def test_set_index_out_of_range_is_rejected(self):
        first = _log([_bench(85)])
        edit = _log(
            [
                {
                    "name": "Barbell Flat Bench Press",
                    "sets": [
                        {
                            "weight_lbs": 95,
                            "sets": 1,
                            "reps": 5,
                            "set_index": 3,
                        }
                    ],
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "set_index"):
            merge_log_with_history(edit, [first])

    def test_quest_seed_is_still_replaced(self):
        seeded = Session(
            date=DAY,
            session_type="push",
            exercises=[
                seed_exercise(
                    "DB Flat Press",
                    title_rx={"weight_lbs": 50.0, "sets": 3, "reps": 10},
                )
            ],
        )
        incoming = _log(
            [
                {
                    "name": "DB Flat Press",
                    "sets": [{"weight_lbs": 55, "sets": 3, "reps": 8}],
                }
            ]
        )
        merged = merge_log_with_history(incoming, [seeded])
        self.assertEqual(len(merged.exercises), 1)
        self.assertEqual(len(merged.exercises[0].sets), 1)
        self.assertEqual(merged.exercises[0].sets[0].weight_lbs, 55)
        self.assertEqual(merged.exercises[0].sets[0].reps, 8)
        self.assertFalse(merged.exercises[0].quest_seeded)

    def test_other_day_does_not_receive_the_set(self):
        yesterday = _log([_bench(80)], date="2026-09-30")
        today = _log([_bench(85)])
        merged = merge_log_with_history(today, [yesterday])
        self.assertEqual(merged.date, DAY)
        self.assertEqual(len(merged.exercises[0].sets), 1)
        self.assertEqual(merged.exercises[0].sets[0].weight_lbs, 85)

    def test_date_move_still_replaces_on_name(self):
        existing = Session(
            date=DAY,
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="Barbell Flat Bench Press",
                    sets=[SetEntry(85, 1, 8)],
                )
            ],
        )
        incoming = Session(
            date=DAY,
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="Barbell Flat Bench Press",
                    sets=[SetEntry(100, 1, 5)],
                )
            ],
        )
        replaced = merge_same_day_session(incoming, existing)
        self.assertEqual(len(replaced.exercises[0].sets), 1)
        self.assertEqual(replaced.exercises[0].sets[0].weight_lbs, 100)
        appended = merge_same_day_session(incoming, existing, same_name="append")
        self.assertEqual(len(appended.exercises[0].sets), 2)
        self.assertEqual(appended.exercises[0].sets[0].weight_lbs, 85)


class SecondSetClient(unittest.TestCase):
    def test_today_line_lists_every_set_and_form_resets(self):
        script = ROOT / "tests" / "manual_log_second_set_963.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok manual-log-second-set-963", proc.stdout)
        submit = JS.split("async function submitWorkout", 1)[1].split(
            "async function submitIngredient", 1
        )[0]
        self.assertLess(
            submit.find("resetManualLogExercises()"),
            submit.find("await loadDashboard(false)"),
        )
        self.assertGreaterEqual(submit.find("resetManualLogExercises()"), 0)
        today = JS.split("function renderTodayLoggedLifts", 1)[1].split(
            "function applyWorkoutLogToLocalState", 1
        )[0]
        self.assertIn("loggedLiftDetail(ex)", today)
        self.assertNotIn("ex.sets[0]", today)
        self.assertIn("/app.js?v=first-set-977-1", HTML)
        self.assertNotIn("/app.js?v=energy-scale-961-1", HTML)
        self.assertIn('const CACHE = "fitdash-shell-v125"', SW)
        self.assertNotIn("fitdash-shell-v120", SW)


if __name__ == "__main__":
    unittest.main()
