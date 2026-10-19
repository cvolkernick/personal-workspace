"""Contract tests: token-locked read-only agent API (GET /api/agent/<section>).

Each section: 200 with the agent token, 401 without / wrong / unset. Payload
envelope has generated_at + per-section as_of. Timestamps come straight from
the store. A pantry edit shows on the next read. Nothing is written.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as fitdash_server  # noqa: E402
from rt_dashboard import agent_read  # noqa: E402
from rt_dashboard.agent_read import SECTIONS, Loaders, respond_agent_read  # noqa: E402

TOKEN = "dummy-test-token-not-real"
NOW = datetime(2026, 10, 19, 21, 0, 0, tzinfo=timezone.utc)
HEALTH_TS = 1792443600.0  # 2026-10-19T21:00:00Z

DASH = {
    "meta": {"local_today": "2026-10-19", "generated_at": "2026-10-19T20:59:00Z"},
    "sessions": [
        {"date": "2026-10-18", "session_type": "push", "closed_at": "2026-10-18T23:00:00Z",
         "exercises": [{"name": "Bench", "sets": [{"weight_lbs": 100, "reps": 8}], "volume": 800}]},
        {"date": "2026-09-01", "session_type": "pull", "exercises": []},
    ],
    "health": {
        "food_logs": [
            {"date": "2026-10-19", "time": "08:15", "name": "Oats with banana", "calories": 300},
            {"date": "2026-10-10", "time": "12:00", "name": "Tilapia bowl", "calories": 400},
            {"date": "2026-08-01", "name": "old", "calories": 1},
        ],
        "sleep": [{"date": "2026-10-19", "sleep_hours": 7.5, "efficiency_pct": 90, "source": "google_health"}],
        "hydration": [{"date": "2026-10-19", "water_ml": 500}],
    },
    "recovery": {"label": "Ready", "score": 80, "reasons": []},
    "hydration_bars": {"pacing": {"consumed": 500, "target": 3000, "status": "behind", "intake_source": "hidrate"}},
    "nutrition_store": {"targets": {"calories": 2100, "protein_g": 210}, "meal_plan": {"meals": []}},
    "workout_store": {"plan": {"session_type": "pull", "exercises": []}},
}

TARGETS = {
    "payload": {"calories": 2100, "protein_g": 210, "carbs_g": 200, "fat_g": 60, "phase": "cut"},
    "updated_at": "2026-10-01T12:00:00Z",
    "updated_by": "nutrition: update daily macro targets",
    "updated_by_tracked": True,
    "source": "turso",
}


def _inv(in_stock=True, updated_at="2026-10-15T10:00:00Z"):
    return {
        "payload": {"ingredients": [
            {"id": "oats", "name": "Oats", "in_stock": in_stock, "qty": 2},
            {"id": "tilapia", "name": "Tilapia", "in_stock": False},
        ]},
        "updated_at": updated_at,
        "source": "turso",
    }


class _Loaders(Loaders):
    def __init__(self, inv=None, targets=None):
        self.writes = 0
        super().__init__(
            dashboard=lambda: json.loads(json.dumps(DASH)),
            targets=lambda: targets or TARGETS,
            inventory=lambda: inv() if callable(inv) else (inv or _inv()),
            health_fetched_at=lambda: HEALTH_TS,
        )


def _call(section, headers=None, query="", loaders=None):
    return respond_agent_read(section, headers or {}, query, loaders or _Loaders(), now=NOW)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


class AuthContract(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FITDASH_AGENT_TOKEN": TOKEN}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_every_section_200_with_token(self):
        for s in SECTIONS:
            with self.subTest(section=s):
                status, body = _call(s, AUTH)
                self.assertEqual(status, 200, body)
                self.assertTrue(body["ok"])
                self.assertEqual(body["section"], s)
                self.assertEqual(body["generated_at"], "2026-10-19T21:00:00Z")
                self.assertIn("as_of", body)
                self.assertTrue(body["read_only"])

    def test_every_section_401_without_token(self):
        for s in SECTIONS:
            with self.subTest(section=s):
                status, body = _call(s, {})
                self.assertEqual(status, 401)
                self.assertEqual(body["reason"], "missing")
                self.assertNotIn("data", body)

    def test_wrong_token_401(self):
        for s in SECTIONS:
            status, body = _call(s, {"Authorization": "Bearer nope"})
            self.assertEqual(status, 401)
            self.assertEqual(body["reason"], "invalid")
            self.assertNotIn(TOKEN, json.dumps(body))

    def test_house_service_token_is_not_the_agent_token(self):
        with mock.patch.dict(os.environ, {"FITDASH_SERVICE_TOKEN": "house"}):
            status, _ = _call("targets", {"Authorization": "Bearer house"})
        self.assertEqual(status, 401)

    def test_unset_env_fails_closed(self):
        with mock.patch.dict(os.environ, {"FITDASH_AGENT_TOKEN": ""}):
            status, body = _call("targets", AUTH)
        self.assertEqual(status, 401)
        self.assertEqual(body["reason"], "unconfigured")

    def test_x_header_also_accepted(self):
        status, _ = _call("targets", {"X-FitDash-Service-Token": TOKEN})
        self.assertEqual(status, 200)

    def test_unknown_section_404_only_after_auth(self):
        self.assertEqual(_call("nope", {})[0], 401)
        self.assertEqual(_call("nope", AUTH)[0], 404)


class SectionContract(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FITDASH_AGENT_TOKEN": TOKEN}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_targets_timestamps_match_store(self):
        _, body = _call("targets", AUTH)
        d = body["data"]
        self.assertEqual(d["updated_at"], TARGETS["updated_at"])
        self.assertEqual(body["as_of"], TARGETS["updated_at"])
        self.assertEqual(d["updated_by"], TARGETS["updated_by"])
        self.assertEqual(d["kcal"], 2100.0)
        self.assertEqual(d["phase"], "cut")
        self.assertEqual(set(d["macros"]), set(agent_read.MACRO_KEYS))

    def test_targets_missing_history_is_null_with_note(self):
        rec = dict(TARGETS, updated_by=None, updated_by_tracked=False)
        _, body = _call("targets", AUTH, loaders=_Loaders(targets=rec))
        self.assertIsNone(body["data"]["updated_by"])
        self.assertIn("not recoverable", body["data"]["note"])

    def test_inventory_shape_and_last_used(self):
        _, body = _call("inventory", AUTH)
        d = body["data"]
        self.assertEqual(body["as_of"], "2026-10-15T10:00:00Z")
        oats = d["items"][0]
        self.assertEqual(set(oats), {"id", "name", "category", "qty", "in_stock", "updated_at", "last_used_at"})
        self.assertEqual(oats["qty"], 2)
        self.assertEqual(oats["last_used_at"], "2026-10-19T08:15")
        self.assertEqual(d["items"][1]["last_used_at"], "2026-10-10T12:00")

    def test_inventory_edit_shows_on_next_read(self):
        state = {"rec": _inv(True)}
        loaders = _Loaders(inv=lambda: state["rec"])
        _, first = _call("inventory", AUTH, loaders=loaders)
        self.assertTrue(first["data"]["items"][0]["in_stock"])
        state["rec"] = _inv(False, "2026-10-19T20:00:00Z")  # simulated pantry write
        _, second = _call("inventory", AUTH, loaders=loaders)
        self.assertFalse(second["data"]["items"][0]["in_stock"])
        self.assertEqual(second["as_of"], "2026-10-19T20:00:00Z")

    def test_logs_range_filters(self):
        _, body = _call("logs", AUTH, query="from=2026-10-10&to=2026-10-19")
        d = body["data"]
        self.assertEqual([m["date"] for m in d["meals"]], ["2026-10-10", "2026-10-19"])
        self.assertEqual([s["date"] for s in d["sessions"]], ["2026-10-18"])
        self.assertEqual(d["sessions_as_of"], "2026-10-18T23:00:00Z")
        self.assertEqual(d["meals_as_of"], "2026-10-19T21:00:00Z")

    def test_logs_default_last_7_days(self):
        _, body = _call("logs", AUTH)
        self.assertEqual((body["data"]["from"], body["data"]["to"]), ("2026-10-13", "2026-10-19"))

    def test_logs_bad_range_400(self):
        for q in ("from=bad", "from=2026-10-19&to=2026-10-01", "from=2026-01-01&to=2026-10-19"):
            self.assertEqual(_call("logs", AUTH, query=q)[0], 400, q)

    def test_training_has_today_muscle_sets_guardrails(self):
        _, body = _call("training", AUTH)
        d = body["data"]
        for k in ("today", "week", "muscle_sets", "guardrails", "phase_barometer"):
            self.assertIn(k, d)

    def test_plan_today_and_week(self):
        _, body = _call("plan", AUTH)
        self.assertIn("today", body["data"])
        self.assertIn("week", body["data"])
        self.assertEqual(body["data"]["week"]["workout_plan"]["session_type"], "pull")

    def test_recovery_source_and_as_of_each(self):
        _, body = _call("recovery", AUTH)
        for k in ("sleep", "readiness", "hydration"):
            self.assertIn("source", body["data"][k])
            self.assertIn("as_of", body["data"][k])
        self.assertEqual(body["data"]["sleep"]["source"], "google_health")
        self.assertEqual(body["data"]["hydration"]["source"], "hidrate")
        self.assertEqual(body["data"]["readiness"]["as_of"], "2026-10-19T21:00:00Z")

    def test_recovery_empty_is_null_not_invented(self):
        loaders = _Loaders()
        loaders.dashboard = lambda: {"meta": {}}
        _, body = _call("recovery", AUTH, loaders=loaders)
        self.assertIsNone(body["data"]["sleep"]["as_of"])
        self.assertIsNone(body["data"]["readiness"]["source"])


class ReadOnlyDbReads(unittest.TestCase):
    def test_targets_reader_only_selects(self):
        sqls = []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=()):
                sqls.append(sql)

                class R:
                    def fetchone(self_inner):
                        return {"payload": json.dumps({"calories": 1}), "updated_at": "t", "updated_by": "u"}
                return R()

        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=True), \
             mock.patch("rt_dashboard.turso_http.connect", return_value=Conn()):
            rec = agent_read.read_targets_record("")
            agent_read.read_inventory_record("")
        self.assertEqual(rec["updated_at"], "t")
        self.assertEqual(rec["updated_by"], "u")
        self.assertTrue(all(s.strip().upper().startswith("SELECT") for s in sqls), sqls)


class HttpRoute(unittest.TestCase):
    """End-to-end through DashboardHandler; loopback must NOT bypass the token."""

    def setUp(self):
        self._patches = [
            mock.patch.dict(os.environ, {"FITDASH_AGENT_TOKEN": TOKEN, "FITDASH_SERVICE_LOOPBACK": "1"}),
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value=json.loads(json.dumps(DASH))),
            mock.patch.object(fitdash_server, "_auth_required", return_value=False),
            mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False),
            mock.patch("rt_dashboard.dashboard_cache.load_health_cache", return_value=(None, HEALTH_TS, {})),
        ]
        for p in self._patches:
            p.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for p in reversed(self._patches):
            p.stop()

    def _get(self, path, token=None):
        req = urllib.request.Request(self.base + path)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_sections_over_http(self):
        for s in SECTIONS:
            with self.subTest(section=s):
                self.assertEqual(self._get(f"/api/agent/{s}")[0], 401)  # loopback, no token
                status, body = self._get(f"/api/agent/{s}", TOKEN)
                self.assertEqual(status, 200, body)
                self.assertIn("generated_at", body)
                self.assertIn("as_of", body)


if __name__ == "__main__":
    unittest.main()
