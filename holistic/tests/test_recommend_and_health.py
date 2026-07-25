"""Tests for next-action recommender, Duchess migration, health sync helpers."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.domain import (  # noqa: E402
    add_log,
    build_rolling_plan,
    empty_state,
    normalize_state,
    seed_starter,
)
from holistic.time_allocator.health_sync import sync_sleep_logs  # noqa: E402
from holistic.time_allocator.recommend import recommend_next  # noqa: E402


class DuchessMigrationTests(unittest.TestCase):
    def test_migrates_old_130_minute_target(self) -> None:
        raw = {
            "version": 2,
            "items": [],
            "targets": [
                {
                    "id": "duchess-walk",
                    "title": "Walk Duchess",
                    "kind": "daily_duration",
                    "minutes": 130,
                    "priority": 9,
                }
            ],
            "logs": [],
        }
        state = normalize_state(raw)
        t = state["targets"][0]
        self.assertEqual(t["minutes"], 45)
        self.assertEqual(t["minutes_min"], 30)
        self.assertEqual(t["minutes_max"], 60)


class RecommendTests(unittest.TestCase):
    def test_core_task_urgencies(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        plan = build_rolling_plan(
            state, now=datetime(2026, 7, 17, 10, 0, 0), as_of=date(2026, 7, 17)
        )
        recs = recommend_next(state, plan=plan, now=datetime(2026, 7, 17, 10, 0, 0), limit=10)
        by_id = {r["id"]: r for r in recs}
        if "lyft" in by_id:
            self.assertEqual(by_id["lyft"]["urgency"], "high")
        if "workout" in by_id:
            self.assertEqual(by_id["workout"]["urgency"], "medium")
        if "duchess-walk" in by_id:
            self.assertEqual(by_id["duchess-walk"]["urgency"], "low")
        # High urgency (lyft) should sort before low (duchess) when both present
        ids = [r["id"] for r in recs]
        if "lyft" in ids and "duchess-walk" in ids:
            self.assertLess(ids.index("lyft"), ids.index("duchess-walk"))

    def test_after_duchess_logged_not_forced_again(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        state = add_log(state, "duchess-walk", 45, on=date(2026, 7, 17))
        plan = build_rolling_plan(
            state, now=datetime(2026, 7, 17, 10, 0, 0), as_of=date(2026, 7, 17)
        )
        self.assertNotIn("duchess-walk", {b["id"] for b in plan["blocks"]})


class HealthSyncTests(unittest.TestCase):
    def test_sync_sleep_from_samples(self) -> None:
        state = seed_starter(empty_state(), personal=True)
        fake = (
            [
                {"date": "2026-07-15", "sleep_hours": 7.5, "source": "test"},
                {"date": "2026-07-16", "sleep_hours": 8.2, "source": "test"},
            ],
            "test_source",
        )
        with mock.patch(
            "holistic.time_allocator.health_sync.fetch_sleep_samples",
            return_value=fake,
        ):
            new_state, meta = sync_sleep_logs(state, days=7)
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["imported"], 2)
        sleep_logs = [lg for lg in new_state["logs"] if lg["target_id"] == "sleep"]
        self.assertEqual(len(sleep_logs), 2)
        self.assertEqual(sleep_logs[0]["value"], 7.5)


if __name__ == "__main__":
    unittest.main()
