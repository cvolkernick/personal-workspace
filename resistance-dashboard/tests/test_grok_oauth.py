"""SuperGrok device-code + planner hook. No live xAI token store."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from api.ask._post import ask_body
from api.ask.grok.disconnect import grok_disconnect_body
from api.ask.grok.poll import grok_poll_body
from api.ask.grok.start import grok_start_body
from api.ask.plan import ask_plan_body
from api.ask.status import ask_status_body
from api.auth.session_util import SESSION_COOKIE, make_session
from rt_dashboard.grok_oauth import CONNECT_ERROR, public_start_payload
from rt_dashboard.grok_planner import (
    dashboard_plan_slots,
    generate_grok_plans,
    honest_empty_meal,
    honest_empty_workout,
)


def _session_headers():
    token = make_session(
        {"id": "user-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class CookieLessAskRoutes(unittest.TestCase):
    def test_start_status_ask_disconnect_plan_are_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for status, body in (
                grok_start_body({})[:2],
                ask_status_body({}),
                ask_body({}, {}),
                grok_disconnect_body({}),
                ask_plan_body({}),
            ):
                self.assertEqual(status, 401)
                self.assertEqual(body["error"], "auth_required")
                self.assertNotIn("client_secret", json.dumps(body))
                raw = json.dumps(body)
                self.assertNotIn("<html", raw.lower())


class StartPayload(unittest.TestCase):
    def test_public_start_never_includes_secrets(self):
        raw = {
            "ok": True,
            "verification_uri": "https://accounts.x.ai/oauth2/device",
            "user_code": "ABCD-EFGH",
            "expires_in": 1800,
            "interval": 5,
            "_device_code": "secret-device",
            "client_secret": "should-never-leak",
            "client_id": "should-never-leak",
            "device_code": "should-never-leak",
            "access_token": "should-never-leak",
        }
        pub = public_start_payload(raw)
        blob = json.dumps(pub)
        self.assertTrue(pub["ok"])
        self.assertIn("verification_uri", pub)
        self.assertIn("user_code", pub)
        for banned in (
            "client_secret",
            "client_id",
            "device_code",
            "_device_code",
            "access_token",
            "refresh_token",
        ):
            self.assertNotIn(banned, pub)
            self.assertNotIn("should-never-leak", blob)

    def test_start_with_cookie_strips_secret(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        started = {
            "ok": True,
            "verification_uri": "https://accounts.x.ai/oauth2/device",
            "user_code": "ABCD-EFGH",
            "expires_in": 1800,
            "interval": 5,
            "_device_code": "dc-test",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.grok_oauth.start_device_code", return_value=started
            ):
                status, body, extra = grok_start_body(_session_headers())
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["user_code"], "ABCD-EFGH")
        blob = json.dumps(body)
        self.assertNotIn("client_secret", blob)
        self.assertNotIn("dc-test", blob)
        self.assertNotIn("b1a00492", blob)
        self.assertTrue(any(k == "Set-Cookie" for k, _ in extra))


class AskStatusCreds(unittest.TestCase):
    def test_none_vs_fallback_vs_connected_no_tokens(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        headers = None
        with mock.patch.dict(os.environ, env, clear=True):
            headers = _session_headers()
            with mock.patch(
                "rt_dashboard.grok_ask.resolve_xai_credentials",
                return_value={
                    "token": None,
                    "source": "none",
                    "email": None,
                    "expires_at": None,
                    "expired": False,
                    "error": CONNECT_ERROR,
                },
            ):
                status, body = ask_status_body(headers)
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertFalse(body.get("connected"))
        self.assertEqual(body["source"], "none")
        self.assertIn("Connect SuperGrok", body["error"])
        self.assertNotIn("token", body)
        self.assertNotIn("access_token", json.dumps(body))

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.grok_ask.resolve_xai_credentials",
                return_value={
                    "token": "env-key-must-not-leak",
                    "source": "xai_api_key",
                    "email": None,
                    "expires_at": None,
                    "expired": False,
                },
            ):
                status, body = ask_status_body(headers)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body.get("connected"))
        self.assertEqual(body["source"], "xai_api_key")
        self.assertNotIn("env-key-must-not-leak", json.dumps(body))

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "rt_dashboard.grok_ask.resolve_xai_credentials",
                return_value={
                    "token": "user-token-must-not-leak",
                    "source": "supergrok_session",
                    "email": "g@example.com",
                    "expires_at": "2026-08-19T00:00:00Z",
                    "expired": False,
                },
            ):
                status, body = ask_status_body(headers)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["connected"])
        self.assertEqual(body["email"], "g@example.com")
        self.assertNotIn("user-token-must-not-leak", json.dumps(body))


class PlannerHonestEmpty(unittest.TestCase):
    def test_dashboard_slots_honest_empty_without_creds(self):
        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={"token": None, "source": "none", "expired": False, "error": CONNECT_ERROR},
        ):
            meal, workout = dashboard_plan_slots("user-1")
        self.assertEqual(meal["items"], [])
        self.assertEqual(meal["meals"], [])
        self.assertTrue(meal["empty"])
        self.assertIn("Connect SuperGrok", meal["message"])
        self.assertEqual(workout["exercises"], [])
        self.assertTrue(workout["empty"])
        self.assertIn("Connect SuperGrok", workout["message"])
        self.assertIsNone(meal.get("inventory"))
        # Hybrid fill: session_type + continuity even when SuperGrok is down.
        self.assertEqual(workout["session_type"], "push")
        self.assertEqual(workout["next_session_type"], "push")
        cont = workout.get("training_continuity") or {}
        self.assertEqual(cont.get("phase"), "restart")
        self.assertIsNone(cont.get("days_since"))
        self.assertTrue(cont.get("summary"))

    def test_generate_does_not_invent_canned_plan(self):
        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials",
            return_value={"token": None, "source": "none", "expired": False, "error": CONNECT_ERROR},
        ):
            out = generate_grok_plans("user-1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["meal"]["items"], [])
        self.assertEqual(out["workout"]["exercises"], [])
        self.assertIn("Connect SuperGrok", out["error"])
        self.assertEqual(out["workout"]["session_type"], "push")
        self.assertEqual((out["workout"].get("training_continuity") or {}).get("phase"), "restart")

    def test_low_recovery_still_passes_next_ppl_into_plan_context(self):
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return {
                "answer": json.dumps(
                    {
                        "meal": {"message": "ok", "items": [], "meals": []},
                        "workout": {
                            "session_type": "rest",
                            "is_rest_day": True,
                            "message": "Generated rest day",
                            "exercises": [],
                        },
                    }
                ),
                "model": "grok-test",
                "auth_source": "supergrok_session",
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
                recovery={"score": 35, "sparse": False, "label": "Caution"},
                goals={"rest_if_recovery_below": 40, "split": "ppl"},
                next_session_type="pull",
                catalog={"exercises": [{"name": "DB Flat Press", "available": True}]},
            )
        self.assertTrue(out["ok"])
        blob = captured["messages"][1]["content"]
        self.assertIn('"next_session_type": "pull"', blob)
        self.assertIn('"rest_if_recovery_below": 40', blob)
        self.assertIn('"force_rest": true', blob)
        self.assertTrue(out["workout"]["is_rest_day"])
        self.assertEqual(out["workout"]["session_type"], "rest")
        self.assertEqual((out["workout"].get("rest_gate") or {}).get("force_rest"), True)
        self.assertEqual(out["workout"].get("next_session_type"), "pull")
        self.assertNotIn("user-token-must-not-leak", json.dumps(out))

    def test_honest_empty_helpers_have_no_canned_food(self):
        meal = honest_empty_meal()
        workout = honest_empty_workout()
        blob = json.dumps(meal) + json.dumps(workout)
        self.assertNotIn("chicken", blob.lower())
        self.assertNotIn("yogurt", blob.lower())
        self.assertNotIn("rice", blob.lower())


class PollNoTokens(unittest.TestCase):
    def test_approved_response_omits_tokens(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        ticket = None
        with mock.patch.dict(os.environ, env, clear=True):
            from rt_dashboard.grok_oauth import DEVICE_COOKIE, make_device_ticket

            ticket = make_device_ticket("dc-test", 1800)
            headers = _session_headers()
            headers["Cookie"] += f"; {DEVICE_COOKIE}={ticket}"
            poll = {
                "ok": True,
                "status": "approved",
                "email": "g@example.com",
                "_tokens": {
                    "access_token": "tok-must-not-leak",
                    "refresh_token": "rt-must-not-leak",
                    "expires_at": "2026-08-19T00:00:00Z",
                    "email": "g@example.com",
                },
            }
            with mock.patch("rt_dashboard.grok_oauth.poll_device_code", return_value=poll):
                with mock.patch("rt_dashboard.grok_sessions.save_grok_session") as save:
                    status, body, _extra = grok_poll_body(headers)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["connected"])
        blob = json.dumps(body)
        self.assertNotIn("tok-must-not-leak", blob)
        self.assertNotIn("rt-must-not-leak", blob)
        self.assertNotIn("access_token", blob)
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
