"""Speed fixes: golden equivalence + caching behavior.

The reference functions below are the pre-change per-span implementations,
copied verbatim. The new single-pass code must produce identical outputs on
fixture data (timed/untimed/bad-time logs, dict and dataclass inputs, naps,
missing nights, an open day).
"""

from __future__ import annotations

import copy
import gzip
import io
import json
import os
import random
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard import nutrition_day as nd  # noqa: E402
from rt_dashboard.calorie_bars import build_calorie_bars_payload  # noqa: E402
from rt_dashboard.models import CaloriesBurnedDay, FoodLogEntry  # noqa: E402

ET = ZoneInfo("America/New_York")


# ---- reference (pre-change) implementations ------------------------------
def _ref_sum_intake_for_span(food_logs, span, *, now=None, with_rows=True):
    clock = now if now is not None else span.end
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=span.start.tzinfo)
    else:
        clock = clock.astimezone(span.start.tzinfo)
    cutoff = min(clock, span.end)
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    count = 0
    nutrient_maps = []
    rows = []
    tz = span.start.tzinfo
    for log in food_logs or []:
        dt = nd.food_log_event_time(log, default_tz=tz)
        if dt is None:
            continue
        dt = dt.astimezone(tz)
        if dt < span.intake_from or dt >= cutoff:
            continue
        d = nd.food_log_as_dict(log) or {}
        try:
            totals["calories"] += float(d.get("calories") or 0)
            totals["protein_g"] += float(d.get("protein_g") or 0)
            totals["carbs_g"] += float(d.get("carbs_g") or 0)
            totals["fat_g"] += float(d.get("fat_g") or 0)
            count += 1
            nutrient_maps.append(d.get("nutrients"))
            rows.append(d)
        except (TypeError, ValueError):
            continue
    from rt_dashboard.nutrition_micros import sum_micros

    micros = sum_micros(nutrient_maps) if count else {}
    out = {
        "calories": round(totals["calories"], 1),
        "protein_g": round(totals["protein_g"], 1),
        "carbs_g": round(totals["carbs_g"], 1),
        "fat_g": round(totals["fat_g"], 1),
        "log_count": count,
        "source": "waking_day_logs" if count else "none",
        "date": span.day_id,
        "window_start": span.start.isoformat(timespec="seconds"),
        "window_end": span.end.isoformat(timespec="seconds"),
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "food_log_count": count,
    }
    if micros:
        out["micros"] = micros
    out["logs"] = rows
    return out


def _ref_food_logs_for_span(food_logs, span):
    tz = span.start.tzinfo
    out = []
    for log in food_logs or []:
        dt = nd.food_log_event_time(log, default_tz=tz)
        if dt is None:
            continue
        if not span.contains_intake(dt):
            continue
        d = nd.food_log_as_dict(log)
        if d:
            out.append(d)
    return out


def _fixture(seed=7):
    rnd = random.Random(seed)
    base = datetime(2026, 8, 1, tzinfo=ET)
    iv = []
    logs = []
    burned = []
    for i in range(45):
        day = base + timedelta(days=i)
        if i % 11 != 5:  # a few missing nights
            onset = day.replace(hour=23) + timedelta(minutes=rnd.randint(-90, 150))
            wake = onset + timedelta(hours=rnd.uniform(5, 9))
            iv.append({"start": onset.isoformat(timespec="seconds"),
                       "end": wake.isoformat(timespec="seconds"), "source": "google_health"})
        if i % 4 == 0:  # afternoon nap
            ns = day.replace(hour=15, minute=10)
            iv.append({"start": ns.isoformat(timespec="seconds"),
                       "end": (ns + timedelta(minutes=70)).isoformat(timespec="seconds"),
                       "source": "google_health"})
        burned.append(CaloriesBurnedDay(date=day.strftime("%Y-%m-%d"), calories=2400 + rnd.randint(-300, 600)))
        for _ in range(rnd.randint(0, 9)):
            hh, mm = rnd.randint(0, 23), rnd.randint(0, 59)
            t = rnd.choice([f"{hh:02d}:{mm:02d}", f"{hh}.{mm:02d}", None, "bad", f"{hh:02d}:{mm:02d}:00"])
            kw = dict(
                date=day.strftime("%Y-%m-%d"), name=f"food{rnd.randint(1, 99)}",
                calories=round(rnd.uniform(20, 900), 1), protein_g=round(rnd.uniform(0, 60), 1),
                carbs_g=rnd.choice([None, round(rnd.uniform(0, 90), 1)]), fat_g=round(rnd.uniform(0, 40), 1),
                time=t, nutrients=rnd.choice([{}, {"fiber_g": 3.0, "sodium_g": 0.4, "sugar_g": 5.5}]),
            )
            logs.append(FoodLogEntry(**kw) if rnd.random() < 0.7 else dict(kw))
    logs.append(FoodLogEntry(date="2026-08-1", name="short date", calories=10))
    logs.append({"date": "2026-08-10", "time": "12:00", "calories": "x", "name": "bad cal"})
    rnd.shuffle(logs)
    return iv, logs, burned


