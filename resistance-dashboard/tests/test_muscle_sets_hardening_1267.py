"""Hardening for the #1193 muscle_sets feed (#1267).

Failure isolation on /api/agent/today, the cap override surviving goals
normalization, the overrun warning compared in matching units, the history
horizon, and the route-level feed tests.
"""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import server as fitdash_server
from rt_dashboard import muscle_sets as ms
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_planner import generate_workout_plan, normalize_goals
from rt_dashboard.workout_store import load_workspace_catalog, load_workspace_goals

ET = ZoneInfo("America/New_York")
CATALOG, _CSRC = load_workspace_catalog()
GOALS, _GSRC = load_workspace_goals()
DAY = "2026-10-16"  # Friday of the week that starts Mon 10/12


def _sets(*triples):
    return [{"weight_lbs": w, "sets": n, "reps": r} for (w, n, r) in triples]


def _ex(name, *triples):
    return {"name": name, "sets": _sets(*triples)}


def _session(day, letter, *exercises):
    return {"date": day, "session_type": letter, "exercises": list(exercises)}


def _payload(day, sessions, **extra):
    base = {
        "coach": {"today": {"date": day}},
        "meta": {"local_today": day},
        "sessions": sessions,
        "workout_store": {"goals": copy.deepcopy(GOALS), "catalog": CATALOG, "plan": {}},
    }
    base.update(extra)
    return base


def _day(monday, offset):
    return (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")


def _balanced_week(monday, *, full=True):
    """A balanced PPL week: 63 proper sets, 91 credited."""
    sessions = [
        _session(
            _day(monday, 0), "push",
            _ex("DB Flat Press", (50, 4, 8)), _ex("DB Shoulder Press", (30, 3, 8)),
            _ex("Lateral Raises", (15, 4, 12)), _ex("Tricep Pushdowns", (40, 3, 12)),
        ),
        _session(
            _day(monday, 1), "pull",
            _ex("Pulldowns", (100, 4, 8)), _ex("Seated Cable Row", (90, 4, 8)),
            _ex("DB Curls", (25, 3, 12)), _ex("Reverse Pec Deck", (40, 3, 12)),
        ),
        _session(
            _day(monday, 2), "legs",
            _ex("Leg Press", (200, 4, 8)), _ex("RDL", (135, 3, 8)),
            _ex("Calf Extensions", (80, 4, 12)), _ex("Ab Wheel Rollout", (0, 3, 10)),
        ),
        _session(
            _day(monday, 3), "push",
            _ex("DB Incline Press", (45, 4, 8)), _ex("Cable Lateral Raise", (10, 4, 12)),
            _ex("Tricep Pushdowns (Rope)", (40, 3, 12)),
        ),
        _session(
            _day(monday, 4), "pull",
            _ex("Machine Row", (100, 4, 8)), _ex("Hammer Curls", (25, 3, 12)),
            _ex("Smith Shrugs", (100, 3, 12)),
        ),
    ]
    return sessions if full else sessions[:2]


FOUR_WEEKS = ("2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05")


def _balanced_history():
    out = []
    for monday in FOUR_WEEKS:
        out += _balanced_week(monday)
    return out


def _get(port, path="/api/agent/today"):
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:  # pragma: no cover - reported as a test failure
        return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")


class _RouteCase(unittest.TestCase):
    """HTTP ``GET /api/agent/today`` over dict sessions (the load_dashboard_data shape)."""

    sessions = None

    def setUp(self):
        sessions = self.sessions or [
            _session("2026-10-13", "push", _ex("DB Flat Press", (50, 3, 8))),
        ]
        payload = _payload(DAY, sessions, recovery={"score": 80, "sparse": True})
        now = datetime(2026, 10, 16, 9, 0, tzinfo=ET)
        self._patches = [
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value=payload),
            mock.patch.object(fitdash_server, "_auth_required", return_value=False),
            mock.patch("rt_dashboard.timeutil.local_now", return_value=now),
            mock.patch("rt_dashboard.timeutil.local_today_iso", return_value=DAY),
            mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False),
            mock.patch(
                "rt_dashboard.gym_calendar.sync_gym_from_workout",
                return_value={"ok": True, "skipped": True},
            ),
            mock.patch(
                "rt_dashboard.google_health.GoogleHealthClient.credentials_present",
                return_value=False,
            ),
        ]
        for patch in self._patches:
            patch.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        self.port = int(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        for patch in reversed(self._patches):
            patch.stop()


class FeedRoute(_RouteCase):
    def test_route_returns_muscle_sets_for_dict_sessions(self):
        status, body = _get(self.port)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"), body)
        sets = body["week"]["muscle_sets"]
        self.assertEqual(list(sets), list(ms.VOLUME_GROUPS))
        self.assertEqual(sets["chest"]["proper_sets"], 3.0)
        self.assertEqual(sets["chest"]["tier"], "red")
        self.assertEqual(body["week"]["muscle_set_budget"]["week_proper_sets"], 3)
        for key in ("nutrition", "logged_sessions", "sleep", "start", "end"):
            self.assertIn(key, body["week"])


class FailureIsolation(_RouteCase):
    def test_forced_builder_failure_keeps_the_route_at_200(self):
        with mock.patch.object(
            ms, "build_muscle_set_report", side_effect=RuntimeError("token=sk-secret boom")
        ):
            status, body = _get(self.port)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["week"]["muscle_sets"], {})
        error = body["week"]["muscle_set_budget"]["error"]
        self.assertIsInstance(error, str)
        self.assertLess(len(error), 120)
        self.assertIn("RuntimeError", error)
        self.assertNotIn("sk-secret", error)
        self.assertNotIn("token", error)
        # The rest of the week still comes through.
        self.assertIn("nutrition", body["week"])
        self.assertIn("today", body)

    def test_failure_is_logged_without_the_message(self):
        with mock.patch.object(
            ms, "build_muscle_set_report", side_effect=ValueError("secret-value")
        ):
            with self.assertLogs("rt_dashboard.agent_today", level="WARNING") as logs:
                export_agent_today(_payload(DAY, []))
        joined = "\n".join(logs.output)
        self.assertIn("muscle_sets", joined)
        self.assertIn("ValueError", joined)
        self.assertNotIn("secret-value", joined)


