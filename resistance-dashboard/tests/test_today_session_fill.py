"""Hybrid Today session_type fill before SuperGrok Generate (#240)."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.dashboard import dashboard_body
from rt_dashboard.grok_oauth import CONNECT_ERROR
from rt_dashboard.grok_planner import dashboard_plan_slots, generate_grok_plans
from rt_dashboard.models import ExerciseEntry, HealthSnapshot, Session, SetEntry
from rt_dashboard.workout_store import load_workspace_goals, stamp_today_session


def _session(date, st="push"):
    return Session(
        date=date,
        session_type=st,
        exercises=[
            ExerciseEntry(
                name="DB Flat Press",
                sets=[SetEntry(weight_lbs=45, sets=2, reps=10)],
            )
        ],
    )


def _signed_headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class StampTodaySession(unittest.TestCase):
    def test_goals_missing_session_type_is_null(self):
        slot = stamp_today_session({"exercises": []}, [], None)
        self.assertIsNone(slot.get("session_type"))
        self.assertIsNone(slot.get("next_session_type"))
        self.assertEqual((slot.get("training_continuity") or {}).get("phase"), "restart")

    def test_cold_start_is_rotation_zero_restart_not_hard_plan(self):
        goals, _ = load_workspace_goals()
        slot = stamp_today_session(
            {"exercises": [], "empty": True, "message": "Connect SuperGrok"},
            [],
            goals,
        )
        self.assertEqual(slot["session_type"], goals["rotation"][0])
        self.assertEqual(slot["next_session_type"], goals["rotation"][0])
        self.assertEqual(slot["exercises"], [])
        self.assertEqual((slot.get("training_continuity") or {}).get("phase"), "restart")
        self.assertIsNone((slot.get("training_continuity") or {}).get("days_since"))

    def test_days_since_60_is_restart(self):
        goals, _ = load_workspace_goals()
        slot = stamp_today_session(
            {"exercises": []},
            [_session("2026-06-20", "push")],
            goals,
            as_of="2026-08-19",
        )
        self.assertEqual(slot["session_type"], "pull")
        cont = slot.get("training_continuity") or {}
        self.assertEqual(cont.get("phase"), "restart")
        self.assertGreaterEqual(cont.get("days_since"), 60)
        self.assertTrue(cont.get("summary"))

    def test_rest_gate_fills_rest_slot_keeps_next_ppl(self):
        goals, _ = load_workspace_goals()
        slot = stamp_today_session(
            {"exercises": [], "empty": True},
            [_session("2026-08-17", "push")],
            goals,
            {"score": 35, "sparse": False},
            as_of="2026-08-18",
        )
        self.assertTrue(slot["is_rest_day"])
        self.assertEqual(slot["session_type"], "rest")
        self.assertEqual(slot["next_session_type"], "pull")
        self.assertEqual(slot["exercises"], [])


class DashboardPlanSlotsHybrid(unittest.TestCase):
    def test_supergrok_disconnected_session_type_present(self):
        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={
                "token": None,
                "source": "none",
                "expired": False,
                "error": CONNECT_ERROR,
            },
        ):
            _meal, workout = dashboard_plan_slots("user-1", sessions=[], goals=None)
        self.assertEqual(workout["exercises"], [])
        self.assertEqual(workout["session_type"], "push")
        self.assertEqual((workout.get("training_continuity") or {}).get("phase"), "restart")

    def test_layoff_continuity_restart(self):
        goals, _ = load_workspace_goals()
        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={
                "token": None,
                "source": "none",
                "expired": False,
                "error": CONNECT_ERROR,
            },
        ):
            _meal, workout = dashboard_plan_slots(
                "user-1",
                sessions=[_session("2026-06-20", "legs")],
                goals=goals,
                as_of="2026-08-20",
            )
        self.assertEqual(workout["session_type"], "push")
        self.assertEqual(workout["exercises"], [])
        cont = workout.get("training_continuity") or {}
        self.assertEqual(cont.get("phase"), "restart")
        self.assertGreaterEqual(cont.get("days_since"), 60)


class DashboardPayloadHybrid(unittest.TestCase):
    def test_get_dashboard_always_stamps_workout_session_type(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([_session("2026-08-17", "push")], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ), mock.patch(
                "rt_dashboard.grok_ask.resolve_xai_credentials",
                return_value={
                    "token": None,
                    "source": "none",
                    "expired": False,
                    "error": CONNECT_ERROR,
                },
            ):
                status, body = dashboard_body(_signed_headers())
        self.assertEqual(status, 200)
        wo = body.get("workout") or {}
        self.assertEqual(wo.get("session_type"), "pull")
        self.assertEqual(wo.get("next_session_type"), "pull")
        self.assertEqual(wo.get("exercises") or [], [])
        cont = wo.get("training_continuity") or {}
        self.assertIn(cont.get("phase"), ("normal", "rusty", "return", "reentry", "restart"))
        self.assertIn("summary", cont)
        self.assertEqual(body["workout_store"]["next_session_type"], "pull")
        self.assertEqual(
            (body["workout_store"].get("training_continuity") or {}).get("phase"),
            cont.get("phase"),
        )

    def test_cold_start_dashboard_workout_is_restart_not_fake_hard_plan(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(HealthSnapshot(), []),
            ):
                status, body = dashboard_body(_signed_headers())
        self.assertEqual(status, 200)
        wo = body.get("workout") or {}
        self.assertEqual(wo.get("session_type"), "push")
        self.assertEqual(wo.get("exercises") or [], [])
        self.assertEqual((wo.get("training_continuity") or {}).get("phase"), "restart")

    def test_rest_gate_dashboard_workout_exposes_next_ppl(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        from rt_dashboard.models import RecoveryStatus, SleepSample

        recovery = RecoveryStatus(label="Caution", score=35.0, reasons=["unit"])

        health = HealthSnapshot()
        health.sleep = [
            SleepSample(date="2026-08-17", sleep_hours=7.5, source="google_health")
        ]
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([_session("2026-08-17", "push")], [], "turso"),
            ), mock.patch(
                "api.dashboard._load_health",
                return_value=(health, []),
            ), mock.patch(
                "rt_dashboard.recovery.compute_recovery_status",
                return_value=recovery,
            ):
                status, body = dashboard_body(_signed_headers())
        self.assertEqual(status, 200)
        wo = body.get("workout") or {}
        self.assertTrue(wo.get("is_rest_day"))
        self.assertEqual(wo.get("session_type"), "rest")
        self.assertEqual(wo.get("next_session_type"), "pull")
        self.assertEqual(body["workout_store"]["next_session_type"], "pull")


class GenerateIgnoresCatalogDefaultSets(unittest.TestCase):
    def test_generate_returns_exercises_without_catalog_default_sets_three(self):
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return {
                "answer": json.dumps(
                    {
                        "meal": {"message": "ok", "items": [], "meals": []},
                        "workout": {
                            "session_type": "pull",
                            "is_rest_day": False,
                            "message": "Generated pull",
                            "exercises": [
                                {
                                    "name": "Seated Cable Row",
                                    "default_sets": 3,
                                    "prescription": {
                                        "sets": 2,
                                        "reps": 10,
                                        "weight_lbs": 80,
                                    },
                                }
                            ],
                        },
                    }
                ),
                "model": "grok-test",
                "auth_source": "supergrok_session",
            }

        catalog = {
            "exercises": [
                {
                    "name": "Seated Cable Row",
                    "available": True,
                    "default_sets": 3,
                }
            ]
        }
        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={
                "token": "user-token-must-not-leak",
                "source": "supergrok_session",
                "expired": False,
            },
        ), mock.patch(
            "rt_dashboard.grok_ask.chat_completions",
            side_effect=fake_chat,
        ):
            out = generate_grok_plans(
                "user-1",
                recovery={"score": 80, "sparse": False, "label": "Ready"},
                goals={
                    "rotation": ["push", "pull", "legs"],
                    "default_hard_sets": 2,
                    "rest_if_recovery_below": 40,
                },
                next_session_type="pull",
                catalog=catalog,
                sessions_brief=[{"date": "2026-08-17", "session_type": "push"}],
            )
        self.assertTrue(out["ok"])
        exercises = out["workout"]["exercises"]
        self.assertEqual(len(exercises), 1)
        self.assertEqual(exercises[0]["name"], "Seated Cable Row")
        self.assertNotEqual(exercises[0].get("default_sets"), 3)
        self.assertEqual(exercises[0].get("default_sets"), 2)
        self.assertEqual((exercises[0].get("prescription") or {}).get("sets"), 2)
        blob = captured["messages"][1]["content"]
        self.assertIn("default_sets=3", blob)
        self.assertIn('"default_hard_sets": 2', blob)
        self.assertIn('"ignore_catalog_default_sets": true', blob)
        self.assertNotIn("user-token-must-not-leak", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
