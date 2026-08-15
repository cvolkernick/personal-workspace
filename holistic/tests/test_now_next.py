"""Fixed-clock tests for the NOW / NEXT / THEN composer."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.now_next import (  # noqa: E402
    NO_LIVE_PLAN,
    compose_now_next,
    fly_order,
)

TZ = timezone(timedelta(hours=-4))
WINDOW_START = datetime(2026, 8, 15, 10, 0, 0, tzinfo=TZ)

BLOCKS = [
    {"id": "sleep", "title": "Sleep", "role": "reserve", "minutes": 480},
    {"id": "duchess-walk", "title": "Walk Duchess", "role": "fixed", "minutes": 45},
    {"id": "workout", "title": "Workout", "role": "session", "minutes": 60},
    {"id": "lyft", "title": "Lyft driving", "role": "fill", "minutes": 855},
]


def _plan(
    start: datetime = WINDOW_START,
    blocks: list[dict] | None = None,
) -> dict:
    rows = list(BLOCKS if blocks is None else blocks)
    total = sum(int(b["minutes"]) for b in rows)
    return {
        "window_start": start.isoformat(timespec="seconds"),
        "window_end": (start + timedelta(minutes=total)).isoformat(timespec="seconds"),
        "blocks": rows,
    }


class FlyOrderTests(unittest.TestCase):
    def test_reserve_flies_last(self) -> None:
        ids = [b["id"] for b in fly_order(BLOCKS)]
        self.assertEqual(ids, ["duchess-walk", "workout", "lyft", "sleep"])


class ComposeNowNextTests(unittest.TestCase):
    def test_in_block(self) -> None:
        # 10:20 is 20 min into Duchess (first fly leg 10:00–10:45)
        clock = datetime(2026, 8, 15, 10, 20, 0, tzinfo=TZ)
        packet = compose_now_next(_plan(), now=clock)
        self.assertFalse(packet["stale"])
        self.assertIsNone(packet["reason"])
        now = packet["now"]
        nxt = packet["next"]
        then = packet["then"]
        self.assertIsNotNone(now)
        self.assertEqual(now["id"], "duchess-walk")
        self.assertEqual(now["role"], "fixed")
        self.assertEqual(now["title"], "Walk Duchess")
        self.assertEqual(now["start"], "2026-08-15T10:00:00-04:00")
        self.assertEqual(now["end"], "2026-08-15T10:45:00-04:00")
        self.assertEqual(now["remaining_seconds"], 25 * 60)
        self.assertEqual(nxt["id"], "workout")
        self.assertEqual(nxt["start"], "2026-08-15T10:45:00-04:00")
        self.assertEqual(then["id"], "lyft")
        self.assertEqual(then["start"], "2026-08-15T11:45:00-04:00")
        self.assertNotEqual(now["id"], nxt["id"])
        self.assertNotEqual(now["id"], then["id"])
        self.assertEqual(packet["generated_at"], clock.isoformat(timespec="seconds"))

    def test_on_boundary(self) -> None:
        # Exclusive end: 10:45 is the start of Workout, not Duchess
        clock = datetime(2026, 8, 15, 10, 45, 0, tzinfo=TZ)
        packet = compose_now_next(_plan(), now=clock)
        self.assertFalse(packet["stale"])
        self.assertEqual(packet["now"]["id"], "workout")
        self.assertEqual(packet["now"]["start"], "2026-08-15T10:45:00-04:00")
        self.assertEqual(packet["now"]["end"], "2026-08-15T11:45:00-04:00")
        self.assertEqual(packet["now"]["remaining_seconds"], 60 * 60)
        self.assertEqual(packet["next"]["id"], "lyft")
        self.assertEqual(packet["then"]["id"], "sleep")
        self.assertNotEqual(packet["now"]["id"], packet["next"]["id"])

    def test_empty_plan(self) -> None:
        clock = datetime(2026, 8, 15, 10, 20, 0, tzinfo=TZ)
        for plan in (None, {}, {"blocks": []}, {"window_start": WINDOW_START.isoformat()}):
            packet = compose_now_next(plan, now=clock)
            self.assertTrue(packet["stale"], plan)
            self.assertIsNone(packet["now"], plan)
            self.assertIsNone(packet["next"], plan)
            self.assertIsNone(packet["then"], plan)
            self.assertEqual(packet["reason"], NO_LIVE_PLAN)

    def test_all_blocks_in_past(self) -> None:
        start = datetime(2026, 8, 1, 8, 0, 0, tzinfo=TZ)
        clock = datetime(2026, 8, 15, 10, 0, 0, tzinfo=TZ)
        packet = compose_now_next(_plan(start=start), now=clock)
        self.assertTrue(packet["stale"])
        self.assertIsNone(packet["now"])
        self.assertIsNone(packet["next"])
        self.assertIsNone(packet["then"])
        self.assertEqual(packet["reason"], NO_LIVE_PLAN)

    def test_honest_empty_next_then_on_last_leg(self) -> None:
        clock = datetime(2026, 8, 16, 8, 0, 0, tzinfo=TZ)  # inside sleep (last)
        packet = compose_now_next(_plan(), now=clock)
        self.assertFalse(packet["stale"])
        self.assertEqual(packet["now"]["id"], "sleep")
        self.assertIsNone(packet["next"])
        self.assertIsNone(packet["then"])

    def test_unparseable_window_start(self) -> None:
        packet = compose_now_next(
            {"window_start": "not-a-date", "blocks": BLOCKS},
            now=WINDOW_START,
        )
        self.assertTrue(packet["stale"])
        self.assertIsNone(packet["now"])
        self.assertEqual(packet["reason"], NO_LIVE_PLAN)


if __name__ == "__main__":
    unittest.main()
