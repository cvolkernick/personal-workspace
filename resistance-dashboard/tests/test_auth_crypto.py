"""Phase 1b: crypto box + user store + auth gating helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from rt_dashboard.crypto_box import load_or_create_master_key, open_str, seal_str
from rt_dashboard.user_store import UserStore
from rt_dashboard.workout_repo import WorkoutRepository
from rt_dashboard.models import ExerciseEntry, Session, SetEntry


class CryptoBoxTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "master.key"
            os.environ["FITDASH_MASTER_KEY_FILE"] = str(key_path)
            # reset module cache
            import rt_dashboard.crypto_box as cb

            cb._KEY = None
            key = load_or_create_master_key()
            self.assertEqual(len(key), 32)
            tok = seal_str('{"hi":1}', aad="user:abc")
            self.assertFalse(tok.startswith("{"))
            plain = open_str(tok, aad="user:abc")
            self.assertEqual(plain, '{"hi":1}')
            with self.assertRaises(ValueError):
                open_str(tok, aad="user:other")
            cb._KEY = None
            del os.environ["FITDASH_MASTER_KEY_FILE"]


class UserStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fitdash.db"
        os.environ["FITDASH_MASTER_KEY"] = "test-master-key-for-unit-tests-only!!"
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        self.store = UserStore(db_path=self.db)

    def tearDown(self) -> None:
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        os.environ.pop("FITDASH_MASTER_KEY", None)
        self.tmp.cleanup()

    def test_session_lifecycle(self) -> None:
        self.store.upsert_user_from_google(
            sub="google-sub-1",
            email="chris@example.com",
            display_name="Chris",
            health_refresh_token="1//refresh",
        )
        sid = self.store.create_session("google-sub-1")
        sess = self.store.resolve_session(sid)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["user_id"], "google-sub-1")
        self.assertEqual(sess["email"], "chris@example.com")
        tok = self.store.get_health_refresh_token("google-sub-1")
        self.assertEqual(tok, "1//refresh")
        self.store.destroy_session(sid)
        self.assertIsNone(self.store.resolve_session(sid))

    def test_list_users_with_health_token_newest_first(self) -> None:
        self.store.upsert_user_from_google(
            sub="older",
            email="old@example.com",
            display_name="Old",
            health_refresh_token="1//old",
        )
        self.store.upsert_user_from_google(
            sub="no-token",
            email="none@example.com",
            display_name="None",
        )
        self.store.upsert_user_from_google(
            sub="newer",
            email="new@example.com",
            display_name="New",
            health_refresh_token="1//new",
        )
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at=? WHERE id=?",
                ("2026-01-01T00:00:00Z", "older"),
            )
            conn.execute(
                "UPDATE users SET last_login_at=? WHERE id=?",
                ("2026-08-15T12:00:00Z", "newer"),
            )
            conn.commit()
        listed = self.store.list_users_with_health_token()
        ids = [u["id"] for u in listed]
        self.assertEqual(ids, ["newer", "older"])
        self.assertNotIn("no-token", ids)

    def test_claim_legacy_default(self) -> None:
        repo = WorkoutRepository(db_path=self.db, user_id="default")
        repo.upsert_session(
            Session(
                date="2026-07-01",
                session_type="push",
                exercises=[
                    ExerciseEntry("Press", [SetEntry(50, 3, 10)])
                ],
            )
        )
        self.store.upsert_user_from_google(
            sub="google-sub-2",
            email="u@example.com",
            display_name="U",
        )
        n = self.store.claim_legacy_default_workouts("google-sub-2")
        self.assertEqual(n, 1)
        mine = WorkoutRepository(db_path=self.db, user_id="google-sub-2")
        self.assertEqual(mine.count(), 1)
        self.assertEqual(WorkoutRepository(db_path=self.db, user_id="default").count(), 0)
        # Exercises must survive claim of *encrypted* default rows (not just count)
        sessions = mine.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].exercises[0].name, "Press")
        self.assertEqual(sessions[0].exercises[0].sets[0].weight_lbs, 50.0)
        # Disk must be sealed under new user AAD, not leftover default AAD
        import sqlite3

        raw = sqlite3.connect(str(self.db)).execute(
            "SELECT exercises_json FROM workout_sessions WHERE user_id='google-sub-2'"
        ).fetchone()[0]
        self.assertFalse(str(raw).lstrip().startswith("["))
        from rt_dashboard.crypto_box import open_str

        plain = open_str(str(raw), aad="user:google-sub-2:workout")
        self.assertIn("Press", plain)
        with self.assertRaises(ValueError):
            open_str(str(raw), aad="user:default:workout")


class EncryptedWorkoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fitdash.db"
        os.environ["FITDASH_MASTER_KEY"] = "another-test-master-key-32b-xxxx"
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        self.repo = WorkoutRepository(db_path=self.db, user_id="user-a")

    def tearDown(self) -> None:
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        os.environ.pop("FITDASH_MASTER_KEY", None)
        self.tmp.cleanup()

    def test_ciphertext_on_disk(self) -> None:
        self.repo.upsert_session(
            Session(
                date="2026-08-01",
                session_type="legs",
                exercises=[ExerciseEntry("Squat", [SetEntry(135, 3, 5)])],
            )
        )
        import sqlite3

        conn = sqlite3.connect(str(self.db))
        raw = conn.execute(
            "SELECT exercises_json FROM workout_sessions WHERE user_id='user-a'"
        ).fetchone()[0]
        conn.close()
        self.assertFalse(str(raw).lstrip().startswith("["), "expected encrypted blob")
        sessions = self.repo.list_sessions()
        self.assertEqual(sessions[0].exercises[0].name, "Squat")


if __name__ == "__main__":
    unittest.main()
