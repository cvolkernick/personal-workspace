"""A full /log session with no quest check-off must not recurse.

The Today loader used to rebuild the morning plan by dropping only rows
whose civil date matched ``as_of``. After midnight the wake still counts
last night's log, the civil date does not, and that rebuild called itself
until Python raised RecursionError. The same full log on the same civil
day left an empty plan, so the quest said "0 lifts as prescribed".
"""

from __future__ import annotations

import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from rt_dashboard.analytics import dashboard_payload, volume_by_day
from rt_dashboard.coach import build_today_board, compute_weekly_review
from rt_dashboard.guardrails import build_guardrails
from rt_dashboard.models import ExerciseEntry, RecoveryStatus, Session, SetEntry
from rt_dashboard.muscle_sets import build_muscle_set_report
from rt_dashboard.workout_planner import (
    default_catalog,
    generate_workout_plan,
    normalize_goals,
    primary_hinge_key,
    programmed_groups,
)
from server import _redact_secrets, log_workout_plan_failure

ET = ZoneInfo("America/New_York")
MAP_STATES = {"under", "on", "over", "gap"}


def _sets(name: str, n: int = 3) -> ExerciseEntry:
    return ExerciseEntry(name=name, sets=[SetEntry(weight_lbs=100, sets=n, reps=8)])


def _prior_pull() -> Session:
    return Session(
        date="2026-11-16",
        session_type="pull",
        exercises=[_sets("Pulldowns")],
        closed_at="2026-11-16T23:00:00-05:00",
        status="complete",
    )


def _prescribe(day: str, when: datetime, wake: str, history):
    return generate_workout_plan(
        default_catalog(),
        normalize_goals({}),
        history,
        recovery_score=80,
        recovery_label="Ready",
        as_of=day,
        now=when,
        last_wake_at=wake,
        train_parent_completed=False,
    )


def _log_every_prescribed(plan: dict, day: str, closed_at: str) -> Session:
    """The Log tab save: real sets, no quest check-off, session left in progress."""
    rows = []
    for ex in plan.get("exercises") or []:
        n = int((ex.get("prescription") or {}).get("sets") or 2)
        rows.append(_sets(ex["name"], n))
    return Session(
        date=day,
        session_type=str(plan.get("session_type") or "legs"),
        exercises=rows,
        closed_at=closed_at,
        status="in_progress",
    )


