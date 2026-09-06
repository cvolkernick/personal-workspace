"""Service-token / loopback auth for FitDash machine reads.

Covers ``_service_auth_ok`` and HTTP ``GET /api/day_constraints`` /
``GET /api/agent/today`` so Orchestra / agents do not need a Google
browser session. Other personal APIs stay session-gated.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as fitdash_server  # noqa: E402


FAKE_PACKET = {
    "as_of": "2026-08-15T01:00:00Z",
    "civil_day": "2026-08-14",
    "train_recommendation": "train",
    "protein_gap_band": "ok",
}
FAKE_BATTERY = {"pct_charged": 41.0, "mode": "awake", "empty_at": None, "summary": "ok"}


class _Headers:
    def __init__(self, data: Optional[Dict[str, str]] = None) -> None:
        self._data = {str(k).lower(): str(v) for k, v in (data or {}).items()}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(str(key).lower(), default)


class ServiceAuthHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "FITDASH_REQUIRE_AUTH",
                "FITDASH_SERVICE_TOKEN",
                "FITDASH_SERVICE_LOOPBACK",
            )
        }

    def tearDown(self) -> None:
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _set_env(self, *, token: str = "", loopback: str = "1") -> None:
        if token:
            os.environ["FITDASH_SERVICE_TOKEN"] = token
        else:
            os.environ.pop("FITDASH_SERVICE_TOKEN", None)
        os.environ["FITDASH_SERVICE_LOOPBACK"] = loopback

    def test_loopback_allowed_by_default(self) -> None:
        self._set_env(token="", loopback="1")
        self.assertTrue(fitdash_server._service_auth_ok(_Headers(), "127.0.0.1"))
        self.assertTrue(fitdash_server._service_auth_ok(_Headers(), "::1"))
        self.assertTrue(fitdash_server._service_auth_ok(_Headers(), "localhost"))

    def test_lan_denied_without_token(self) -> None:
        self._set_env(token="", loopback="1")
        self.assertFalse(
            fitdash_server._service_auth_ok(_Headers(), "192.168.100.5")
        )
        self.assertFalse(
            fitdash_server._service_auth_ok(_Headers(), "100.67.114.2")
        )

    def test_loopback_denied_when_disabled(self) -> None:
        self._set_env(token="", loopback="0")
        self.assertFalse(fitdash_server._service_auth_ok(_Headers(), "127.0.0.1"))

    def test_matching_token_allows_non_loopback(self) -> None:
        self._set_env(token="house-secret", loopback="0")
        bearer = _Headers({"Authorization": "Bearer house-secret"})
        custom = _Headers({"X-FitDash-Service-Token": "house-secret"})
        wrong = _Headers({"Authorization": "Bearer nope"})
        self.assertTrue(fitdash_server._service_auth_ok(bearer, "192.168.100.5"))
        self.assertTrue(fitdash_server._service_auth_ok(custom, "10.0.0.8"))
        self.assertFalse(fitdash_server._service_auth_ok(wrong, "192.168.100.5"))
        self.assertFalse(fitdash_server._service_auth_ok(_Headers(), "192.168.100.5"))


class DayConstraintsHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "FITDASH_REQUIRE_AUTH",
                "FITDASH_SERVICE_TOKEN",
                "FITDASH_SERVICE_LOOPBACK",
            )
        }
        os.environ["FITDASH_REQUIRE_AUTH"] = "1"
        os.environ.pop("FITDASH_SERVICE_TOKEN", None)
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "1"
        self._load_patch = mock.patch.object(
            fitdash_server,
            "load_dashboard_data",
            return_value={
                "day_constraints": FAKE_PACKET,
                "sleep_battery": FAKE_BATTERY,
            },
        )
        self._load_patch.start()
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), fitdash_server.DashboardHandler
        )
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._load_patch.stop()
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str, headers: Optional[Dict[str, str]] = None) -> tuple[int, dict]:
        req = Request(self._url(path), headers=headers or {})
        try:
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return int(resp.status), body
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw}
            return int(exc.code), body

    def test_loopback_day_constraints_ok_without_session(self) -> None:
        status, body = self._get("/api/day_constraints")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("day_constraints"), FAKE_PACKET)

    def test_loopback_sleep_battery_still_ok(self) -> None:
        status, body = self._get("/api/sleep_battery")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("pct_charged"), 41.0)

    def test_dashboard_stays_session_gated_on_loopback(self) -> None:
        status, body = self._get("/api/dashboard")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "auth_required")

    def test_loopback_disabled_requires_token(self) -> None:
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "0"
        os.environ["FITDASH_SERVICE_TOKEN"] = "house-secret"
        denied_status, denied = self._get("/api/day_constraints")
        self.assertEqual(denied_status, 401)
        self.assertEqual(denied.get("error"), "auth_required")
        ok_status, ok_body = self._get(
            "/api/day_constraints",
            headers={"X-FitDash-Service-Token": "house-secret"},
        )
        self.assertEqual(ok_status, 200)
        self.assertEqual(ok_body.get("day_constraints"), FAKE_PACKET)
        bearer_status, bearer_body = self._get(
            "/api/day_constraints",
            headers={"Authorization": "Bearer house-secret"},
        )
        self.assertEqual(bearer_status, 200)
        self.assertTrue(bearer_body.get("ok"))


class WarmHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "FITDASH_REQUIRE_AUTH",
                "FITDASH_SERVICE_TOKEN",
                "FITDASH_SERVICE_LOOPBACK",
            )
        }
        os.environ["FITDASH_REQUIRE_AUTH"] = "1"
        os.environ.pop("FITDASH_SERVICE_TOKEN", None)
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "1"
        self._warm_patch = mock.patch.object(
            fitdash_server,
            "schedule_incremental_warm",
            return_value=True,
        )
        self._resolve_patch = mock.patch.object(
            fitdash_server,
            "_resolve_warm_user_and_token",
            return_value=("google-sub-1", "1//rt"),
        )
        self._load_patch = mock.patch.object(
            fitdash_server,
            "load_dashboard_data",
            side_effect=AssertionError("warm must not build a full dashboard"),
        )
        self._warm_patch.start()
        self._resolve_patch.start()
        self._load_patch.start()
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), fitdash_server.DashboardHandler
        )
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._warm_patch.stop()
        self._resolve_patch.stop()
        self._load_patch.stop()
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str, headers: Optional[Dict[str, str]] = None) -> tuple[int, dict]:
        req = Request(self._url(path), headers=headers or {})
        try:
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return int(resp.status), body
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw}
            return int(exc.code), body

    def test_loopback_warm_ok_without_session(self) -> None:
        status, body = self._get("/api/warm")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("scheduled"))
        self.assertTrue(body.get("incremental"))
        self.assertFalse(body.get("force_refresh"))
        self.assertEqual(body.get("user_id"), "google-sub-1")
        fitdash_server.schedule_incremental_warm.assert_called_once()
        kwargs = fitdash_server.schedule_incremental_warm.call_args.kwargs
        self.assertTrue(kwargs.get("force_schedule"))
        self.assertEqual(kwargs.get("health_refresh_token"), "1//rt")

    def test_warm_stays_off_dashboard_full_pull(self) -> None:
        _, body = self._get("/api/warm")
        self.assertNotEqual(body.get("force_refresh"), True)
        fitdash_server.load_dashboard_data.assert_not_called()


class AgentTodayHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "FITDASH_REQUIRE_AUTH",
                "FITDASH_SERVICE_TOKEN",
                "FITDASH_SERVICE_LOOPBACK",
            )
        }
        os.environ["FITDASH_REQUIRE_AUTH"] = "1"
        os.environ.pop("FITDASH_SERVICE_TOKEN", None)
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "1"
        self._load_patch = mock.patch.object(
            fitdash_server,
            "load_dashboard_data",
            return_value={
                "day_constraints": FAKE_PACKET,
                "sleep_battery": FAKE_BATTERY,
                "coach": {"today": {"date": "2026-08-23"}},
                "meta": {"local_today": "2026-08-23"},
                "workout": {
                    "session_type": "pull",
                    "is_rest_day": False,
                    "exercises": [],
                },
                "sessions": [],
                "health": {
                    "active_zone_minutes": [
                        {
                            "date": "2026-08-16",
                            "fat_burn_minutes": 12,
                            "cardio_minutes": 8,
                            "peak_minutes": 2,
                            "total_minutes": 22,
                            "source": "google_health",
                        }
                    ]
                },
            },
        )
        self._load_patch.start()
        # Auth tests are not the auto-gen suite — keep SuperGrok off this path.
        self._fill_patch = mock.patch(
            "rt_dashboard.agent_plan.fill_stamped_workout",
            side_effect=lambda *a, **kw: kw.get("workout") or {},
        )
        self._fill_patch.start()
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), fitdash_server.DashboardHandler
        )
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._fill_patch.stop()
        self._load_patch.stop()
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str, headers: Optional[Dict[str, str]] = None) -> tuple[int, dict]:
        req = Request(self._url(path), headers=headers or {})
        try:
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return int(resp.status), body
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw}
            return int(exc.code), body

    def test_loopback_agent_today_ok_without_session(self) -> None:
        status, body = self._get("/api/agent/today")
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body["today"]["date"], "2026-08-23")
        self.assertEqual(body["today"]["workout"]["session_type"], "pull")
        self.assertFalse(body["today"]["workout"]["is_rest_day"])
        self.assertIn("active_zone_minutes", body["today"])
        self.assertEqual(len(body["today"]["active_zone_minutes"]), 1)
        self.assertEqual(body["today"]["active_zone_minutes"][0]["date"], "2026-08-16")
        self.assertEqual(body["today"]["active_zone_minutes"][0]["total_minutes"], 22)
        self.assertIn("nutrition", body["today"])
        self.assertIsNone(body["today"]["nutrition"]["calories"])
        self.assertEqual(body["today"]["nutrition"]["meals"], [])
        self.assertEqual(body["week"]["start"], "2026-08-17")
        self.assertEqual(body["week"]["logged_sessions"], [])
        self.assertEqual(body["week"]["sleep"], [])
        self.assertIsNone(body["week"]["nutrition"]["calories"])
        self.assertNotIn("house-secret", json.dumps(body))

    def test_dashboard_stays_session_gated_on_loopback(self) -> None:
        status, body = self._get("/api/dashboard")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "auth_required")

    def test_loopback_disabled_requires_token(self) -> None:
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "0"
        os.environ["FITDASH_SERVICE_TOKEN"] = "house-secret"
        denied_status, denied = self._get("/api/agent/today")
        self.assertEqual(denied_status, 401)
        self.assertEqual(denied.get("error"), "auth_required")
        self.assertNotIn("today", denied)
        self.assertNotIn("week", denied)
        self.assertNotIn("active_zone_minutes", denied)
        self.assertNotIn("house-secret", json.dumps(denied))
        ok_status, ok_body = self._get(
            "/api/agent/today",
            headers={"X-FitDash-Service-Token": "house-secret"},
        )
        self.assertEqual(ok_status, 200)
        self.assertTrue(ok_body.get("ok"))
        self.assertEqual(ok_body["today"]["workout"]["session_type"], "pull")
        self.assertEqual(ok_body["today"]["active_zone_minutes"][0]["total_minutes"], 22)
        self.assertNotIn("house-secret", json.dumps(ok_body))
        bearer_status, bearer_body = self._get(
            "/api/agent/today",
            headers={"Authorization": "Bearer house-secret"},
        )
        self.assertEqual(bearer_status, 200)
        self.assertTrue(bearer_body.get("ok"))
        self.assertEqual(len(bearer_body["today"]["active_zone_minutes"]), 1)
        self.assertNotIn("house-secret", json.dumps(bearer_body))


if __name__ == "__main__":
    unittest.main()
