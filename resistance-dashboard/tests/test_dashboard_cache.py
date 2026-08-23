"""Tests for local dashboard remote cache."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.dashboard_cache import (
    health_from_dict,
    is_fresh,
    load_github_sessions_cache,
    load_health_cache,
    save_github_sessions_cache,
    save_health_cache,
)
from rt_dashboard.models import (
    ActiveZoneMinutesDay,
    ExerciseEntry,
    HealthSnapshot,
    Session,
    SetEntry,
    WeightSample,
)


class TestDashboardCache(unittest.TestCase):
    def test_is_fresh(self):
        now = time.time()
        self.assertTrue(is_fresh(now - 10, ttl=3600))
        self.assertFalse(is_fresh(now - 4000, ttl=3600))
        self.assertFalse(is_fresh(None, ttl=3600))

    def test_health_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("rt_dashboard.dashboard_cache.CACHE_DIR", Path(td)):
                snap = HealthSnapshot(
                    weight=[WeightSample(date="2026-07-11", weight_lbs=180.5)],
                    sleep=[],
                    nutrition=[],
                    hydration=[],
                    calories_burned=[],
                )
                save_health_cache(snap)
                loaded, fetched_at, meta = load_health_cache()
                self.assertTrue(meta.get("hit"))
                self.assertIsNotNone(fetched_at)
                self.assertIsNotNone(loaded)
                self.assertEqual(len(loaded.weight), 1)
                self.assertEqual(loaded.weight[0].weight_lbs, 180.5)

    def test_sessions_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("rt_dashboard.dashboard_cache.CACHE_DIR", Path(td)):
                s = Session(
                    date="2026-07-10",
                    session_type="push",
                    exercises=[
                        ExerciseEntry(
                            name="Bench",
                            sets=[SetEntry(weight_lbs=135, sets=3, reps=8)],
                        )
                    ],
                )
                save_github_sessions_cache([s])
                sessions, fetched_at, meta = load_github_sessions_cache()
                self.assertTrue(meta.get("hit"))
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0].session_type, "push")
                self.assertEqual(sessions[0].exercises[0].name, "Bench")

    def test_health_from_dict(self):
        snap = health_from_dict(
            {
                "weight": [{"date": "2026-01-01", "weight_lbs": 100}],
                "sleep": [{"date": "2026-01-01", "sleep_hours": 7.5}],
            }
        )
        self.assertEqual(snap.weight[0].weight_lbs, 100)
        self.assertEqual(snap.sleep[0].sleep_hours, 7.5)

    def test_azm_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("rt_dashboard.dashboard_cache.CACHE_DIR", Path(td)):
                snap = HealthSnapshot(
                    active_zone_minutes=[
                        ActiveZoneMinutesDay(
                            date="2026-08-16",
                            fat_burn_minutes=12,
                            cardio_minutes=8,
                            peak_minutes=2,
                            total_minutes=22,
                        )
                    ]
                )
                save_health_cache(snap)
                loaded, _, meta = load_health_cache()
                self.assertTrue(meta.get("hit"))
                self.assertIsNotNone(loaded)
                self.assertEqual(len(loaded.active_zone_minutes), 1)
                self.assertEqual(loaded.active_zone_minutes[0].total_minutes, 22.0)
                self.assertEqual(
                    loaded.to_dict()["active_zone_minutes"][0]["fat_burn_minutes"],
                    12.0,
                )


if __name__ == "__main__":
    unittest.main()
