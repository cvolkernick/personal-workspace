"""Unit tests for Hidrate client parsing + hydration overlay (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.hidrate_client import (  # noqa: E402
    HidrateClient,
    _day_total_ml,
    overlay_hidrate_hydration,
)
from rt_dashboard.models import HealthSnapshot, HydrationDay  # noqa: E402


class TestDayTotalMl(unittest.TestCase):
    def test_prefers_total_amount(self):
        self.assertEqual(
            _day_total_ml(
                {
                    "totalAmount": 100.5,
                    "totalBottleAmount": 90,
                    "totalVolumeAmount": 80,
                }
            ),
            100.5,
        )

    def test_falls_back(self):
        self.assertEqual(_day_total_ml({"totalBottleAmount": 42}), 42.0)
        self.assertIsNone(_day_total_ml({}))


class TestOverlayHidrate(unittest.TestCase):
    def test_no_credentials_leaves_gh(self):
        snap = HealthSnapshot(
            hydration=[HydrationDay(date="2026-08-05", water_ml=500, source="google_health")]
        )
        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=False
        ):
            out, meta = overlay_hidrate_hydration(snap, days=14)
        self.assertFalse(meta["configured"])
        self.assertEqual(out.hydration[0].water_ml, 500)
        self.assertEqual(out.hydration[0].source, "google_health")

    def test_hidrate_wins_on_overlap_keeps_older_gh(self):
        snap = HealthSnapshot(
            hydration=[
                HydrationDay(date="2026-07-01", water_ml=2000, source="google_health"),
                HydrationDay(date="2026-08-05", water_ml=100, source="google_health"),
            ]
        )
        hidrate_series = [
            HydrationDay(date="2026-08-05", water_ml=2276, source="hidrate"),
            HydrationDay(date="2026-08-04", water_ml=1400, source="hidrate"),
        ]

        class FakeClient:
            def credentials_present(self) -> bool:
                return True

            def fetch_hydration_days(self, days: int = 90):
                return hidrate_series

        with patch(
            "rt_dashboard.hidrate_client.hidrate_credentials_present", return_value=True
        ):
            out, meta = overlay_hidrate_hydration(
                snap, days=14, client=FakeClient()  # type: ignore[arg-type]
            )
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["days"], 2)
        by = {h.date: h for h in out.hydration}
        self.assertEqual(by["2026-07-01"].water_ml, 2000)
        self.assertEqual(by["2026-07-01"].source, "google_health")
        self.assertEqual(by["2026-08-05"].water_ml, 2276)
        self.assertEqual(by["2026-08-05"].source, "hidrate")
        self.assertEqual(by["2026-08-04"].water_ml, 1400)
        self.assertEqual(by["2026-08-04"].source, "hidrate")


class TestFetchHydrationDaysParse(unittest.TestCase):
    def test_skips_future_and_dedupes(self):
        client = HidrateClient(email="x@y.z", password="secret")
        rows = [
            {"date": "2026-08-05", "totalAmount": 100},
            {"date": "2026-08-05", "totalAmount": 2276},  # later wins via by-date
            {"date": "2099-01-01", "totalAmount": 999},
            {"date": "2026-08-04", "totalAmount": 50},
        ]
        with patch.object(client, "ensure_session"), patch.object(
            client, "fetch_day_rows", return_value=rows
        ), patch(
            "rt_dashboard.hidrate_client.local_today_iso", return_value="2026-08-05"
        ), patch(
            "rt_dashboard.hidrate_client.local_tz"
        ) as tz_mock:
            # local_tz only used for start date; return UTC-ish
            from datetime import timezone

            tz_mock.return_value = timezone.utc
            series = client.fetch_hydration_days(days=14)
        by = {h.date: h.water_ml for h in series}
        self.assertNotIn("2099-01-01", by)
        self.assertEqual(by["2026-08-05"], 2276.0)
        self.assertEqual(by["2026-08-04"], 50.0)
        self.assertTrue(all(h.source == "hidrate" for h in series))


if __name__ == "__main__":
    unittest.main()