def _jsonable(x):
    return json.loads(json.dumps(x, default=str, sort_keys=True))


class GoldenEquivalence(unittest.TestCase):
    def setUp(self):
        self.iv, self.logs, self.burned = _fixture()
        self.nows = [
            datetime(2026, 9, 14, 1, 30, tzinfo=ET),
            datetime(2026, 9, 10, 13, 5, tzinfo=ET),
            datetime(2026, 8, 20, 6, 0, tzinfo=ET),
        ]

    def _days(self, now):
        return nd.list_nutrition_days(now=now, tz_name="America/New_York", sleep_intervals=self.iv)

    def test_span_sums_and_rows_match(self):
        for now in self.nows:
            days = self._days(now)
            self.assertGreaterEqual(len(days), 15)
            prepared = nd.prepare_food_logs(self.logs, days[0].start.tzinfo)
            for span in days:
                for clock in (span.end, now):
                    ref = _ref_sum_intake_for_span(self.logs, span, now=clock)
                    self.assertEqual(_jsonable(nd.sum_intake_for_span(self.logs, span, now=clock)), _jsonable(ref))
                    self.assertEqual(_jsonable(nd.sum_intake_for_span(prepared, span, now=clock)), _jsonable(ref))
                self.assertEqual(_jsonable(nd.food_logs_for_span(self.logs, span)),
                                 _jsonable(_ref_food_logs_for_span(self.logs, span)))

    def test_trends_bucket_matches(self):
        for now in self.nows:
            days = self._days(now)
            new = nd.bucket_intake_by_nutrition_day(self.logs, days)
            with mock.patch.object(nd, "sum_intake_for_span", _ref_sum_intake_for_span), \
                 mock.patch.object(nd, "prepare_food_logs", lambda logs, tz: logs):
                ref = nd.bucket_intake_by_nutrition_day(self.logs, days)
            self.assertEqual(_jsonable(new), _jsonable(ref))

    def _ref_compose(self, **kw):
        with mock.patch.object(nd, "sum_intake_for_span", _ref_sum_intake_for_span), \
             mock.patch.object(nd, "food_logs_for_span", _ref_food_logs_for_span), \
             mock.patch.object(nd, "prepare_food_logs", lambda logs, tz: logs), \
             mock.patch.object(nd, "_memo_prepared", lambda logs, tz: logs):
            return nd._compose_nutrition_today_uncached(**kw)

    def test_compose_matches_inside_and_outside_build_scope(self):
        for now in self.nows:
            kw = dict(now=now, tz_name="America/New_York", sleep_intervals=self.iv,
                      food_logs=self.logs, calories_burned=self.burned)
            ref = _jsonable(self._ref_compose(**kw))
            self.assertEqual(_jsonable(nd.compose_nutrition_today(**kw)), ref)
            with nd.nutrition_build_scope():
                a = nd.compose_nutrition_today(**kw)
                b = nd.compose_nutrition_today(**dict(kw, food_logs=list(self.logs)))
            self.assertEqual(_jsonable(a), ref)
            self.assertEqual(_jsonable(b), ref)

    def test_build_scope_computes_identical_calls_once_and_copies(self):
        kw = dict(now=self.nows[0], tz_name="America/New_York", sleep_intervals=self.iv,
                  food_logs=self.logs, calories_burned=self.burned)
        calls = []
        real = nd._compose_nutrition_today_uncached

        def spy(**k):
            calls.append(1)
            return real(**k)

        with mock.patch.object(nd, "_compose_nutrition_today_uncached", spy):
            with nd.nutrition_build_scope():
                a = nd.compose_nutrition_today(**kw)
                a["today_consumed"]["calories"] = -1  # caller mutation
                b = nd.compose_nutrition_today(**kw)
                c = nd.compose_nutrition_today(**dict(kw, calories_burned=None))
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(b["today_consumed"]["calories"], -1)
        self.assertEqual(b["today_consumed"], c["today_consumed"])

    def test_scope_sees_changed_logs(self):
        kw = dict(now=self.nows[0], tz_name="America/New_York", sleep_intervals=self.iv,
                  food_logs=self.logs)
        with nd.nutrition_build_scope():
            before = nd.compose_nutrition_today(**kw)["today_consumed"]["calories"]
            today = before and self.nows[0].strftime("%Y-%m-%d")
            extra = FoodLogEntry(date="2026-09-13", name="late", calories=111.0, time="23:30")
            after = nd.compose_nutrition_today(**dict(kw, food_logs=self.logs + [extra]))
        ref = self._ref_compose(**dict(kw, food_logs=self.logs + [extra]))
        self.assertEqual(_jsonable(after), _jsonable(ref))
        del today

    def test_calorie_bars_match(self):
        for now in self.nows:
            kw = dict(now=now, tz_name="America/New_York", sleep_intervals=self.iv,
                      food_logs=self.logs, calories_burned=self.burned, sleep_battery={})
            new = build_calorie_bars_payload(**kw)
            with mock.patch.object(nd, "sum_intake_for_span", _ref_sum_intake_for_span), \
                 mock.patch.object(nd, "food_logs_for_span", _ref_food_logs_for_span), \
                 mock.patch.object(nd, "prepare_food_logs", lambda logs, tz: logs), \
                 mock.patch.object(nd, "_memo_prepared", lambda logs, tz: logs):
                ref = build_calorie_bars_payload(**kw)
            self.assertEqual(_jsonable(new), _jsonable(ref))


