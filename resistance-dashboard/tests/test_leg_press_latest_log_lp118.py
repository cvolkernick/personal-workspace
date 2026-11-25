"""Leg Press progression builds from the latest log after the +118 sled backfill (lp118).

After the one-time backfill, older Leg Press history reads in total pounds
(11/08: 328 x 2 x 4). Chris then logged 208 x 1 x 10 on 2026-11-24 (sled
already included). The next Legs prescription must come from that latest
first working set (208 x 10 -> +5 lb = 213, add_load), not from the older,
heavier 11/08 row (328 x 4 -> 323, drop_load).
"""

from __future__ import annotations

import unittest

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import (
    catalog_exercises,
    default_catalog,
    last_performance,
    prescribe,
)


def _legs(day: str, sets, closed: str) -> Session:
    return Session(
        date=day,
        session_type="legs",
        exercises=[
            ExerciseEntry(
                name="Leg Press",
                sets=[SetEntry(weight_lbs=w, sets=n, reps=r) for w, n, r in sets],
            )
        ],
        closed_at=closed,
        status="complete",
    )


HISTORY = [
    _legs("2026-11-04", [(318.0, 1, 8), (328.0, 1, 5)], "2026-11-05T00:06:19-05:00"),
    _legs("2026-11-08", [(328.0, 2, 4)], "2026-11-09T03:14:59-05:00"),
]
LATEST = _legs("2026-11-24", [(208.0, 1, 10)], "2026-11-25T03:37:07-05:00")


class LegPressLatestLogTest(unittest.TestCase):
    def setUp(self) -> None:
        by_id = {e["id"]: e for e in catalog_exercises(default_catalog(), available_only=False)}
        self.by_id = by_id
        self.leg_press = by_id["leg-press"]

    def test_latest_log_wins_over_heavier_older_history(self) -> None:
        last = last_performance(HISTORY + [LATEST], "Leg Press", self.by_id)
        self.assertEqual(last["date"], "2026-11-24")
        self.assertEqual(last["first_weight_lbs"], 208.0)
        self.assertEqual(last["first_reps"], 10)
        rx = prescribe(self.leg_press, last, default_hard_sets=2)
        self.assertEqual(rx["weight_lbs"], 213.0)
        self.assertEqual(rx["load"], 213.0)
        self.assertEqual(rx["progression_reason"], "add_load")
        self.assertIn("208 lb", rx["rationale"])

    def test_without_latest_log_falls_back_to_backfilled_11_08(self) -> None:
        last = last_performance(HISTORY, "Leg Press", self.by_id)
        rx = prescribe(self.leg_press, last, default_hard_sets=2)
        self.assertEqual(rx["weight_lbs"], 323.0)
        self.assertEqual(rx["progression_reason"], "drop_load")

    def test_no_sled_auto_add(self) -> None:
        # Logged totals are used as typed: 208 stays 208, never 208 + 118.
        last = last_performance([LATEST], "Leg Press", self.by_id)
        self.assertEqual(last["weight_lbs"], 208.0)


if __name__ == "__main__":
    unittest.main()
