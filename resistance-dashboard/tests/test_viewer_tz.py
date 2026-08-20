"""Viewer civil-day TZ: request zone, else DASHBOARD_TZ, else America/New_York."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body, request_tz_name
from rt_dashboard.calorie_bars import (
    build_calorie_bars_payload,
    eating_window_fraction,
)
from rt_dashboard.hydration_bars import build_hydration_bars_payload
from rt_dashboard.models import HealthSnapshot, SleepSample
from rt_dashboard.sleep_battery import sleep_battery_from_fitdash_sleep
from rt_dashboard.timeutil import local_now, local_today_iso, local_tz, resolve_tz_name


PINNED = datetime(2026, 8, 19, 2, 0, 0, tzinfo=timezone.utc)
# 12:00 America/New_York on the civil day before PINNED
NOON_NY_AS_UTC = datetime(2026, 8, 18, 16, 0, 0, tzinfo=timezone.utc)
DASHBOARD_PY = Path(__file__).resolve().parents[1] / "api" / "dashboard.py"


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


class ViewerTzSleepAndEating(unittest.TestCase):
    """Wake and eating share eating_window_fraction on the viewer clock."""

    def test_civil_day_fallback_uses_viewer_midnight_not_process_utc(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            now = local_now("America/New_York", now=PINNED)
            self.assertEqual(now.strftime("%Y-%m-%d"), "2026-08-18")
            win = eating_window_fraction(
                now=now,
                tz_name="America/New_York",
                last_wake_at=None,
                empty_at=None,
            )
            self.assertEqual(win["source"], "civil_day_fallback")
            self.assertTrue(win["window_start"].startswith("2026-08-18T00:00:00"))
            self.assertNotIn("+00:00", win["window_start"])
            # Process UTC midnight would have been 2026-08-19
            utc_win = eating_window_fraction(
                now=PINNED, last_wake_at=None, empty_at=None
            )
            self.assertTrue(utc_win["window_start"].startswith("2026-08-19T00:00:00"))

    def test_daily_sleep_wake_is_viewer_7am_not_utc(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            sleep = [
                SleepSample(
                    date="2026-08-18", sleep_hours=8.0, source="google_health"
                )
            ]
            bat = sleep_battery_from_fitdash_sleep(
                sleep, now=NOON_NY_AS_UTC, tz_name="America/New_York"
            )
            self.assertEqual(bat["data_source"], "daily_sleep_approx")
            wake = datetime.fromisoformat(bat["last_wake_at"])
            self.assertEqual(wake.strftime("%Y-%m-%d %H:%M"), "2026-08-18 07:00")
            self.assertEqual(wake.utcoffset(), timedelta(hours=-4))
            utc_bat = sleep_battery_from_fitdash_sleep(
                sleep, now=NOON_NY_AS_UTC
            )
            utc_wake = datetime.fromisoformat(utc_bat["last_wake_at"])
            self.assertEqual(utc_wake.utcoffset(), timedelta(0))

    def test_calorie_bars_share_sleep_battery_window_not_0800_2000(self):
        env = {"TZ": "UTC"}
        with mock.patch.dict(os.environ, env, clear=True):
            sleep = [
                SleepSample(
                    date="2026-08-18", sleep_hours=8.0, source="google_health"
                )
            ]
            now = local_now("America/New_York", now=NOON_NY_AS_UTC)
            bat = sleep_battery_from_fitdash_sleep(
                sleep, now=now, tz_name="America/New_York"
            )
            payload = build_calorie_bars_payload(
                today_consumed={"calories": 0},
                targets={"calories": 2100},
                sleep_battery=bat,
                now=now,
                tz_name="America/New_York",
            )
            win = payload["pacing"]["window"]
            self.assertEqual(win["source"], "sleep_battery")
            self.assertEqual(win["window_start"], bat["last_wake_at"])
            self.assertEqual(win["window_end"], bat["empty_at"])
            self.assertNotIn("T08:00:00", win["window_start"])
            self.assertNotIn("T20:00:00", win["window_end"])
            self.assertAlmostEqual(
                win["fraction"],
                bat["hours_awake"] / bat["awake_budget_hours"],
                places=3,
            )
            hydro = build_hydration_bars_payload(
                hydration=[],
                weight=[],
                sleep_battery=bat,
                as_of="2026-08-18",
                now=now,
                tz_name="America/New_York",
            )
            self.assertEqual(hydro["pacing"]["window"]["window_start"], win["window_start"])
            self.assertEqual(hydro["pacing"]["window"]["window_end"], win["window_end"])
            self.assertAlmostEqual(
                hydro["pacing"]["window_fraction"], win["fraction"], places=4
            )

    def test_dashboard_passes_viewer_now_into_battery_and_bars(self):
        text = DASHBOARD_PY.read_text(encoding="utf-8")
        self.assertIn("now = local_now(tz_name)", text)
        self.assertIn("now=now", text)
        self.assertIn("tz_name=tz_name", text)
        self.assertIn("sleep_battery_from_fitdash_sleep", text)
        self.assertIn("build_calorie_bars_payload", text)
        self.assertIn("build_hydration_bars_payload", text)

    def test_dashboard_wake_and_eating_windows_are_et_not_utc(self):
        """Assay: ?tz=America/New_York → wake/eating are ET, one clock, not Vercel UTC."""
        env = {"TZ": "UTC", "GOOGLE_CLIENT_SECRET": "test-secret"}
        health = HealthSnapshot(
            sleep=[
                SleepSample(
                    date="2026-08-18", sleep_hours=8.0, source="google_health"
                )
            ]
        )
        query = "tz=America/New_York"

        def _pinned_local_now(preferred=None, *, now=None):
            clock = now if now is not None else PINNED
            if clock.tzinfo is None:
                clock = clock.replace(tzinfo=timezone.utc)
            return clock.astimezone(local_tz(preferred))

        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            tz_name = request_tz_name(headers, query)
            self.assertEqual(tz_name, "America/New_York")
            with mock.patch(
                "rt_dashboard.timeutil.local_now", side_effect=_pinned_local_now
            ), mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(health, []),
            ):
                status, body = dashboard_body(headers, query)

        self.assertEqual(status, 200)
        self.assertEqual((body.get("meta") or {}).get("timezone"), "America/New_York")
        self.assertEqual((body.get("meta") or {}).get("local_today"), "2026-08-18")

        bat = body.get("sleep_battery") or {}
        eat = ((body.get("calorie_bars") or {}).get("pacing") or {}).get("window") or {}
        hydro = ((body.get("hydration_bars") or {}).get("pacing") or {}).get("window") or {}

        wake = datetime.fromisoformat(bat["last_wake_at"])
        eat_start = datetime.fromisoformat(eat["window_start"])
        self.assertEqual(wake.strftime("%Y-%m-%d %H:%M"), "2026-08-18 07:00")
        self.assertEqual(wake.utcoffset(), timedelta(hours=-4))
        self.assertNotEqual(wake.utcoffset(), timedelta(0))
        self.assertNotIn("+00:00", bat["last_wake_at"])
        self.assertNotIn("+00:00", eat["window_start"])
        # One clock: eating window is the sleep-battery wake→empty, not a second window.
        self.assertEqual(eat["source"], "sleep_battery")
        self.assertEqual(eat["window_start"], bat["last_wake_at"])
        self.assertEqual(eat["window_end"], bat["empty_at"])
        self.assertEqual(hydro["window_start"], eat["window_start"])
        self.assertEqual(hydro["window_end"], eat["window_end"])
        self.assertEqual(eat_start.utcoffset(), timedelta(hours=-4))
        self.assertNotIn("T08:00:00", eat["window_start"])
        self.assertNotIn("T20:00:00", eat["window_end"])

        utc_bat = sleep_battery_from_fitdash_sleep(
            health.sleep, now=PINNED
        )
        utc_wake = datetime.fromisoformat(utc_bat["last_wake_at"])
        self.assertEqual(utc_wake.utcoffset(), timedelta(0))
        self.assertNotEqual(bat["last_wake_at"], utc_bat["last_wake_at"])


if __name__ == "__main__":
    unittest.main()
