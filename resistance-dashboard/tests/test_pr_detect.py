"""Automatic PR detection from history."""

from __future__ import annotations

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.pr_detect import apply_auto_prs, historical_bests, is_exercise_pr


def _ex(name, weight, sets=3, reps=10, is_pr=False):
    return ExerciseEntry(
        name=name,
        sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
        is_pr=is_pr,
    )


def _sess(date, st, *exs):
    return Session(date=date, session_type=st, exercises=list(exs))


class TestPrDetect(unittest.TestCase):
    def test_first_time_is_pr(self):
        entry = _ex("DB Flat Press", 50)
        self.assertTrue(is_exercise_pr(entry, {}))

    def test_weight_pr(self):
        hist = historical_bests(
            [_sess("2026-05-01", "push", _ex("DB Flat Press", 45, 3, 10))]
        )
        self.assertTrue(is_exercise_pr(_ex("DB Flat Press", 50, 3, 8), hist))
        self.assertFalse(is_exercise_pr(_ex("DB Flat Press", 45, 3, 8), hist))

    def test_rep_pr_same_weight(self):
        # higher reps → higher e1rm
        hist = historical_bests(
            [_sess("2026-05-01", "push", _ex("DB Flat Press", 50, 3, 8))]
        )
        self.assertTrue(is_exercise_pr(_ex("DB Flat Press", 50, 3, 12), hist))

    def test_apply_skips_same_day_history(self):
        history = [
            _sess("2026-05-01", "push", _ex("DB Flat Press", 40)),
            _sess("2026-07-12", "push", _ex("DB Flat Press", 100)),  # same day later in file
        ]
        new = _sess("2026-07-12", "push", _ex("DB Flat Press", 50))
        apply_auto_prs(new, history)
        # Compared only to 2026-05-01 (before_date), 50 > 40 → PR
        self.assertTrue(new.exercises[0].is_pr)

    def test_ignore_test_names(self):
        entry = _ex("Skeptic Press", 999)
        self.assertFalse(is_exercise_pr(entry, {}))


if __name__ == "__main__":
    unittest.main()
