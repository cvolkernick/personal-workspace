"""Civil date range for Google Health dailyRollUp."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from rt_dashboard.google_health import GoogleHealthClient


class TestCivilRangeBody(unittest.TestCase):
    def test_end_is_exclusive_plus_one(self):
        c = GoogleHealthClient()
        # Fixed inclusive end day
        end = datetime(2026, 7, 28, 15, 0, 0, tzinfo=timezone.utc)
        body = c._civil_range_body(days=3, end_date=end)
        start = body["range"]["start"]["date"]
        api_end = body["range"]["end"]["date"]
        self.assertEqual(start, {"year": 2026, "month": 7, "day": 26})
        # Inclusive last day 28 → exclusive end 29
        self.assertEqual(api_end, {"year": 2026, "month": 7, "day": 29})
        self.assertEqual(body["windowSizeDays"], 1)
        self.assertEqual(body["pageSize"], 3)


if __name__ == "__main__":
    unittest.main()
