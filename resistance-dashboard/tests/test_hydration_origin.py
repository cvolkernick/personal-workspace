"""GH hydration origin classification + extras parse (no network)."""

from __future__ import annotations

import unittest
from typing import Optional

from rt_dashboard.dashboard_cache import health_from_dict
from rt_dashboard.google_health import (
    attach_non_hidrate_ml,
    extras_ml_by_date_from_points,
    hydration_origin_is_hidrate,
    hydration_origin_is_non_hidrate,
    parse_hydration_log_points,
)
from rt_dashboard.hidrate_client import HIDRATE_ANDROID_PACKAGE
from rt_dashboard.models import HydrationDay


def _point(ml: float, *, date: str = "2026-08-15", package: Optional[str] = None):
    year, month, day = (int(x) for x in date.split("-"))
    pt = {
        "hydrationLog": {
            "interval": {
                "civilStartTime": {"date": {"year": year, "month": month, "day": day}}
            },
            "amountConsumed": {"milliliters": ml},
        }
    }
    if package is not None:
        pt["dataSource"] = {"application": {"packageName": package}}
    return pt


class TestHydrationOrigin(unittest.TestCase):
    def test_hidrate_package_and_name_token(self):
        hidrate = _point(100, package=HIDRATE_ANDROID_PACKAGE)
        spark = {
            "dataSource": {"device": {"displayName": "HidrateSpark"}},
            "hydrationLog": hidrate["hydrationLog"],
        }
        glass = _point(250, package="com.google.android.apps.healthdata")
        unlabeled = _point(80)
        self.assertTrue(hydration_origin_is_hidrate(hidrate))
        self.assertTrue(hydration_origin_is_hidrate(spark))
        self.assertFalse(hydration_origin_is_hidrate(glass))
        self.assertFalse(hydration_origin_is_hidrate(unlabeled))
        self.assertTrue(hydration_origin_is_non_hidrate(glass))
        self.assertFalse(hydration_origin_is_non_hidrate(hidrate))
        self.assertFalse(hydration_origin_is_non_hidrate(unlabeled))

    def test_extras_exclude_hidrate_and_unlabeled(self):
        payload = {
            "dataPoints": [
                _point(100, package=HIDRATE_ANDROID_PACKAGE),
                _point(250, package="com.google.android.apps.healthdata"),
                _point(80),
            ]
        }
        extras = extras_ml_by_date_from_points(payload, days=60)
        self.assertEqual(extras, {"2026-08-15": 250.0})

    def test_parse_points_keeps_full_total_and_extras(self):
        payload = {
            "dataPoints": [
                _point(100, package=HIDRATE_ANDROID_PACKAGE),
                _point(250, package="com.google.android.apps.healthdata"),
            ]
        }
        days = parse_hydration_log_points(payload, days=60)
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0].water_ml, 350.0)
        self.assertEqual(days[0].non_hidrate_ml, 250.0)
        self.assertEqual(days[0].source, "google_health")

    def test_attach_stamps_and_adds_extra_only_date(self):
        days = [
            HydrationDay(date="2026-08-15", water_ml=1613.8, source="google_health"),
        ]
        stamped = attach_non_hidrate_ml(
            days, {"2026-08-15": 250.0, "2026-08-16": 125.0}
        )
        by = {h.date: h for h in stamped}
        self.assertEqual(by["2026-08-15"].water_ml, 1613.8)
        self.assertEqual(by["2026-08-15"].non_hidrate_ml, 250.0)
        self.assertEqual(by["2026-08-16"].water_ml, 125.0)
        self.assertEqual(by["2026-08-16"].non_hidrate_ml, 125.0)

    def test_cache_roundtrip_preserves_extras(self):
        snap = health_from_dict(
            {
                "hydration": [
                    {
                        "date": "2026-08-15",
                        "water_ml": 1863.8,
                        "source": "hidrate+google_health",
                        "non_hidrate_ml": 250,
                    }
                ]
            }
        )
        self.assertEqual(snap.hydration[0].non_hidrate_ml, 250.0)
        dumped = snap.to_dict()
        again = health_from_dict(dumped)
        self.assertEqual(again.hydration[0].non_hidrate_ml, 250.0)


if __name__ == "__main__":
    unittest.main()
