"""Agent read-only Today export (#293 / #312). Token/loopback allow; cookie-less deny.

Fixtures cover empty vs populated dashboard slices. No invented ml / sessions / AZM.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import agent_today_body, dispatch_client_route
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.models import (
    ActiveZoneMinutesDay,
    ExerciseEntry,
    HealthSnapshot,
    Session,
    SetEntry,
)


POPULATED = {
    "sessions": [
        {
            "date": "2026-08-23",
            "session_type": "pull",
            "exercises": [
                {
                    "name": "DB Row",
                    "weight_lbs": 50,
                    "sets": [{"weight_lbs": 50, "sets": 3, "reps": 10}],
                    "reps": 10,
                    "volume": 1500,
                }
            ],
        }
    ],
    "workout": {
        "session_type": "pull",
        "is_rest_day": False,
        "exercises": [{"name": "DB Row", "sets": 3, "reps": 10, "weight_lbs": 50}],
        "message": "Pull day",
    },
    "workout_store": {
        "plan": {
            "session_type": "pull",
            "is_rest_day": False,
            "exercises": [{"name": "DB Row", "sets": 3, "reps": 10, "weight_lbs": 50}],
            "message": "Pull day",
        }
    },
    "hydration_bars": {
        "pacing": {
            "consumed": 800.0,
            "target": 3175.0,
            "status": "behind",
            "band": "behind",
            "delta_vs_pace": -400.0,
            "fill_pct": 25.2,
            "expected_pct": 40.0,
            "window_fraction": 0.4,
            "intake_source": "hidrate",
            "civil_day_ml": 200.0,
        },
        "bottle": {"available": True, "percent": 81.0, "status": "ok"},
    },
    "hidrate_bottle": {
        "available": True,
        "percent": 81.0,
        "status": "ok",
        "name": "Puck",
        "field": "batteryLevel",
        "error": None,
    },
    "sleep_battery": {
        "last_wake_at": "2026-08-23T07:00:00-04:00",
        "empty_at": "2026-08-23T23:00:00-04:00",
        "pct_charged": 62.0,
        "mode": "awake",
        "summary": "62% · 8.0h until empty",
        "hours_awake": 8.0,
        "hours_until_empty": 8.0,
    },
    "coach": {"today": {"date": "2026-08-23"}},
    "meta": {"local_today": "2026-08-23", "error": None},
    "health": {
        "active_zone_minutes": [
            {
                "date": f"2026-08-{day:02d}",
                "fat_burn_minutes": 10.0 + day,
                "cardio_minutes": 4.0,
                "peak_minutes": 1.0,
                "total_minutes": 15.0 + day,
                "source": "google_health",
            }
            for day in range(16, 23)
        ]
    },
}

AZM_DAY = {
    "date": "2026-08-16",
    "fat_burn_minutes": 12,
    "cardio_minutes": 8,
    "peak_minutes": 2,
    "total_minutes": 22,
    "source": "google_health",
}


class ExportFixtures(unittest.TestCase):
    def test_empty_payload_is_honest(self):
        body = export_agent_today({})
        self.assertTrue(body["ok"])
        today = body["today"]
        self.assertIsNone(today["date"])
        self.assertTrue(today["workout"]["empty"])
        self.assertIsNone(today["workout"]["session_type"])
        self.assertFalse(today["workout"]["is_rest_day"])
        self.assertEqual(today["workout"]["plan_exercises"], [])
        self.assertEqual(today["workout"]["logged_exercises"], [])
        self.assertIsNone(today["hydration_wake"]["consumed"])
        self.assertIsNone(today["hydration_wake"]["target"])
        self.assertIsNone(today["hydration_wake"]["pace"])
        self.assertFalse(today["bottle"]["available"])
        self.assertIsNone(today["bottle"]["percent"])
        self.assertIsNone(today["wake_window"]["last_wake_at"])
        self.assertIsNone(today["wake_window"]["empty_at"])
        self.assertEqual(today["active_zone_minutes"], [])
        self.assertNotIn("error", body)

    def test_populated_slice_copies_existing_fields_only(self):
        body = export_agent_today(POPULATED)
        self.assertTrue(body["ok"])
        today = body["today"]
        self.assertEqual(today["date"], "2026-08-23")
        wo = today["workout"]
        self.assertEqual(wo["session_type"], "pull")
        self.assertFalse(wo["is_rest_day"])
        self.assertEqual(wo["plan_exercises"][0]["name"], "DB Row")
        self.assertEqual(wo["logged_exercises"][0]["name"], "DB Row")
        self.assertEqual(wo["logged_exercises"][0]["weight_lbs"], 50)
        self.assertFalse(wo["empty"])
        hyd = today["hydration_wake"]
        self.assertEqual(hyd["consumed"], 800.0)
        self.assertEqual(hyd["target"], 3175.0)
        self.assertEqual(hyd["status"], "behind")
        self.assertEqual(hyd["civil_day_ml"], 200.0)
        self.assertNotEqual(hyd["consumed"], hyd["civil_day_ml"])
        self.assertEqual(today["bottle"]["percent"], 81.0)
        self.assertEqual(today["wake_window"]["last_wake_at"], "2026-08-23T07:00:00-04:00")
        self.assertEqual(today["wake_window"]["empty_at"], "2026-08-23T23:00:00-04:00")
        azm = today["active_zone_minutes"]
        self.assertEqual(len(azm), 7)
        self.assertEqual(azm[0]["date"], "2026-08-16")
        self.assertEqual(azm[0]["total_minutes"], 31.0)
        self.assertEqual(azm[-1]["date"], "2026-08-22")

    def test_rest_day_is_signal_not_empty(self):
        body = export_agent_today(
            {
                "workout": {"session_type": "rest", "is_rest_day": True, "exercises": []},
                "workout_store": {
                    "plan": {"session_type": "rest", "is_rest_day": True, "exercises": []}
                },
                "coach": {"today": {"date": "2026-08-23"}},
                "sessions": [],
            }
        )
        wo = body["today"]["workout"]
        self.assertTrue(wo["is_rest_day"])
        self.assertEqual(wo["session_type"], "rest")
        self.assertEqual(wo["plan_exercises"], [])
        self.assertEqual(wo["logged_exercises"], [])
        self.assertFalse(wo["empty"])

    def test_does_not_invent_logged_session(self):
        body = export_agent_today(
            {
                "workout": {"session_type": "push", "is_rest_day": False, "exercises": []},
                "sessions": [],
                "coach": {"today": {"date": "2026-08-23"}},
            }
        )
        self.assertEqual(body["today"]["workout"]["logged_exercises"], [])
        self.assertEqual(body["today"]["workout"]["session_type"], "push")

    def test_logged_session_objects_flatten(self):
        session = Session(
            date="2026-08-23",
            session_type="legs",
            exercises=[
                ExerciseEntry(
                    name="Goblet squat",
                    sets=[SetEntry(weight_lbs=40, sets=2, reps=8)],
                )
            ],
        )
        body = export_agent_today(
            {
                "sessions": [session],
                "coach": {"today": {"date": "2026-08-23"}},
            }
        )
        logged = body["today"]["workout"]["logged_exercises"]
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["name"], "Goblet squat")
        self.assertEqual(logged[0]["weight_lbs"], 40)

    def test_azm_missing_health_is_honest_empty(self):
        body = export_agent_today({"coach": {"today": {"date": "2026-08-23"}}})
        self.assertEqual(body["today"]["active_zone_minutes"], [])

    def test_azm_empty_list_stays_empty(self):
        body = export_agent_today(
            {
                "health": {"active_zone_minutes": []},
                "coach": {"today": {"date": "2026-08-23"}},
            }
        )
        self.assertEqual(body["today"]["active_zone_minutes"], [])

    def test_azm_copies_healthsnapshot_field_only(self):
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
        body = export_agent_today(
            {
                "health": snap,
                "coach": {"today": {"date": "2026-08-23"}},
            }
        )
        self.assertEqual(body["today"]["active_zone_minutes"], [AZM_DAY])

    def test_does_not_invent_azm_from_other_series(self):
        body = export_agent_today(
            {
                "health": {
                    "calories_burned": [{"date": "2026-08-16", "calories": 400}],
                    "hydration": [{"date": "2026-08-16", "water_ml": 2000}],
                    "weight": [{"date": "2026-08-16", "weight_lbs": 180}],
                },
                "coach": {"today": {"date": "2026-08-23"}},
            }
        )
        self.assertEqual(body["today"]["active_zone_minutes"], [])


class VercelAgentTodayAuth(unittest.TestCase):
    def _assert_no_secrets(self, body: dict) -> None:
        blob = json.dumps(body)
        self.assertNotIn("house-secret", blob)
        self.assertNotIn("test-secret", blob)
        self.assertNotIn("1//", blob)

    def test_cookie_less_without_token_is_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = agent_today_body({}, "")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertFalse(body.get("ok", True))
        self.assertNotIn("today", body)
        self.assertNotIn("active_zone_minutes", body)
        self.assertNotIn("<html", json.dumps(body).lower())
        self._assert_no_secrets(body)

    def test_dashboard_stays_cookie_gated(self):
        from api.dashboard import dashboard_body

        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = dashboard_body({}, "")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")

    def test_token_allows_without_google_cookie(self):
        env = {"FITDASH_SERVICE_TOKEN": "house-secret", "FITDASH_SERVICE_LOOPBACK": "0"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.workout._util._agent_today_from_stores",
                return_value=(200, export_agent_today(POPULATED)),
            ) as load:
                status, body = agent_today_body(
                    {"X-FitDash-Service-Token": "house-secret"}, ""
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["today"]["date"], "2026-08-23")
        self.assertEqual(body["today"]["workout"]["session_type"], "pull")
        self.assertIn("active_zone_minutes", body["today"])
        self.assertEqual(len(body["today"]["active_zone_minutes"]), 7)
        self.assertEqual(body["today"]["active_zone_minutes"][0]["date"], "2026-08-16")
        self._assert_no_secrets(body)
        load.assert_called_once()

    def test_bearer_token_allows(self):
        env = {"FITDASH_SERVICE_TOKEN": "house-secret", "FITDASH_SERVICE_LOOPBACK": "0"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.workout._util._agent_today_from_stores",
                return_value=(200, export_agent_today({})),
            ):
                status, body = agent_today_body(
                    {"Authorization": "Bearer house-secret"}, ""
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["today"]["workout"]["empty"])
        self.assertEqual(body["today"]["active_zone_minutes"], [])
        self._assert_no_secrets(body)

    def test_wrong_token_denied(self):
        env = {"FITDASH_SERVICE_TOKEN": "house-secret", "FITDASH_SERVICE_LOOPBACK": "0"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = agent_today_body(
                {"Authorization": "Bearer nope"}, "", client_host="192.168.100.5"
            )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("today", body)
        self.assertNotIn("active_zone_minutes", body)
        self._assert_no_secrets(body)

    def test_loopback_mock_allows_without_token(self):
        env = {"FITDASH_SERVICE_LOOPBACK": "1"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.workout._util._agent_today_from_stores",
                return_value=(200, export_agent_today({})),
            ):
                status, body = agent_today_body({}, "", client_host="127.0.0.1")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNone(body["today"]["hydration_wake"]["consumed"])
        self.assertEqual(body["today"]["active_zone_minutes"], [])

    def test_dispatch_rewrite_and_path(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = dispatch_client_route({}, "_r=agent_today", "GET")
            self.assertEqual(status, 401)
            self.assertEqual(body["error"], "auth_required")
            status, body = dispatch_client_route(
                {}, "", "GET", path="/api/agent/today"
            )
            self.assertEqual(status, 401)

    def test_signed_in_uses_dashboard_slice(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "api.dashboard.dashboard_body",
                return_value=(200, POPULATED),
            ):
                status, body = agent_today_body(headers, "")
        self.assertEqual(status, 200)
        self.assertEqual(body["today"]["bottle"]["percent"], 81.0)
        self.assertEqual(len(body["today"]["active_zone_minutes"]), 7)
        self._assert_no_secrets(body)

    def test_store_path_honest_when_health_and_hidrate_missing(self):
        env = {
            "FITDASH_SERVICE_TOKEN": "house-secret",
            "FITDASH_SERVICE_LOOPBACK": "0",
            "TZ": "UTC",
        }
        empty_bottle = {
            "available": False,
            "percent": None,
            "status": "not_configured",
            "name": None,
            "field": None,
            "error": None,
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions", return_value=([], [], "turso")
            ), mock.patch(
                "rt_dashboard.google_health.GoogleHealthClient.credentials_present",
                return_value=False,
            ), mock.patch(
                "rt_dashboard.hidrate_client.hidrate_bottle_charge",
                return_value=empty_bottle,
            ), mock.patch(
                "rt_dashboard.hidrate_client.hidrate_hydration_samples",
                return_value=[],
            ):
                status, body = agent_today_body(
                    {"X-FitDash-Service-Token": "house-secret"}, ""
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["today"]["workout"]["logged_exercises"], [])
        self.assertFalse(body["today"]["bottle"]["available"])
        self.assertIsNone(body["today"]["bottle"]["percent"])
        # No wake → hydration consumed is 0 from the Sip helper, not civil-day fake green
        hyd = body["today"]["hydration_wake"]
        self.assertIn(hyd["consumed"], (0, 0.0, None))
        if hyd["consumed"] in (0, 0.0):
            self.assertEqual(hyd.get("intake_source"), "none")
        self.assertIsNone(body["today"]["wake_window"]["last_wake_at"])
        self.assertEqual(body["today"]["active_zone_minutes"], [])
        self._assert_no_secrets(body)

    def test_store_path_copies_healthsnapshot_azm(self):
        env = {
            "FITDASH_SERVICE_TOKEN": "house-secret",
            "FITDASH_SERVICE_LOOPBACK": "0",
            "TZ": "UTC",
        }
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
        empty_bottle = {
            "available": False,
            "percent": None,
            "status": "not_configured",
            "name": None,
            "field": None,
            "error": None,
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions", return_value=([], [], "turso")
            ), mock.patch(
                "rt_dashboard.google_health.GoogleHealthClient.credentials_present",
                return_value=True,
            ), mock.patch(
                "api.dashboard._load_health", return_value=(snap, [])
            ), mock.patch(
                "rt_dashboard.hidrate_client.hidrate_bottle_charge",
                return_value=empty_bottle,
            ), mock.patch(
                "rt_dashboard.hidrate_client.hidrate_hydration_samples",
                return_value=[],
            ):
                status, body = agent_today_body(
                    {"X-FitDash-Service-Token": "house-secret"}, ""
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["today"]["active_zone_minutes"], [AZM_DAY])
        self._assert_no_secrets(body)


if __name__ == "__main__":
    unittest.main()
