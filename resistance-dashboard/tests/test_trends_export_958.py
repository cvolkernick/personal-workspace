"""#958: agent read of the Trends calories-vs-weight window.

The browser session on the box expires. This route uses the existing
house service token and returns daily rows from the same series the
Trends chart plots. A bad token is a named 401, not an empty body.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as fitdash_server  # noqa: E402
from api.healthz import healthz_body  # noqa: E402
from api.workout._util import dispatch_client_route  # noqa: E402
from rt_dashboard.trends_export import (  # noqa: E402
    export_trends_window,
    kcal_by_date,
)

END = "2026-10-01"
TOKEN = "house-secret"
HEALTH = {
    "nutrition": [
        {"date": "2026-10-01", "calories": 2100},
        {"date": "2026-09-15", "calories": 0},
        {"date": "2026-08-01", "calories": "1800"},
    ],
    "calories_burned": [
        {"date": "2026-10-01", "calories": 2500},
        {"date": "2026-09-15", "calories": 0},
        {"date": "2026-07-20", "calories": 2300},
        {"date": "2026-06-01", "calories": 9999},
    ],
    "weight": [
        {"date": "2026-10-01", "weight_lbs": 180.5},
        {"date": "2026-09-15", "weight_lbs": 181},
        {"date": "2026-09-15", "weight_lbs": 181.4},
    ],
    "food_logs": [
        {"date": "2026-08-02", "name": "eggs", "calories": 200},
    ],
    "error": None,
}


def _by_date(body):
    return {row["date"]: row for row in body["rows"]}


class TrendsWindow(unittest.TestCase):
    def _export(self, health=None, days=90):
        return export_trends_window(
            HEALTH if health is None else health,
            days=days,
            end=END,
            tz_name="America/New_York",
        )

    def test_ninety_rows_and_burned_matches_chart_series(self):
        body = self._export()
        self.assertTrue(body["ok"])
        self.assertEqual(body["days"], 90)
        self.assertEqual(len(body["rows"]), 90)
        self.assertEqual(body["start"], "2026-07-04")
        self.assertEqual(body["end"], END)
        self.assertEqual(body["burned_source"], "health.calories_burned.calories")
        chart = kcal_by_date(HEALTH["calories_burned"])
        rows = _by_date(body)
        # Three spot-checks against the chart's daily burned map.
        self.assertEqual(rows["2026-10-01"]["burned_kcal"], chart["2026-10-01"])
        self.assertEqual(rows["2026-09-15"]["burned_kcal"], chart["2026-09-15"])
        self.assertEqual(rows["2026-07-20"]["burned_kcal"], chart["2026-07-20"])
        self.assertEqual(rows["2026-10-01"]["burned_kcal"], 2500)
        self.assertEqual(rows["2026-09-15"]["burned_kcal"], 0)
        self.assertEqual(rows["2026-07-20"]["burned_kcal"], 2300)
        self.assertNotIn("2026-06-01", rows)

    def test_unlogged_day_is_null_not_zero(self):
        rows = _by_date(self._export())
        quiet = rows["2026-07-04"]
        self.assertIsNone(quiet["intake_kcal"])
        self.assertIsNone(quiet["burned_kcal"])
        self.assertIsNone(quiet["weight_lb"])
        self.assertIs(quiet["logged"], False)
        self.assertIsNone(quiet["intake_kcal"])
        dumped = json.dumps(quiet)
        self.assertIn('"logged": false', dumped)
        self.assertNotIn('"logged": 0', dumped)
        self.assertNotIn('"intake_kcal": 0', dumped)

    def test_logged_zero_stays_zero_and_weight_last_wins(self):
        rows = _by_date(self._export())
        zero = rows["2026-09-15"]
        self.assertEqual(zero["intake_kcal"], 0)
        self.assertEqual(zero["burned_kcal"], 0)
        self.assertIs(zero["logged"], True)
        self.assertEqual(zero["weight_lb"], 181.4)
        today = rows["2026-10-01"]
        self.assertEqual(today["intake_kcal"], 2100)
        self.assertEqual(today["weight_lb"], 180.5)
        self.assertIs(today["logged"], True)
        food_only = rows["2026-08-02"]
        self.assertIs(food_only["logged"], True)
        self.assertIsNone(food_only["intake_kcal"])
        self.assertEqual(rows["2026-08-01"]["intake_kcal"], 1800.0)

    def test_health_failure_with_no_series_is_not_ninety_zeros(self):
        body = self._export({"error": "invalid_grant"})
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "health_unavailable")
        self.assertIn("invalid_grant", body["message"])
        self.assertEqual(body["alert"], "fitdash_trends_health")
        self.assertNotIn("rows", body)
        self.assertNotIn(TOKEN, json.dumps(body))

    def test_cached_series_keeps_rows_and_names_the_error(self):
        health = dict(HEALTH)
        health["error"] = "cached after 401"
        body = self._export(health)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["rows"]), 90)
        self.assertEqual(body["health_error"], "cached after 401")


class TrendsHttp(unittest.TestCase):
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
        self._load = mock.patch.object(
            fitdash_server,
            "load_dashboard_data",
            return_value={"health": HEALTH},
        )
        self._today = mock.patch(
            "rt_dashboard.trends_export.local_today_iso",
            return_value=END,
        )
        self._load.start()
        self._today.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._today.stop()
        self._load.stop()
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _get(self, path: str, headers=None):
        req = Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return int(resp.status), json.loads(raw), raw
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            return int(exc.code), body, raw

    def test_loopback_returns_ninety_rows_without_a_session(self):
        status, body, raw = self._get("/api/trends/export")
        self.assertEqual(status, 200, raw)
        self.assertEqual(len(body["rows"]), 90)
        self.assertEqual(_by_date(body)["2026-07-20"]["burned_kcal"], 2300)
        self.assertNotIn(TOKEN, raw)
        dash_status, dash, _raw = self._get("/api/dashboard")
        self.assertEqual(dash_status, 401)
        self.assertEqual(dash.get("error"), "auth_required")

    def test_invalid_token_is_explicit_even_on_loopback(self):
        os.environ["FITDASH_SERVICE_TOKEN"] = TOKEN
        status, body, raw = self._get(
            "/api/trends/export",
            headers={"Authorization": "Bearer not-the-token"},
        )
        self.assertEqual(status, 401)
        self.assertGreaterEqual(len(raw.strip()), 20)
        self.assertEqual(body["error"], "auth_required")
        self.assertIn("rejected", body["message"])
        self.assertEqual(body["alert"], "fitdash_agent_read_auth")
        self.assertEqual(body["agent_read"], "ready")
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn("not-the-token", raw)
        self.assertNotIn("rows", body)

    def test_bearer_works_when_loopback_is_off(self):
        os.environ["FITDASH_SERVICE_LOOPBACK"] = "0"
        os.environ["FITDASH_SERVICE_TOKEN"] = TOKEN
        denied, denied_body, _raw = self._get("/api/trends/export")
        self.assertEqual(denied, 401)
        self.assertTrue(denied_body["message"])
        self.assertEqual(denied_body["alert"], "fitdash_agent_read_auth")
        status, body, raw = self._get(
            "/api/trends/export?days=90",
            headers={"X-FitDash-Service-Token": TOKEN},
        )
        self.assertEqual(status, 200, raw)
        self.assertEqual(len(body["rows"]), 90)
        self.assertEqual(_by_date(body)["2026-10-01"]["burned_kcal"], 2500)
        self.assertEqual(_by_date(body)["2026-09-15"]["burned_kcal"], 0)
        self.assertEqual(_by_date(body)["2026-07-20"]["burned_kcal"], 2300)
        self.assertNotIn(TOKEN, raw)

    def test_bad_days_is_400(self):
        status, body, _raw = self._get("/api/trends/export?days=0")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_days")
        self.assertIn("1 to 366", body["message"])

    def test_healthz_names_agent_read_without_the_token(self):
        status, body, raw = self._get("/api/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["agent_read"], "unconfigured")
        self.assertEqual(body["alert"], "fitdash_agent_read_unconfigured")
        os.environ["FITDASH_SERVICE_TOKEN"] = TOKEN
        status, body, raw = self._get("/api/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["agent_read"], "ready")
        self.assertNotIn("alert", body)
        self.assertNotIn(TOKEN, raw)


class TrendsVercelRoute(unittest.TestCase):
    def test_rewrite_and_no_extra_function(self):
        raw = (ROOT / "vercel.json").read_text(encoding="utf-8")
        self.assertIn("/api/trends/export", raw)
        self.assertIn("/api/dashboard?_r=trends_export", raw)
        self.assertNotIn("api/trends/export.py", raw)
        self.assertFalse((ROOT / "api" / "trends_export.py").exists())
        self.assertFalse((ROOT / "api" / "trends").exists())
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("/api/trends/export", readme)
        self.assertIn("FITDASH_SERVICE_TOKEN", readme)
        self.assertIn("Do not put it in the repo", readme)

    def test_service_token_dispatch_matches_chart(self):
        env = {
            "FITDASH_SERVICE_TOKEN": TOKEN,
            "FITDASH_SERVICE_LOOPBACK": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch(
                "api.dashboard._load_health",
                return_value=(HEALTH, []),
            ), mock.patch(
                "rt_dashboard.trends_export.local_today_iso",
                return_value=END,
            ):
                status, body = dispatch_client_route(
                    {"Authorization": "Bearer " + TOKEN},
                    "days=90",
                    "GET",
                    path="/api/trends/export",
                    client_host="10.1.1.1",
                )
        self.assertEqual(status, 200)
        rows = _by_date(body)
        chart = kcal_by_date(HEALTH["calories_burned"])
        for day in ("2026-10-01", "2026-09-15", "2026-07-20"):
            self.assertEqual(rows[day]["burned_kcal"], chart[day])
        self.assertIsNone(rows["2026-07-04"]["intake_kcal"])
        self.assertIs(rows["2026-07-04"]["logged"], False)
        self.assertNotIn(TOKEN, json.dumps(body))

    def test_query_token_does_not_authorize_or_echo(self):
        env = {
            "FITDASH_SERVICE_TOKEN": TOKEN,
            "FITDASH_SERVICE_LOOPBACK": "0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            status, body = dispatch_client_route(
                {},
                "days=90&token=" + TOKEN,
                "GET",
                path="/api/trends/export",
                client_host="10.1.1.1",
            )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertTrue(body["message"])
        self.assertEqual(body["alert"], "fitdash_agent_read_auth")
        self.assertNotIn(TOKEN, json.dumps(body))

    def test_vercel_healthz_ready_omits_the_secret(self):
        env = {
            "VERCEL_ENV": "production",
            "VERCEL_GIT_COMMIT_SHA": "abc",
            "FITDASH_SERVICE_TOKEN": TOKEN,
        }
        with mock.patch.dict(os.environ, env, clear=True):
            body = healthz_body()
        self.assertEqual(body["agent_read"], "ready")
        self.assertNotIn("alert", body)
        self.assertNotIn(TOKEN, json.dumps(body))
        self.assertEqual(body["gitSha"], "abc")


if __name__ == "__main__":
    unittest.main()