class FeedUnchanged(unittest.TestCase):
    def _sessions(self):
        return [
            _session("2026-10-13", "push", _ex("DB Flat Press", (50, 3, 8)),
                     _ex("Lateral Raises", (15, 3, 12))),
            _session("2026-10-15", "pull", _ex("Pulldowns", (100, 3, 8))),
        ]

    ADDED = {"muscle_sets", "muscle_set_budget", "muscle_set_coach"}

    def test_only_the_three_additive_week_keys_are_new(self):
        out = export_agent_today(_payload(DAY, self._sessions()))
        self.assertEqual(
            set(out["week"]),
            {"start", "end", "nutrition", "logged_sessions", "sleep"} | self.ADDED,
        )
        self.assertTrue({"ok", "today", "week"} <= set(out))
        self.assertEqual(out["week"]["start"], "2026-10-12")
        self.assertEqual(out["week"]["end"], DAY)  # still Monday through today

    def test_failure_path_changes_nothing_but_the_muscle_keys(self):
        payload = _payload(DAY, self._sessions())
        good = export_agent_today(copy.deepcopy(payload))
        with mock.patch.object(ms, "build_muscle_set_report", side_effect=RuntimeError("x")):
            bad = export_agent_today(copy.deepcopy(payload))
        for out in (good, bad):
            for key in self.ADDED:
                out["week"].pop(key, None)
        self.assertEqual(good, bad)

    def test_dict_object_and_to_dict_sessions_agree(self):
        dicts = self._sessions()
        objects = [
            Session(
                date=s["date"],
                session_type=s["session_type"],
                exercises=[
                    ExerciseEntry(
                        name=e["name"],
                        sets=[SetEntry(weight_lbs=t["weight_lbs"], sets=t["sets"], reps=t["reps"])
                              for t in e["sets"]],
                    )
                    for e in s["exercises"]
                ],
            )
            for s in dicts
        ]
        a = export_agent_today(_payload(DAY, dicts))["week"]["muscle_sets"]
        b = export_agent_today(_payload(DAY, objects))["week"]["muscle_sets"]
        c = export_agent_today(_payload(DAY, [o.to_dict() for o in objects]))["week"]["muscle_sets"]
        self.assertEqual(a, b)
        self.assertEqual(a, c)


