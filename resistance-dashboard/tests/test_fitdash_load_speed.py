"""Load-path speed: stale payload, scoped quests, gzip, today cache, Turso reuse.

Output of the cold build stays the same when independent sections run together.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from rt_dashboard.coaching_targets import (  # noqa: E402
    coaching_settings_scope,
    load_overrides,
)
from rt_dashboard.models import (  # noqa: E402
    CaloriesBurnedDay,
    FoodLogEntry,
    HydrationDay,
    SetEntry,
    SleepSample,
    WeightSample,
)


def _jsonable(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True))


class PlainDictMatchesAsdict(unittest.TestCase):
    def test_flat_samples_match_asdict(self):
        samples = [
            WeightSample(date="2026-08-01", weight_lbs=180.2, source="google_health"),
            SleepSample(date="2026-08-01", sleep_hours=7.5, efficiency_pct=None),
            HydrationDay(date="2026-08-01", water_ml=400.0, source="hidrate"),
            CaloriesBurnedDay(date="2026-08-01", calories=2400.0),
            FoodLogEntry(
                date="2026-08-01",
                name="oats",
                calories=300.0,
                protein_g=12.0,
                nutrients={"fiber_g": 4.0},
            ),
            SetEntry(weight_lbs=135.0, sets=3, reps=8, warmup=False, rir=None),
            SetEntry(weight_lbs=45.0, sets=1, reps=10, warmup=True, rir=3),
        ]
        for sample in samples:
            self.assertEqual(sample.to_dict(), asdict(sample) if not isinstance(sample, SetEntry) else sample.to_dict())
            if not isinstance(sample, SetEntry):
                self.assertEqual(_jsonable(sample.to_dict()), _jsonable(asdict(sample)))

    def test_set_entry_omits_default_flags(self):
        bare = SetEntry(weight_lbs=100.0, sets=1, reps=5).to_dict()
        self.assertNotIn("warmup", bare)
        self.assertNotIn("rir", bare)
        flagged = SetEntry(weight_lbs=100.0, sets=1, reps=5, warmup=True, rir=2).to_dict()
        self.assertTrue(flagged["warmup"])
        self.assertEqual(flagged["rir"], 2)


class StalePayload(unittest.TestCase):
    def setUp(self):
        server.invalidate_dashboard_memo()

    def tearDown(self):
        server.drain_payload_rebuilds()
        server.invalidate_dashboard_memo()
        server._PAYLOAD_MEMO_ENABLED = False

    def test_stale_hit_returns_immediately_and_single_flights(self):
        calls = []
        release = threading.Event()

        def build(**_k):
            calls.append(1)
            if len(calls) > 1:
                release.wait(2)
            return {"n": len(calls), "nested": {"x": 1}}

        with mock.patch.object(server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(server, "build_dashboard_data", build), \
             mock.patch.object(server, "_health_cache_mtime", lambda: 1.0):
            first = server.load_dashboard_data(user_id="u")
            self.assertEqual(first["n"], 1)
            key = server._payload_key("u")
            with server._PAYLOAD_MEMO_LOCK:
                built_at, mtime, gen, data = server._PAYLOAD_MEMO[key]
                server._PAYLOAD_MEMO[key] = (built_at - 10_000, mtime, gen, data)
            out = []
            threads = [
                threading.Thread(target=lambda: out.append(server.load_dashboard_data(user_id="u")))
                for _ in range(2)
            ]
            t0 = time.perf_counter()
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 0.5)
            self.assertTrue(all(item["n"] == 1 for item in out))
            deadline = time.time() + 2
            while len(calls) < 2 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(calls), 2)
            release.set()
            server.drain_payload_rebuilds()
            again = server.load_dashboard_data(user_id="u")
            self.assertEqual(again["n"], 2)
            self.assertEqual(len(calls), 2)

    def test_post_then_get_sees_the_write(self):
        db = {"total": 100}
        calls = []

        def build(**_k):
            calls.append(db["total"])
            return {"total": db["total"]}

        class Handler:
            def _handle_post(self):
                db["total"] = 250

        with mock.patch.object(server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(server, "build_dashboard_data", build), \
             mock.patch.object(server, "_health_cache_mtime", lambda: 1.0):
            self.assertEqual(server.load_dashboard_data(user_id="u")["total"], 100)
            server.DashboardHandler.do_POST(Handler())
            seen = server.load_dashboard_data(user_id="u")
            self.assertEqual(seen["total"], 250)
            self.assertEqual(server.load_dashboard_data(user_id="u")["total"], 250)
            self.assertEqual(calls, [100, 250])

    def test_no_payload_still_single_flights(self):
        calls = []

        def build(**_k):
            calls.append(1)
            time.sleep(0.05)
            return {"n": len(calls)}

        with mock.patch.object(server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(server, "build_dashboard_data", build), \
             mock.patch.object(server, "_health_cache_mtime", lambda: 1.0):
            out = []
            threads = [
                threading.Thread(target=lambda: out.append(server.load_dashboard_data(user_id="u")))
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(item["n"] == 1 for item in out))


class DailyTasksPatch(unittest.TestCase):
    def setUp(self):
        server.invalidate_dashboard_memo()

    def tearDown(self):
        server.drain_payload_rebuilds()
        server.invalidate_dashboard_memo()
        server._PAYLOAD_MEMO_ENABLED = False

    def test_patch_keeps_the_rest_of_the_payload(self):
        calls = []

        def build(**_k):
            calls.append(1)
            return {
                "marker": "keep",
                "daily_tasks": {"summary": {"done": 0, "total": 4}},
                "coach": {"today": {"date": "2026-08-01", "daily_tasks": {"summary": {"done": 0}}}},
                "nutrition_store": {"inventory": {"ingredients": [{"id": "oats"}]}},
            }

        with mock.patch.object(server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(server, "build_dashboard_data", build), \
             mock.patch.object(server, "_health_cache_mtime", lambda: 1.0):
            server.load_dashboard_data(user_id="u")
            quests = {"summary": {"done": 3, "total": 4}, "groups": [{"group": "training"}]}
            server.patch_daily_tasks_memo("u", quests)
            body = server.load_dashboard_data(user_id="u")
            self.assertEqual(len(calls), 1)
            self.assertEqual(body["marker"], "keep")
            self.assertEqual(body["nutrition_store"]["inventory"]["ingredients"][0]["id"], "oats")
            self.assertEqual(body["daily_tasks"]["summary"]["done"], 3)
            self.assertEqual(body["coach"]["today"]["daily_tasks"]["summary"]["done"], 3)
            body["daily_tasks"]["summary"]["done"] = 0
            cached = server.load_dashboard_data(user_id="u")
            self.assertEqual(cached["daily_tasks"]["summary"]["done"], 3)
            self.assertEqual(len(calls), 1)


class JsonGzip(unittest.TestCase):
    def _handler(self, headers):
        handler = server.DashboardHandler.__new__(server.DashboardHandler)
        handler.request_version = "HTTP/1.1"
        handler.command = "GET"

        class _H(dict):
            def get(self, key, default=None):
                for name, value in self.items():
                    if str(name).lower() == str(key).lower():
                        return value
                return default

        handler.headers = _H(headers)
        handler.sent = []
        handler.wfile = io.BytesIO()
        handler.send_response = lambda code, msg=None: handler.sent.append(("status", code))
        handler.send_header = lambda key, value: handler.sent.append((key, value))
        handler.end_headers = lambda: None
        handler.log_message = lambda *args, **kwargs: None
        return handler

    def _headers(self, handler):
        return {key: value for key, value in handler.sent if key != "status"}

    def test_gzip_when_accepted_and_compact_otherwise(self):
        payload = {"ok": True, "items": ["a", "b"], "n": 2}
        with mock.patch("http.server.BaseHTTPRequestHandler.end_headers", lambda self: None):
            gzipped = self._handler({"Accept-Encoding": "gzip, deflate"})
            gzipped._send_json(payload)
            plain = self._handler({})
            plain._send_json(payload)
        gz_headers = self._headers(gzipped)
        plain_headers = self._headers(plain)
        self.assertEqual(gz_headers["Content-Encoding"], "gzip")
        self.assertEqual(gz_headers["Vary"], "Accept-Encoding")
        self.assertEqual(gz_headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(gzip.decompress(gzipped.wfile.getvalue())), payload)
        self.assertNotIn("Content-Encoding", plain_headers)
        self.assertEqual(plain_headers["Vary"], "Accept-Encoding")
        self.assertEqual(plain_headers["Cache-Control"], "no-store")
        raw = plain.wfile.getvalue()
        self.assertNotIn(b"\n", raw)
        self.assertNotIn(b": ", raw)
        self.assertEqual(json.loads(raw), payload)

    def test_gzip_q0_is_not_gzip(self):
        payload = {"ok": True, "n": 1}
        with mock.patch("http.server.BaseHTTPRequestHandler.end_headers", lambda self: None):
            refused = self._handler({"Accept-Encoding": "gzip;q=0"})
            refused._send_json(payload)
            star = self._handler({"Accept-Encoding": "gzip;q=0, *;q=1"})
            star._send_json(payload)
            partial = self._handler({"Accept-Encoding": "gzip;q=0.5"})
            partial._send_json(payload)
        self.assertNotIn("Content-Encoding", self._headers(refused))
        self.assertEqual(json.loads(refused.wfile.getvalue()), payload)
        self.assertNotIn("Content-Encoding", self._headers(star))
        self.assertEqual(self._headers(partial)["Content-Encoding"], "gzip")
        self.assertEqual(json.loads(gzip.decompress(partial.wfile.getvalue())), payload)


class AgentTodayCache(unittest.TestCase):
    def setUp(self):
        server.invalidate_dashboard_memo()
        self._was = server._PAYLOAD_MEMO_ENABLED

    def tearDown(self):
        server.invalidate_agent_today_cache()
        server._PAYLOAD_MEMO_ENABLED = self._was

    def test_second_call_hits_and_invalidate_misses(self):
        calls = []

        def export(data, error=None):
            calls.append(1)
            return {"ok": True, "n": len(calls), "date": "2026-08-01"}

        with mock.patch.object(server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(server, "load_dashboard_data", return_value={"meta": {}}), \
             mock.patch.object(server, "_overlay_agent_workout", side_effect=lambda data, *a, **k: data), \
             mock.patch.object(server, "export_agent_today", export):
            first = server.load_agent_today_response("u", query="", read_only=True)
            second = server.load_agent_today_response("u", query="", read_only=True)
            self.assertEqual(first["n"], 1)
            self.assertEqual(second["n"], 1)
            self.assertEqual(len(calls), 1)
            second["n"] = 99
            self.assertEqual(server.load_agent_today_response("u", query="", read_only=True)["n"], 1)
            server.invalidate_agent_today_cache()
            third = server.load_agent_today_response("u", query="", read_only=True)
            self.assertEqual(third["n"], 2)
            self.assertEqual(len(calls), 2)
            other = server.load_agent_today_response("u", query="x=1", read_only=True)
            self.assertEqual(other["n"], 3)

    def test_ttl_env_defaults_to_300(self):
        saved = os.environ.pop("FITDASH_TODAY_TTL_SEC", None)
        old = os.environ.pop("FITDASH_TODAY_CACHE_SEC", None)
        try:
            self.assertEqual(server.agent_today_cache_ttl_sec(), 300.0)
            os.environ["FITDASH_TODAY_TTL_SEC"] = "120"
            self.assertEqual(server.agent_today_cache_ttl_sec(), 120.0)
            self.assertEqual(server.today_refresh_wait_sec(), 90.0)
            os.environ["FITDASH_TODAY_TTL_SEC"] = "0"
            self.assertEqual(server.agent_today_cache_ttl_sec(), 0.0)
        finally:
            if saved is None:
                os.environ.pop("FITDASH_TODAY_TTL_SEC", None)
            else:
                os.environ["FITDASH_TODAY_TTL_SEC"] = saved
            if old is not None:
                os.environ["FITDASH_TODAY_CACHE_SEC"] = old

    def test_warm_fills_cache_and_write_rewarms(self):
        import time

        calls = []

        def export(data, error=None):
            time.sleep(0.05)
            calls.append(time.perf_counter())
            return {"ok": True, "n": len(calls)}

        server._TODAY_WARM_ARMED = True
        server._PAYLOAD_MEMO_ENABLED = True
        try:
            with mock.patch.object(server, "load_dashboard_data", return_value={"meta": {}}), \
                 mock.patch.object(server, "_overlay_agent_workout", side_effect=lambda data, *a, **k: data), \
                 mock.patch.object(server, "export_agent_today", export), \
                 mock.patch("rt_dashboard.agent_read.agent_targets_uid", return_value="u"):
                t0 = time.perf_counter()
                server.warm_agent_today()
                warm_s = time.perf_counter() - t0
                before = len(calls)
                t1 = time.perf_counter()
                hit = server.load_agent_today_response("u", query="", read_only=True)
                hit_s = time.perf_counter() - t1
                self.assertEqual(hit["n"], 1)
                self.assertEqual(len(calls), before)
                self.assertLess(hit_s, warm_s)
                server.invalidate_dashboard_memo()
                deadline = time.time() + 2
                while len(calls) < before + 2 and time.time() < deadline:
                    time.sleep(0.02)
                self.assertGreaterEqual(len(calls), before + 2)
                again = server.load_agent_today_response("u", query="", read_only=True)
                self.assertEqual(again["ok"], True)
                self.assertGreater(warm_s, hit_s)
        finally:
            server._TODAY_WARM_ARMED = False
            server._PAYLOAD_MEMO_ENABLED = self._was
            server.invalidate_agent_today_cache()


class CoachingSettingsAndTurso(unittest.TestCase):
    def test_scope_fetches_once(self):
        calls = []

        def uncached(user_id=""):
            calls.append(user_id)
            return {"steps": 8000, "kcal": 2100, "hydration_ml": None, "updated_at": None}

        with mock.patch("rt_dashboard.coaching_targets._load_overrides_uncached", uncached):
            with coaching_settings_scope():
                first = load_overrides("")
                second = load_overrides("")
                from rt_dashboard.coaching_targets import stored_override

                kcal = stored_override("kcal")
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["steps"], 8000)
        self.assertEqual(second["kcal"], 2100)
        self.assertEqual(kcal, 2100)
        second["steps"] = 1
        with mock.patch("rt_dashboard.coaching_targets._load_overrides_uncached", uncached):
            with coaching_settings_scope():
                self.assertEqual(load_overrides("")["steps"], 8000)

    def test_shared_turso_reuses_connection_and_reconnects(self):
        import http.client

        from rt_dashboard import turso_http as th

        made = []

        class FakeResp:
            status = 200

            def read(self):
                return json.dumps(
                    {
                        "results": [
                            {
                                "type": "ok",
                                "response": {
                                    "result": {
                                        "cols": [{"name": "a"}],
                                        "rows": [[{"type": "integer", "value": "7"}]],
                                    }
                                },
                            }
                        ]
                    }
                ).encode()

            def getheader(self, key, default=""):
                return default

        class FakeConn:
            fail_next = False

            def __init__(self, *args, **kwargs):
                made.append(self)

            def request(self, *args, **kwargs):
                return None

            def getresponse(self):
                if FakeConn.fail_next:
                    FakeConn.fail_next = False
                    raise http.client.RemoteDisconnected("gone")
                return FakeResp()

            def close(self):
                return None

        th.reset_shared_connection()
        env = {
            "TURSO_DATABASE_URL": "libsql://example.turso.io",
            "TURSO_AUTH_TOKEN": "test-token",
        }
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(http.client, "HTTPSConnection", FakeConn):
            for _ in range(4):
                self.assertEqual(th.shared_read("SELECT steps FROM coaching_target_settings").fetchone()["a"], 7)
            self.assertEqual(len(made), 1)
            FakeConn.fail_next = True
            self.assertEqual(th.shared_read("SELECT 1").fetchone()["a"], 7)
            self.assertEqual(len(made), 2)
        th.reset_shared_connection()


class HidrateConnectionReuse(unittest.TestCase):
    def test_requests_share_one_connection(self):
        import http.client

        from rt_dashboard.hidrate_client import HidrateClient, reset_hidrate_connection

        made = []

        class FakeResp:
            status = 200

            def read(self):
                return b'{"results":[]}'

            def getheader(self, key, default=""):
                return default

        class FakeConn:
            def __init__(self, *args, **kwargs):
                made.append(self)

            def request(self, *args, **kwargs):
                return None

            def getresponse(self):
                return FakeResp()

            def close(self):
                return None

        reset_hidrate_connection()
        client = HidrateClient(
            email="a@b.c",
            password="secret",
            server_url="https://example.test/parse",
        )
        with mock.patch.object(http.client, "HTTPSConnection", FakeConn):
            client._request("GET", "/classes/Day", params={"limit": "1"}, session=True)
            client._request("GET", "/classes/Bottle", session=True)
        self.assertEqual(len(made), 1)
        reset_hidrate_connection()


class PayloadEquality(unittest.TestCase):
    def test_parallel_and_serial_payloads_match(self):
        """Same fixture, serial sections vs the thread pool, equal bodies."""
        from rt_dashboard.models import HealthSnapshot

        et = ZoneInfo("America/New_York")
        frozen = datetime(2026, 8, 10, 15, 0, tzinfo=et)
        logs = []
        weight = []
        sleep = []
        burned = []
        hydration = []
        for offset in range(21):
            day = (frozen - timedelta(days=20 - offset)).strftime("%Y-%m-%d")
            weight.append(WeightSample(date=day, weight_lbs=180 - offset * 0.1))
            sleep.append(SleepSample(date=day, sleep_hours=7.2))
            burned.append(CaloriesBurnedDay(date=day, calories=2300))
            hydration.append(HydrationDay(date=day, water_ml=500))
            for meal in range(4):
                logs.append(
                    FoodLogEntry(
                        date=day,
                        name=f"meal-{meal}",
                        calories=120 + meal,
                        protein_g=10 + meal,
                        carbs_g=15,
                        fat_g=4,
                        time=f"{8 + meal * 3:02d}:15",
                        nutrients={"fiber_g": 2.0},
                    )
                )
        snap = HealthSnapshot(
            weight=weight,
            sleep=sleep,
            food_logs=logs,
            hydration=hydration,
            calories_burned=burned,
        )

        def run(parallel: str):
            # A build persists the coach weekly store. The next build must not
            # read that file, or tdee_history differs for reasons other than
            # threading.
            with tempfile.TemporaryDirectory(prefix="fitdash-eq-") as td:
                root = Path(td)
                env = {
                    "FITDASH_BUILD_PARALLEL": parallel,
                    "FITDASH_COACH_STATE_DIR": str(root / "coach"),
                    "RESISTANCE_DASHBOARD_CONFIG_DIR": str(root / "config"),
                    "FITDASH_PHASE_BAROMETER_DIR": str(root / "phase"),
                    "FITDASH_HSA_DIR": str(root / "hsa"),
                    "FITDASH_LABS_DIR": str(root / "labs"),
                    "FITDASH_DB_PATH": str(root / "workouts.sqlite3"),
                    "DASHBOARD_CACHE_DIR": str(root / "cache"),
                    "LOCAL_WORKSPACE_DIR": str(root / "workspace"),
                    "FITDASH_QUEST_GT_SYNC": "0",
                    "GITHUB_PREFER_LOCAL": "1",
                }
                with mock.patch.dict(os.environ, env, clear=False), \
                     mock.patch.object(server, "local_now", return_value=frozen), \
                     mock.patch("rt_dashboard.timeutil.local_now", return_value=frozen), \
                     mock.patch.object(server, "load_health_cache", return_value=(snap, time.time(), {"hit": True, "fresh": True, "age_sec": 1})), \
                     mock.patch.object(server, "hidrate_credentials_present", return_value=False), \
                     mock.patch.object(server, "maybe_schedule_background_refresh", return_value=False), \
                     mock.patch.object(server, "save_health_cache", return_value=None):
                    return server.build_dashboard_data(force_refresh=False, user_id="eq-user")

        def _strip(node):
            if isinstance(node, dict):
                for key in ("load_ms", "generated_at", "fetched_at"):
                    node.pop(key, None)
                for value in node.values():
                    _strip(value)
            elif isinstance(node, list):
                for value in node:
                    _strip(value)

        serial = run("0")
        parallel = run("1")
        _strip(serial)
        _strip(parallel)
        self.assertEqual(_jsonable(serial), _jsonable(parallel))
        self.assertIn("nutrition_day", serial)
        self.assertIn("workout_store", serial)
        self.assertIn("daily_tasks", serial)


class WarmLoopRebuildsPayload(unittest.TestCase):
    def tearDown(self):
        server.drain_payload_rebuilds()
        server.invalidate_dashboard_memo()
        server._PAYLOAD_MEMO_ENABLED = False

    def test_hook_rebuilds_after_health_refresh(self):
        from rt_dashboard import background_refresh as br

        calls = []
        mtime = {"v": 1.0}

        def build(**_k):
            calls.append(mtime["v"])
            return {"n": len(calls), "mtime": mtime["v"]}

        def refresh_health(**_k):
            mtime["v"] = 2.0

        def hook():
            server.refresh_memoized_payloads()

        br.register_after_health_refresh(hook)
        try:
            with mock.patch.object(server, "_PAYLOAD_MEMO_ENABLED", True), \
                 mock.patch.object(server, "build_dashboard_data", build), \
                 mock.patch.object(server, "_health_cache_mtime", lambda: mtime["v"]), \
                 mock.patch.object(br, "_refresh_health", side_effect=refresh_health), \
                 mock.patch.object(br, "_refresh_github"):
                server.load_dashboard_data(user_id=None)
                self.assertEqual(len(calls), 1)
                br._spawn_refresh(local_dir="", token="", incremental=True, health_refresh_token="")
                deadline = time.time() + 2
                while len(calls) < 2 and time.time() < deadline:
                    time.sleep(0.01)
                server.drain_payload_rebuilds()
            self.assertGreaterEqual(len(calls), 2)
        finally:
            if hook in br._after_health_refresh:
                br._after_health_refresh.remove(hook)


if __name__ == "__main__":
    unittest.main()
