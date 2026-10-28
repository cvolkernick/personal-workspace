"""FitDash steps chart history endpoint (plan 2026-10-26, AC1-AC10 + A6)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard import cut_coach as cc  # noqa: E402  (Goal 3b coach, master)
from rt_dashboard import steps_history as sh  # noqa: E402
from rt_dashboard.timeutil import local_today_iso  # noqa: E402

TODAY = "2026-10-26"  # Monday


def _day(base: str, n: int) -> str:
    return (datetime.strptime(base, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def _health(start: str, end: str, value=lambda i: 6000 + 10 * i, skip=()):
    rows, i, d = [], 0, start
    while d <= end:
        if d not in skip:
            rows.append({"date": d, "steps": value(i), "source": "google_health"})
        d = _day(d, 1)
        i += 1
    return {"steps": rows}


class DenseAndDedupe(unittest.TestCase):
    def test_steps_history_dense_with_nulls(self):  # AC1
        h = _health("2026-09-01", TODAY, skip={"2026-10-20"})
        h["steps"].append({"date": "2026-10-21", "steps": 0, "source": "google_health"})  # 0 -> null
        h["steps"] = [r for r in h["steps"] if not (r["date"] == "2026-10-21" and r["steps"] != 0)]
        out = sh.build_steps_history(h, end=TODAY, weeks=8, today=TODAY)
        dates = [d["date"] for d in out["days"]]
        self.assertEqual(len(dates), len(set(dates)))
        self.assertEqual(dates[0], out["from"])
        self.assertEqual(dates[-1], TODAY)
        self.assertEqual(len(dates), (datetime.strptime(TODAY, "%Y-%m-%d") - datetime.strptime(out["from"], "%Y-%m-%d")).days + 1)
        by = {d["date"]: d for d in out["days"]}
        self.assertIsNone(by["2026-10-20"]["steps"])
        self.assertIsNone(by["2026-10-21"]["steps"])
        self.assertNotIn(0, [d["steps"] for d in out["days"]])
        self.assertEqual(out["coverage"]["days_missing"], 2)

    def test_a6_13_weeks_outside_90_day_pull_are_null_and_counted(self):  # A6 (AC1/AC3)
        # Today is Sunday 11-01; the cache holds only the 90-day pull (today-89 .. today).
        today = "2026-11-01"
        oldest = _day(today, -89)
        h = _health(oldest, today)
        out = sh.build_steps_history(h, end=today, weeks=13, today=today)
        self.assertEqual(out["from"], sh.monday_of(out["from"]))  # starts on a Monday
        self.assertEqual(len(out["days"]), 91)
        early = [d for d in out["days"] if d["date"] < oldest]
        self.assertEqual([d["date"] for d in early], [out["from"]])
        self.assertTrue(all(d["steps"] is None and d["rolling_7d"] is None for d in early))  # null, never 0
        self.assertEqual(out["coverage"]["days_missing"], 1)
        self.assertEqual(out["coverage"]["days_present"], 90)
        self.assertEqual(out["coverage"]["oldest_available"], oldest)
        # Monday after: end = last Sunday, pull moved a day -> the 2 earliest days are outside.
        h2 = _health(_day("2026-11-02", -89), "2026-11-02")
        out2 = sh.build_steps_history(h2, end=today, weeks=13, today="2026-11-02")
        early2 = [d for d in out2["days"] if d["steps"] is None]
        self.assertEqual(len(early2), 2)
        self.assertEqual(out2["coverage"]["days_missing"], 2)
        self.assertEqual([d["date"] for d in early2], [out2["from"], _day(out2["from"], 1)])

    def test_steps_history_dedupe_by_date(self):  # AC2
        h = {"steps": [
            {"date": "2026-10-22", "steps": 4000, "source": "google_health"},
            {"date": "2026-10-22", "steps": 5000, "source": "google_health"},
        ]}
        out = sh.build_steps_history(h, end="2026-10-25", weeks=1, today=TODAY)
        rows = [d for d in out["days"] if d["date"] == "2026-10-22"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["steps"], 5000)  # last merged wins, never 9000


class RangeParams(unittest.TestCase):
    def test_steps_history_range_and_params(self):  # AC3
        for w in range(1, 14):
            out = sh.build_steps_history({"steps": []}, end="2026-10-22", weeks=w, today=TODAY)
            self.assertEqual(datetime.strptime(out["from"], "%Y-%m-%d").weekday(), 0)
            self.assertEqual(len(out["weeks"]), w)
        self.assertEqual(sh.parse_params("", TODAY), ((8, TODAY), None))
        self.assertEqual(sh.parse_params("weeks=13&end=2026-10-20", TODAY), ((13, "2026-10-20"), None))
        for bad in ("weeks=0", "weeks=14", "weeks=x", "weeks=2.5"):
            self.assertEqual(sh.parse_params(bad, TODAY)[1]["error"], "bad_weeks")
        for bad in ("end=2026-13-01", "end=yesterday", "end=2026-10-2"):
            self.assertEqual(sh.parse_params(bad, TODAY)[1]["error"], "bad_end")
        self.assertEqual(sh.parse_params("end=2027-01-01", TODAY)[0], (8, TODAY))  # clamped


class RollingAndPartial(unittest.TestCase):
    def test_steps_history_rolling_7d(self):  # AC4
        h = _health("2026-10-01", TODAY, value=lambda i: 1000 * (i % 3 + 5))
        out = sh.build_steps_history(h, end=TODAY, weeks=2, today=TODAY)
        by = {d["date"]: d for d in out["days"]}
        st = {r["date"]: r["steps"] for r in h["steps"]}
        win = [st[_day("2026-10-25", -i)] for i in range(7)]
        self.assertEqual(by["2026-10-25"]["rolling_7d"], round(sum(win) / 7, 1))
        # only 4 present in the window -> null
        h2 = _health("2026-10-01", TODAY, skip={"2026-10-21", "2026-10-22", "2026-10-23"})
        by2 = {d["date"]: d for d in sh.build_steps_history(h2, end=TODAY, weeks=2, today=TODAY)["days"]}
        self.assertEqual(by2["2026-10-25"]["rolling_days"], 4)
        self.assertIsNone(by2["2026-10-25"]["rolling_7d"])
        h3 = _health("2026-10-01", TODAY, skip={"2026-10-21", "2026-10-22"})
        by3 = {d["date"]: d for d in sh.build_steps_history(h3, end=TODAY, weeks=2, today=TODAY)["days"]}
        self.assertEqual(by3["2026-10-25"]["rolling_days"], 5)
        self.assertIsNotNone(by3["2026-10-25"]["rolling_7d"])

    def test_steps_history_today_partial(self):  # AC5
        today = "2026-10-28"  # Wednesday
        h = _health("2026-10-01", today, value=lambda i: 99999 if False else 5000)
        h["steps"][-1]["steps"] = 100  # partial today
        out = sh.build_steps_history(h, end=today, weeks=1, today=today)
        last = out["days"][-1]
        self.assertTrue(last["partial"])
        self.assertEqual(last["steps"], 100)
        self.assertTrue(all(not d["partial"] for d in out["days"][:-1]))
        self.assertEqual(last["rolling_days"], 6)  # today excluded
        self.assertEqual(last["rolling_7d"], 5000.0)
        wk = out["weeks"][-1]
        self.assertEqual(wk["step_days"], 2)  # Mon+Tue only
        self.assertEqual(wk["avg_steps"], 5000)
        self.assertFalse(wk["complete"])


class CoachParity(unittest.TestCase):
    def test_steps_history_weekly_parity_with_week_math(self):  # AC6
        h = _health("2026-08-01", TODAY, value=lambda i: 4000 + (i * 137) % 5000,
                    skip={"2026-10-02", "2026-10-03", "2026-10-04", "2026-09-20"})
        out = sh.build_steps_history(h, end=TODAY, weeks=13, today=TODAY)
        steps = sh.daily_steps(h)
        for wk in out["weeks"]:
            if not wk["complete"]:
                continue
            days = [_day(wk["week_start"], i) for i in range(7)]
            vals = [steps[d]["steps"] for d in days if d in steps]
            self.assertEqual(wk["step_days"], len(vals))
            self.assertEqual(wk["steps_used"], len(vals) >= sh.MIN_DAYS)
            self.assertEqual(wk["avg_steps"], round(sum(vals) / len(vals), 0) if vals else None)
        last = [w for w in out["weeks"] if w["complete"]][-1]
        self.assertEqual(last["week_start"], "2026-10-19")  # last completed Mon-Sun before TODAY


class TargetAndNoWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"FITDASH_COACH_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_steps_history_target_from_coach_card_readonly(self):  # AC7
        data = {"health": _health("2026-10-01", TODAY), "meta": {"local_today": TODAY},
                "nutrition_store": {"targets": {"calories": 2000, "phase": "cut"}}}
        st, body = sh.steps_history_response(data, "", today=TODAY)
        self.assertEqual(st, 200)
        expect = cc.today_targets(cc.coach_card_for_payload(data, as_of=TODAY))["step_target"]
        self.assertEqual(body["step_target"]["value"], expect)
        self.assertEqual(body["step_target"]["source"], "coach_card.step_target")
        with mock.patch.object(cc, "coach_card_for_payload", side_effect=RuntimeError("boom")):
            st, body = sh.steps_history_response(data, "", today=TODAY)
        self.assertEqual((st, body["step_target"]["value"]), (200, None))  # coach down -> null

    def test_steps_history_no_writes(self):  # AC10
        sp = Path(self.tmp.name, "coach_state.json")
        sp.write_text(json.dumps({"step_target": None}))
        hp = Path(self.tmp.name, "health.json")
        hp.write_text(json.dumps({"health": _health("2026-10-01", TODAY)}))
        before = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (sp, hp)}
        listing = sorted(os.listdir(self.tmp.name))
        data = json.loads(hp.read_text())
        for q in ("", "weeks=4", "weeks=13", "end=2026-10-01", "weeks=99"):
            sh.steps_history_response(data, q, today=TODAY)
        after = {p: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (sp, hp)}
        self.assertEqual(before, after)
        self.assertEqual(listing, sorted(os.listdir(self.tmp.name)))
        src = (ROOT / "rt_dashboard" / "steps_history.py").read_text()
        for w in ("save_state", "persist_card", "turso", "write_text", "open("):
            self.assertNotIn(w, src.replace("never saves it", ""))


class EtBoundaries(unittest.TestCase):
    def test_steps_history_et_boundaries(self):  # AC8
        # 2026-11-02 03:59Z = Sun 11-01 22:59 EST (after DST ends) -> today is 11-01 (a Sunday).
        now = datetime(2026, 11, 2, 3, 59, tzinfo=timezone.utc)
        self.assertEqual(local_today_iso("America/New_York", now=now), "2026-11-01")
        # 2026-11-02 05:01Z = Mon 00:01 EST -> new civil day and new week.
        self.assertEqual(local_today_iso("America/New_York", now=datetime(2026, 11, 2, 5, 1, tzinfo=timezone.utc)), "2026-11-02")
        # Before DST end: 2026-10-27 03:59Z = Mon 10-26 23:59 EDT.
        self.assertEqual(local_today_iso("America/New_York", now=datetime(2026, 10, 27, 3, 59, tzinfo=timezone.utc)), "2026-10-26")
        h = _health("2026-09-01", "2026-11-02")
        out = sh.build_steps_history(h, end="2026-11-01", weeks=2, today="2026-11-01")
        self.assertEqual((out["from"], out["to"]), ("2026-10-19", "2026-11-01"))
        self.assertEqual(len(out["days"]), 14)  # DST night is still one civil day
        self.assertTrue(out["days"][-1]["partial"])
        self.assertEqual(out["weeks"][-1]["week_start"], "2026-10-26")
        out2 = sh.build_steps_history(h, end="2026-11-02", weeks=2, today="2026-11-02")
        self.assertEqual(out2["from"], "2026-10-26")
        self.assertTrue(out2["weeks"][0]["complete"])


import server as fitdash_server  # noqa: E402

TAILNET = "100.67.114.2"


class _TailnetServer(ThreadingHTTPServer):
    def finish_request(self, request, client_address):  # noqa: N802
        self.RequestHandlerClass(request, (TAILNET, client_address[1]), self)


class RouteAuth(unittest.TestCase):  # AC9
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "FITDASH_REQUIRE_AUTH": "1", "FITDASH_SERVICE_TOKEN": "house-secret", "FITDASH_AGENT_TOKEN": "agent-secret",
            "FITDASH_SERVICE_LOOPBACK": "1", "FITDASH_COACH_STATE_DIR": self.tmp.name})
        self.env.start()
        self.load = mock.patch.object(fitdash_server, "load_dashboard_data",
                                      return_value={"health": _health("2026-08-01", TODAY), "meta": {"local_today": TODAY}})
        self.load.start()
        self.httpd = _TailnetServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.load.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _get(self, path, headers=None):
        req = Request(f"http://127.0.0.1:{self.httpd.server_address[1]}{path}", headers=headers or {})
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_steps_history_auth(self):
        st, body = self._get("/api/agent/steps_history")
        st_cc, body_cc = self._get("/api/agent/coach_card")
        self.assertEqual(st, 401)
        self.assertEqual(st_cc, 401)
        self.assertEqual(set(body), set(body_cc))
        auth = {"Authorization": "Bearer agent-secret"}
        st, body = self._get("/api/agent/steps_history?weeks=4", auth)
        self.assertEqual(st, 200)
        self.assertEqual(body["schema"], "fitdash.steps_history.v1")
        self.assertEqual(len(body["weeks"]), 4)
        self.assertNotIn("agent-secret", json.dumps(body))
        self.assertEqual(self._get("/api/agent/steps_history?weeks=0", auth)[1]["error"], "bad_weeks")
        self.assertEqual(self._get("/api/agent/steps_history?end=nope", auth)[0], 400)
        self.assertEqual(self._get("/api/agent/steps_history", {"Authorization": "Bearer wrong"})[0], 401)

    def test_no_vercel_route(self):
        vercel = (ROOT / "vercel.json").read_text()
        self.assertNotIn("steps_history", vercel)
        self.assertFalse(list((ROOT / "api").rglob("*steps_history*")))


if __name__ == "__main__":
    unittest.main()
