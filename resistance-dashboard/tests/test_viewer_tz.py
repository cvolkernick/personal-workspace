"""Viewer civil-day TZ: request zone, else DASHBOARD_TZ, else America/New_York."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from api.dashboard import request_tz_name
from rt_dashboard.timeutil import local_today_iso, resolve_tz_name


PINNED = datetime(2026, 8, 19, 2, 0, 0, tzinfo=timezone.utc)


class ViewerTzResolve(unittest.TestCase):
    def test_request_tz_wins_over_env_and_vercel_utc(self):
        env = {"TZ": "UTC", "DASHBOARD_TZ": "America/Chicago"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_tz_name("America/New_York"), "America/New_York")

    def test_dashboard_tz_when_no_client_tz(self):
        env = {"TZ": "UTC", "DASHBOARD_TZ": "America/Chicago"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_tz_name(None), "America/Chicago")
            self.assertEqual(resolve_tz_name(""), "America/Chicago")
            self.assertEqual(resolve_tz_name("not-a-zone"), "America/Chicago")

    def test_fallback_is_new_york_not_vercel_tz(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_tz_name(None), "America/New_York")
            self.assertEqual(resolve_tz_name("garbage/Zone"), "America/New_York")

    def test_two_am_utc_is_prior_civil_day_in_new_york(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                local_today_iso("America/New_York", now=PINNED),
                "2026-08-18",
            )

    def test_no_client_tz_uses_dashboard_tz_civil_day(self):
        env = {"TZ": "UTC", "DASHBOARD_TZ": "America/New_York"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(local_today_iso(None, now=PINNED), "2026-08-18")

    def test_no_client_tz_and_no_dashboard_tz_ignores_vercel_utc(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(local_today_iso(None, now=PINNED), "2026-08-18")

    def test_auckland_is_next_civil_day_not_hardcoded_ny(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                local_today_iso("Pacific/Auckland", now=PINNED),
                "2026-08-19",
            )


class RequestTzName(unittest.TestCase):
    def test_query_param(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                request_tz_name({}, "tz=America/New_York&refresh=1"),
                "America/New_York",
            )

    def test_header_when_no_query(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                request_tz_name({"X-Viewer-TZ": "America/Chicago"}, ""),
                "America/Chicago",
            )

    def test_garbage_falls_back_not_to_utc(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(request_tz_name({}, "tz=Nope/Nope"), "America/New_York")


if __name__ == "__main__":
    unittest.main()