class PayloadMemo(unittest.TestCase):
    def setUp(self):
        import server

        self.server = server
        server.invalidate_dashboard_memo()

    def tearDown(self):
        self.server.invalidate_dashboard_memo()

    def test_disabled_outside_serving_mode(self):
        calls = []
        with mock.patch.object(self.server, "build_dashboard_data", lambda **k: calls.append(k) or {"n": len(calls)}):
            self.server.load_dashboard_data(user_id="u")
            self.server.load_dashboard_data(user_id="u")
        self.assertEqual(len(calls), 2)

    def test_memo_ttl_force_and_concurrency(self):
        calls = []

        def build(**k):
            calls.append(k)
            time.sleep(0.2)
            return {"n": len(calls), "nested": {"x": 1}}

        with mock.patch.object(self.server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(self.server, "build_dashboard_data", build), \
             mock.patch.object(self.server, "_health_cache_mtime", lambda: 1.0):
            out = []
            ts = [threading.Thread(target=lambda: out.append(self.server.load_dashboard_data(user_id="u"))) for _ in range(3)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(o["n"] == 1 for o in out))
            out[0]["nested"]["x"] = 99
            self.assertEqual(self.server.load_dashboard_data(user_id="u")["nested"]["x"], 1)
            self.server.load_dashboard_data(user_id="other")
            self.assertEqual(len(calls), 2)
            self.server.load_dashboard_data(user_id="u", force_refresh=True)
            self.assertEqual(len(calls), 3)
            self.server.invalidate_dashboard_memo()
            self.server.load_dashboard_data(user_id="u")
            self.assertEqual(len(calls), 4)

    def test_health_cache_change_invalidates(self):
        calls = []
        m = {"v": 1.0}
        with mock.patch.object(self.server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(self.server, "build_dashboard_data", lambda **k: calls.append(1) or {}), \
             mock.patch.object(self.server, "_health_cache_mtime", lambda: m["v"]):
            self.server.load_dashboard_data(user_id="u")
            self.server.load_dashboard_data(user_id="u")
            m["v"] = 2.0
            self.server.load_dashboard_data(user_id="u")
        self.assertEqual(len(calls), 2)


class PayloadMemoWriteGeneration(unittest.TestCase):
    def setUp(self):
        import server

        self.server = server
        server.invalidate_dashboard_memo()

    def test_build_racing_a_write_is_not_cached(self):
        """A POST during an in-flight build must not leave that build cached."""
        calls = []
        started = threading.Event()
        release = threading.Event()

        def build(**k):
            calls.append(1)
            if len(calls) == 1:
                started.set()
                release.wait(2)
                return {"v": "pre-write"}
            return {"v": "post-write"}

        with mock.patch.object(self.server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(self.server, "build_dashboard_data", build), \
             mock.patch.object(self.server, "_health_cache_mtime", lambda: 1.0):
            out = {}
            t = threading.Thread(target=lambda: out.setdefault("a", self.server.load_dashboard_data(user_id="u")))
            t.start()
            self.assertTrue(started.wait(2))
            self.server.invalidate_dashboard_memo()  # the write lands mid-build
            release.set()
            t.join()
            self.assertEqual(out["a"]["v"], "pre-write")
            self.assertEqual(self.server.load_dashboard_data(user_id="u")["v"], "post-write")
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.server.load_dashboard_data(user_id="u")["v"], "post-write")
            self.assertEqual(len(calls), 2)


class PayloadMemoPostCommitBump(unittest.TestCase):
    def setUp(self):
        import server

        self.server = server
        server.invalidate_dashboard_memo()

    def test_build_started_after_pre_bump_but_before_commit_is_not_cached(self):
        """Pre-bump -> build starts -> commit + post-bump -> build ends: not cached."""
        db = {"total": 100}
        calls = []
        started = threading.Event()
        release = threading.Event()

        def build(**k):
            calls.append(1)
            snap = db["total"]  # build reads before the commit
            if len(calls) == 1:
                started.set()
                release.wait(2)
            return {"total": snap}

        out = {}

        class FakeHandler:
            def _handle_post(fh):
                # The POST handler is running: pre-bump already happened.
                t = threading.Thread(
                    target=lambda: out.setdefault("a", self.server.load_dashboard_data(user_id="u"))
                )
                t.start()
                self.assertTrue(started.wait(2))
                db["total"] = 250  # commit lands mid-build
                fh.t = t

        fh = FakeHandler()
        with mock.patch.object(self.server, "_PAYLOAD_MEMO_ENABLED", True), \
             mock.patch.object(self.server, "build_dashboard_data", build), \
             mock.patch.object(self.server, "_health_cache_mtime", lambda: 1.0):
            self.server.DashboardHandler.do_POST(fh)  # post-bump in finally
            release.set()
            fh.t.join()
            self.assertEqual(out["a"]["total"], 100)
            self.assertEqual(self.server.load_dashboard_data(user_id="u")["total"], 250)
            self.assertEqual(len(calls), 2)

    def test_post_bumps_even_when_handler_raises(self):
        gen0 = self.server._PAYLOAD_WRITE_GEN

        class Boom:
            def _handle_post(self):
                raise RuntimeError("x")

        with self.assertRaises(RuntimeError):
            self.server.DashboardHandler.do_POST(Boom())
        self.assertEqual(self.server._PAYLOAD_WRITE_GEN, gen0 + 2)


class HealthCacheWriteOnlyIfChanged(unittest.TestCase):
    def test_unchanged_overlay_skips_write(self):
        from rt_dashboard import dashboard_cache as dc
        from rt_dashboard.models import HealthSnapshot, WeightSample

        with tempfile.TemporaryDirectory() as td, mock.patch.object(dc, "cache_dir", lambda: Path(td)):
            snap = HealthSnapshot(weight=[WeightSample(date="2026-10-01", weight_lbs=180.0)])
            dc.save_health_cache(snap)
            p = Path(td) / dc.HEALTH_CACHE
            first = json.loads(p.read_text())["fetched_at"]
            time.sleep(0.01)
            r = dc.save_health_cache(snap, only_if_changed=True)
            self.assertFalse(r["saved"])
            self.assertEqual(json.loads(p.read_text())["fetched_at"], first)
            snap.weight.append(WeightSample(date="2026-10-02", weight_lbs=179.0))
            r = dc.save_health_cache(snap, only_if_changed=True)
            self.assertTrue(r["saved"])
            self.assertEqual(len(json.loads(p.read_text())["health"]["weight"]), 2)


class HidrateStaleWhileRevalidate(unittest.TestCase):
    def test_stale_series_served_and_refreshed_in_background(self):
        from rt_dashboard import hidrate_client as hc

        client = hc.HidrateClient.__new__(hc.HidrateClient)
        old = {"days": 14, "fetched_at": time.time() - 600, "series": ["stale"]}
        refreshed = threading.Event()

        def live(self, days=90, use_cache=True):
            self.assertFalse(use_cache) if False else None
            refreshed.set()
            return ["fresh"]

        with mock.patch.object(hc, "_SERIES_CACHE", old):
            real = hc.HidrateClient.fetch_hydration_days

            def wrapper(self, days=90, *, use_cache=True):
                if not use_cache:
                    refreshed.set()
                    return ["fresh"]
                return real(self, days, use_cache=use_cache)

            with mock.patch.object(hc.HidrateClient, "fetch_hydration_days", wrapper):
                got = client.fetch_hydration_days(days=14)
                self.assertEqual(got, ["stale"])
                self.assertTrue(refreshed.wait(2))
        del live


class StaticAssets(unittest.TestCase):
    def _handler(self, path, headers=None):
        import server

        h = server.DashboardHandler.__new__(server.DashboardHandler)
        h.directory = str(server.STATIC_DIR)
        h.path = path
        h.command = "GET"
        h.request_version = "HTTP/1.1"
        h.headers = {k: v for k, v in (headers or {}).items()}

        class _H(dict):
            def get(self, k, d=None):
                for kk, v in self.items():
                    if kk.lower() == k.lower():
                        return v
                return d

        h.headers = _H(h.headers)
        h.sent = []
        h.wfile = io.BytesIO()
        h.send_response = lambda code, msg=None: h.sent.append(("status", code))
        h.send_header = lambda k, v: h.sent.append((k, v))
        h._headers_buffer = []
        h.log_message = lambda *a, **k: None
        return h

    def _headers(self, h):
        return {k: v for k, v in h.sent if k != "status"}

    def test_versioned_js_gzip_immutable_and_304(self):
        h = self._handler("/app.js?v=quest-resync-1304-1", {"Accept-Encoding": "gzip, br"})
        with mock.patch("http.server.BaseHTTPRequestHandler.end_headers", lambda self: None):
            f = h.send_head()
        hd = self._headers(h)
        self.assertEqual(hd["Content-Encoding"], "gzip")
        self.assertIn("immutable", hd["Cache-Control"])
        body = gzip.decompress(f.read())
        self.assertEqual(body, (Path(h.directory) / "app.js").read_bytes())
        etag = hd["ETag"]
        h2 = self._handler("/app.js?v=quest-resync-1304-1", {"Accept-Encoding": "gzip", "If-None-Match": etag})
        with mock.patch("http.server.BaseHTTPRequestHandler.end_headers", lambda self: None):
            self.assertIsNone(h2.send_head())
        self.assertIn(("status", 304), h2.sent)

    def test_index_short_cache_with_etag_and_sw_no_cache(self):
        with mock.patch("http.server.BaseHTTPRequestHandler.end_headers", lambda self: None):
            h = self._handler("/")
            f = h.send_head()
            hd = self._headers(h)
            self.assertNotIn("Content-Encoding", hd)
            self.assertIn("max-age=30", hd["Cache-Control"])
            self.assertTrue(hd["ETag"])
            self.assertIn(b"<html", f.read().lower())
            h = self._handler("/sw.js", {"Accept-Encoding": "gzip"})
            h.send_head()
            self.assertEqual(self._headers(h)["Cache-Control"], "no-cache")

    def test_sw_precache_matches_index_versions(self):
        import re

        idx = (ROOT / "static" / "index.html").read_text()
        sw = (ROOT / "static" / "sw.js").read_text()
        for asset in re.findall(r'(?:src|href)="(/[^"]+\?v=[^"]+)"', idx):
            self.assertIn(f'"{asset}"', sw)
        self.assertIn('const CACHE = "fitdash-shell-v144"', sw)
        self.assertIn('url.searchParams.has("v")', sw)


class TursoKeepAlive(unittest.TestCase):
    def test_reuses_connection_and_retries_stale_read_only(self):
        from rt_dashboard import turso_http as th
        import http.client

        made = []

        class FakeResp:
            status = 200

            def __init__(self):
                pass

            def read(self):
                return json.dumps({"results": [{"type": "ok", "response": {"result": {"cols": [{"name": "a"}], "rows": [[{"type": "integer", "value": "1"}]]}}}]}).encode()

            def getheader(self, k, d=""):
                return d

        class FakeConn:
            fail_next = False

            def __init__(self, *a, **k):
                made.append(self)

            def request(self, *a, **k):
                pass

            def getresponse(self):
                if FakeConn.fail_next:
                    FakeConn.fail_next = False
                    raise http.client.RemoteDisconnected("gone")
                return FakeResp()

            def close(self):
                pass

        with mock.patch.object(http.client, "HTTPSConnection", FakeConn):
            c = th.TursoConnection("libsql://x.turso.io", "t")
            with th.keepalive_scope():
                for _ in range(5):
                    self.assertEqual(c.execute("SELECT 1").fetchone()["a"], 1)
                self.assertEqual(len(made), 1)
                FakeConn.fail_next = True
                c.execute("SELECT 1")  # stale reused socket: one retry
                self.assertEqual(len(made), 2)
                FakeConn.fail_next = True
                with self.assertRaises(RuntimeError):
                    c.execute("INSERT INTO t VALUES (1)")  # sent write: never replayed


if __name__ == "__main__":
    unittest.main()
