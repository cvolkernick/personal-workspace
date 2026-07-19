"""Planned vs actual allocation comparison."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.actual import (  # noqa: E402
    allocation_delta,
    build_actual_allocation,
)
from holistic.time_allocator.domain import (  # noqa: E402
    build_rolling_plan,
    empty_state,
    log_action_progress,
    seed_starter,
)


class ActualAllocationTests(unittest.TestCase):
    def test_actual_includes_confirmed_walk_minutes(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        tz = timezone(timedelta(hours=-4))
        now = datetime(2026, 7, 19, 14, 0, tzinfo=tz)
        state["activity_reviews"] = [
            {
                "id": "ex-1",
                "start": datetime(2026, 7, 19, 8, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 7, 19, 8, 19, tzinfo=tz).isoformat(),
                "minutes": 19,
                "exercise_type": "WALKING",
                "status": "confirmed_duchess",
                "local_date": "2026-07-19",
                "target_hint": "duchess-walk",
            }
        ]
        state = log_action_progress(
            state, "duchess-walk", minutes=19, on=date(2026, 7, 19)
        )
        actual = build_actual_allocation(state, now=now)
        by_id = {b["id"]: b for b in actual["blocks"]}
        self.assertIn("duchess-walk", by_id)
        self.assertEqual(by_id["duchess-walk"]["minutes"], 19)

    def test_delta_positive_when_under_plan(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        now = datetime(2026, 7, 19, 12, 0, 0)
        planned = build_rolling_plan(state, now=now, ignore_progress=True)
        actual = build_actual_allocation(state, now=now)
        delta = {r["id"]: r for r in allocation_delta(planned, actual)}
        # Duchess fully recommended, nothing actual → positive gap
        self.assertGreater(delta["duchess-walk"]["delta_minutes"], 0)
        self.assertEqual(delta["duchess-walk"]["planned_minutes"], 45)


if __name__ == "__main__":
    unittest.main()