class LogOnlySessionDoesNotRecurse(unittest.TestCase):
    def test_after_midnight_full_log_closes_without_recursion(self):
        """1:02 AM ET, wake still yesterday, every prescribed lift logged via /log."""
        evening = datetime(2026, 11, 17, 18, 0, tzinfo=ET)
        wake = "2026-11-17T07:00:00-05:00"
        prescribed = _prescribe("2026-11-17", evening, wake, [_prior_pull()])
        self.assertGreaterEqual(len(prescribed.get("exercises") or []), 5)
        logged = _log_every_prescribed(
            prescribed, "2026-11-17", "2026-11-17T20:30:00-05:00"
        )
        started = time.perf_counter()
        plan = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            [_prior_pull(), logged],
            recovery_score=80,
            recovery_label="Ready",
            as_of="2026-11-18",
            now=datetime(2026, 11, 18, 1, 2, tzinfo=ET),
            last_wake_at=wake,
            train_parent_completed=False,
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0, f"Today plan took {elapsed:.2f}s")
        self._assert_closed(plan, prescribed)

    def test_same_day_full_log_is_closed_not_zero_lifts(self):
        evening = datetime(2026, 11, 17, 18, 0, tzinfo=ET)
        wake = "2026-11-17T07:00:00-05:00"
        prescribed = _prescribe("2026-11-17", evening, wake, [_prior_pull()])
        logged = _log_every_prescribed(
            prescribed, "2026-11-17", "2026-11-17T20:30:00-05:00"
        )
        plan = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            [_prior_pull(), logged],
            recovery_score=80,
            recovery_label="Ready",
            as_of="2026-11-17",
            now=datetime(2026, 11, 17, 21, 0, tzinfo=ET),
            last_wake_at=wake,
            train_parent_completed=False,
        )
        self._assert_closed(plan, prescribed)

    def _assert_closed(self, plan, prescribed):
        letter = str(prescribed.get("session_type") or "").lower()
        self.assertIn(letter, ("push", "pull", "legs"))
        self.assertTrue(plan.get("already_trained_today"))
        self.assertTrue(plan.get("session_closed_today"))
        self.assertEqual(str(plan.get("session_type") or "").lower(), letter)
        self.assertEqual(plan.get("exercises") or [], [])
        matched = int(plan.get("prescribed_match_count") or 0)
        self.assertGreaterEqual(matched, 5)
        self.assertLessEqual(matched, len(prescribed["exercises"]))
        message = str(plan.get("message") or "")
        self.assertNotIn("SESSION", message)
        self.assertIn(letter.upper(), message)
        self.assertIn(f"{matched} lifts as prescribed", message)
        self.assertNotIn("0 lifts", message)
        board = build_today_board(
            as_of="2026-11-18",
            recovery=RecoveryStatus(label="Ready", score=80),
            workout_plan=plan,
            meal_plan={},
            consumed={},
            targets={},
            adherence={},
        )
        self.assertEqual(board.get("recommendation"), "done")
        train = next(a for a in board["actions"] if a.get("id") == "train-session")
        text = str(train.get("text") or "")
        self.assertNotIn("SESSION", text)
        self.assertNotRegex(text, r"today's SESSION session")
        self.assertRegex(
            text,
            rf"^{letter.title()} session complete \({matched} of \d+ lifts as prescribed\)$",
        )
        self.assertNotIn("0 lifts", text)

    def test_one_logged_lift_keeps_the_rest_of_the_plan(self):
        """A partial /log still drops only that lift. Rotation slots stay open."""
        history = [_prior_pull()]
        before = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            history,
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-17",
        )
        calf = Session(
            date="2026-11-17",
            session_type="legs",
            exercises=[_sets("Calf Extensions", 1)],
            closed_at="2026-11-17T20:00:00-05:00",
            status="in_progress",
        )
        after = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            history + [calf],
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-17",
            now=datetime(2026, 11, 17, 21, 0, tzinfo=ET),
            last_wake_at="2026-11-17T07:00:00-05:00",
            train_parent_completed=False,
        )
        before_names = [row["name"] for row in before["exercises"]]
        after_names = [row["name"] for row in after["exercises"]]
        self.assertIn("Calf Extensions", before_names)
        self.assertNotIn("Calf Extensions", after_names)
        for name in before_names:
            if name == "Calf Extensions":
                continue
            self.assertIn(name, after_names)
        self.assertFalse(after.get("already_trained_today"))
        hinges = [row for row in after["exercises"] if primary_hinge_key(row)]
        self.assertLessEqual(len(hinges), 1)
        total = sum(int((row.get("prescription") or {}).get("sets") or 0) for row in after["exercises"])
        self.assertLessEqual(total, 14)


