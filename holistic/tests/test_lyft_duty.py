"""Lyft 12h drive / 6h break duty cycle."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.domain import (  # noqa: E402
    build_rolling_plan,
    empty_state,
    seed_starter,
)
from holistic.time_allocator.lyft_duty import (  # noqa: E402
    schedule_drive_in_window,
    set_lyft_driven,
)


class LyftDutyTests(unittest.TestCase):
    def test_remaining_cap_limits_fill_current_cycle(self) -> None:
        # 4h already driven → 8h left; free window 14h; remaining mode = only 8h drive
        sched = schedule_drive_in_window(
            available_minutes=14 * 60,
            driven_minutes=4 * 60,
            drive_cap_minutes=12 * 60,
            break_minutes=6 * 60,
            allow_next_cycle=False,
        )
        self.assertEqual(sched["drive_minutes"], 8 * 60)
        self.assertEqual(sched["segments"][0]["role"], "drive")
        self.assertNotIn("break", [s["role"] for s in sched["segments"]])

    def test_recommended_includes_break_and_next_cycle(self) -> None:
        sched = schedule_drive_in_window(
            available_minutes=14 * 60,
            driven_minutes=4 * 60,
            drive_cap_minutes=12 * 60,
            break_minutes=6 * 60,
            allow_next_cycle=True,
        )
        # 8h drive + 6h break + 0 next (14-8-6=0)
        self.assertEqual(sched["drive_minutes"], 8 * 60)
        self.assertIn("break", [s["role"] for s in sched["segments"]])

    def test_at_cap_requires_break_first(self) -> None:
        sched = schedule_drive_in_window(
            available_minutes=10 * 60,
            driven_minutes=12 * 60,
            drive_cap_minutes=12 * 60,
            break_minutes=6 * 60,
            allow_next_cycle=True,
        )
        # 6h break + 4h drive
        self.assertEqual(sched["drive_minutes"], 4 * 60)
        self.assertEqual(sched["segments"][0]["role"], "break")
        self.assertEqual(sched["segments"][0]["minutes"], 6 * 60)

    def test_plan_uses_duty_remaining(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        # enough workouts so no forced session; clear duchess by logging full
        from holistic.time_allocator.domain import add_log, log_action_progress
        from datetime import date

        for i in range(3):
            state = add_log(state, "workout", 1, on=date(2026, 7, 16 + i))
        state = log_action_progress(state, "duchess-walk", complete=True, on=date(2026, 7, 19))
        state = set_lyft_driven(state, 10 * 60)  # 10h driven → 2h left
        plan = build_rolling_plan(
            state, now=datetime(2026, 7, 19, 12, 0, 0), as_of=date(2026, 7, 19)
        )
        lyft = next(b for b in plan["blocks"] if b["id"] == "lyft")
        self.assertEqual(lyft["minutes"], 2 * 60)
        self.assertEqual(lyft.get("remaining_drive_minutes"), 2 * 60)


if __name__ == "__main__":
    unittest.main()
