"""Phase 1a: SQLite workout repository."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.workout_repo import WorkoutRepository


class WorkoutRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fitdash.db"
        self.repo = WorkoutRepository(db_path=self.db, user_id="test-user")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _session(self, date: str = "2026-08-01", st: str = "push") -> Session:
        return Session(
            date=date,
            session_type=st,
            exercises=[
                ExerciseEntry(
                    name="DB Flat Press",
                    sets=[SetEntry(weight_lbs=50, sets=3, reps=10)],
                    is_pr=False,
                    raw="- DB Flat Press: 50 lbs x 3 x 10",
                )
            ],
            notes="unit test",
            source_file="fitness/workouts/push.md",
        )

    def test_upsert_and_list(self) -> None:
        w = self.repo.upsert_session(self._session())
        self.assertTrue(w["ok"])
        self.assertEqual(w["backend"], "sqlite")
        self.assertEqual(self.repo.count(), 1)
        sessions = self.repo.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].date, "2026-08-01")
        self.assertEqual(sessions[0].exercises[0].name, "DB Flat Press")
        self.assertEqual(sessions[0].exercises[0].sets[0].weight_lbs, 50)

    def test_upsert_replaces_same_day_type(self) -> None:
        self.repo.upsert_session(self._session())
        s2 = self._session()
        s2.exercises[0].sets[0].weight_lbs = 55
        self.repo.upsert_session(s2)
        self.assertEqual(self.repo.count(), 1)
        self.assertEqual(self.repo.list_sessions()[0].exercises[0].sets[0].weight_lbs, 55)

    def test_import_from_markdown_fixture(self) -> None:
        root = Path(self.tmp.name) / "ws"
        wdir = root / "fitness" / "workouts"
        wdir.mkdir(parents=True)
        fixture = (
            Path(__file__).resolve().parent / "fixtures" / "sample_push.md"
        )
        if fixture.is_file():
            (wdir / "push.md").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            (wdir / "push.md").write_text(
                "# Push\n\n## August 1, 2026 - Push\n\n- DB Flat Press: 50 lbs x 3 x 10\n",
                encoding="utf-8",
            )
        result = self.repo.import_from_markdown_dir(root)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["imported"], 1)
        self.assertGreaterEqual(self.repo.count(), 1)

    def test_ensure_seeded_once(self) -> None:
        root = Path(self.tmp.name) / "ws2"
        wdir = root / "fitness" / "workouts"
        wdir.mkdir(parents=True)
        (wdir / "pull.md").write_text(
            "# Pull\n\n## July 20, 2026 - Pull\n\n- Seated Cable Row: 105 lbs x 1 x 10\n",
            encoding="utf-8",
        )
        r1 = self.repo.ensure_seeded_from_workspace(root)
        self.assertTrue(r1.get("seeded"))
        n = self.repo.count()
        r2 = self.repo.ensure_seeded_from_workspace(root)
        self.assertFalse(r2.get("seeded"))
        self.assertEqual(self.repo.count(), n)


if __name__ == "__main__":
    unittest.main()