class MuscleMapFollowsLoggedSets(unittest.TestCase):
    def test_body_map_states_match_the_list_after_a_log_only_session(self):
        evening = datetime(2026, 11, 17, 18, 0, tzinfo=ET)
        wake = "2026-11-17T07:00:00-05:00"
        prescribed = _prescribe("2026-11-17", evening, wake, [_prior_pull()])
        logged = _log_every_prescribed(
            prescribed, "2026-11-17", "2026-11-17T20:30:00-05:00"
        )
        plan = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            [_prior_pull(), logged],
            recovery_score=80,
            as_of="2026-11-18",
            now=datetime(2026, 11, 18, 1, 2, tzinfo=ET),
            last_wake_at=wake,
            train_parent_completed=False,
        )
        catalog = default_catalog()
        programmed = programmed_groups(catalog)
        self.assertIn("quads", programmed)
        payload = {
            "sessions": [logged.to_dict(), _prior_pull().to_dict()],
            "workout_store": {
                "catalog": catalog,
                "goals": normalize_goals({}),
                "plan": plan,
            },
        }
        report = build_muscle_set_report(payload, as_of="2026-11-18")
        by_id = {row["id"]: row for row in report["body_map"]}
        colored = 0
        for group, row in report["muscle_sets"].items():
            mapped = by_id[group]
            self.assertEqual(mapped["sets"], row["proper_sets"])
            self.assertIn(mapped["state"], MAP_STATES)
            if row["proper_sets"] and group in programmed:
                self.assertIn(mapped["state"], ("under", "on", "over"))
                self.assertNotIn("no programmed lift", mapped["detail"])
                colored += 1
            if group not in programmed:
                self.assertEqual(mapped["state"], "gap")
        self.assertGreater(report["muscle_sets"]["quads"]["proper_sets"], 0)
        self.assertGreater(colored, 0)
        # The failure mode: a wiped catalog still credits names, and the map
        # is grey because nothing is programmed.
        wiped = build_muscle_set_report(
            {
                "sessions": [logged.to_dict()],
                "workout_store": {
                    "catalog": {"exercises": []},
                    "plan": {"exercises": []},
                },
            },
            as_of="2026-11-18",
        )
        self.assertGreater(wiped["muscle_sets"]["quads"]["proper_sets"], 0)
        self.assertTrue(all(row["state"] == "gap" for row in wiped["body_map"]))


class TrendsCardsOnTheLogOnlyPath(unittest.TestCase):
    """Cards that read workout_plan, plus the session-backed Trends cards."""

    def setUp(self):
        evening = datetime(2026, 11, 17, 18, 0, tzinfo=ET)
        wake = "2026-11-17T07:00:00-05:00"
        self.prescribed = _prescribe("2026-11-17", evening, wake, [_prior_pull()])
        self.logged = _log_every_prescribed(
            self.prescribed, "2026-11-17", "2026-11-17T20:30:00-05:00"
        )
        self.prior = _prior_pull()
        self.plan = generate_workout_plan(
            default_catalog(),
            normalize_goals({}),
            [self.prior, self.logged],
            recovery_score=80,
            as_of="2026-11-18",
            now=datetime(2026, 11, 18, 1, 2, tzinfo=ET),
            last_wake_at=wake,
            train_parent_completed=False,
        )
        self.sessions = [self.prior, self.logged]
        self.payload = {
            "sessions": [s.to_dict() for s in self.sessions],
            "workout_store": {
                "catalog": default_catalog(),
                "goals": normalize_goals({}),
                "plan": self.plan,
            },
        }

    def test_guardrails_read_the_plan_and_still_render(self):
        report = build_guardrails(self.payload, as_of="2026-11-18")
        tiles = report.get("tiles") or []
        self.assertGreaterEqual(len(tiles), 1)
        self.assertTrue(all(isinstance(tile, dict) and tile.get("id") for tile in tiles))
        # This plan is not a deload. The log date must not be painted as one.
        self.assertNotEqual(str(self.plan.get("session_type") or ""), "deload")

    def test_session_cards_do_not_need_the_plan(self):
        days = volume_by_day(self.sessions, days=90)
        logged_day = next(row for row in days if row.get("date") == "2026-11-17")
        self.assertGreater(float(logged_day.get("volume") or 0), 0)
        trends = dashboard_payload(self.sessions, goals=normalize_goals({}))
        self.assertIn("Hack Squat", trends["strength_trends"])
        self.assertTrue(trends["strength_trends"]["Hack Squat"])
        review = compute_weekly_review(
            sessions=self.sessions,
            recovery=RecoveryStatus(label="Ready", score=80),
            weight=[],
            sleep=[],
            nutrition=[],
            targets={},
            adherence={},
            as_of="2026-11-18",
        )
        self.assertTrue(review.get("items") or review.get("bullets"))


