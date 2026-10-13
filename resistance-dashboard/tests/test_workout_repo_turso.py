"""get_repo() picks the Turso-backed repo when Turso env is set (fake data)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from rt_dashboard import turso_repo
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_repo import (
    TursoWorkoutRepository,
    WorkoutRepository,
    get_repo,
)

FAKE_ENV = {
    "TURSO_DATABASE_URL": "libsql://fake-db.example.test",
    "TURSO_AUTH_TOKEN": "fake-token",
}


def _session(date: str = "2026-01-02", st: str = "push") -> Session:
    return Session(
        date=date,
        session_type=st,
        exercises=[
            ExerciseEntry(
                name="Test Press",
                sets=[SetEntry(weight_lbs=10, sets=1, reps=1)],
                is_pr=False,
                raw="- Test Press: 10 lbs x 1 x 1",
            )
        ],
        notes="fake",
        source_file="",
    )


class GetRepoSelectionTests(unittest.TestCase):
    def test_turso_env_selects_turso_repo(self):
        with mock.patch.dict(os.environ, FAKE_ENV, clear=False):
            repo = get_repo(user_id="fake-sub-123")
        self.assertIsInstance(repo, TursoWorkoutRepository)
        self.assertEqual(repo.user_id, "fake-sub-123")

    def test_no_turso_env_selects_sqlite(self):
        env = {k: "" for k in FAKE_ENV}
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {**env, "FITDASH_DB_PATH": str(Path(tmp) / "t.db")}
        ):
            self.assertIsInstance(get_repo(user_id="u"), WorkoutRepository)

    def test_explicit_db_path_keeps_sqlite(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, FAKE_ENV):
            repo = get_repo(db_path=Path(tmp) / "t.db", user_id="u")
        self.assertIsInstance(repo, WorkoutRepository)

    def test_default_user_id_from_env(self):
        with mock.patch.dict(os.environ, {**FAKE_ENV, "FITDASH_USER_ID": "fake-sub-9"}):
            self.assertEqual(get_repo().user_id, "fake-sub-9")


class TursoRepoDelegationTests(unittest.TestCase):
    def setUp(self):
        self.repo = TursoWorkoutRepository(user_id="fake-sub-123")

    def test_list_and_count(self):
        rows = [_session(), _session("2026-01-03", "pull")]
        with mock.patch.object(turso_repo, "list_sessions", return_value=rows) as m:
            self.assertEqual(self.repo.list_sessions(), rows)
            self.assertEqual(self.repo.count(), 2)
        m.assert_called_with("fake-sub-123")

    def test_upsert_relocate_delete(self):
        s = _session()
        with mock.patch.object(turso_repo, "upsert_session", return_value={"ok": True}) as up:
            self.assertEqual(self.repo.upsert_session(s), {"ok": True})
        up.assert_called_once_with("fake-sub-123", s)
        with mock.patch.object(turso_repo, "relocate_session", return_value={"ok": True}) as rl:
            self.repo.relocate_session(s, "2026-01-01")
        rl.assert_called_once_with("fake-sub-123", s, "2026-01-01")
        with mock.patch.object(turso_repo, "delete_session", return_value=True) as dl:
            self.assertTrue(self.repo.delete_session("2026-01-02", "push"))
        dl.assert_called_once_with("fake-sub-123", "2026-01-02", "push")

    def test_never_seeds_or_bulk_imports(self):
        with mock.patch.object(turso_repo, "list_sessions", return_value=[]), mock.patch.object(
            turso_repo, "upsert_session"
        ) as up:
            res = self.repo.ensure_seeded_from_workspace("/nonexistent")
            self.assertFalse(res["seeded"])
            self.assertFalse(self.repo.import_from_markdown_dir("/nonexistent")["ok"])
            self.assertFalse(self.repo.import_sessions([_session()])["ok"])
        up.assert_not_called()


if __name__ == "__main__":
    unittest.main()
