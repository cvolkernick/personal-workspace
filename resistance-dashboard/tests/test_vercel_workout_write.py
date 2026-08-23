"""Vercel POST /api/workouts writes to Turso. Not unconditional preview_read_only."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from pathlib import Path

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import dispatch_client_route, workouts_write
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.turso_http import TursoCursor, TursoRow
from rt_dashboard.workout_log import parse_log_body
from rt_dashboard.workout_repo import _row_to_session, _seal_exercises

JS = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
    encoding="utf-8"
)
UTIL = (
    Path(__file__).resolve().parents[1] / "api" / "workout" / "_util.py"
).read_text(encoding="utf-8")


UI_LOG_BODY = {
    "session_type": "push",
    "date": "2026-08-23",
    "notes": "public FitDash log",
    "exercises": [
        {
            "name": "DB Flat Press",
            "sets": [{"weight_lbs": 50, "sets": 3, "reps": 10}],
        }
    ],
}


class MemoryTurso:
    """In-memory workout_sessions store shaped like TursoConnection.execute."""

    def __init__(self):
        self.rows = {}  # (user_id, date, session_type) -> TursoRow

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql, params=()):
        norm = " ".join((sql or "").split()).upper()
        if norm.startswith("CREATE TABLE"):
            return TursoCursor([], [])
        if norm.startswith("UPDATE WORKOUT_SESSIONS"):
            notes, source, payload, uid, date, st = params
            key = (uid, date, st)
            existing = self.rows.get(key)
            if existing:
                existing["notes"] = notes
                existing["source_file"] = source
                existing["exercises_json"] = payload
            return TursoCursor([], [])
        if "INSERT INTO WORKOUT_SESSIONS" in norm:
            if len(params) >= 8:
                uid, date, st, notes, source, payload, created, updated = params[:8]
            else:
                uid, date, st, notes, source, payload = params[:6]
                created = updated = ""
            key = (uid, date, st)
            existing = self.rows.get(key)
            self.rows[key] = TursoRow(
                {
                    "id": existing["id"] if existing else len(self.rows) + 1,
                    "user_id": uid,
                    "date": date,
                    "session_type": st,
                    "notes": notes,
                    "source_file": source,
                    "exercises_json": payload,
                    "created_at": existing["created_at"] if existing else created,
                    "updated_at": updated,
                }
            )
            return TursoCursor([], [])
        if "FROM WORKOUT_SESSIONS" in norm and "AND DATE =" in norm:
            uid, date, st = params
            row = self.rows.get((uid, date, st))
            return TursoCursor(
                ["date", "session_type", "notes", "source_file", "exercises_json"],
                [row] if row else [],
            )
        if "FROM WORKOUT_SESSIONS" in norm:
            uid = params[0]
            found = [r for k, r in self.rows.items() if k[0] == uid]
            found.sort(key=lambda r: (r["session_type"] or ""))
            found.sort(key=lambda r: (r["date"] or ""), reverse=True)
            return TursoCursor(
                ["date", "session_type", "notes", "source_file", "exercises_json"],
                found,
            )
        raise AssertionError(f"unexpected sql: {sql}")


def _session_cookie():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class ParseLogBodyMatchesUi(unittest.TestCase):
    def test_nested_sets_from_app_js(self):
        session = parse_log_body(UI_LOG_BODY)
        self.assertEqual(session.date, "2026-08-23")
        self.assertEqual(session.session_type, "push")
        self.assertEqual(session.notes, "public FitDash log")
        self.assertEqual(session.exercises[0].name, "DB Flat Press")
        self.assertEqual(session.exercises[0].sets[0].weight_lbs, 50)
        self.assertEqual(session.exercises[0].sets[0].sets, 3)
        self.assertEqual(session.exercises[0].sets[0].reps, 10)

    def test_flat_form_from_readme(self):
        session = parse_log_body(
            {
                "session_type": "pull",
                "date": "2026-08-22",
                "exercises": [
                    {
                        "name": "Seated Cable Row",
                        "weight_lbs": 105,
                        "sets": 1,
                        "reps": 10,
                    }
                ],
            }
        )
        self.assertEqual(session.session_type, "pull")
        self.assertEqual(session.exercises[0].sets[0].weight_lbs, 105)

    def test_rejects_bad_session_type(self):
        with self.assertRaises(ValueError):
            parse_log_body({"session_type": "arms", "date": "2026-08-23", "exercises": []})


class TursoUpsertSession(unittest.TestCase):
    def setUp(self):
        self.store = MemoryTurso()
        os.environ["FITDASH_MASTER_KEY"] = "test-master-key-for-unit-tests-only!!"
        import rt_dashboard.crypto_box as cb

        cb._KEY = None

    def tearDown(self):
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        os.environ.pop("FITDASH_MASTER_KEY", None)

    def _session(self, weight=50.0):
        return Session(
            date="2026-08-23",
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="DB Flat Press",
                    sets=[SetEntry(weight_lbs=weight, sets=3, reps=10)],
                )
            ],
            notes="unit",
        )

    def test_upsert_seals_and_lists_back(self):
        from rt_dashboard import turso_repo

        with mock.patch.object(turso_repo, "turso_enabled", return_value=True), mock.patch.object(
            turso_repo, "connect", return_value=self.store
        ):
            result = turso_repo.upsert_session("sub-1", self._session())
            sessions, notes = turso_repo.list_sessions_detailed("sub-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["backend"], "turso")
        self.assertEqual(result["path"], "turso")
        self.assertTrue(result["verified_on_readback"])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].exercises[0].name, "DB Flat Press")
        self.assertEqual(sessions[0].exercises[0].sets[0].weight_lbs, 50)
        self.assertFalse(notes)
        raw = self.store.rows[("sub-1", "2026-08-23", "push")]["exercises_json"]
        self.assertFalse(str(raw).lstrip().startswith("["), "expected sealed blob")

    def test_upsert_same_key_replaces(self):
        from rt_dashboard import turso_repo

        with mock.patch.object(turso_repo, "turso_enabled", return_value=True), mock.patch.object(
            turso_repo, "connect", return_value=self.store
        ):
            turso_repo.upsert_session("sub-1", self._session(50))
            turso_repo.upsert_session("sub-1", self._session(55))
            sessions, _ = turso_repo.list_sessions_detailed("sub-1")
        self.assertEqual(len(self.store.rows), 1)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].exercises[0].sets[0].weight_lbs, 55)

    def test_upsert_without_turso_raises(self):
        from rt_dashboard import turso_repo

        with mock.patch.object(turso_repo, "turso_enabled", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                turso_repo.upsert_session("sub-1", self._session())
        self.assertIn("turso env missing", str(ctx.exception))


class WorkoutsWriteRoute(unittest.TestCase):
    def setUp(self):
        os.environ["FITDASH_MASTER_KEY"] = "test-master-key-for-unit-tests-only!!"
        import rt_dashboard.crypto_box as cb

        cb._KEY = None

    def tearDown(self):
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        os.environ.pop("FITDASH_MASTER_KEY", None)

    def test_cookie_less_is_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = workouts_write({}, UI_LOG_BODY)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotEqual(body.get("error"), "preview_read_only")

    def test_signed_in_without_turso_is_clear_error(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ):
                status, body = workouts_write(_session_cookie(), UI_LOG_BODY)
        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "turso_env_missing")
        self.assertIn("TURSO", body["message"])
        self.assertNotEqual(body["error"], "preview_read_only")
        self.assertNotIn("Pi FitDash", body["message"])

    def test_signed_in_invalid_body_is_400(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ):
                status, body = workouts_write(
                    _session_cookie(),
                    {"session_type": "push", "date": "2026-08-23"},
                )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("exercises", body["error"])

    def test_signed_in_write_is_200_and_readback_shows_session(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        store = MemoryTurso()

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.turso_repo.turso_enabled", return_value=True
            ), mock.patch(
                "rt_dashboard.turso_repo.connect", return_value=store
            ):
                status, body = workouts_write(_session_cookie(), UI_LOG_BODY)
                again, again_body = workouts_write(_session_cookie(), UI_LOG_BODY)
                from api.workout._util import workouts_body

                get_status, get_body = workouts_body(_session_cookie())
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertNotEqual(body.get("error"), "preview_read_only")
        self.assertEqual(body["write"]["backend"], "turso")
        self.assertEqual(body["write"]["path"], "turso")
        self.assertTrue(body["write"]["verified_on_readback"])
        self.assertEqual(body["session"]["exercises"][0]["name"], "DB Flat Press")
        self.assertEqual(body["session_count"], 1)
        self.assertEqual(again, 200)
        self.assertEqual(again_body["session_count"], 1)
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(get_status, 200)
        self.assertEqual(get_body["session_count"], 1)
        self.assertEqual(get_body["sessions"][0]["exercises"][0]["name"], "DB Flat Press")
        self.assertFalse(get_body["readonly"])

    def test_util_workouts_write_is_not_a_denied_stub(self):
        write_fn = UTIL.split("def workouts_write", 1)[1].split("def generate_body", 1)[0]
        self.assertNotIn("return _write_denied", write_fn)
        self.assertIn("save_preview_session", write_fn)
        self.assertIn("turso_env_missing", write_fn)

    def test_ui_prefers_message_on_log_failure(self):
        submit = JS.split("async function submitWorkout", 1)[1].split(
            "async function submitIngredient", 1
        )[0]
        self.assertIn("data.message || data.error", submit)
        self.assertIn('fetch("/api/workouts"', submit)

    def test_dispatch_post_workouts_is_not_always_403(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = dispatch_client_route(
                _session_cookie(),
                "_r=workouts",
                "POST",
                payload=UI_LOG_BODY,
            )
        self.assertNotEqual(status, 403)
        self.assertNotEqual(body.get("error"), "preview_read_only")


class SealedRowMapping(unittest.TestCase):
    def setUp(self):
        os.environ["FITDASH_MASTER_KEY"] = "test-master-key-for-unit-tests-only!!"
        import rt_dashboard.crypto_box as cb

        cb._KEY = None

    def tearDown(self):
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        os.environ.pop("FITDASH_MASTER_KEY", None)

    def test_seal_roundtrip_matches_sqlite_aad(self):
        session = Session(
            date="2026-08-23",
            session_type="legs",
            exercises=[ExerciseEntry("Squat", [SetEntry(135, 3, 5)])],
        )
        blob = _seal_exercises("sub-1", session.exercises)
        row = TursoRow(
            {
                "date": session.date,
                "session_type": session.session_type,
                "notes": "",
                "source_file": "",
                "exercises_json": blob,
            }
        )
        read = _row_to_session(row, "sub-1")
        self.assertEqual(read.exercises[0].name, "Squat")
        self.assertFalse(blob.lstrip().startswith("["))


if __name__ == "__main__":
    unittest.main()
