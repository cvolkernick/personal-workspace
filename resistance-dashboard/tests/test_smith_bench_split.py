"""Smith Bench splits into flat + incline, and historical logs rename in place."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rt_dashboard.crypto_box import open_str
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.smith_bench_migrate import (
    NEW_ID,
    NEW_NAME,
    is_legacy_smith_bench_name,
    migrate_sqlite,
    migrate_turso,
    rewrite_custom_payload,
    rewrite_exercise_list,
    rewrite_overlay,
    rewrite_stored_exercises,
)
from rt_dashboard.workout_planner import (
    INCLINE_PRESS_FAMILY,
    NAME_ALIASES,
    load_json_file,
    pattern_family,
    session_types_for_lift_name,
)
from rt_dashboard.workout_repo import WorkoutRepository
from rt_dashboard.workout_store import load_workspace_catalog


def _exercise(name: str, **extra):
    row = {
        "name": name,
        "sets": [{"weight_lbs": 50.0, "sets": 3, "reps": 10}, {"weight_lbs": 45.0, "sets": 1, "reps": 8}],
        "is_pr": True,
        "raw": f"{name}: 50 lbs x 3 x 10",
        "quest_seeded": True,
        "note": "keep-me",
    }
    row.update(extra)
    return row


class Rewrite(unittest.TestCase):
    def test_legacy_name_only(self):
        self.assertTrue(is_legacy_smith_bench_name("Smith Bench"))
        self.assertTrue(is_legacy_smith_bench_name("  smith   bench "))
        self.assertFalse(is_legacy_smith_bench_name("Smith Flat Bench"))
        self.assertFalse(is_legacy_smith_bench_name("Smith Incline Bench"))
        self.assertFalse(is_legacy_smith_bench_name("Smith Bench Press"))

    def test_exercise_list_preserves_sets_and_flags(self):
        original = [
            _exercise("Smith Bench"),
            _exercise("DB Flat Press"),
            _exercise("Smith Incline Bench"),
        ]
        rewritten, n = rewrite_exercise_list(original)
        self.assertEqual(n, 1)
        self.assertEqual(original[0]["name"], "Smith Bench")
        self.assertEqual(rewritten[0]["name"], NEW_NAME)
        self.assertEqual(rewritten[0]["sets"], original[0]["sets"])
        self.assertTrue(rewritten[0]["is_pr"])
        self.assertEqual(rewritten[0]["raw"], "Smith Bench: 50 lbs x 3 x 10")
        self.assertTrue(rewritten[0]["quest_seeded"])
        self.assertEqual(rewritten[0]["note"], "keep-me")
        self.assertEqual(rewritten[1]["name"], "DB Flat Press")
        self.assertEqual(rewritten[2]["name"], "Smith Incline Bench")
        again, n2 = rewrite_exercise_list(rewritten)
        self.assertEqual(n2, 0)
        self.assertEqual(again[0]["name"], NEW_NAME)

    def test_custom_and_overlay(self):
        custom, n = rewrite_custom_payload(
            {
                "exercises": [
                    {"id": "smith-bench", "name": "Smith Bench", "priority": 8, "available": True},
                    {"id": "smith-flat-bench", "name": "Smith Flat Bench", "priority": 1},
                ]
            }
        )
        self.assertEqual(n, 1)
        ids = [ex["id"] for ex in custom["exercises"]]
        self.assertEqual(ids, ["smith-flat-bench"])
        self.assertEqual(custom["exercises"][0]["name"], NEW_NAME)
        self.assertEqual(custom["exercises"][0]["priority"], 8)
        again, n2 = rewrite_custom_payload(custom)
        self.assertEqual(n2, 0)

        overlay, n3 = rewrite_overlay(
            {"enabled": ["db-flat-press", "smith-bench"], "disabled": ["smith-bench", "db-row"]}
        )
        self.assertGreaterEqual(n3, 1)
        self.assertEqual(overlay["enabled"], ["db-flat-press"])
        self.assertEqual(overlay["disabled"], [NEW_ID, "db-row"])
        overlay2, n4 = rewrite_overlay(overlay)
        self.assertEqual(n4, 0)
        self.assertEqual(overlay2, overlay)


class Catalog(unittest.TestCase):
    def test_shipped_catalog_has_both_presses(self):
        root = Path(__file__).resolve().parents[2] / "fitness" / "exercises" / "catalog.json"
        bundled = Path(__file__).resolve().parents[1] / "fitness" / "exercises" / "catalog.json"
        self.assertEqual(root.read_text(encoding="utf-8"), bundled.read_text(encoding="utf-8"))
        catalog = load_json_file(root, {})
        by_id = {ex["id"]: ex for ex in catalog["exercises"]}
        self.assertNotIn("smith-bench", by_id)
        flat = by_id["smith-flat-bench"]
        incline = by_id["smith-incline-bench"]
        self.assertEqual(flat["name"], "Smith Flat Bench")
        self.assertEqual(incline["name"], "Smith Incline Bench")
        for ex in (flat, incline):
            self.assertEqual(ex["session_types"], ["push"])
            self.assertEqual(ex["movement"], "compound")
            self.assertEqual(ex["equipment"], ["smith_machine"])
            self.assertEqual(ex["default_sets"], 3)
            self.assertEqual(ex["default_reps"], 8)
            self.assertEqual(ex["rep_range"], [6, 10])
            self.assertTrue(ex["available"])
        self.assertEqual(flat["primary_muscles"], ["chest"])
        self.assertEqual(flat["secondary_muscles"], ["triceps", "shoulders"])
        self.assertEqual(flat["priority"], 8)
        self.assertEqual(incline["primary_muscles"], ["chest"])
        self.assertEqual(incline["secondary_muscles"], ["shoulders"])
        loaded, _src = load_workspace_catalog()
        loaded_ids = {ex["id"] for ex in loaded["exercises"]}
        self.assertIn("smith-flat-bench", loaded_ids)
        self.assertIn("smith-incline-bench", loaded_ids)
        self.assertNotIn("smith-bench", loaded_ids)

    def test_aliases_resolve_free_text(self):
        self.assertEqual(NAME_ALIASES["smith bench"], "smith-flat-bench")
        self.assertEqual(NAME_ALIASES["smith flat bench"], "smith-flat-bench")
        self.assertEqual(NAME_ALIASES["smith incline bench"], "smith-incline-bench")
        self.assertEqual(session_types_for_lift_name("smith bench"), ("push",))
        self.assertEqual(session_types_for_lift_name("smith incline bench"), ("push",))
        self.assertEqual(
            pattern_family({"id": "smith-incline-bench", "name": "Smith Incline Bench", "movement": "compound", "primary_muscles": ["chest"]}),
            INCLINE_PRESS_FAMILY,
        )


class SqliteMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fitdash.db"
        self.repo = WorkoutRepository(db_path=self.db, user_id="default")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sealed_and_plaintext_rows_rename_once(self):
        self.repo.upsert_session(
            Session(
                date="2026-03-27",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="Smith Bench",
                        sets=[SetEntry(weight_lbs=50, sets=3, reps=10)],
                        is_pr=True,
                        raw="Smith Bench: 50 lbs x 3 x 10",
                        quest_seeded=False,
                    ),
                    ExerciseEntry(
                        name="DB Flat Press",
                        sets=[SetEntry(weight_lbs=35, sets=3, reps=10)],
                        is_pr=False,
                        raw="DB Flat Press: 35 lbs x 3 x 10",
                    ),
                ],
                notes="keep-notes",
                source_file="fitness/workouts/push.md",
            )
        )
        plain = json.dumps(
            [
                _exercise("SMITH BENCH"),
                _exercise("Smith Incline Bench"),
            ],
            separators=(",", ":"),
        )
        with sqlite3.connect(self.db) as conn:
            before = conn.execute("SELECT COUNT(*) FROM workout_sessions").fetchone()[0]
            conn.execute(
                """
                INSERT INTO workout_sessions(
                  user_id, date, session_type, notes, source_file,
                  exercises_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "default",
                    "2026-03-14",
                    "push",
                    "plain",
                    "",
                    plain,
                    "2026-03-14T00:00:00Z",
                    "2026-03-14T00:00:00Z",
                ),
            )
        first = migrate_sqlite(self.db)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["renamed_rows"], 2)
        self.assertEqual(first["session_count"], before + 1)
        self.assertTrue(Path(first["backup"]).is_file())
        names = sorted(p.name for p in Path(self.tmp.name).iterdir())
        backups = [
            p
            for p in Path(self.tmp.name).glob("*.bak-smith-bench-922-*")
            if not p.name.endswith("-wal") and not p.name.endswith("-shm")
        ]
        self.assertEqual(len(backups), 1, names)

        sessions = self.repo.list_sessions()
        by_date = {s.date: s for s in sessions}
        sealed = by_date["2026-03-27"]
        self.assertEqual(sealed.notes, "keep-notes")
        self.assertEqual(sealed.exercises[0].name, NEW_NAME)
        self.assertEqual(sealed.exercises[0].sets[0].weight_lbs, 50)
        self.assertEqual(sealed.exercises[0].sets[0].sets, 3)
        self.assertEqual(sealed.exercises[0].sets[0].reps, 10)
        self.assertTrue(sealed.exercises[0].is_pr)
        self.assertEqual(sealed.exercises[0].raw, "Smith Bench: 50 lbs x 3 x 10")
        self.assertEqual(sealed.exercises[1].name, "DB Flat Press")
        self.assertEqual(sealed.exercises[1].sets[0].weight_lbs, 35)

        with sqlite3.connect(self.db) as conn:
            after = conn.execute("SELECT COUNT(*) FROM workout_sessions").fetchone()[0]
            blob = conn.execute(
                """
                SELECT exercises_json FROM workout_sessions
                WHERE date = ? AND session_type = ?
                """,
                ("2026-03-14", "push"),
            ).fetchone()[0]
        self.assertEqual(after, before + 1)
        plain_rows = json.loads(blob)
        self.assertEqual(plain_rows[0]["name"], NEW_NAME)
        self.assertEqual(plain_rows[0]["sets"][1]["weight_lbs"], 45.0)
        self.assertTrue(plain_rows[0]["is_pr"])
        self.assertEqual(plain_rows[0]["raw"], "SMITH BENCH: 50 lbs x 3 x 10")
        self.assertTrue(plain_rows[0]["quest_seeded"])
        self.assertEqual(plain_rows[0]["note"], "keep-me")
        self.assertEqual(plain_rows[1]["name"], "Smith Incline Bench")

        bak = sqlite3.connect(backups[0])
        try:
            old = bak.execute(
                "SELECT exercises_json FROM workout_sessions WHERE date = '2026-03-27'"
            ).fetchone()[0]
        finally:
            bak.close()
        old_plain = open_str(old, aad="user:default:workout")
        self.assertIn("Smith Bench", old_plain)
        self.assertNotIn("Smith Flat Bench", old_plain)

        log = Path(first["log"]).read_text(encoding="utf-8")
        self.assertIn("2026-03-27", log)
        self.assertIn("2026-03-14", log)

        stored = blob
        second = migrate_sqlite(self.db)
        self.assertTrue(second["ok"], second)
        self.assertTrue(second.get("noop"))
        self.assertEqual(second["renamed_rows"], 0)
        backups_after = [
            p
            for p in Path(self.tmp.name).glob("*.bak-smith-bench-922-*")
            if not p.name.endswith("-wal") and not p.name.endswith("-shm")
        ]
        self.assertEqual(len(backups_after), 1, sorted(p.name for p in Path(self.tmp.name).iterdir()))
        with sqlite3.connect(self.db) as conn:
            again = conn.execute(
                "SELECT exercises_json FROM workout_sessions WHERE date = '2026-03-14'"
            ).fetchone()[0]
        self.assertEqual(again, stored)

    def test_unreadable_row_is_left_alone(self):
        self.repo.upsert_session(
            Session(
                date="2026-03-21",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="Smith Bench",
                        sets=[SetEntry(weight_lbs=45, sets=3, reps=10)],
                        raw="Smith Bench: 45 lbs x 3 x 10",
                    )
                ],
            )
        )
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """
                INSERT INTO workout_sessions(
                  user_id, date, session_type, notes, source_file,
                  exercises_json, created_at, updated_at
                ) VALUES ('default', '2026-01-01', 'push', '', '', 'not-a-cipher', 't', 't')
                """
            )
            junk = conn.execute(
                "SELECT exercises_json FROM workout_sessions WHERE date = '2026-01-01'"
            ).fetchone()[0]
        result = migrate_sqlite(self.db)
        self.assertFalse(result["ok"])
        self.assertEqual(result["decrypt_failures"], 1)
        self.assertEqual(result["renamed_rows"], 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workout_sessions").fetchone()[0], 2)
            self.assertEqual(
                conn.execute(
                    "SELECT exercises_json FROM workout_sessions WHERE date = '2026-01-01'"
                ).fetchone()[0],
                junk,
            )
            stamp = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'smith_bench_split_922'"
            ).fetchone()
        self.assertIsNone(stamp)
        renamed = rewrite_stored_exercises("not-a-cipher", "default")
        self.assertIsNone(renamed[0])
        self.assertTrue(renamed[2])