class CapOverridePassThrough(unittest.TestCase):
    def test_normalize_goals_round_trip(self):
        self.assertEqual(normalize_goals({"muscle_set_cap_override": 42})["muscle_set_cap_override"], 42)
        once = normalize_goals({"muscle_set_cap_override": 42})
        self.assertEqual(normalize_goals(once)["muscle_set_cap_override"], 42)
        self.assertEqual(normalize_goals({"muscle_set_cap_override": "55"})["muscle_set_cap_override"], 55)

    def test_empty_and_invalid_values_stay_absent(self):
        for raw in (None, "", 0, -3, "abc", True, float("nan")):
            with self.subTest(raw=raw):
                self.assertNotIn(
                    "muscle_set_cap_override", normalize_goals({"muscle_set_cap_override": raw})
                )
        self.assertNotIn("muscle_set_cap_override", normalize_goals({}))
        self.assertNotIn("muscle_set_cap_override", normalize_goals(None))

    def test_production_goals_loader_keeps_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goals.json"
            body = copy.deepcopy(GOALS)
            body["muscle_set_cap_override"] = 48
            path.write_text(json.dumps(body), encoding="utf-8")
            with mock.patch("rt_dashboard.workout_store.GOALS_PATH", path), mock.patch(
                "rt_dashboard.workout_store._workspace_file_candidates",
                return_value=[path],
            ):
                goals, source = load_workspace_goals()
        self.assertEqual(goals["muscle_set_cap_override"], 48)
        self.assertNotEqual(source, "default")

    def test_report_cap_uses_the_normalized_override(self):
        goals = normalize_goals({**GOALS, "muscle_set_cap_override": 48})
        payload = _payload(DAY, _balanced_history())
        payload["workout_store"]["goals"] = goals
        budget = ms.build_muscle_set_report(payload, as_of=DAY)["budget"]
        self.assertEqual(budget["cap"], 48)
        self.assertEqual(budget["cap_override"], 48)
        self.assertEqual(budget["cap_source"], "override")

    def test_default_goals_file_has_no_override(self):
        self.assertIsNone(normalize_goals(GOALS).get("muscle_set_cap_override"))


