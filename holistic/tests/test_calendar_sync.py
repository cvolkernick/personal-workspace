"""Calendar busy time reduces free active allocation."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.calendar_sync import (  # noqa: E402
    busy_minutes_in_window,
    calendar_blocks_for_plan,
    merge_busy_intervals,
    normalize_event,
)
from holistic.time_allocator.domain import build_rolling_plan, seed_starter  # noqa: E402


class CalendarSyncTests(unittest.TestCase):
    def test_normalize_skips_all_day_and_transparent(self) -> None:
        all_day = {
            "id": "a",
            "summary": "Holiday",
            "start": {"date": "2026-07-26"},
            "end": {"date": "2026-07-27"},
        }
        free = {
            "id": "b",
            "summary": "Focus block",
            "transparency": "transparent",
            "start": {"dateTime": "2026-07-26T10:00:00-04:00"},
            "end": {"dateTime": "2026-07-26T11:00:00-04:00"},
        }
        busy = {
            "id": "c",
            "summary": "Dentist",
            "start": {"dateTime": "2026-07-26T14:00:00-04:00"},
            "end": {"dateTime": "2026-07-26T15:00:00-04:00"},
        }
        self.assertIsNone(normalize_event(all_day))
        self.assertIsNone(normalize_event(free))
        n = normalize_event(busy)
        self.assertIsNotNone(n)
        assert n is not None
        self.assertEqual(n["title"], "Dentist")
        self.assertIn("2026-07-26", n["start"])

    def test_merge_overlapping(self) -> None:
        tz = timezone(timedelta(hours=-4))
        win0 = datetime(2026, 7, 26, 8, 0, tzinfo=tz)
        win1 = datetime(2026, 7, 26, 20, 0, tzinfo=tz)
        events = [
            {
                "start": datetime(2026, 7, 26, 10, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 7, 26, 11, 30, tzinfo=tz).isoformat(),
            },
            {
                "start": datetime(2026, 7, 26, 11, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 7, 26, 12, 0, tzinfo=tz).isoformat(),
            },
        ]
        merged = merge_busy_intervals(events, win_start=win0, win_end=win1)
        self.assertEqual(len(merged), 1)
        mins = (merged[0][1] - merged[0][0]).total_seconds() / 60
        self.assertAlmostEqual(mins, 120.0, places=0)

    def test_plan_reduces_fill_for_calendar(self) -> None:
        tz = timezone(timedelta(hours=-4))
        now = datetime(2026, 7, 26, 8, 0, tzinfo=tz)
        state = seed_starter(None, personal=True)
        # Two-hour meeting this morning
        state["calendar_events"] = [
            {
                "id": "m1",
                "title": "Client call",
                "start": datetime(2026, 7, 26, 10, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 7, 26, 12, 0, tzinfo=tz).isoformat(),
                "source": "google_calendar",
            }
        ]
        plan = build_rolling_plan(state, now=now, ignore_progress=True)
        self.assertEqual(plan.get("calendar_busy_minutes"), 120)
        cal = next(b for b in plan["blocks"] if b.get("id") == "calendar")
        self.assertEqual(cal["minutes"], 120)
        self.assertEqual(cal["role"], "calendar")
        # Free active = 16h awake - 2h calendar = 14h (960 - 120) before other claims
        self.assertEqual(plan.get("free_active_minutes"), 960 - 120)
        lyft = next((b for b in plan["blocks"] if b.get("id") == "lyft"), None)
        # Lyft fill must be smaller than without calendar (would have been ~all free)
        self.assertIsNotNone(lyft)
        assert lyft is not None
        self.assertLess(int(lyft["minutes"]), 960)

    def test_busy_minutes_window(self) -> None:
        tz = timezone(timedelta(hours=-4))
        now = datetime(2026, 7, 26, 12, 0, tzinfo=tz)
        events = [
            {
                "id": "x",
                "title": "Gym",
                "start": datetime(2026, 7, 26, 14, 0, tzinfo=tz).isoformat(),
                "end": datetime(2026, 7, 26, 15, 0, tzinfo=tz).isoformat(),
                "target_hint": "workout",
            }
        ]
        total, slices = busy_minutes_in_window(events, now=now, window_minutes=24 * 60)
        self.assertEqual(total, 60)
        self.assertEqual(len(slices), 1)
        blocks, busy, notes = calendar_blocks_for_plan(
            {"calendar_events": events}, now=now, window_minutes=24 * 60
        )
        self.assertEqual(busy, 60)
        self.assertEqual(blocks[0]["id"], "calendar")
        self.assertTrue(any("workout" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