class RotationStillVaries(unittest.TestCase):
    def test_push_compounds_change_across_weeks_and_guards_hold(self):
        catalog = default_catalog()
        goals = normalize_goals({})
        history = [
            Session(
                date="2026-11-10",
                session_type="pull",
                exercises=[_sets("Pulldowns", 2)],
                status="complete",
                closed_at="2026-11-10T20:00:00-05:00",
            )
        ]
        seen = []
        for day in ("2026-11-11", "2026-11-14", "2026-11-17"):
            plan = generate_workout_plan(
                catalog,
                goals,
                history,
                recovery_score=80,
                session_type="push",
                as_of=day,
            )
            names = [
                row["name"]
                for row in plan["exercises"]
                if row.get("movement") == "compound"
            ]
            seen.append(tuple(names))
            total = sum(
                int((row.get("prescription") or {}).get("sets") or 0)
                for row in plan["exercises"]
            )
            self.assertLessEqual(total, 14)
            self.assertGreaterEqual(len(plan["exercises"]), 5)
            history.append(
                Session(
                    date=day,
                    session_type="push",
                    exercises=[
                        ExerciseEntry(
                            name=row["name"],
                            sets=[
                                SetEntry(
                                    weight_lbs=50,
                                    sets=int((row.get("prescription") or {}).get("sets") or 2),
                                    reps=8,
                                )
                            ],
                        )
                        for row in plan["exercises"]
                    ],
                    status="complete",
                    closed_at=f"{day}T20:00:00-05:00",
                )
            )
        self.assertNotEqual(seen[0], seen[1])
        self.assertNotEqual(seen[1], seen[2])
        legs = generate_workout_plan(
            catalog,
            goals,
            history,
            recovery_score=80,
            session_type="legs",
            as_of="2026-11-18",
        )
        hinges = [row for row in legs["exercises"] if primary_hinge_key(row)]
        self.assertEqual(len(hinges), 1)
        self.assertLessEqual(
            sum(int((row.get("prescription") or {}).get("sets") or 0) for row in legs["exercises"]),
            14,
        )


class WorkoutPlanFailureLog(unittest.TestCase):
    def test_traceback_is_logged_and_secrets_are_not(self):
        secret = "github_pat_" + ("A" * 20)
        try:
            raise RuntimeError(f"boom bearer {secret} token=super-secret-value")
        except RuntimeError as exc:
            with self.assertLogs("fitdash.workout_plan", level="ERROR") as caught:
                log_workout_plan_failure(exc)
        text = "\n".join(caught.output)
        self.assertIn("Traceback", text)
        self.assertIn("RuntimeError", text)
        self.assertIn("workout_plan failed", text)
        self.assertNotIn(secret, text)
        self.assertNotIn("super-secret-value", text)
        self.assertIn("[redacted]", text)
        self.assertNotIn(secret, _redact_secrets(f"x-access-token:{secret}@github.com"))


class TodayErrorBannerStays(unittest.TestCase):
    def test_load_errors_are_persistent_banners(self):
        from pathlib import Path

        js = Path(__file__).resolve().parents[1].joinpath("static", "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("opts.persist", js)
        self.assertIn('showAlert(`Lift source note: ${meta.error}`, "warn", { persist: true })', js)
        self.assertIn('showAlert(`Partial load: ${data.meta.error}`, "warn", { persist: true })', js)
        self.assertIn(
            'showAlert(`Failed to load dashboard: ${e.message}`, "err", { persist: true })',
            js,
        )
        sw = Path(__file__).resolve().parents[1].joinpath("static", "sw.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('const CACHE = "fitdash-shell-v176"', sw)


if __name__ == "__main__":
    unittest.main()
