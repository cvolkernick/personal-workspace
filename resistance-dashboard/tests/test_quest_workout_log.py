"""Lift quest complete upserts today's log. Non-lifts do not write."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import daily_tasks_complete_body
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.pr_detect import apply_auto_prs
from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.quest_workout_log import (
    apply_quest_to_session,
    attach_lift_quest_log,
    is_unedited_seed,
    looks_like_lift_quest,
    parse_quest_title,
    ppl_session_type,
    seed_exercise,
    seed_fingerprint,
)
from rt_dashboard.workout_log import parse_log_body

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
UTIL = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _seeded(name="DB Flat Press", weight=50.0, sets=3, reps=10):
    entry = seed_exercise(
        name,
        title_rx={"weight_lbs": weight, "sets": sets, "reps": reps},
    )
    return entry


PUSH_PLAN = {
    "session_type": "push",
    "is_rest_day": False,
    "exercises": [
        {"name": "DB Flat Press", "weight_lbs": 50, "sets": 3, "reps": 10},
        {"name": "Push-up"},  # no prescription
    ],
}


class TitleParse(unittest.TestCase):
    def test_prescription_from_quest_title(self):
        parsed = parse_quest_title("DB Flat Press (50 lb 3×10)")
        self.assertEqual(parsed["name"], "DB Flat Press")
        self.assertEqual(parsed["weight_lbs"], 50)
        self.assertEqual(parsed["sets"], 3)
        self.assertEqual(parsed["reps"], 10)

    def test_name_only_is_movement(self):
        parsed = parse_quest_title("Push-up")
        self.assertEqual(parsed["name"], "Push-up")
        self.assertIsNone(parsed["weight_lbs"])
        self.assertIsNone(parsed["sets"])


class LiftDetection(unittest.TestCase):
    def test_training_ex_slug_is_lift(self):
        self.assertTrue(
            looks_like_lift_quest(
                group="training",
                title="DB Flat Press (50 lb 3×10)",
                slug="ex-db-flat-press",
            )
        )

    def test_meal_is_not_lift(self):
        self.assertFalse(
            looks_like_lift_quest(
                group="nutrition",
                title="Next meal: Chicken · 210g",
                slug="meal-0-chicken-0",
            )
        )

    def test_hydration_is_not_lift(self):
        self.assertFalse(
            looks_like_lift_quest(
                group="other",
                title="Drink 3L water",
                slug="action-hydration-0",
            )
        )
        self.assertFalse(
            looks_like_lift_quest(
                group="nutrition",
                title="Hydration: 3L",
                slug="hydrate",
            )
        )

    def test_session_action_is_not_lift(self):
        self.assertFalse(
            looks_like_lift_quest(
                group="training",
                title="Complete today's PUSH session",
                slug="train-session",
            )
        )


class SeedHonesty(unittest.TestCase):
    def test_full_rx_copies_plan(self):
        entry = seed_exercise(
            "DB Flat Press",
            planned=PUSH_PLAN["exercises"][0],
        )
        self.assertTrue(entry.quest_seeded)
        self.assertFalse(entry.is_pr)
        self.assertEqual(entry.sets[0].weight_lbs, 50)
        self.assertEqual(entry.sets[0].sets, 3)
        self.assertEqual(entry.sets[0].reps, 10)
        self.assertEqual(entry.raw, seed_fingerprint(entry.sets))

    def test_no_rx_is_movement_only(self):
        entry = seed_exercise("Push-up", planned={"name": "Push-up"})
        self.assertTrue(entry.quest_seeded)
        self.assertFalse(entry.sets)
        self.assertFalse(entry.is_pr)
        self.assertEqual(entry.raw, "quest-seeded:movement")

    def test_partial_rx_does_not_invent_weight(self):
        entry = seed_exercise(
            "Push-up",
            title_rx={"sets": 3, "reps": 10},
        )
        self.assertFalse(entry.sets)


class ApplyQuestToSession(unittest.TestCase):
    def test_lift_complete_upserts(self):
        session, info = apply_quest_to_session(
            completed=True,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[],
            today="2026-08-23",
        )
        self.assertEqual(info["action"], "upsert")
        self.assertTrue(info["wrote"])
        self.assertIsNotNone(session)
        self.assertEqual(session.date, "2026-08-23")
        self.assertEqual(session.session_type, "push")
        self.assertEqual(len(session.exercises), 1)
        ex = session.exercises[0]
        self.assertEqual(ex.name, "DB Flat Press")
        self.assertEqual(ex.sets[0].weight_lbs, 50)
        self.assertTrue(ex.quest_seeded)
        self.assertFalse(ex.is_pr)

    def test_no_prescription_is_movement_only(self):
        session, info = apply_quest_to_session(
            completed=True,
            group="training",
            title="Push-up",
            slug="ex-push-up",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[],
            today="2026-08-23",
        )
        self.assertEqual(info["action"], "upsert")
        self.assertTrue(info["movement_only"])
        self.assertFalse(session.exercises[0].sets)
        self.assertNotIn(0, [s.weight_lbs for s in session.exercises[0].sets])

    def test_meal_complete_does_not_write(self):
        session, info = apply_quest_to_session(
            completed=True,
            group="nutrition",
            title="Next meal: Chicken · 210g",
            slug="meal-0-chicken-0",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[],
            today="2026-08-23",
        )
        self.assertIsNone(session)
        self.assertFalse(info["wrote"])
        self.assertEqual(info["reason"], "not_lift")

    def test_hydration_complete_does_not_write(self):
        session, info = apply_quest_to_session(
            completed=True,
            group="other",
            title="Drink 3L water",
            slug="hydrate",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[],
            today="2026-08-23",
        )
        self.assertIsNone(session)
        self.assertFalse(info["wrote"])

    def test_duplicate_complete_single_entry(self):
        first, _ = apply_quest_to_session(
            completed=True,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[],
            today="2026-08-23",
        )
        again, info = apply_quest_to_session(
            completed=True,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[first],
            today="2026-08-23",
        )
        self.assertIsNone(again)
        self.assertEqual(info["action"], "dedupe")
        self.assertEqual(len(first.exercises), 1)

    def test_uncheck_removes_unedited_seed(self):
        seeded = _seeded()
        existing = Session(
            date="2026-08-23",
            session_type="push",
            exercises=[seeded],
        )
        session, info = apply_quest_to_session(
            completed=False,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[existing],
            today="2026-08-23",
        )
        self.assertEqual(info["action"], "uncheck_remove")
        self.assertTrue(info["wrote"])
        self.assertEqual(session.exercises, [])

    def test_uncheck_keeps_edited_row(self):
        edited = ExerciseEntry(
            name="DB Flat Press",
            sets=[SetEntry(weight_lbs=55, sets=3, reps=8)],
            is_pr=False,
            quest_seeded=False,
            raw="",
        )
        existing = Session(
            date="2026-08-23",
            session_type="push",
            exercises=[edited],
        )
        session, info = apply_quest_to_session(
            completed=False,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[existing],
            today="2026-08-23",
        )
        self.assertIsNone(session)
        self.assertEqual(info["action"], "uncheck_keep")
        self.assertEqual(info["reason"], "edited")

    def test_uncheck_keeps_seed_whose_load_changed(self):
        changed = _seeded()
        changed.sets[0].weight_lbs = 60
        self.assertFalse(is_unedited_seed(changed))
        existing = Session(
            date="2026-08-23",
            session_type="push",
            exercises=[changed],
        )
        session, info = apply_quest_to_session(
            completed=False,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[existing],
            today="2026-08-23",
        )
        self.assertIsNone(session)
        self.assertEqual(info["action"], "uncheck_keep")

    def test_rest_day_lift_leaf_still_writes(self):
        session, info = apply_quest_to_session(
            completed=True,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="rest",
            today_workout={
                "session_type": "rest",
                "is_rest_day": True,
                "next_session_type": "push",
                "exercises": [
                    {"name": "DB Flat Press", "weight_lbs": 50, "sets": 3, "reps": 10}
                ],
            },
            sessions=[],
            today="2026-08-23",
        )
        self.assertEqual(info["action"], "upsert")
        self.assertTrue(info["wrote"])
        self.assertEqual(session.session_type, "push")
        self.assertEqual(session.exercises[0].name, "DB Flat Press")
        self.assertEqual(info["session_type"], "push")

    def test_appends_to_existing_today_session(self):
        prior = Session(
            date="2026-08-23",
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="Cable Fly",
                    sets=[SetEntry(weight_lbs=20, sets=3, reps=12)],
                )
            ],
        )
        session, info = apply_quest_to_session(
            completed=True,
            group="training",
            title="DB Flat Press (50 lb 3×10)",
            slug="ex-db-flat-press",
            session_type="push",
            today_workout=PUSH_PLAN,
            sessions=[prior],
            today="2026-08-23",
        )
        self.assertEqual(info["action"], "upsert")
        self.assertEqual([e.name for e in session.exercises], ["Cable Fly", "DB Flat Press"])


class ParseLogBodyHonesty(unittest.TestCase):
    def test_manual_log_still_requires_sets(self):
        with self.assertRaises(ValueError) as ctx:
            parse_log_body(
                {
                    "session_type": "push",
                    "date": "2026-08-23",
                    "exercises": [{"name": "DB Flat Press"}],
                }
            )
        self.assertIn("no sets", str(ctx.exception))

    def test_movement_only_seed_allowed(self):
        session = parse_log_body(
            {
                "session_type": "push",
                "date": "2026-08-23",
                "exercises": [
                    {"name": "Push-up", "movement_only": True, "quest_seeded": True}
                ],
            }
        )
        self.assertEqual(session.exercises[0].name, "Push-up")
        self.assertFalse(session.exercises[0].sets)
        self.assertTrue(session.exercises[0].quest_seeded)
        self.assertFalse(session.exercises[0].is_pr)


class ApplyAutoPrsSkipsSeed(unittest.TestCase):
    def test_seeded_row_stays_unflagged(self):
        history = [
            Session(
                date="2026-05-01",
                session_type="push",
                exercises=[
                    ExerciseEntry(
                        name="DB Flat Press",
                        sets=[SetEntry(weight_lbs=40, sets=3, reps=10)],
                    )
                ],
            )
        ]
        seeded = _seeded()
        session = Session(
            date="2026-08-23", session_type="push", exercises=[seeded]
        )
        apply_auto_prs(session, history)
        self.assertFalse(session.exercises[0].is_pr)


class AttachAndRoute(unittest.TestCase):
    def test_attach_meal_does_not_persist(self):
        persist = mock.Mock()
        result = attach_lift_quest_log(
            {"ok": True, "task": {"id": "t1"}},
            {
                "group": "nutrition",
                "title": "Next meal: Chicken · 210g",
                "slug": "meal-0-chicken-0",
                "session_type": "push",
            },
            True,
            user_id="sub-1",
            sessions=[],
            today="2026-08-23",
            persist=persist,
        )
        persist.assert_not_called()
        self.assertFalse(result["workout_log"]["wrote"])
        self.assertEqual(result["workout_log"]["reason"], "not_lift")

    def test_attach_lift_persists_once(self):
        persist = mock.Mock(return_value={"ok": True, "path": "turso"})
        payload = {
            "group": "training",
            "title": "DB Flat Press (50 lb 3×10)",
            "slug": "ex-db-flat-press",
            "session_type": "push",
        }
        first = attach_lift_quest_log(
            {"ok": True},
            payload,
            True,
            user_id="sub-1",
            sessions=[],
            today="2026-08-23",
            today_workout=PUSH_PLAN,
            persist=persist,
        )
        persist.assert_called_once()
        written = persist.call_args[0][1]
        self.assertEqual(written.exercises[0].name, "DB Flat Press")
        self.assertFalse(written.exercises[0].is_pr)
        second = attach_lift_quest_log(
            {"ok": True},
            payload,
            True,
            user_id="sub-1",
            sessions=[written],
            today="2026-08-23",
            today_workout=PUSH_PLAN,
            persist=persist,
        )
        self.assertEqual(persist.call_count, 1)
        self.assertEqual(second["workout_log"]["action"], "dedupe")

    def test_complete_route_lift_upserts(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        persist = mock.Mock(return_value={"ok": True, "path": "turso"})
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t1"}},
            ), mock.patch(
                "rt_dashboard.quest_workout_log.persist_quest_session",
                persist,
            ), mock.patch(
                "rt_dashboard.quest_workout_log._default_load_sessions",
                return_value=[],
            ), mock.patch(
                "rt_dashboard.quest_workout_log.local_today_iso",
                return_value="2026-08-23",
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {
                        "list_id": "L1",
                        "task_id": "t1",
                        "completed": True,
                        "group": "training",
                        "title": "DB Flat Press (50 lb 3×10)",
                        "slug": "ex-db-flat-press",
                        "session_type": "push",
                    },
                )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertTrue(body["workout_log"]["wrote"])
        self.assertEqual(body["workout_log"]["action"], "upsert")
        persist.assert_called_once()
        session = persist.call_args[0][1]
        self.assertEqual(session.exercises[0].name, "DB Flat Press")
        self.assertEqual(session.exercises[0].sets[0].weight_lbs, 50)
        self.assertTrue(session.exercises[0].quest_seeded)
        self.assertFalse(session.exercises[0].is_pr)

    def test_complete_route_meal_does_not_write(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        persist = mock.Mock()
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t2"}},
            ), mock.patch(
                "rt_dashboard.quest_workout_log.persist_quest_session",
                persist,
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {
                        "list_id": "L1",
                        "task_id": "t2",
                        "completed": True,
                        "group": "nutrition",
                        "title": "Next meal: Oats · 80g",
                        "slug": "meal-0-oats-0",
                    },
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        persist.assert_not_called()
        self.assertFalse(body["workout_log"]["wrote"])

    def test_complete_route_uncheck_removes_seed(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        persist = mock.Mock(return_value={"ok": True, "path": "turso"})
        existing = Session(
            date="2026-08-23",
            session_type="push",
            exercises=[_seeded()],
        )
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t1"}},
            ), mock.patch(
                "rt_dashboard.quest_workout_log.persist_quest_session",
                persist,
            ), mock.patch(
                "rt_dashboard.quest_workout_log._default_load_sessions",
                return_value=[existing],
            ), mock.patch(
                "rt_dashboard.quest_workout_log.local_today_iso",
                return_value="2026-08-23",
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {
                        "list_id": "L1",
                        "task_id": "t1",
                        "completed": False,
                        "group": "training",
                        "title": "DB Flat Press (50 lb 3×10)",
                        "slug": "ex-db-flat-press",
                        "session_type": "push",
                    },
                )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["workout_log"]["action"], "uncheck_remove")
        persist.assert_called_once()
        self.assertEqual(persist.call_args[0][1].exercises, [])

    def test_existing_complete_without_group_still_ok(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        persist = mock.Mock()
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t1"}},
            ), mock.patch(
                "rt_dashboard.quest_workout_log.persist_quest_session",
                persist,
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {"list_id": "L1", "task_id": "t1", "completed": True},
                )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        persist.assert_not_called()

    def test_complete_route_rest_session_type_still_writes(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        persist = mock.Mock(return_value={"ok": True, "path": "turso"})
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.daily_plan_tasks.complete_leaf",
                return_value={"ok": True, "task": {"id": "t1"}},
            ), mock.patch(
                "rt_dashboard.quest_workout_log.persist_quest_session",
                persist,
            ), mock.patch(
                "rt_dashboard.quest_workout_log._default_load_sessions",
                return_value=[],
            ):
                status, body = daily_tasks_complete_body(
                    _headers(),
                    {
                        "list_id": "L1",
                        "task_id": "t1",
                        "completed": True,
                        "group": "training",
                        "title": "DB Flat Press (50 lb 3×10)",
                        "slug": "ex-db-flat-press",
                        "date": "2026-08-23",
                        "session_type": "rest",
                        "next_session_type": "push",
                    },
                )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["workout_log"]["wrote"])
        self.assertEqual(body["workout_log"]["action"], "upsert")
        self.assertEqual(body["workout_log"]["session_type"], "push")
        persist.assert_called_once()
        session = persist.call_args[0][1]
        self.assertEqual(session.date, "2026-08-23")
        self.assertEqual(session.session_type, "push")
        self.assertEqual(session.exercises[0].name, "DB Flat Press")


class LoggedExercisesPaint(unittest.TestCase):
    def test_ppl_session_type_skips_rest(self):
        self.assertEqual(ppl_session_type("rest", "push"), "push")
        self.assertEqual(ppl_session_type("REST", None, "pull"), "pull")
        self.assertEqual(ppl_session_type("rest", ""), "")

    def test_lift_complete_lands_in_logged_exercises(self):
        persist = mock.Mock(return_value={"ok": True, "path": "turso"})
        written = []

        def _persist(uid, session):
            written.append(session)
            persist(uid, session)
            return {"ok": True, "path": "turso"}

        result = attach_lift_quest_log(
            {"ok": True},
            {
                "group": "training",
                "title": "DB Flat Press (50 lb 3×10)",
                "slug": "ex-db-flat-press",
                "session_type": "push",
                "date": "2026-08-23",
            },
            True,
            user_id="sub-1",
            sessions=[],
            today="2026-08-23",
            today_workout=PUSH_PLAN,
            persist=_persist,
        )
        self.assertTrue(result["workout_log"]["wrote"])
        session = written[0]
        food = {
            "date": "2026-08-23",
            "name": "Oats",
            "calories": 300,
            "protein_g": 12,
        }
        body = export_agent_today(
            {
                "sessions": [session],
                "workout": {"session_type": "push", "is_rest_day": False},
                "nutrition_store": {"food_logs_today": [food]},
                "health": {"food_logs": [food]},
                "coach": {"today": {"date": "2026-08-23"}},
                "meta": {"local_today": "2026-08-23"},
            }
        )
        logged = body["today"]["workout"]["logged_exercises"]
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["name"], "DB Flat Press")
        self.assertEqual(logged[0]["weight_lbs"], 50)
        self.assertEqual(body["today"]["workout"]["session_type"], "push")

    def test_uncheck_removes_unedited_seed_from_logged_exercises(self):
        seeded = _seeded()
        existing = Session(
            date="2026-08-23", session_type="push", exercises=[seeded]
        )
        persist = mock.Mock(return_value={"ok": True, "path": "turso"})
        result = attach_lift_quest_log(
            {"ok": True},
            {
                "group": "training",
                "title": "DB Flat Press (50 lb 3×10)",
                "slug": "ex-db-flat-press",
                "session_type": "push",
            },
            False,
            user_id="sub-1",
            sessions=[existing],
            today="2026-08-23",
            today_workout=PUSH_PLAN,
            persist=persist,
        )
        self.assertEqual(result["workout_log"]["action"], "uncheck_remove")
        emptied = persist.call_args[0][1]
        body = export_agent_today(
            {
                "sessions": [emptied],
                "coach": {"today": {"date": "2026-08-23"}},
                "meta": {"local_today": "2026-08-23"},
            }
        )
        self.assertEqual(body["today"]["workout"]["logged_exercises"], [])

    def test_food_logs_unaffected_by_lift_write(self):
        persist = mock.Mock(return_value={"ok": True, "path": "turso"})
        food = {"date": "2026-08-23", "name": "Eggs", "calories": 180}
        payload = {
            "sessions": [],
            "nutrition_store": {"food_logs_today": [food]},
            "health": {"food_logs": [food]},
            "coach": {"today": {"date": "2026-08-23"}},
            "meta": {"local_today": "2026-08-23"},
        }
        before = export_agent_today(payload)
        attach_lift_quest_log(
            {"ok": True},
            {
                "group": "training",
                "title": "Lateral Raises (20 lb 3×12)",
                "slug": "ex-lateral-raises",
                "session_type": "push",
            },
            True,
            user_id="sub-1",
            sessions=[],
            today="2026-08-23",
            today_workout=PUSH_PLAN,
            persist=persist,
        )
        after = export_agent_today(payload)
        self.assertEqual(
            (before.get("today") or {}),
            (after.get("today") or {}),
        )
        self.assertEqual(
            payload["nutrition_store"]["food_logs_today"][0]["name"], "Eggs"
        )
        persist.assert_called_once()

    def test_non_lift_does_not_write_or_change_logged_exercises(self):
        persist = mock.Mock()
        result = attach_lift_quest_log(
            {"ok": True},
            {
                "group": "nutrition",
                "title": "Next meal: Chicken · 210g",
                "slug": "meal-0-chicken-0",
                "session_type": "push",
            },
            True,
            user_id="sub-1",
            sessions=[],
            today="2026-08-23",
            persist=persist,
        )
        persist.assert_not_called()
        self.assertEqual(result["workout_log"]["reason"], "not_lift")
        body = export_agent_today(
            {
                "sessions": [],
                "workout": {"session_type": "push", "is_rest_day": False},
                "coach": {"today": {"date": "2026-08-23"}},
            }
        )
        self.assertEqual(body["today"]["workout"]["logged_exercises"], [])


class Wiring(unittest.TestCase):
    def test_complete_path_calls_attach(self):
        complete = UTIL.split("def daily_tasks_complete_body", 1)[1].split(
            "def inventory_write", 1
        )[0]
        self.assertIn("attach_lift_quest_log", complete)
        self.assertIn("complete_leaf", complete)
        self.assertNotIn("preview_read_only", complete)

    def test_ui_sends_group_and_title(self):
        handler = JS.split("async function onDailyQuestClick", 1)[1].split(
            "function bindDailyQuestClicks", 1
        )[0]
        self.assertIn("group: questGroup", handler)
        self.assertIn("title: questTitle", handler)
        self.assertIn("todayWo.session_type", handler)
        self.assertIn("pplType", handler)
        self.assertIn("next_session_type:", handler)
        self.assertIn("date: localToday", handler)
        self.assertIn("completed: wantCompleted", handler)
        self.assertIn("looksLikeLiftQuest", handler)
        self.assertIn("applyWorkoutLogToLocalState", handler)
        self.assertIn("uncheck_remove", JS)
        self.assertIn("today-logged-lifts", JS)
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("today-logged-lifts", html)
        render = JS.split("const renderCard = (it, g) =>", 1)[1].split(
            "Object.keys(byMeal)", 1
        )[0]
        self.assertIn("data-group=", render)
        self.assertIn("data-title=", render)
        self.assertIn("data-slug=", render)
        self.assertIn('done ? " is-done"', render)
        self.assertIn("Uncheck", render)
        self.assertIn("function looksLikeLiftQuest", JS)
        body = JS.split("function buildDailyQuestBodyHtml", 1)[1].split(
            "function applyQuestsCollapseDom", 1
        )[0]
        self.assertIn("looksLikeLiftQuest(g.group || x.group, x.title, x.slug)", body)
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".quest-card.is-done .quest-card-text", css)
        self.assertIn("text-decoration: line-through", css)


if __name__ == "__main__":
    unittest.main()