class TursoMigration(unittest.TestCase):
    def test_turso_shaped_connection_renames_and_backs_up(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "turso.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE workout_sessions (
              user_id TEXT, date TEXT, session_type TEXT,
              exercises_json TEXT, notes TEXT, source_file TEXT,
              created_at TEXT, updated_at TEXT
            )
            """
        )
        plain = json.dumps([_exercise("smith bench")], separators=(",", ":"))
        conn.execute(
            "INSERT INTO workout_sessions(user_id, date, session_type, exercises_json) VALUES (?, ?, ?, ?)",
            ("default", "2026-03-27", "push", plain),
        )
        conn.execute(
            """
            CREATE TABLE custom_movements (user_id TEXT PRIMARY KEY, payload TEXT, updated_at TEXT)
            """
        )
        conn.execute(
            "INSERT INTO custom_movements(user_id, payload, updated_at) VALUES (?, ?, ?)",
            (
                "default",
                json.dumps({"exercises": [{"id": "smith-bench", "name": "Smith Bench", "priority": 8}]}),
                "t",
            ),
        )
        conn.execute(
            """
            CREATE TABLE exercise_library (user_id TEXT PRIMARY KEY, payload TEXT, updated_at TEXT)
            """
        )
        conn.execute(
            "INSERT INTO exercise_library(user_id, payload, updated_at) VALUES (?, ?, ?)",
            ("default", json.dumps({"enabled": ["smith-bench"], "disabled": []}), "t"),
        )
        conn.commit()

        class _CM:
            def __enter__(self):
                return conn

            def __exit__(self, *args):
                return False

        first = migrate_turso(lambda: _CM())
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["renamed_rows"], 1)
        self.assertEqual(first["session_count"], 1)
        row = conn.execute("SELECT exercises_json FROM workout_sessions").fetchone()
        self.assertEqual(json.loads(row["exercises_json"])[0]["name"], NEW_NAME)
        self.assertEqual(json.loads(row["exercises_json"])[0]["note"], "keep-me")
        backed = conn.execute(f"SELECT exercises_json FROM workout_sessions_bak_smith_922").fetchone()
        self.assertEqual(json.loads(backed["exercises_json"])[0]["name"], "smith bench")
        custom = json.loads(conn.execute("SELECT payload FROM custom_movements").fetchone()["payload"])
        self.assertEqual(custom["exercises"][0]["id"], NEW_ID)
        self.assertEqual(custom["exercises"][0]["name"], NEW_NAME)
        overlay = json.loads(conn.execute("SELECT payload FROM exercise_library").fetchone()["payload"])
        self.assertEqual(overlay["enabled"], [NEW_ID])
        second = migrate_turso(lambda: _CM())
        self.assertTrue(second["ok"], second)
        self.assertTrue(second.get("noop"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM workout_sessions").fetchone()[0], 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
