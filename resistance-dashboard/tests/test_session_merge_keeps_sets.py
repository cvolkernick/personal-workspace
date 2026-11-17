"""Date moves keep every set when the target day already has that session.

The 11/17 legs quest tick (Back Extension Machine 160×1×13) was dropped
when a later legs log (130×1×15 plus a deadlift) moved onto that day.
Cross-session merges now keep the target's sets first, then the moved
sets. A same-session Log-tab save still replaces a quest tick (#963).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import exercise_is_real_log, seed_exercise
from rt_dashboard.turso_http import TursoCursor, TursoRow
from rt_dashboard.workout_log import (
    ensure_source_sets_on_readback,
    mark_merged_from,
    merged_from_days,
    merge_log_with_history,
    merge_same_day_session,
    parse_log_body,
    plan_session_date_move,
    session_persist_stamp,
    sets_digest,
    source_identity_already_merged,
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


X_DAY = "2026-11-10"
Y_DAY = "2026-11-12"
X1_STAMP = "2026-11-10T19:00:00-05:00"
X2_STAMP = "2026-11-14T19:30:00-05:00"
Y_STAMP = "2026-11-12T18:00:00-05:00"


def _lift(name, weight, sets=1, reps=8, *, quest=False, set_index=None) -> ExerciseEntry:
    entry = SetEntry(weight_lbs=weight, sets=sets, reps=reps)
    if set_index is not None:
        entry.set_index = set_index
    return ExerciseEntry(name=name, sets=[entry], quest_seeded=quest)


def _legs_on(day, exercises, stamp, source_file="") -> Session:
    return Session(
        date=day,
        session_type="legs",
        exercises=list(exercises),
        closed_at=stamp,
        source_file=source_file,
    )


def _move_x_to_y(history):
    return plan_session_date_move(
        history,
        session_type="legs",
        from_date=X_DAY,
        to_date=Y_DAY,
    )


def _names(session: Session) -> list:
    return [ex.name for ex in session.exercises]


def _squats(n: int, day: str, stamp: str) -> Session:
    """``n`` separate identical 100x1x10 squat sets."""
    return _legs_on(
        day,
        [
            ExerciseEntry(
                name="Squat",
                sets=[SetEntry(weight_lbs=100, sets=1, reps=10) for _ in range(n)],
            )
        ],
        stamp,
    )


class SourceSessionIdentity(unittest.TestCase):
    """The merge marker is the source session, not only its civil day."""

    def test_x_then_new_x_keeps_both_sessions(self):
        y = _legs_on(Y_DAY, [_lift("Seated Leg Curls", 90, sets=2, reps=12)], Y_STAMP)
        x1 = _legs_on(X_DAY, [_lift("Back Extension Machine", 160, reps=13)], X1_STAMP)
        first = _move_x_to_y([y, x1])
        self.assertEqual(
            _names(first),
            ["Seated Leg Curls", "Back Extension Machine"],
        )
        self.assertIn(X1_STAMP, first.source_file)
        self.assertIn("id=", first.source_file)
        self.assertIn(X_DAY, merged_from_days(first.source_file))
        self.assertTrue(source_identity_already_merged(first.source_file, X_DAY, x1))
        # A later session on X is not that merge, even when the loads match.
        same_loads = _legs_on(
            X_DAY, [_lift("Back Extension Machine", 160, reps=13)], X2_STAMP
        )
        second = _move_x_to_y([first, same_loads])
        back = next(ex for ex in second.exercises if ex.name == "Back Extension Machine")
        self.assertEqual(_triples(back), [(160.0, 1, 13), (160.0, 1, 13)])
        self.assertEqual(_triples(second.exercises[0]), [(90.0, 2, 12)])
        replay = _move_x_to_y([second, same_loads])
        self.assertEqual(_set_rows(replay), _set_rows(second))

    def test_true_retry_adds_nothing(self):
        y = _legs_on(Y_DAY, [_lift("Seated Leg Curls", 90, sets=2, reps=12)], Y_STAMP)
        x1 = _legs_on(
            X_DAY,
            [
                ExerciseEntry(
                    name="Back Extension Machine",
                    sets=[
                        SetEntry(weight_lbs=160, sets=1, reps=13),
                        SetEntry(weight_lbs=150, sets=1, reps=12),
                    ],
                )
            ],
            X1_STAMP,
        )
        first = _move_x_to_y([y, x1])
        again = _move_x_to_y([first, x1])
        self.assertEqual(_set_rows(again), _set_rows(first))
        self.assertEqual(_set_multipliers(again), _set_multipliers(first))
        back = next(ex for ex in again.exercises if ex.name == "Back Extension Machine")
        self.assertEqual(_triples(back), [(160.0, 1, 13), (150.0, 1, 12)])
        flipped = _legs_on(
            X_DAY,
            [
                ExerciseEntry(
                    name="Back Extension Machine",
                    sets=[
                        SetEntry(weight_lbs=150, sets=1, reps=12),
                        SetEntry(weight_lbs=160, sets=1, reps=13),
                    ],
                )
            ],
            X1_STAMP,
        )
        self.assertEqual(sets_digest(x1), sets_digest(flipped))
        ordered = _move_x_to_y([first, flipped])
        self.assertEqual(_set_rows(ordered), _set_rows(first))

    def test_quest_flag_and_set_index_are_part_of_identity(self):
        y = _legs_on(Y_DAY, [_lift("Squat", 100)], Y_STAMP)
        typed = _legs_on(X_DAY, [_lift("Squat", 200, reps=5)], X1_STAMP)
        first = _move_x_to_y([y, typed])
        quest = _legs_on(X_DAY, [_lift("Squat", 200, reps=5, quest=True)], X1_STAMP)
        indexed = _legs_on(
            X_DAY, [_lift("Squat", 200, reps=5, set_index=0)], X1_STAMP
        )
        self.assertNotEqual(sets_digest(typed), sets_digest(quest))
        self.assertNotEqual(sets_digest(typed), sets_digest(indexed))
        self.assertFalse(source_identity_already_merged(first.source_file, X_DAY, quest))
        self.assertFalse(
            source_identity_already_merged(first.source_file, X_DAY, indexed)
        )
        after_quest = _move_x_to_y([first, quest])
        self.assertEqual(len(after_quest.exercises[0].sets), len(first.exercises[0].sets) + 1)
        after_index = _move_x_to_y([first, indexed])
        self.assertEqual(len(after_index.exercises[0].sets), len(first.exercises[0].sets) + 1)

    def test_legacy_day_marker_with_a_different_session_is_not_a_retry(self):
        legacy = f"fitdash-merged-from:{X_DAY}"
        dest = _legs_on(
            Y_DAY,
            [_lift("Seated Leg Curls", 90, sets=2, reps=12)],
            Y_STAMP,
            source_file=f"fitness/workouts/legs.md\n{legacy}",
        )
        src = _legs_on(X_DAY, [_lift("Barbell Deadlift", 135, reps=6)], X2_STAMP)
        self.assertIn(X_DAY, merged_from_days(dest.source_file))
        self.assertFalse(source_identity_already_merged(dest.source_file, X_DAY, src))
        moved = _move_x_to_y([dest, src])
        self.assertEqual(_names(moved), ["Seated Leg Curls", "Barbell Deadlift"])
        self.assertEqual(_triples(moved.exercises[1]), [(135.0, 1, 6)])
        self.assertTrue(moved.source_file.startswith("fitness/workouts/legs.md"))
        self.assertIn(legacy, moved.source_file.splitlines())
        self.assertTrue(source_identity_already_merged(moved.source_file, X_DAY, src))
        # The same new session, retried, adds nothing.
        again = _move_x_to_y([moved, src])
        self.assertEqual(_set_rows(again), _set_rows(moved))

    def test_legacy_day_marker_alone_is_not_proof_for_the_same_session(self):
        src = _legs_on(X_DAY, [_lift("Squat", 200, reps=5)], X1_STAMP)
        dest = _legs_on(
            Y_DAY,
            [
                ExerciseEntry(
                    name="Squat",
                    sets=[
                        SetEntry(weight_lbs=100, sets=1, reps=5),
                        SetEntry(weight_lbs=200, sets=1, reps=5),
                    ],
                )
            ],
            Y_STAMP,
            source_file=f"fitdash-merged-from:{X_DAY}",
        )
        self.assertFalse(source_identity_already_merged(dest.source_file, X_DAY, src))
        moved = _move_x_to_y([dest, src])
        self.assertEqual(len(moved.exercises[0].sets), 3)

    def test_stamp_uses_created_at_when_it_is_set(self):
        src = _legs_on(X_DAY, [_lift("Squat", 200, reps=5)], X1_STAMP)
        src.created_at = "2026-11-10T01:00:00-05:00"
        self.assertEqual(session_persist_stamp(src), "2026-11-10T01:00:00-05:00")
        marked = mark_merged_from("fitness/workouts/legs.md", X_DAY, src)
        self.assertTrue(marked.startswith("fitness/workouts/legs.md"))
        self.assertIn("2026-11-10T01:00:00-05:00", marked)
        self.assertNotIn(X1_STAMP, marked)

    def test_log_tab_append_and_set_index_ignore_the_merge_marker(self):
        digest = "ab" * 32
        existing = Session(
            date=Y_DAY,
            session_type="legs",
            exercises=[
                ExerciseEntry(
                    name="Squat",
                    sets=[
                        SetEntry(weight_lbs=100, sets=1, reps=5),
                        SetEntry(weight_lbs=110, sets=1, reps=5),
                    ],
                )
            ],
            source_file=f"fitdash-merged-from:{X_DAY} id={digest}@{X1_STAMP}",
        )
        incoming = parse_log_body(
            {
                "session_type": "legs",
                "date": Y_DAY,
                "exercises": [
                    {
                        "name": "Squat",
                        "sets": [
                            {
                                "weight_lbs": 120,
                                "sets": 1,
                                "reps": 4,
                                "set_index": 1,
                            }
                        ],
                    }
                ],
            }
        )
        merged = merge_log_with_history(incoming, [existing])
        sets = merged.exercises[0].sets
        self.assertEqual(len(sets), 2)
        self.assertEqual(sets[0].weight_lbs, 100)
        self.assertEqual(sets[1].weight_lbs, 120)
        self.assertIn(digest, merged.source_file)

    def test_two_identical_sets_need_two_copies_beyond_the_destination(self):
        before = _squats(1, Y_DAY, Y_STAMP)
        source = _squats(2, X_DAY, X1_STAMP)
        full = _squats(3, Y_DAY, Y_STAMP)
        short = _squats(2, Y_DAY, Y_STAMP)
        ensure_source_sets_on_readback(source, full, before=before)
        with self.assertRaisesRegex(RuntimeError, "source row kept"):
            ensure_source_sets_on_readback(source, short, before=before)
        # Membership of the triple, or post >= source, would accept `short`
        # (2 >= 2). The pre-existing copy is not one of the two new copies.
        with self.assertRaisesRegex(RuntimeError, "added 1, need 2 beyond 1"):
            ensure_source_sets_on_readback(source, short, before=before)


class SourceIdentitySqlite(unittest.TestCase):
    def setUp(self):
        import rt_dashboard.crypto_box as cb

        os.environ["FITDASH_MASTER_KEY"] = FAKE_ENV["FITDASH_MASTER_KEY"]
        cb._KEY = None
        self.tmp = tempfile.TemporaryDirectory()
        from rt_dashboard.workout_repo import WorkoutRepository

        self.repo = WorkoutRepository(
            db_path=Path(self.tmp.name) / "fitdash.db", user_id="identity"
        )

    def tearDown(self):
        import rt_dashboard.crypto_box as cb

        self.tmp.cleanup()
        cb._KEY = None
        os.environ.pop("FITDASH_MASTER_KEY", None)

    def test_x_then_new_x_both_survive_on_y(self):
        y = _legs_on(Y_DAY, [_lift("Seated Leg Curls", 90, sets=2, reps=12)], Y_STAMP)
        x1 = _legs_on(X_DAY, [_lift("Back Extension Machine", 160, reps=13)], X1_STAMP)
        self.repo.upsert_session(y)
        self.repo.upsert_session(x1)
        first = _move_x_to_y(self.repo.list_sessions())
        self.repo.relocate_session(first, X_DAY)
        x2 = _legs_on(X_DAY, [_lift("Barbell Deadlift", 135, reps=6)], X2_STAMP)
        self.repo.upsert_session(x2)
        second = _move_x_to_y(self.repo.list_sessions())
        self.repo.relocate_session(second, X_DAY)
        rows = self.repo.list_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, Y_DAY)
        self.assertEqual(
            _names(rows[0]),
            ["Seated Leg Curls", "Back Extension Machine", "Barbell Deadlift"],
        )
        self.assertEqual(_triples(rows[0].exercises[0]), [(90.0, 2, 12)])
        self.assertEqual(_triples(rows[0].exercises[1]), [(160.0, 1, 13)])
        self.assertEqual(_triples(rows[0].exercises[2]), [(135.0, 1, 6)])
        self.assertIn(X1_STAMP, rows[0].source_file)
        self.assertIn(X2_STAMP, rows[0].source_file)

    def test_true_retry_relocate_adds_nothing(self):
        y = _legs_on(Y_DAY, [_lift("Seated Leg Curls", 90, sets=2, reps=12)], Y_STAMP)
        x1 = _legs_on(X_DAY, [_lift("Back Extension Machine", 160, reps=13)], X1_STAMP)
        self.repo.upsert_session(y)
        self.repo.upsert_session(x1)
        first = _move_x_to_y(self.repo.list_sessions())
        # Leave the source row in place, the same window a retried request hits.
        self.repo.upsert_session(first)
        again = _move_x_to_y(self.repo.list_sessions())
        self.assertEqual(_set_rows(again), _set_rows(first))
        self.repo.relocate_session(again, X_DAY)
        self.repo.relocate_session(again, X_DAY)
        rows = self.repo.list_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(_set_rows(rows[0]), _set_rows(first))
        self.assertEqual(_triples(rows[0].exercises[1]), [(160.0, 1, 13)])

    def test_destination_missing_a_set_does_not_delete_the_source(self):
        y = _legs_on(Y_DAY, [_lift("Seated Leg Curls", 90, sets=2, reps=12)], Y_STAMP)
        x1 = _legs_on(
            X_DAY,
            [
                _lift("Back Extension Machine", 160, reps=13),
                _lift("Barbell Deadlift", 135, reps=6),
            ],
            X1_STAMP,
        )
        self.repo.upsert_session(y)
        self.repo.upsert_session(x1)
        incomplete = _legs_on(
            Y_DAY,
            [
                _lift("Seated Leg Curls", 90, sets=2, reps=12),
                _lift("Back Extension Machine", 160, reps=13),
            ],
            Y_STAMP,
        )
        with self.assertLogs("fitdash.workout_log", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "source row kept"):
                self.repo.relocate_session(incomplete, X_DAY)
        self.assertTrue(any("deadlift" in line.lower() for line in logs.output))
        rows = {row.date: row for row in self.repo.list_sessions()}
        self.assertIn(X_DAY, rows)
        self.assertEqual(
            _names(rows[X_DAY]),
            ["Back Extension Machine", "Barbell Deadlift"],
        )
        self.assertEqual(_triples(rows[X_DAY].exercises[1]), [(135.0, 1, 6)])

    def test_legacy_marker_relocate_keeps_the_new_session(self):
        dest = _legs_on(
            Y_DAY,
            [_lift("Seated Leg Curls", 90, sets=2, reps=12)],
            Y_STAMP,
            source_file=f"fitdash-merged-from:{X_DAY}",
        )
        src = _legs_on(X_DAY, [_lift("Barbell Deadlift", 135, reps=6)], X2_STAMP)
        self.repo.upsert_session(dest)
        self.repo.upsert_session(src)
        moved = _move_x_to_y(self.repo.list_sessions())
        self.repo.relocate_session(moved, X_DAY)
        rows = self.repo.list_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(_names(rows[0]), ["Seated Leg Curls", "Barbell Deadlift"])
        self.assertEqual(_triples(rows[0].exercises[1]), [(135.0, 1, 6)])

    def test_two_identical_source_sets_land_as_three_and_source_is_deleted(self):
        dest = _squats(1, Y_DAY, Y_STAMP)
        src = _squats(2, X_DAY, X1_STAMP)
        self.repo.upsert_session(dest)
        self.repo.upsert_session(src)
        moved = _move_x_to_y(self.repo.list_sessions())
        self.assertEqual(len(moved.exercises[0].sets), 3)
        self.repo.relocate_session(moved, X_DAY)
        rows = self.repo.list_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, Y_DAY)
        self.assertEqual(len(rows[0].exercises[0].sets), 3)
        self.assertEqual(
            _triples(rows[0].exercises[0]),
            [(100.0, 1, 10), (100.0, 1, 10), (100.0, 1, 10)],
        )

    def test_readback_of_two_does_not_delete_when_destination_already_had_one(self):
        dest = _squats(1, Y_DAY, Y_STAMP)
        src = _squats(2, X_DAY, X1_STAMP)
        self.repo.upsert_session(dest)
        self.repo.upsert_session(src)
        # One source copy dropped: the row shows the original plus one new copy.
        short = _squats(2, Y_DAY, Y_STAMP)
        with self.assertLogs("fitdash.workout_log", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "added 1, need 2 beyond 1"):
                self.repo.relocate_session(short, X_DAY)
        self.assertTrue(any("refusing to delete" in line for line in logs.output))
        rows = {row.date: row for row in self.repo.list_sessions()}
        self.assertIn(X_DAY, rows)
        self.assertEqual(len(rows[X_DAY].exercises[0].sets), 2)
        self.assertEqual(
            _triples(rows[X_DAY].exercises[0]),
            [(100.0, 1, 10), (100.0, 1, 10)],
        )


class SourceIdentityTurso(RelocateDeletesSourceAfterReadback):
    def _read(self, day):
        from rt_dashboard import turso_repo

        with mock.patch.dict(os.environ, FAKE_ENV, clear=False), mock.patch.object(
            turso_repo, "connect", return_value=self.store
        ):
            return turso_repo.read_session(UID, day, "legs")

    def test_x_then_new_x_both_survive_on_readback(self):
        y = _legs_on(Y_DAY, [_lift("Seated Leg Curls", 90, sets=2, reps=12)], Y_STAMP)
        x1 = _legs_on(X_DAY, [_lift("Back Extension Machine", 160, reps=13)], X1_STAMP)
        self._seed(y, x1)
        first = _move_x_to_y([y, x1])
        self._reloc(first, X_DAY)
        self.assertIsNone(self._read(X_DAY))
        stored = self._read(Y_DAY)
        x2 = _legs_on(X_DAY, [_lift("Barbell Deadlift", 135, reps=6)], X2_STAMP)
        self._seed(x2)
        second = _move_x_to_y([stored, x2])
        self._reloc(second, X_DAY)
        final = self._read(Y_DAY)
        self.assertIsNone(self._read(X_DAY))
        self.assertEqual(
            _names(final),
            ["Seated Leg Curls", "Back Extension Machine", "Barbell Deadlift"],
        )
        self.assertEqual(_triples(final.exercises[1]), [(160.0, 1, 13)])
        self.assertEqual(_triples(final.exercises[2]), [(135.0, 1, 6)])

    def test_readback_missing_a_set_does_not_delete_the_source(self):
        from rt_dashboard.workout_repo import _seal_exercises

        y = _legs_on(Y_DAY, [_lift("Seated Leg Curls", 90, sets=2, reps=12)], Y_STAMP)
        x1 = _legs_on(
            X_DAY,
            [
                _lift("Back Extension Machine", 160, reps=13),
                _lift("Barbell Deadlift", 135, reps=6),
            ],
            X1_STAMP,
        )
        merged = _move_x_to_y([y, x1])
        self._seed(y, x1)
        kept = [ex for ex in merged.exercises if ex.name != "Barbell Deadlift"]
        self.store.substitute_payload = _seal_exercises(UID, kept)
        with self.assertLogs("fitdash.workout_log", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "source row kept"):
                self._reloc(merged, X_DAY)
        self.assertTrue(any("missing" in line.lower() for line in logs.output))
        self.assertNotIn("delete", [event["op"] for event in self.store.events])
        self.assertIn((UID, X_DAY, "legs"), self.store.rows)
        stored = _open_exercises(
            UID, self.store.rows[(UID, X_DAY, "legs")]["exercises_json"]
        )
        self.assertEqual(_names_of(stored), ["Back Extension Machine", "Barbell Deadlift"])
        self.assertEqual(_triples(stored[1]), [(135.0, 1, 6)])

    def test_readback_dropping_one_identical_copy_keeps_the_source(self):
        from rt_dashboard.workout_repo import _seal_exercises

        dest = _squats(1, Y_DAY, Y_STAMP)
        src = _squats(2, X_DAY, X1_STAMP)
        merged = _move_x_to_y([dest, src])
        self.assertEqual(len(merged.exercises[0].sets), 3)
        self._seed(dest, src)
        dropped = _squats(2, Y_DAY, Y_STAMP)
        self.store.substitute_payload = _seal_exercises(UID, dropped.exercises)
        with self.assertLogs("fitdash.workout_log", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "added 1, need 2 beyond 1"):
                self._reloc(merged, X_DAY)
        self.assertTrue(any("refusing to delete" in line for line in logs.output))
        self.assertNotIn("delete", [event["op"] for event in self.store.events])
        self.assertIn((UID, X_DAY, "legs"), self.store.rows)
        stored = _open_exercises(
            UID, self.store.rows[(UID, X_DAY, "legs")]["exercises_json"]
        )
        self.assertEqual(len(stored[0].sets), 2)


def _names_of(exercises) -> list:
    return [ex.name for ex in exercises]


if __name__ == "__main__":
    unittest.main()
