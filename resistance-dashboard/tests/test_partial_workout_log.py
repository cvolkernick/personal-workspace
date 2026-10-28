"""Partial workout log: reassess, finish, day roll, ET date."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import apply_quest_to_session
from rt_dashboard.session_work import (
    STATUS_COMPLETE,
    STATUS_MISSED,
    STATUS_OPEN,
    STATUS_PARTIAL,
    effective_status,
    freeze_plan,
    progress_label,
    reassess_plan,
    session_advances_rotation,
    session_counts_for_streak,
    session_work_done,
    sets_required,
)
from rt_dashboard.training_day import closed_ppl_for_planning, resolve_log_date
from rt_dashboard.workout_log import (
    finalize_logged_session,
    merge_same_day_session,
    plan_session_date_move,
)
from rt_dashboard.workout_planner import (
    days_since_last_session,
    last_performance,
    last_session_type,
    weekly_set_tally,
)
from rt_dashboard.workout_repo import WorkoutRepository

ET = ZoneInfo("America/New_York")
DAY = "2026-10-27"


def _set(weight, sets, reps, warmup=False, rir=None):
    entry = SetEntry(weight_lbs=weight, sets=sets, reps=reps, warmup=warmup)
    if rir is not None:
        entry.rir = rir
    return entry


def _ex(name, sets, *, seeded=False):
    return ExerciseEntry(
        name=name,
        sets=sets,
        quest_seeded=seeded,
        raw=("quest-seeded:50/3/8" if seeded else ""),
    )


def _plan(names, sets=3, weight=50, reps=8):
    return freeze_plan(
        [
            {
                "name": name,
                "sets": sets,
                "reps": reps,
                "weight_lbs": weight,
                "movement": "compound",
                "primary_muscles": ["chest"],
            }
            for name in names
        ],
        "push",
    )


def _session(exercises, *, status=None, plan=None, day=DAY, closed=True):
    return Session(
        date=day,
        session_type="push",
        exercises=exercises,
        closed_at=(f"{day}T23:36:00-04:00" if closed else None),
        status=status,
        plan_objective=plan,
    )


CATALOG = {
    "exercises": [
        {
            "id": "db-bench",
            "name": "DB Bench",
            "movement": "compound",
            "primary_muscles": ["chest"],
            "session_types": ["push"],
        },
        {
            "id": "db-floor",
            "name": "DB Floor Press",
            "movement": "compound",
            "primary_muscles": ["chest"],
            "session_types": ["push"],
        },
        {
            "id": "curl",
            "name": "DB Curl",
            "movement": "isolation",
            "primary_muscles": ["biceps"],
            "default_reps": 10,
            "session_types": ["pull"],
        },
    ]
}


class WorkDoneTests(unittest.TestCase):
    def test_single_manual_lift_does_not_close(self):
        plan = _plan(["A", "B", "C", "D"])
        session = _session([_ex("A", [_set(60, 3, 8)])], status=STATUS_OPEN, plan=plan)
        decision = session_work_done(session, plan, CATALOG)
        self.assertFalse(decision)
        self.assertEqual(decision.reason, "planned_lifts_uncovered")
        self.assertEqual(
            closed_ppl_for_planning([session], as_of=DAY, now=datetime(2026, 10, 27, 23, 0, tzinfo=ET)),
            None,
        )

    def test_off_plan_weight_still_covers(self):
        plan = _plan(["A"], sets=3, weight=100)
        # Need max(1, 3-1) = 2 working sets. Weight is not the planned 100.
        session = _session([_ex("A", [_set(80, 1, 6), _set(70, 1, 8)])], plan=plan)
        decision = session_work_done(session, plan)
        self.assertTrue(decision)
        self.assertEqual(decision.reason, "all_planned_lifts_covered")
        self.assertEqual(sets_required(3), 2)

    def test_warmups_do_not_cover(self):
        plan = _plan(["A"], sets=3, weight=100)
        session = _session(
            [
                _ex(
                    "A",
                    [
                        _set(40, 1, 10),  # under 50% of planned 100
                        _set(100, 1, 8, warmup=True),
                        _set(100, 1, 8),
                    ],
                )
            ],
            plan=plan,
        )
        decision = session_work_done(session, plan)
        self.assertFalse(decision)
        self.assertEqual(decision.uncovered[0]["logged_sets"], 1)

    def test_manual_that_covers_the_plan_completes_via_reassess(self):
        plan = _plan(["A", "B"])
        session = _session(
            [
                _ex("A", [_set(55, 3, 8)]),
                _ex("B", [_set(40, 2, 10)]),
            ],
            plan=plan,
        )
        done = finalize_logged_session(session, existing=None, plan_exercises=None)
        self.assertTrue(session_work_done(done, done.plan_objective))
        self.assertEqual(done.status, STATUS_COMPLETE)

    def test_no_plan_stays_open(self):
        session = _session([_ex("A", [_set(50, 3, 8)])])
        done = finalize_logged_session(session, existing=None)
        self.assertEqual(done.status, STATUS_OPEN)
        self.assertFalse(session_work_done(done, None))

    def test_legacy_reads_complete(self):
        legacy = _session([_ex("A", [_set(50, 3, 8)])], status=None)
        self.assertIsNone(legacy.status)
        self.assertEqual(effective_status(legacy, now=datetime(2026, 10, 27, 23, tzinfo=ET)), "legacy")
        self.assertTrue(session_counts_for_streak(legacy))
        self.assertEqual(last_session_type([legacy]), "push")

    def test_finish_marks_uncovered_skipped_and_progression_ignores_them(self):
        plan = _plan(["A", "B"])
        session = _session([_ex("A", [_set(50, 3, 8)])], status=STATUS_OPEN, plan=plan)
        done = finalize_logged_session(session, existing=session, finish=True)
        self.assertEqual(done.status, STATUS_COMPLETE)
        self.assertEqual(done.skipped, [{"name": "B", "slug": "ex-b", "disposition": "skipped"}])
        self.assertTrue(all(row["disposition"] != "failed" for row in done.skipped))
        self.assertIsNone(last_performance([done], "B"))
        self.assertIsNotNone(last_performance([done], "A"))
        self.assertTrue(session_counts_for_streak(done))

    def test_substitution_covers_slot_without_progressing_original(self):
        plan = freeze_plan(
            [
                {
                    "name": "DB Bench",
                    "sets": 3,
                    "reps": 8,
                    "weight_lbs": 50,
                    "movement": "compound",
                    "primary_muscles": ["chest"],
                }
            ],
            "push",
        )
        session = _session(
            [_ex("DB Floor Press", [_set(40, 3, 8)])],
            status=STATUS_COMPLETE,
            plan=plan,
        )
        decision = session_work_done(session, plan, CATALOG)
        self.assertTrue(decision)
        self.assertEqual(decision.substitutions[0]["logged_name"], "DB Floor Press")
        self.assertEqual(decision.substitutions[0]["disposition"], "substitution")
        self.assertIsNone(last_performance([session], "DB Bench", {"db-bench": CATALOG["exercises"][0]}))
        self.assertIsNotNone(last_performance([session], "DB Floor Press"))

    def test_progress_label(self):
        plan = _plan(["A", "B", "C", "D", "E", "F"])
        session = _session(
            [
                _ex("A", [_set(50, 3, 8)]),
                _ex("B", [_set(50, 3, 8)]),
                _ex("C", [_set(50, 3, 8)]),
            ],
            status=STATUS_OPEN,
            plan=plan,
        )
        self.assertEqual(progress_label(session, plan), "3 of 6 exercises")


class DayRollTests(unittest.TestCase):
    def test_partial_close_credits_streak_and_advances_rotation(self):
        plan = _plan(["A", "B"])
        session = _session(
            [_ex("A", [_set(50, 3, 8)])],
            status=STATUS_OPEN,
            plan=plan,
            day="2026-10-27",
        )
        before = datetime(2026, 10, 28, 3, 59, tzinfo=ET)
        after = datetime(2026, 10, 28, 4, 0, tzinfo=ET)
        self.assertEqual(effective_status(session, now=before), STATUS_OPEN)
        self.assertFalse(session_advances_rotation(session, now=before))
        self.assertEqual(effective_status(session, now=after), STATUS_PARTIAL)
        self.assertTrue(session_counts_for_streak(session, now=after))
        from rt_dashboard.session_work import annotate_day_roll

        annotate_day_roll(session, now=after)
        self.assertEqual(session.status, STATUS_PARTIAL)
        self.assertEqual(session.skipped[0]["name"], "B")
        self.assertEqual(session.skipped[0]["disposition"], "skipped")
        self.assertEqual(last_session_type([session]), "push")

    def test_zero_covered_is_missed_and_does_not_advance(self):
        earlier = _session([_ex("Old", [_set(40, 3, 8)])], day="2026-10-20", status=None)
        earlier.session_type = "legs"
        missed = _session([], status=STATUS_OPEN, plan=_plan(["A", "B"]), day="2026-10-27")
        after = datetime(2026, 10, 28, 4, 0, tzinfo=ET)
        self.assertEqual(effective_status(missed, now=after), STATUS_MISSED)
        self.assertFalse(session_counts_for_streak(missed, now=after))
        self.assertFalse(session_advances_rotation(missed, now=after))
        self.assertEqual(last_session_type([earlier, missed]), "legs")

    def test_open_session_ignored_by_progression_and_counted_in_volume(self):
        open_s = _session(
            [_ex("A", [_set(50, 2, 10)])],
            status=STATUS_OPEN,
            day="2099-01-01",
        )
        done = _session(
            [_ex("A", [_set(40, 2, 8)])],
            status=STATUS_COMPLETE,
            day="2026-10-20",
        )
        self.assertIsNone(last_performance([open_s], "A"))
        self.assertEqual(last_performance([open_s, done], "A")["weight_lbs"], 40)
        catalog = {
            "exercises": [
                {
                    "id": "a",
                    "name": "A",
                    "movement": "compound",
                    "primary_muscles": ["chest"],
                    "session_types": ["push"],
                }
            ]
        }
        tally = weekly_set_tally([open_s], catalog, as_of="2099-01-01")
        self.assertGreater(tally["total_set_credits"], 0)
        self.assertGreater(open_s.volume, 0)
        self.assertIsNone(days_since_last_session([open_s], as_of="2099-01-01"))


class ReassessAdjustTests(unittest.TestCase):
    def test_flag_off_does_not_change_sets_or_load(self):
        plan = _plan(["DB Bench", "DB Curl"], sets=3, weight=50)
        session = _session(
            [_ex("DB Bench", [_set(50, 3, 4, rir=0)])],
            status=STATUS_OPEN,
            plan=plan,
        )
        before = [dict(leaf) for leaf in plan["leaves"]]
        out = reassess_plan(session, plan, enabled=False, catalog=CATALOG)
        self.assertFalse(out["changed"])
        self.assertEqual(out["plan"]["leaves"], before)
        self.assertFalse(os.environ.get("FITDASH_REASSESS_ADJUST"))

    def test_flag_on_trims_one_set_and_can_add_one_lift(self):
        plan = _plan(["DB Bench"], sets=3, weight=50)
        # A second uncovered chest lift to trim, plus enough weekly sets.
        plan["leaves"].append(
            {
                "slug": "ex-db-floor-press",
                "name": "DB Floor Press",
                "title": "DB Floor Press",
                "sets": 3,
                "reps": 8,
                "weight_lbs": 40.0,
                "volume": 960.0,
                "movement": "compound",
                "primary_muscles": ["chest"],
            }
        )
        session = _session(
            [_ex("DB Bench", [_set(50, 4, 4, rir=0)])],
            status=STATUS_OPEN,
            plan=plan,
        )
        out = reassess_plan(
            session,
            plan,
            enabled=True,
            catalog=CATALOG,
            week_sessions=[],
        )
        self.assertTrue(out["enabled"])
        floor = next(x for x in out["plan"]["leaves"] if x["name"] == "DB Floor Press")
        bench = next(x for x in out["plan"]["leaves"] if x["name"] == "DB Bench")
        self.assertEqual(floor["sets"], 2)
        self.assertEqual(bench["weight_lbs"], 50.0)
        added = [x for x in out["plan"]["leaves"] if x.get("added_by_reassess")]
        self.assertLessEqual(len(added), 1)


class SaveFlowTests(unittest.TestCase):
    def test_two_adds_append_same_session_and_same_id_does_not(self):
        plan = _plan(["A", "B"])
        first = _session([_ex("A", [_set(50, 1, 8)])], status=STATUS_OPEN, plan=plan)
        first.submission_id = "sub-1"
        second_in = Session(
            date=DAY,
            session_type="push",
            exercises=[_ex("A", [_set(50, 1, 8)])],
            closed_at=first.closed_at,
            submission_id="sub-2",
        )
        merged = merge_same_day_session(second_in, first, same_name="append")
        merged = finalize_logged_session(
            merged, existing=first, submission_id="sub-2"
        )
        self.assertEqual(len(merged.exercises), 1)
        self.assertEqual(sum(s.sets for s in merged.exercises[0].sets), 2)
        self.assertEqual(merged.status, STATUS_OPEN)
        again = Session(
            date=DAY,
            session_type="push",
            exercises=[_ex("A", [_set(50, 1, 8)])],
            submission_id="sub-2",
        )
        self.assertEqual(merged.submission_id, "sub-2")
        self.assertEqual(again.submission_id, merged.submission_id)

    def test_sqlite_append_and_duplicate_submission(self):
        from rt_dashboard.training_day import training_day_iso

        day = training_day_iso(tz_name="America/New_York")
        with tempfile.TemporaryDirectory() as tmp:
            repo = WorkoutRepository(db_path=Path(tmp) / "fitdash.db", user_id="partial-test")
            plan_rows = [
                {"name": "A", "sets": 3, "reps": 8, "weight_lbs": 50},
                {"name": "B", "sets": 3, "reps": 8, "weight_lbs": 50},
            ]
            one = finalize_logged_session(
                Session(
                    date=day,
                    session_type="push",
                    exercises=[_ex("A", [_set(50, 1, 8)])],
                    closed_at=f"{day}T12:00:00-04:00",
                ),
                plan_exercises=plan_rows,
                submission_id="once",
            )
            repo.upsert_session(one)
            two = finalize_logged_session(
                merge_same_day_session(
                    Session(
                        date=day,
                        session_type="push",
                        exercises=[_ex("B", [_set(40, 1, 8)])],
                        closed_at=one.closed_at,
                    ),
                    one,
                    same_name="append",
                ),
                existing=one,
                submission_id="twice",
            )
            repo.upsert_session(two)
            rows = repo.list_sessions()
            self.assertEqual(len(rows), 1)
            names = sorted(ex.name for ex in rows[0].exercises)
            self.assertEqual(names, ["A", "B"])
            self.assertEqual(rows[0].status, STATUS_OPEN)
            dup = finalize_logged_session(
                merge_same_day_session(
                    Session(
                        date=day,
                        session_type="push",
                        exercises=[_ex("B", [_set(40, 1, 8)])],
                        closed_at=one.closed_at,
                        submission_id="twice",
                    ),
                    two,
                    same_name="append",
                ),
                existing=two,
                submission_id="twice",
            )
            # Caller drops the duplicate before merge; the stored id matches.
            self.assertEqual(two.submission_id, "twice")
            self.assertEqual(dup.submission_id, "twice")
            repo.upsert_session(two)
            self.assertEqual(len(repo.list_sessions()), 1)

    def test_quest_then_manual_then_finish_skips_nothing(self):
        names = ["Lift A", "Lift B", "Lift C", "Lift D"]
        plan_rows = [
            {"name": n, "sets": 3, "reps": 8, "weight_lbs": 50} for n in names
        ]
        plan = freeze_plan(plan_rows, "push")
        sessions = []
        day = DAY

        def check(name):
            session, info = apply_quest_to_session(
                completed=True,
                group="training",
                title=f"{name} (50 lb 3×8)",
                slug="ex-" + name.lower().replace(" ", "-"),
                session_type="push",
                sessions=sessions,
                today=day,
            )
            self.assertEqual(info["action"], "upsert")
            prior = sessions[0] if sessions else None
            done = finalize_logged_session(
                session, existing=prior, plan_exercises=plan_rows
            )
            done.plan_objective = done.plan_objective or plan
            return done

        sessions[:] = [check("Lift A")]
        sessions[:] = [check("Lift B")]
        manual = merge_same_day_session(
            Session(
                date=day,
                session_type="push",
                exercises=[
                    _ex("Lift C", [_set(35, 1, 12), _set(30, 1, 10), _set(30, 1, 8)])
                ],
                closed_at=sessions[0].closed_at,
            ),
            sessions[0],
            same_name="append",
        )
        manual = finalize_logged_session(
            manual, existing=sessions[0], plan_exercises=plan_rows
        )
        self.assertEqual(manual.status, STATUS_OPEN)
        got = [ex.name for ex in manual.exercises]
        self.assertEqual(got, ["Lift A", "Lift B", "Lift C"])
        decision = session_work_done(manual, manual.plan_objective)
        self.assertIn("Lift D", [row["name"] for row in decision.uncovered])
        self.assertEqual(progress_label(manual, manual.plan_objective), "3 of 4 exercises")
        sessions[:] = [manual]
        sessions[:] = [check("Lift D")]
        finished = finalize_logged_session(sessions[0], existing=sessions[0], finish=True)
        self.assertEqual(finished.status, STATUS_COMPLETE)
        self.assertEqual(finished.skipped or [], [])
        self.assertEqual(
            [ex.name for ex in finished.exercises],
            ["Lift A", "Lift B", "Lift C", "Lift D"],
        )


class DateTests(unittest.TestCase):
    def test_2336_et_is_not_the_next_utc_date(self):
        now = datetime(2026, 10, 27, 23, 36, tzinfo=ET)
        utc = now.astimezone(timezone.utc)
        self.assertEqual(utc.strftime("%Y-%m-%d"), "2026-10-28")
        self.assertEqual(
            resolve_log_date("", now=now, tz_name="America/New_York"),
            "2026-10-27",
        )
        self.assertEqual(
            resolve_log_date(utc.strftime("%Y-%m-%dT%H:%M:%SZ"), now=now, tz_name="America/New_York"),
            "2026-10-27",
        )
        # EST: 23:36 is already past 04:00 UTC the next morning.
        winter = datetime(2026, 1, 15, 23, 36, tzinfo=ET)
        winter_utc = winter.astimezone(timezone.utc)
        self.assertEqual(winter_utc.strftime("%Y-%m-%d"), "2026-01-16")
        self.assertGreaterEqual(int(winter_utc.strftime("%H")), 4)
        self.assertEqual(
            resolve_log_date(
                winter_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                now=winter,
                tz_name="America/New_York",
            ),
            "2026-01-15",
        )
        history = [
            Session(
                date="2026-10-27",
                session_type="push",
                exercises=[_ex("A", [_set(50, 1, 8)])],
                closed_at="2026-10-27T23:36:00-04:00",
            )
        ]
        moved = plan_session_date_move(
            history,
            session_type="push",
            from_date="2026-10-27",
            to_date=utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            tz_name="America/New_York",
            now=now,
        )
        self.assertEqual(moved.date, "2026-10-27")


class LegacySqliteTests(unittest.TestCase):
    def test_null_status_reads_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = WorkoutRepository(db_path=Path(tmp) / "fitdash.db", user_id="legacy-test")
            legacy = Session(
                date="2026-09-01",
                session_type="pull",
                exercises=[_ex("Row", [_set(40, 3, 8)])],
                closed_at="2026-09-01T18:00:00-04:00",
            )
            repo.upsert_session(legacy)
            # The write stored NULL because status was unset.
            import sqlite3

            conn = sqlite3.connect(tmp + "/fitdash.db")
            status = conn.execute("SELECT status FROM workout_sessions").fetchone()[0]
            conn.close()
            self.assertIsNone(status)
            loaded = repo.list_sessions()[0]
            self.assertTrue(session_counts_for_streak(loaded))
            self.assertEqual(effective_status(loaded), "legacy")

