"""RHR, steps and AZM fetches reach the 90-day cutoff (no 4-page / partial stop)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from rt_dashboard.google_health import GoogleHealthClient, GoogleHealthError


def _client():
    c = GoogleHealthClient.__new__(GoogleHealthClient)
    return c


class RhrPaging(unittest.TestCase):
    def test_pages_until_cutoff_beyond_four_pages(self):
        today = datetime.now().astimezone().date()
        # 10 points per page, newest first, 12 pages => 120 days of history.
        pages = []
        for i in range(12):
            pts = []
            for j in range(10):
                d = today - timedelta(days=i * 10 + j)
                pts.append({"dailyRestingHeartRate": {"date": {"year": d.year, "month": d.month, "day": d.day},
                                                       "beatsPerMinute": 55}})
            pages.append({"dataPoints": pts, "nextPageToken": f"t{i}" if i < 11 else None})
        calls = []

        def fake_request(method, url, body=None):
            calls.append(url)
            return pages[len(calls) - 1]

        c = _client()
        c._request = fake_request
        c._data_point_date = lambda p: "%04d-%02d-%02d" % tuple(
            p["dailyRestingHeartRate"]["date"][k] for k in ("year", "month", "day"))
        with mock.patch("rt_dashboard.google_health.parse_daily_resting_heart_rate_points",
                        side_effect=lambda data, days: data["dataPoints"]):
            out = c.fetch_resting_heart_rate(days=90)
        self.assertGreater(len(calls), 4)
        self.assertEqual(len(calls), 10)  # stops on the first page older than the cutoff, not page 12
        oldest = min(c._data_point_date(p) for p in out)
        self.assertLessEqual(oldest, (today - timedelta(days=89)).isoformat())

    def test_safety_cap_stops_runaway(self):
        n = {"i": 0}

        def fake_request(method, url, body=None):
            n["i"] += 1
            return {"dataPoints": [], "nextPageToken": "again"}

        c = _client()
        c._request = fake_request
        c._paginate_data_points("x", max_pages=1, until_date="2000-01-01", safety_cap=7)
        self.assertEqual(n["i"], 7)


class RollupChunks(unittest.TestCase):
    def _run(self, name, fail_window=None):
        windows = []

        def fake_rollup(data_type, days=30, *, end_date=None):
            end = end_date.date() if isinstance(end_date, datetime) else end_date
            windows.append((end, days))
            if fail_window is not None and len(windows) == fail_window:
                raise GoogleHealthError("boom")
            return {"rollupDataPoints": [{"d": (end - timedelta(days=k)).isoformat()} for k in range(days)]}

        c = _client()
        c.daily_rollup = fake_rollup
        target = {"steps": "parse_steps_rollup", "azm": "parse_active_zone_minutes_rollup"}[name]
        with mock.patch(f"rt_dashboard.google_health.{target}", side_effect=lambda d: d["rollupDataPoints"]):
            out = c.fetch_steps(90) if name == "steps" else c.fetch_active_zone_minutes(90)
        return out, windows

    def _assert_full(self, out):
        today = datetime.now().astimezone().date()
        days = {p["d"] for p in out}
        self.assertIn((today - timedelta(days=89)).isoformat(), days)
        self.assertIn(today.isoformat(), days)

    def test_steps_reach_cutoff(self):
        out, _ = self._run("steps")
        self.assertEqual(len(out), 90)
        self._assert_full(out)

    def test_azm_reach_cutoff(self):
        out, _ = self._run("azm")
        self.assertEqual(len(out), 90)
        self._assert_full(out)

    def test_one_failed_window_does_not_truncate_history(self):
        # Window 3 fails (and its 7-day retry fails): older windows still fetched.
        out, windows = self._run("azm", fail_window=3)
        self._assert_full(out)
        out2, _ = self._run("steps", fail_window=3)
        self._assert_full(out2)


if __name__ == "__main__":
    unittest.main()
