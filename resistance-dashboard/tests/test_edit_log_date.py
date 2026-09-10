"""Edit civil date on an existing workout log (#543)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import dispatch_client_route, workouts_date_write
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_log import plan_session_date_move
from rt_dashboard.workout_planner import next_session_type, ppl_logged_on_day
from rt_dashboard.workout_repo import WorkoutRepository
from rt_dashboard.workout_store import load_workspace_goals

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _legs(date="2026-09-09", closed_at="2026-09-09T00:30:00-04:00"):
    return Session(
        date=date,
        session_type="legs",
        exercises=[
            ExerciseEntry(
                name="RDL",
                sets=[SetEntry(weight_lbs=40, sets=2, reps=7)],
            )
        ],
        notes="late night",
        closed_at=closed_at,
    )


def _cookie():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class PlanSessionDateMove(unittest.TestCase):
    def test_moves_civil_date_keeps_sets_and_closed_at(self):
        src = _legs()
        moved = plan_session_date_move(
            [src],
            session_type="legs",
            from_date="2026-09-09",
            to_date="2026-09-08",
        )
        self.assertEqual(moved.date, "2026-09-08")
        self.assertEqual(moved.session_type, "legs")
        self.assertEqual(moved.exercises[0].name, "RDL")
        self.assertEqual(moved.exercises[0].sets[0].weight_lbs, 40)
        self.assertEqual(moved.exercises[0].sets[0].sets, 2)
        self.assertEqual(moved.exercises[0].sets[0].reps, 7)
        self.assertEqual(moved.closed_at, "2026-09-09T00:30:00-04:00")
        self.assertEqual(moved.notes, "late night")

    def test_missing_session_errors(self):
        with self.assertRaisesRegex(ValueError, "session not found"):
            plan_session_date_move(
                [],
                session_type="legs",
                from_date="2026-09-09",
                to_date="2026-09-08",
            )

    def test_occupied_target_errors(self):
        tue = _legs("2026-09-09")
        mon = _legs("2026-09-08")
        mon.exercises[0].sets[0].weight_lbs = 35
        with self.assertRaisesRegex(ValueError, "already has that session"):
            plan_session_date_move(
                [tue, mon],
                session_type="legs",
                from_date="2026-09-09",
                to_date="2026-09-08",
            )

    def test_same_date_is_identity(self):
        src = _legs()
        moved = plan_session_date_move(
            [src],
            session_type="legs",
            from_date="2026-09-09",
            to_date="2026-09-09",
        )
        self.assertEqual(moved.date, "2026-09-09")
        self.assertEqual(moved.exercises[0].sets[0].weight_lbs, 40)

    def test_invalid_date(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            plan_session_date_move(
                [_legs()],
                session_type="legs",
                from_date="09/09/2026",
                to_date="2026-09-08",
            )


class RelocateClearsOldDay(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fitdash.db"
        self.repo = WorkoutRepository(db_path=self.db, user_id="test-user")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tue_legs_to_mon_clears_tue_and_keeps_sets(self):
        self.repo.upsert_session(_legs("2026-09-09"))
        moved = plan_session_date_move(
            self.repo.list_sessions(),
            session_type="legs",
            from_date="2026-09-09",
            to_date="2026-09-08",
        )
        self.repo.relocate_session(moved, "2026-09-09")
        sessions = self.repo.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].date, "2026-09-08")
        self.assertEqual(sessions[0].exercises[0].sets[0].weight_lbs, 40)
        self.assertEqual(sessions[0].closed_at, "2026-09-09T00:30:00-04:00")
        self.assertIsNone(ppl_logged_on_day(sessions, "2026-09-09"))
        self.assertEqual(ppl_logged_on_day(sessions, "2026-09-08"), "legs")
        goals, _ = load_workspace_goals()
        self.assertEqual(next_session_type(sessions, goals), "push")


class WorkoutsDateWrite(unittest.TestCase):
    def test_cookie_less_401(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = workouts_date_write(
                {},
                {
                    "session_type": "legs",
                    "from_date": "2026-09-09",
                    "to_date": "2026-09-08",
                },
            )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")

    def test_move_updates_old_and_new_day_letters(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        history = [_legs()]

        def fake_load(_uid):
            return list(history), [], "turso"

        def fake_reloc(_uid, session, from_date):
            history[:] = [s for s in history if not (
                s.date[:10] == str(from_date)[:10]
                and s.session_type == session.session_type
            )]
            history.append(session)
            return {
                "ok": True,
                "backend": "turso",
                "path": "turso",
                "verified_on_readback": True,
            }

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions", side_effect=fake_load
            ), mock.patch(
                "rt_dashboard.turso_repo.relocate_session", side_effect=fake_reloc
            ):
                status, body = workouts_date_write(
                    _cookie(),
                    {
                        "session_type": "legs",
                        "from_date": "2026-09-09",
                        "to_date": "2026-09-08",
                    },
                )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["session"]["date"], "2026-09-08")
        self.assertEqual(body["session"]["exercises"][0]["sets"][0]["weight_lbs"], 40)
        self.assertIsNone(body["old_day_logged"])
        self.assertEqual(body["new_day_logged"], "legs")
        self.assertEqual(body["next_session_type"], "push")

    def test_not_found_is_404(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions", return_value=([], [], "turso")
            ):
                status, body = workouts_date_write(
                    _cookie(),
                    {
                        "session_type": "legs",
                        "from_date": "2026-09-09",
                        "to_date": "2026-09-08",
                    },
                )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "session not found")

    def test_dispatch_get_is_405_post_is_not_log_upsert(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = dispatch_client_route(
                {}, "", "GET", path="/api/workouts/date"
            )
        self.assertEqual(status, 405)
        self.assertEqual(body["error"], "method_not_allowed")
        util = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")
        chunk = util.split("def workouts_date_write", 1)[1].split(
            "def agent_generate_plan_body", 1
        )[0]
        self.assertIn("plan_session_date_move", chunk)
        self.assertNotIn("merge_log_with_history", chunk)
        self.assertNotIn("parse_log_body", chunk)


class HistoryDateMarkup(unittest.TestCase):
    def test_history_has_in_place_date_picker(self):
        hist = HTML[HTML.find('id="history-card"') : HTML.find('id="charts-volume-strength"')]
        self.assertIn("Change the civil date of a logged session in place", hist)
        self.assertIn('id="session-list"', hist)
        self.assertIn("hist-date", APP_JS)
        self.assertIn("hist-date-save", APP_JS)
        self.assertIn('fetch("/api/workouts/date"', APP_JS)
        self.assertIn("function submitHistoryDate", APP_JS)
        self.assertIn('type="date"', APP_JS)
        self.assertNotIn("delete + re-log", APP_JS)

    def test_cache_bumped(self):
        self.assertIn("/app.js?v=phase-baro-home-1", HTML)
        self.assertIn("/app.js?v=phase-baro-home-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v94"', SW)
        self.assertNotIn("fitdash-shell-v90", SW)
        self.assertNotIn("fitdash-shell-v89", SW)
        self.assertNotIn("fitdash-shell-v88", SW)
        self.assertNotIn("fitdash-shell-v87", SW)
        self.assertNotIn("/app.js?v=library-add-1", HTML)
        self.assertNotIn("/app.js?v=library-add-1", SW)
        self.assertNotIn("fitdash-shell-v82", SW)


if __name__ == "__main__":
    unittest.main()