class WarningUnits(unittest.TestCase):
    def test_balanced_week_has_no_false_drop_warning(self):
        report = ms.build_muscle_set_report(
            _payload(DAY, _balanced_history() + _balanced_week("2026-10-12", full=False)),
            as_of=DAY,
        )
        budget = report["budget"]
        self.assertEqual(budget["cap"], 63)  # cap units are unchanged: proper sets
        self.assertEqual(budget["credited_avg_4w"], 91.0)
        self.assertEqual(budget["demand_min"], 68)
        self.assertFalse(budget["overrun"])
        self.assertIsNone(budget["warning"])
        self.assertIsNone(budget["drop_suggestion"])

    def test_cap_unit_and_planner_inputs_are_unchanged(self):
        budget = ms.build_muscle_set_report(
            _payload(DAY, _balanced_history()), as_of=DAY
        )["budget"]
        self.assertIsInstance(budget["cap"], int)
        self.assertEqual(budget["cap"], 63)
        self.assertEqual(budget["cap_raw"], 63)
        self.assertEqual(budget["week_proper_sets"], 0)

    def _thin_history(self):
        sessions = []
        for monday in FOUR_WEEKS:
            sessions.append(
                _session(monday, "push", _ex("DB Flat Press", (50, 4, 8)),
                         _ex("Lateral Raises", (15, 4, 12)))
            )
        return sessions

    def test_genuine_overrun_still_warns_and_names_the_group(self):
        report = ms.build_muscle_set_report(_payload(DAY, self._thin_history()), as_of=DAY)
        budget = report["budget"]
        self.assertLess(budget["credited_avg_4w"], budget["demand_min"])
        self.assertTrue(budget["overrun"])
        self.assertIn(budget["drop_suggestion"], ms.PRIORITY_GROUPS)
        self.assertIn("Drop", budget["warning"])
        self.assertIn(ms.GROUP_LABELS[budget["drop_suggestion"]], budget["warning"])
        feed = export_agent_today(_payload(DAY, self._thin_history()))["week"]["muscle_set_budget"]
        self.assertEqual(feed["warning"], budget["warning"])
        self.assertEqual(feed["drop_suggestion"], budget["drop_suggestion"])

    def test_override_below_demand_still_warns(self):
        payload = _payload(DAY, _balanced_history())
        payload["workout_store"]["goals"]["muscle_set_cap_override"] = 30
        budget = ms.build_muscle_set_report(payload, as_of=DAY)["budget"]
        self.assertEqual(budget["cap"], 30)
        self.assertTrue(budget["overrun"])
        self.assertIn("Drop", budget["warning"])

    def test_no_cap_means_no_warning(self):
        budget = ms.build_muscle_set_report(_payload(DAY, []), as_of=DAY)["budget"]
        self.assertIsNone(budget["cap"])
        self.assertIsNone(budget["warning"])
        self.assertFalse(budget["overrun"])

    def test_budget_warning_helper_keeps_its_old_call_shape(self):
        self.assertIsNone(ms.budget_warning(None, "chest"))
        self.assertIsNone(ms.budget_warning(68, "chest"))
        self.assertIn("Drop chest", ms.budget_warning(30, "chest"))
        self.assertIsNone(ms.budget_warning(63, "chest", credit_cap=91.0))
        self.assertIn("Drop chest", ms.budget_warning(63, "chest", credit_cap=40.0))


class PlannerCapUnchanged(unittest.TestCase):
    """Planner ``cap_left`` reads ``budget.cap`` and ``week_proper_sets`` only.

    These plans were recorded on master at 8bc53f1c before the change.
    """

    @staticmethod
    def _s(day, kind, *lifts):
        return Session(
            date=day,
            session_type=kind,
            exercises=[ExerciseEntry(name=n, sets=[SetEntry(w, s, r)]) for (n, w, s, r) in lifts],
        )

    def _plan(self, history, kind, as_of):
        plan = generate_workout_plan(
            CATALOG, GOALS, history, recovery_score=80, session_type=kind, as_of=as_of
        )
        return [(r["name"], r["prescription"]["sets"]) for r in plan["exercises"]]

    def test_plans_match_the_pre_change_recording(self):
        low = [
            self._s(m, "push", ("DB Flat Press", 50, 2, 8), ("Lateral Raises", 15, 2, 12))
            for m in FOUR_WEEKS
        ]
        cur = [
            self._s("2026-10-12", "push", ("DB Flat Press", 50, 4, 8),
                    ("Lateral Raises", 15, 4, 12), ("DB Shoulder Press", 30, 3, 8)),
            self._s("2026-10-13", "pull", ("Pulldowns", 100, 4, 8), ("Seated Cable Row", 90, 4, 8)),
        ]
        self.assertEqual(
            self._plan(low + cur, "pull", "2026-10-14"),
            [("Assisted Pullups", 1), ("Machine Row", 1)],
        )
        self.assertEqual(
            self._plan(low + cur, "push", "2026-10-15"),
            [("Smith Flat Bench", 1), ("Tricep Pushdowns (Rope)", 1),
             ("Smith Incline Bench", 1), ("Behind-the-Neck Smith Press", 1)],
        )
        hi = []
        for m in FOUR_WEEKS:
            hi.append(self._s(m, "push", ("DB Flat Press", 50, 4, 8), ("Lateral Raises", 15, 4, 12),
                              ("Smith Shrugs", 100, 3, 12)))
            hi.append(self._s(m, "pull", ("Pulldowns", 100, 4, 8), ("DB Curls", 25, 3, 12)))
        self.assertEqual(
            self._plan(hi, "push", "2026-10-13"),
            [("DB Incline Press", 2), ("Smith Flat Bench", 2), ("Behind-the-Neck Smith Press", 2),
             ("Tricep Pushdowns (Rope)", 1), ("Lateral Raises", 1)],
        )


