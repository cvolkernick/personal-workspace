"""Date moves keep every set when the target day already has that session.

The 11/17 legs quest tick (Back Extension Machine 160×1×13) was dropped
when a later legs log (130×1×15 plus a deadlift) moved onto that day.
Cross-session merges now keep the target's sets first, then the moved
sets. A same-session Log-tab save still replaces a quest tick (#963).
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import exercise_is_real_log, seed_exercise
from rt_dashboard.turso_http import TursoCursor, TursoRow
from rt_dashboard.workout_log import (
    merge_log_with_history,
    merge_same_day_session,
    parse_log_body,
    plan_session_date_move,
)
from rt_dashboard.workout_repo import _open_exercises

TARGET = "2026-11-17"
SOURCE = "2026-11-16"
UID = "merge-sets-user"

FAKE_ENV = {
    "TURSO_DATABASE_URL": "libsql://fake-db.example.test",
    "TURSO_AUTH_TOKEN": "fake-token",
    "FITDASH_MASTER_KEY": "test-master-key-for-unit-tests-only!!",
}


def _set(weight, sets, reps, set_index=None) -> SetEntry:
    entry = SetEntry(weight_lbs=weight, sets=sets, reps=reps)
    if set_index is not None:
        entry.set_index = set_index
    return entry


def _triples(exercise) -> list:
    return [(s.weight_lbs, s.sets, s.reps) for s in exercise.sets]


def _set_rows(session: Session) -> int:
    return sum(len(ex.sets or []) for ex in session.exercises or [])


def _set_multipliers(session: Session) -> int:
    return sum(int(s.sets) for ex in session.exercises or [] for s in ex.sets or [])


def _back_ext(sets, *, quest=False) -> ExerciseEntry:
    if quest:
        # The tick is one logged triple. seed_exercise takes a single rx.
        first = sets[0]
        entry = seed_exercise(
            "Back Extension Machine",
            title_rx={
                "weight_lbs": first.weight_lbs,
                "sets": first.sets,
                "reps": first.reps,
            },
        )
        if len(sets) > 1:
            entry.sets = list(sets)
        return entry
    return ExerciseEntry(name="Back Extension Machine", sets=list(sets), quest_seeded=False)


def _curls() -> ExerciseEntry:
    return ExerciseEntry(
        name="Seated Leg Curls",
        sets=[SetEntry(weight_lbs=90, sets=2, reps=12)],
    )


def _deadlift() -> ExerciseEntry:
    return ExerciseEntry(
        name="Barbell Deadlift",
        sets=[SetEntry(weight_lbs=135, sets=1, reps=6)],
    )


def _target(back_sets, *, quest: bool) -> Session:
    return Session(
        date=TARGET,
        session_type="legs",
        exercises=[_back_ext(back_sets, quest=quest), _curls()],
        closed_at="2026-11-17T18:00:00-05:00",
        notes="quest day",
    )


def _moved(back_sets) -> Session:
    return Session(
        date=SOURCE,
        session_type="legs",
        exercises=[_back_ext(back_sets, quest=False), _deadlift()],
        closed_at="2026-11-16T19:10:00-05:00",
        notes="moved day",
    )


def _move(history):
    return plan_session_date_move(
        history,
        session_type="legs",
        from_date=SOURCE,
        to_date=TARGET,
    )


class RecordingTurso:
    """In-memory workout_sessions. Records read/delete order for relocate."""

    def __init__(self):
        self.rows = {}
        self.events = []
        self.fail_writes = False
        self.substitute_payload = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql, params=()):
        norm = " ".join((sql or "").split()).upper()
        if norm.startswith("CREATE TABLE") or norm.startswith("ALTER TABLE"):
            return TursoCursor([], [])
        if norm.startswith("UPDATE WORKOUT_SESSIONS"):
            if "EXERCISES_JSON" not in norm:
                return TursoCursor([], [])
            if self.fail_writes:
                raise RuntimeError("turso execute: write failed")
            notes, source, payload = params[:3]
            if self.substitute_payload is not None:
                payload = self.substitute_payload
            if len(params) >= 11:
                status, submission_id, plan, skipped, subs, uid, date, st = params[3:11]
            else:
                status = submission_id = plan = skipped = subs = None
                uid, date, st = params[-3:]
            self.events.append({"op": "update", "date": date, "session_type": st})
            key = (uid, date, st)
            existing = self.rows.get(key)
            if existing:
                existing["notes"] = notes
                existing["source_file"] = source
                existing["exercises_json"] = payload
                if status:
                    existing["status"] = status
                if submission_id:
                    existing["submission_id"] = submission_id
                if plan:
                    existing["plan_json"] = plan
                existing["skipped_json"] = skipped
                if subs:
                    existing["substitutions_json"] = subs
            return TursoCursor([], [])
        if "INSERT INTO WORKOUT_SESSIONS" in norm:
            if self.fail_writes:
                raise RuntimeError("turso execute: write failed")
            if len(params) >= 8:
                uid, date, st, notes, source, payload, created, updated = params[:8]
            else:
                uid, date, st, notes, source, payload = params[:6]
                created = updated = ""
            if self.substitute_payload is not None:
                payload = self.substitute_payload
            extra = params[8:13] if len(params) >= 13 else (None, None, None, None, None)
            self.events.append({"op": "insert", "date": date, "session_type": st})
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
                    "status": extra[0] if extra[0] is not None else (existing or {}).get("status"),
                    "submission_id": extra[1] or (existing or {}).get("submission_id"),
                    "plan_json": extra[2] or (existing or {}).get("plan_json"),
                    "skipped_json": extra[3] or (existing or {}).get("skipped_json"),
                    "substitutions_json": extra[4] or (existing or {}).get("substitutions_json"),
                }
            )
            return TursoCursor([], [])
        if norm.startswith("DELETE"):
            uid, date, st = params[:3]
            self.events.append({"op": "delete", "date": date, "session_type": st})
            self.rows.pop((uid, date, st), None)
            return TursoCursor([], [])
        if "FROM WORKOUT_SESSIONS" in norm and "AND DATE =" in norm:
            uid, date, st = params[:3]
            row = self.rows.get((uid, date, st))
            self.events.append(
                {
                    "op": "read",
                    "date": date,
                    "session_type": st,
                    "exercises_json": None if row is None else row.get("exercises_json"),
                }
            )
            return TursoCursor(
                ["date", "session_type", "notes", "source_file", "exercises_json"],
                [row] if row else [],
            )
        if "FROM WORKOUT_SESSIONS" in norm:
            uid = params[0]
            found = [r for k, r in self.rows.items() if k[0] == uid]
            return TursoCursor(
                ["date", "session_type", "notes", "source_file", "exercises_json"],
                found,
            )
        raise AssertionError(f"unexpected sql: {sql}")


class DateMoveKeepsSets(unittest.TestCase):
    def test_quest_tick_and_typed_back_ext_both_kept(self):
        target = _target([_set(160, 1, 13)], quest=True)
        moved = _moved([_set(130, 1, 15)])
        self.assertTrue(target.exercises[0].quest_seeded)
        merged = _move([target, moved])

        self.assertEqual(merged.date, TARGET)
        names = [ex.name for ex in merged.exercises]
        self.assertEqual(
            names,
            ["Back Extension Machine", "Seated Leg Curls", "Barbell Deadlift"],
        )
        back = merged.exercises[0]
        self.assertEqual(_triples(back), [(160.0, 1, 13), (130.0, 1, 15)])
        self.assertFalse(back.quest_seeded)
        self.assertFalse(str(back.raw or "").startswith("quest-seeded:"))
        self.assertTrue(exercise_is_real_log(back))
        curls = merged.exercises[1]
        self.assertEqual(_triples(curls), [(90.0, 2, 12)])
        dead = merged.exercises[2]
        self.assertEqual(_triples(dead), [(135.0, 1, 6)])
        self.assertEqual(_set_rows(merged), _set_rows(target) + _set_rows(moved))
        self.assertEqual(
            _set_multipliers(merged),
            _set_multipliers(target) + _set_multipliers(moved),
        )
        # The source session object is not rewritten in place.
        self.assertTrue(target.exercises[0].quest_seeded)
        self.assertEqual(_triples(target.exercises[0]), [(160.0, 1, 13)])

    def test_typed_target_back_ext_both_kept(self):
        target = _target([_set(160, 1, 13)], quest=False)
        moved = _moved([_set(130, 1, 15)])
        self.assertFalse(target.exercises[0].quest_seeded)
        merged = _move([target, moved])
        back = merged.exercises[0]
        self.assertEqual(back.name, "Back Extension Machine")
        self.assertEqual(_triples(back), [(160.0, 1, 13), (130.0, 1, 15)])
        self.assertFalse(back.quest_seeded)
        self.assertEqual(_triples(merged.exercises[1]), [(90.0, 2, 12)])
        self.assertEqual(_triples(merged.exercises[2]), [(135.0, 1, 6)])
        self.assertEqual(_set_rows(merged), _set_rows(target) + _set_rows(moved))
        self.assertEqual(
            _set_multipliers(merged),
            _set_multipliers(target) + _set_multipliers(moved),
        )

    def test_several_back_ext_sets_stay_in_order_ignoring_set_index(self):
        target_sets = [_set(160, 1, 13, 0), _set(155, 1, 12, 1)]
        moved_sets = [_set(130, 1, 15, 0), _set(125, 1, 10, 1)]
        target = _target(target_sets, quest=False)
        # A second target set is typed, not a one-rx quest seed.
        target.exercises[0].sets = list(target_sets)
        moved = _moved(moved_sets)
        merged = _move([target, moved])
        back = merged.exercises[0]
        self.assertEqual(
            _triples(back),
            [
                (160.0, 1, 13),
                (155.0, 1, 12),
                (130.0, 1, 15),
                (125.0, 1, 10),
            ],
        )
        for entry in back.sets:
            self.assertFalse(hasattr(entry, "set_index"))
        self.assertEqual(_triples(merged.exercises[1]), [(90.0, 2, 12)])
        self.assertEqual(_triples(merged.exercises[2]), [(135.0, 1, 6)])
        self.assertEqual(_set_rows(merged), _set_rows(target) + _set_rows(moved))


class ReplayDoesNotDuplicateSets(unittest.TestCase):
    """A retried date move unions once. The source row may still be present."""

    def test_reapplying_the_same_move_does_not_duplicate_sets(self):
        target = _target([_set(160, 1, 13)], quest=True)
        moved = _moved([_set(130, 1, 15)])
        first = _move([target, moved])
        self.assertEqual(
            _triples(first.exercises[0]),
            [(160.0, 1, 13), (130.0, 1, 15)],
        )
        # Dest row already written; source row not deleted yet.
        again = _move([first, moved])
        self.assertEqual(_triples(again.exercises[0]), _triples(first.exercises[0]))
        self.assertEqual(_triples(again.exercises[1]), [(90.0, 2, 12)])
        self.assertEqual(_triples(again.exercises[2]), [(135.0, 1, 6)])
        self.assertEqual(_set_rows(again), _set_rows(first))
        self.assertEqual(_set_multipliers(again), _set_multipliers(first))
        third = _move([again, moved])
        self.assertEqual(_set_rows(third), _set_rows(first))

    def test_identical_sets_stay_on_the_first_move_and_not_on_replay(self):
        target = _target([_set(160, 1, 13)], quest=False)
        moved = _moved([_set(160, 1, 13)])
        first = _move([target, moved])
        self.assertEqual(
            _triples(first.exercises[0]),
            [(160.0, 1, 13), (160.0, 1, 13)],
        )
        again = _move([first, moved])
        self.assertEqual(_triples(again.exercises[0]), _triples(first.exercises[0]))
        self.assertEqual(_set_rows(again), _set_rows(first))

    def test_a_different_source_day_still_appends(self):
        target = _target([_set(160, 1, 13)], quest=False)
        moved = _moved([_set(130, 1, 15)])
        first = _move([target, moved])
        other = Session(
            date="2026-11-15",
            session_type="legs",
            exercises=[
                ExerciseEntry(
                    name="Back Extension Machine",
                    sets=[SetEntry(weight_lbs=140, sets=1, reps=12)],
                )
            ],
        )
        second = plan_session_date_move(
            [first, other],
            session_type="legs",
            from_date="2026-11-15",
            to_date=TARGET,
        )
        self.assertEqual(
            _triples(second.exercises[0]),
            [(160.0, 1, 13), (130.0, 1, 15), (140.0, 1, 12)],
        )

    def test_sqlite_replay_of_the_same_payload_does_not_add_sets(self):
        import tempfile
        from pathlib import Path

        from rt_dashboard.workout_repo import WorkoutRepository

        target = _target([_set(160, 1, 13)], quest=True)
        moved = _moved([_set(130, 1, 15)])
        with tempfile.TemporaryDirectory() as tmp:
            repo = WorkoutRepository(db_path=Path(tmp) / "fitdash.db", user_id="replay")
            repo.upsert_session(target)
            repo.upsert_session(moved)
            first = plan_session_date_move(
                repo.list_sessions(),
                session_type="legs",
                from_date=SOURCE,
                to_date=TARGET,
            )
            # Write the merged dest and leave the source row (retry window).
            repo.upsert_session(first)
            again = plan_session_date_move(
                repo.list_sessions(),
                session_type="legs",
                from_date=SOURCE,
                to_date=TARGET,
            )
            self.assertEqual(_set_rows(again), _set_rows(first))
            repo.relocate_session(again, SOURCE)
            # The payload is already stored. Writing it again replaces the row.
            repo.relocate_session(again, SOURCE)
            rows = repo.list_sessions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].date, TARGET)
            back = next(ex for ex in rows[0].exercises if ex.name == "Back Extension Machine")
            self.assertEqual(_triples(back), [(160.0, 1, 13), (130.0, 1, 15)])
            self.assertFalse(back.quest_seeded)
            self.assertEqual(_set_rows(rows[0]), _set_rows(first))


class SameSessionQuestReplaceUnchanged(unittest.TestCase):
    def test_log_tab_still_replaces_quest_tick(self):
        seeded = Session(
            date=TARGET,
            session_type="legs",
            exercises=[
                seed_exercise(
                    "Back Extension Machine",
                    title_rx={"weight_lbs": 160.0, "sets": 1, "reps": 13},
                )
            ],
        )
        incoming = parse_log_body(
            {
                "session_type": "legs",
                "date": TARGET,
                "exercises": [
                    {
                        "name": "Back Extension Machine",
                        "sets": [{"weight_lbs": 130, "sets": 1, "reps": 15}],
                    }
                ],
            }
        )
        merged = merge_log_with_history(incoming, [seeded])
        self.assertEqual(len(merged.exercises), 1)
        self.assertEqual(_triples(merged.exercises[0]), [(130.0, 1, 15)])
        self.assertFalse(merged.exercises[0].quest_seeded)
        appended = merge_same_day_session(incoming, seeded, same_name="append")
        self.assertEqual(_triples(appended.exercises[0]), [(130.0, 1, 15)])


class RelocateDeletesSourceAfterReadback(unittest.TestCase):
    def setUp(self):
        os.environ["FITDASH_MASTER_KEY"] = FAKE_ENV["FITDASH_MASTER_KEY"]
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        self.store = RecordingTurso()

    def tearDown(self):
        import rt_dashboard.crypto_box as cb

        cb._KEY = None
        os.environ.pop("FITDASH_MASTER_KEY", None)

    def _reloc(self, session, from_date):
        from rt_dashboard import turso_repo

        with mock.patch.dict(os.environ, FAKE_ENV, clear=False), mock.patch.object(
            turso_repo, "connect", return_value=self.store
        ):
            return turso_repo.relocate_session(UID, session, from_date)

    def _seed(self, *sessions):
        from rt_dashboard import turso_repo

        with mock.patch.dict(os.environ, FAKE_ENV, clear=False), mock.patch.object(
            turso_repo, "connect", return_value=self.store
        ):
            for session in sessions:
                turso_repo.upsert_session(UID, session)
        self.store.events.clear()

    def test_source_deleted_only_after_merged_row_reads_back(self):
        target = _target([_set(160, 1, 13)], quest=True)
        moved = _moved([_set(130, 1, 15)])
        merged = _move([target, moved])
        self._seed(target, moved)
        result = self._reloc(merged, SOURCE)
        self.assertTrue(result.get("verified_on_readback"))

        delete_at = next(
            i
            for i, event in enumerate(self.store.events)
            if event["op"] == "delete" and event["date"] == SOURCE
        )
        prior_reads = [
            event
            for event in self.store.events[:delete_at]
            if event["op"] == "read" and event["date"] == TARGET
        ]
        self.assertTrue(prior_reads)
        seen = _open_exercises(UID, prior_reads[-1]["exercises_json"])
        back = next(ex for ex in seen if ex.name == "Back Extension Machine")
        self.assertEqual(_triples(back), [(160.0, 1, 13), (130.0, 1, 15)])
        self.assertFalse(back.quest_seeded)
        self.assertNotIn((UID, SOURCE, "legs"), self.store.rows)
        dest = self.store.rows[(UID, TARGET, "legs")]
        stored = _open_exercises(UID, dest["exercises_json"])
        self.assertEqual(_triples(stored[0]), [(160.0, 1, 13), (130.0, 1, 15)])
        self.assertEqual(stored[1].name, "Seated Leg Curls")
        self.assertEqual(stored[2].name, "Barbell Deadlift")

    def test_write_failure_keeps_the_source_row(self):
        target = _target([_set(160, 1, 13)], quest=True)
        moved = _moved([_set(130, 1, 15)])
        merged = _move([target, moved])
        self._seed(target, moved)
        source_before = self.store.rows[(UID, SOURCE, "legs")]["exercises_json"]
        self.store.fail_writes = True
        with self.assertRaises(RuntimeError):
            self._reloc(merged, SOURCE)
        self.assertNotIn("delete", [event["op"] for event in self.store.events])
        self.assertIn((UID, SOURCE, "legs"), self.store.rows)
        self.assertEqual(
            self.store.rows[(UID, SOURCE, "legs")]["exercises_json"],
            source_before,
        )
        # The destination is still the original quest tick, not a partial merge.
        dest = _open_exercises(
            UID, self.store.rows[(UID, TARGET, "legs")]["exercises_json"]
        )
        self.assertEqual(_triples(dest[0]), [(160.0, 1, 13)])
        self.assertTrue(dest[0].quest_seeded)

    def test_readback_mismatch_keeps_the_source_row(self):
        from rt_dashboard.workout_repo import _seal_exercises

        target = _target([_set(160, 1, 13)], quest=True)
        moved = _moved([_set(130, 1, 15)])
        merged = _move([target, moved])
        self._seed(target, moved)
        self.store.substitute_payload = _seal_exercises(
            UID,
            [ExerciseEntry(name="Other", sets=[SetEntry(1, 1, 1)])],
        )
        with self.assertRaisesRegex(RuntimeError, "readback"):
            self._reloc(merged, SOURCE)
        self.assertNotIn("delete", [event["op"] for event in self.store.events])
        self.assertIn((UID, SOURCE, "legs"), self.store.rows)
        stored = _open_exercises(
            UID, self.store.rows[(UID, SOURCE, "legs")]["exercises_json"]
        )
        self.assertEqual(stored[0].name, "Back Extension Machine")
        self.assertEqual(_triples(stored[0]), [(130.0, 1, 15)])


if __name__ == "__main__":
    unittest.main()
