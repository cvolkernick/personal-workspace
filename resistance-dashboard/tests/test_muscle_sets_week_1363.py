"""Trends Muscle sets undercount for the 10/26–11/1 week (#1363).

Synthetic fixture shaped like that week's log: the same exercise names,
loads, set counts, reps, and close timestamps. Most rows were logged by
checking a Quest lift leaf (``quest_seeded``), and four names have no
catalog row by that exact name (``Barbell Flat Bench Press``, ``Barbell
Shrugs``, ``Smith Barbell Row``, ``Calf Raises`` whose catalog name is
``Calf Extensions``). Before the fix the tile read chest 0, delts 0,
triceps 0, traps 0, calves 0, hamstrings 1.5.
"""

from __future__ import annotations

import unittest

from rt_dashboard.muscle_sets import (
    build_muscle_set_report,
    exercise_proper_sets,
    rolling_7d_bounds,
)
from rt_dashboard.workout_planner import default_catalog


def _ex(name, rows, *, seeded=True):
    out = {
        "name": name,
        "sets": [{"weight_lbs": w, "sets": n, "reps": r} for (w, n, r) in rows],
    }
    if seeded:
        out["quest_seeded"] = True
        out["raw"] = "quest-seeded:" + ",".join(f"{w:g}/{n}/{r}" for (w, n, r) in rows)
    return out


def _session(day, kind, closed_at, exercises):
    return {
        "date": day,
        "session_type": kind,
        "closed_at": closed_at,
        "exercises": exercises,
    }


WEEK = [
    # 12:18 AM ET on 10/26: first day of the rolling window.
    _session(
        "2026-10-26",
        "legs",
        "2026-10-26T04:18:09Z",
        [
            _ex("Seated Leg Curls", [(100.0, 3, 13)]),
            _ex("Back Extension Machine", [(140.0, 2, 13)]),
            _ex("Barbell Deadlift", [(105.0, 3, 5)]),
            _ex("Leg Press", [(200.0, 2, 6)]),
        ],
    ),
    _session(
        "2026-10-27",
        "push",
        "2026-10-27T04:22:17Z",
        [
            _ex("DB Incline Press", [(45.0, 2, 6)]),
            _ex("DB Shoulder Press", [(30.0, 2, 5)]),
            _ex("Smith Shrugs", [(100.0, 1, 13)]),
            _ex("Tricep Pushdowns", [(42.5, 1, 11)]),
            _ex("Barbell Flat Bench Press", [(95.0, 2, 6)]),
        ],
    ),
    _session(
        "2026-10-27",
        "pull",
        "2026-10-28T03:18:10Z",
        [
            _ex("Assisted Pullups", [(70.0, 2, 8)]),
            _ex("Barbell Shrugs", [(95.0, 1, 14)]),
            _ex("Smith Barbell Row", [(100.0, 1, 10), (110.0, 1, 8)], seeded=False),
            _ex("Back Extension Machine", [(145.0, 1, 12)], seeded=False),
        ],
    ),
    _session(
        "2026-10-30",
        "legs",
        "2026-10-30T07:03:52Z",
        [
            _ex("Barbell Deadlift", [(105.0, 1, 6)]),
            _ex("RDL", [(45.0, 1, 9)]),
            _ex("Calf Raises", [(125.0, 2, 13)]),
            _ex("Leg Press", [(200.0, 2, 7)], seeded=False),
        ],
    ),
    _session(
        "2026-11-01",
        "push",
        "2026-11-01T09:10:10Z",
        [
            _ex("Smith Shrugs", [(100.0, 1, 13)]),
            _ex("DB Flat Press", [(45.0, 2, 9)]),
            _ex("DB Incline Press", [(45.0, 2, 6)]),
            _ex("DB Shoulder Press", [(30.0, 2, 5)]),
        ],
    ),
]

# 11:30 PM ET on 10/25, stored as a UTC instant that reads 10/26. It is
# outside the window and must not count.
BEFORE_EDGE = _session(
    "2026-10-26T03:30:00Z",
    "push",
    "2026-10-26T03:30:00Z",
    [_ex("DB Flat Press", [(45.0, 3, 8)], seeded=False)],
)

EXPECTED = {
    "chest": 8.0,
    "front_delts": 10.0,
    "side_delts": 2.0,
    "rear_delts": 0.0,
    "lats": 5.0,
    "mid_upper_back": 5.0,
    "traps": 5.0,
    "biceps": 2.0,
    "triceps": 7.0,
    "quads": 6.0,
    "hamstrings": 11.5,
    "glutes": 10.0,
    "calves": 2.0,
    "abs": 0.0,
    "lower_back": 7.5,
    "adductors": 0.0,
    "abductors": 0.0,
}


def _report(sessions):
    return build_muscle_set_report(
        {"sessions": sessions}, as_of="2026-11-01", catalog=default_catalog()
    )


class MuscleSetsWeek1363(unittest.TestCase):
    def test_window_is_10_26_through_11_01(self):
        self.assertEqual(rolling_7d_bounds("2026-11-01"), ("2026-10-26", "2026-11-01"))
        self.assertEqual(
            rolling_7d_bounds("2026-11-02T04:30:00Z"), ("2026-10-26", "2026-11-01")
        )

    def test_week_counts_every_logged_session(self):
        report = _report(WEEK)
        got = {g: row["proper_sets"] for g, row in report["muscle_sets"].items()}
        self.assertEqual(got, EXPECTED)
        self.assertEqual(report["window"]["start"], "2026-10-26")
        self.assertEqual(report["window"]["end"], "2026-11-01")

    def test_edge_session_before_midnight_et_is_outside(self):
        report = _report(WEEK + [BEFORE_EDGE])
        got = {g: row["proper_sets"] for g, row in report["muscle_sets"].items()}
        self.assertEqual(got, EXPECTED)

    def test_first_window_day_after_midnight_et_counts(self):
        only = dict(WEEK[0], date="2026-10-26T04:18:09Z")
        report = _report([only])
        self.assertEqual(report["muscle_sets"]["hamstrings"]["proper_sets"], 8.0)

    def test_quest_seed_row_counts(self):
        seed = _ex("DB Flat Press", [(45.0, 2, 9)])
        cat = {"name": "DB Flat Press", "movement": "compound"}
        self.assertEqual(exercise_proper_sets(seed, cat, planned_load=None), 2)

    def test_unmapped_names_resolve(self):
        report = _report([WEEK[1], WEEK[2], WEEK[3]])
        sets = report["muscle_sets"]
        # Barbell Flat Bench Press 2 + DB Incline 2.
        self.assertEqual(sets["chest"]["proper_sets"], 4.0)
        # Calf Raises → catalog id calf-raises (named Calf Extensions).
        self.assertEqual(sets["calves"]["proper_sets"], 2.0)
        # Smith Shrugs 1 + Barbell Shrugs 1 + deadlift 0.5.
        self.assertEqual(sets["traps"]["proper_sets"], 2.5)


if __name__ == "__main__":
    unittest.main()