class HistoryHorizon(unittest.TestCase):
    def _long(self, weeks=40):
        sessions = []
        monday = datetime(2026, 10, 12) - timedelta(weeks=weeks)
        for i in range(weeks):
            sessions += _balanced_week((monday + timedelta(weeks=i)).strftime("%Y-%m-%d"))
        return sessions

    def test_history_is_limited_to_about_ten_weeks(self):
        sessions = self._long()
        seen = {}
        real = ms.week_group_credits

        def spy(sess, *args, **kwargs):
            seen["n"] = len(sess)
            return real(sess, *args, **kwargs)

        with mock.patch.object(ms, "week_group_credits", side_effect=spy):
            ms.build_muscle_set_report(_payload(DAY, sessions), as_of=DAY)
        self.assertLessEqual(seen["n"], 5 * (ms.HISTORY_WEEKS + 1))
        self.assertLess(seen["n"], len(sessions))

    def test_steady_history_gives_the_same_report_as_an_unbounded_one(self):
        sessions = self._long()
        payload = _payload(DAY, sessions + _balanced_week("2026-10-12", full=False))
        bounded = ms.build_muscle_set_report(payload, as_of=DAY)
        with mock.patch.object(ms, "HISTORY_WEEKS", 10_000):
            unbounded = ms.build_muscle_set_report(payload, as_of=DAY)
        self.assertEqual(bounded, unbounded)

    def test_old_sessions_do_not_leak_into_the_four_week_average(self):
        old = [_session("2025-01-06", "push", _ex("DB Flat Press", (50, 40, 8)))]
        base = _payload(DAY, _balanced_history())
        with_old = _payload(DAY, old + _balanced_history())
        self.assertEqual(
            ms.build_muscle_set_report(base, as_of=DAY),
            ms.build_muscle_set_report(with_old, as_of=DAY),
        )

    def test_tonnage_and_deload_are_computed_once(self):
        payload = _payload(DAY, _balanced_history())
        with mock.patch.object(ms, "_tonnage_status", return_value="green") as tonnage, \
                mock.patch.object(ms, "_deload_called", return_value=False) as deload:
            ms.build_muscle_set_report(payload, as_of=DAY)
        self.assertEqual(tonnage.call_count, 1)
        self.assertEqual(deload.call_count, 1)

    def test_450_sessions_stay_fast(self):
        import time

        base = datetime(2025, 7, 1)
        sessions = [
            _session((base + timedelta(days=i)).strftime("%Y-%m-%d"), "push",
                     _ex("DB Flat Press", (50, 4, 8)), _ex("Lateral Raises", (15, 3, 12)),
                     _ex("Pulldowns", (100, 3, 8)))
            for i in range(450)
        ]
        payload = _payload(DAY, sessions)
        ms.build_muscle_set_report(payload, as_of=DAY)  # warm imports
        start = time.perf_counter()
        for _ in range(3):
            ms.build_muscle_set_report(payload, as_of=DAY)
        mean_ms = (time.perf_counter() - start) / 3 * 1000
        self.assertLess(mean_ms, 100.0, f"{mean_ms:.1f} ms")


if __name__ == "__main__":
    unittest.main()
