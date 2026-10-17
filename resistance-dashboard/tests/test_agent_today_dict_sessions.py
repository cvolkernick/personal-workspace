"""``GET /api/agent/today`` 500: ``'dict' object has no attribute 'exercises'`` (#1240).

``load_dashboard_data`` carries ``sessions`` as ``to_dict()`` payloads. When a
session is closed today the fill takes the deterministic planner path, which
runs ``filter_sessions`` and reads ``.exercises``. These tests pin both payload
shapes (dict and ``Session`` object) at each layer that raised, then the HTTP
route end to end.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.request import urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as fitdash_server  # noqa: E402
from rt_dashboard.agent_plan import (  # noqa: E402
    _deterministic_workout,
    fill_stamped_workout,
)
from rt_dashboard.models import ExerciseEntry, Session, SetEntry  # noqa: E402
from rt_dashboard.test_noise import coerce_sessions, filter_sessions  # noqa: E402
from rt_dashboard.workout_plan_store import clear_memory_workout_plans  # noqa: E402
from rt_dashboard.workout_store import (  # noqa: E402
    load_workspace_goals,
    stamp_today_session,
)

ET = ZoneInfo("America/New_York")
DAY = "2026-10-17"
NOW = datetime(2026, 10, 17, 7, 53, tzinfo=ET)
WAKE = "2026-10-17T04:30:00-04:00"
RECOVERY = {"score": 80, "sparse": True, "sleep_battery": {"last_wake_at": WAKE}}


def _session(name: str = "Leg Press", day: str = DAY, letter: str = "legs") -> Session:
    return Session(
        date=day,
        session_type=letter,
        closed_at=f"{day}T05:10:00-04:00",
        exercises=[
            ExerciseEntry(name=name, sets=[SetEntry(weight_lbs=40, sets=2, reps=8)])
        ],
    )


def _shapes(sessions):
    """The same sessions as objects (``Session``) and as dashboard dicts."""
    return {
        "object": list(sessions),
        "dict": [s.to_dict() for s in sessions],
    }


class FilterSessionsShapes(unittest.TestCase):
    def test_filter_sessions_accepts_both_shapes(self):
        for shape, sessions in _shapes([_session(), _session("Skeptic probe")]).items():
            with self.subTest(shape=shape):
                kept = filter_sessions(sessions)
                self.assertEqual(len(kept), 1)
                self.assertIsInstance(kept[0], Session)
                self.assertEqual([e.name for e in kept[0].exercises], ["Leg Press"])

    def test_filter_sessions_mixed_list_and_junk(self):
        mixed = [_session(), _session("Row", letter="pull").to_dict(), None, "x", 3]
        kept = filter_sessions(mixed)
        self.assertEqual([s.session_type for s in kept], ["legs", "pull"])

    def test_coerce_sessions_keeps_objects_and_closed_at(self):
        obj = _session()
        out = coerce_sessions([obj, obj.to_dict()])
        self.assertIs(out[0], obj)
        self.assertEqual(out[1].closed_at, obj.closed_at)
        self.assertEqual(coerce_sessions(None), [])


class ClosedTodayFillShapes(unittest.TestCase):
    def setUp(self):
        self.goals, _ = load_workspace_goals()

    def _stamp(self, sessions):
        stamped = stamp_today_session(
            {"exercises": [], "empty": True},
            sessions,
            self.goals,
            RECOVERY,
            as_of=DAY,
            now=NOW,
        )
        self.assertTrue(stamped.get("session_closed_today"))
        return stamped

    def test_deterministic_workout_accepts_both_shapes(self):
        for shape, sessions in _shapes([_session()]).items():
            with self.subTest(shape=shape):
                plan = _deterministic_workout(
                    {
                        "day": DAY,
                        "goals": self.goals,
                        "recovery": RECOVERY,
                        "sessions": sessions,
                        "now": NOW,
                    }
                )
                self.assertTrue(plan.get("exercises"))

    def test_fill_stamped_workout_after_close_accepts_both_shapes(self):
        base = [_session()]
        for shape, sessions in _shapes(base).items():
            with self.subTest(shape=shape):
                stamped = self._stamp(base)
                clear_memory_workout_plans()
                try:
                    with mock.patch(
                        "rt_dashboard.timeutil.local_now", return_value=NOW
                    ), mock.patch(
                        "rt_dashboard.turso_http.turso_enabled", return_value=False
                    ), mock.patch(
                        "rt_dashboard.gym_calendar.sync_gym_from_workout",
                        return_value={"ok": True, "skipped": True},
                    ):
                        filled = fill_stamped_workout(
                            f"sub-1240-{shape}",
                            day=DAY,
                            workout=stamped,
                            sessions=sessions,
                            goals=self.goals,
                            recovery=RECOVERY,
                        )
                finally:
                    clear_memory_workout_plans()
                self.assertIsNone(filled.get("generate_error"), filled.get("generate_error"))
                self.assertTrue(filled.get("exercises"))
                names = {str(ex.get("name") or "") for ex in filled["exercises"]}
                self.assertNotIn("Leg Press", names)


class AgentTodayEndpointAfterClose(unittest.TestCase):
    """HTTP ``GET /api/agent/today`` with a session closed today and dict sessions."""

    def setUp(self):
        self.goals, _ = load_workspace_goals()
        sessions = [_session().to_dict()]
        payload = {
            "coach": {"today": {"date": DAY}},
            "meta": {"local_today": DAY},
            "sessions": sessions,
            "workout_store": {"goals": self.goals, "plan": {}},
            "recovery": RECOVERY,
            "health": {
                "food_logs": [
                    {"date": DAY, "name": "Eggs", "calories": 180, "protein_g": 16}
                ]
            },
        }
        self._patches = [
            mock.patch.object(fitdash_server, "load_dashboard_data", return_value=payload),
            mock.patch.object(fitdash_server, "_auth_required", return_value=False),
            mock.patch("rt_dashboard.timeutil.local_now", return_value=NOW),
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
        for p in self._patches:
            p.start()
        clear_memory_workout_plans()
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), fitdash_server.DashboardHandler
        )
        self.port = int(self.httpd.server_address[1])
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        clear_memory_workout_plans()
        for p in reversed(self._patches):
            p.stop()

    def test_agent_today_200_with_nutrition_after_same_day_close(self):
        with urlopen(f"http://127.0.0.1:{self.port}/api/agent/today", timeout=20) as resp:
            status = int(resp.status)
            body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"), body)
        self.assertIn("nutrition", body["today"])
        self.assertEqual(body["today"]["nutrition"]["calories"], 180.0)
        self.assertIn("nutrition", body["week"])
        self.assertIn("workout", body["today"])
        self.assertFalse(body["today"]["workout"].get("is_rest_day"))


if __name__ == "__main__":
    unittest.main()
