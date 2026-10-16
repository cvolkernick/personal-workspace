"""#1077: xAI spending-limit degrades to a saved or template plan.

No live xAI call. No env, key, credit, or billing change.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from api.healthz import healthz_body
from api.workout._util import agent_today_body
from rt_dashboard.ai_planner import (
    QUOTA_TITLE,
    clear_ai_planner_state,
    format_quota_message,
    note_quota,
    public_snapshot,
)
from rt_dashboard.agent_plan import ensure_today_grok_plan
from rt_dashboard.grok_ask import (
    CAPACITY_USER_MSG,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    GrokAskError,
    _http_ask_error,
    chat_completions,
)
from rt_dashboard.nutrition_planner import generate_meal_plan
from rt_dashboard.workout_plan_store import (
    clear_memory_workout_plans,
    load_last_good_workout_plan,
    save_last_good_workout_plan,
)

ROOT = Path(__file__).resolve().parents[1]


def _load_ask_py():
    """Vercel loads api/ask.py by path. The api/ask package is a different module."""
    path = ROOT / "api" / "ask.py"
    spec = importlib.util.spec_from_file_location("fitdash_ask_py_1077", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


ASK_PY = _load_ask_py()
SPENDING_BODY = (
    '{"code":"personal-team-blocked:spending-limit","error":"You have run out of '
    'credits or need a Grok subscription."}'
)
AUG_BODY = (
    '{"code":"permission-denied","error":"Your team has either used all available '
    'credits or reached its monthly spending limit."}'
)
CAPACITY_BODY = (
    '{"code":"resource-exhausted","error":"The model is currently at capacity '
    'due to high demand."}'
)
CREDS = {
    "token": "session-token",
    "source": "supergrok_session",
    "expired": False,
}
KEY_CREDS = {
    "token": "api-key-token",
    "source": "xai_api_key",
    "expired": False,
}


def _token_headers():
    return {"X-FitDash-Service-Token": "house-secret"}


class XaiQuotaHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ,
            {
                "RESISTANCE_DASHBOARD_CONFIG_DIR": self._tmp.name,
                "FITDASH_AI_PLANNER_PERSIST": "0",
            },
        )
        self._env.start()
        clear_ai_planner_state()
        clear_memory_workout_plans()
        self._turso = mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=False
        )
        self._turso.start()

    def tearDown(self):
        clear_ai_planner_state()
        clear_memory_workout_plans()
        self._turso.stop()
        self._env.stop()
        self._tmp.cleanup()


class HttpAskErrorQuota(XaiQuotaHarness):
    def test_spending_limit_403_is_xai_quota(self):
        err = _http_ask_error(403, SPENDING_BODY)
        self.assertEqual(err.kind, "xai_quota")
        self.assertEqual(err.xai_code, "personal-team-blocked:spending-limit")
        text = str(err)
        self.assertTrue(text.startswith(QUOTA_TITLE))
        self.assertIn("xai_quota", text)
        self.assertIn("personal-team-blocked:spending-limit", text)
        self.assertNotIn("{", text)

    def test_aug_permission_denied_credits_is_xai_quota(self):
        err = _http_ask_error(403, AUG_BODY)
        self.assertEqual(err.kind, "xai_quota")
        self.assertEqual(err.xai_code, "permission-denied")
        text = str(err)
        self.assertTrue(text.startswith(QUOTA_TITLE))
        self.assertIn("xai_quota", text)
        self.assertIn("permission-denied", text)
        self.assertNotIn("{", text)

    def test_429_resource_exhausted_stays_capacity(self):
        err = _http_ask_error(429, CAPACITY_BODY)
        self.assertEqual(err.kind, "capacity")
        self.assertEqual(str(err), CAPACITY_USER_MSG)
        self.assertNotIn("xai_quota", str(err))
        self.assertNotIn("{", str(err))


class ChatQuotaDoesNotRetryModel(XaiQuotaHarness):
    def test_quota_does_not_call_fallback_model(self):
        calls = []

        def fake_post(token, body):
            calls.append(body["model"])
            raise _http_ask_error(403, SPENDING_BODY)

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials", return_value=dict(KEY_CREDS)
        ), mock.patch("rt_dashboard.grok_ask._post_chat", side_effect=fake_post):
            with self.assertRaises(GrokAskError) as ctx:
                chat_completions(
                    [{"role": "user", "content": "plan"}], user_id="quota-user"
                )
        self.assertEqual(calls, [DEFAULT_MODEL])
        self.assertNotIn(FALLBACK_MODEL, calls)
        self.assertEqual(ctx.exception.kind, "xai_quota")
        self.assertNotIn("{", str(ctx.exception))
        self.assertEqual(ctx.exception.body, "")

    def test_session_quota_retries_api_key_once_then_stops(self):
        calls = []

        def fake_post(token, body):
            calls.append((token, body["model"]))
            raise _http_ask_error(403, SPENDING_BODY)

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials", return_value=dict(CREDS)
        ), mock.patch(
            "rt_dashboard.grok_ask._env_api_key_creds",
            return_value={"token": "api-key-token", "source": "xai_api_key"},
        ), mock.patch("rt_dashboard.grok_ask._post_chat", side_effect=fake_post):
            with self.assertRaises(GrokAskError) as ctx:
                chat_completions(
                    [{"role": "user", "content": "plan"}], user_id="quota-user"
                )
        self.assertEqual(
            calls,
            [("session-token", DEFAULT_MODEL), ("api-key-token", DEFAULT_MODEL)],
        )
        self.assertEqual(ctx.exception.kind, "xai_quota")
        self.assertEqual(ctx.exception.source, "xai_api_key")
        self.assertNotIn("{", str(ctx.exception))

    def test_backoff_skips_xai_until_force(self):
        calls = []

        def fake_post(token, body):
            calls.append(body["model"])
            raise _http_ask_error(403, SPENDING_BODY)

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials", return_value=dict(KEY_CREDS)
        ), mock.patch("rt_dashboard.grok_ask._post_chat", side_effect=fake_post):
            with self.assertRaises(GrokAskError):
                chat_completions(
                    [{"role": "user", "content": "plan"}], user_id="quota-user"
                )
            with self.assertRaises(GrokAskError):
                chat_completions(
                    [{"role": "user", "content": "plan"}], user_id="quota-user"
                )
            self.assertEqual(len(calls), 1)
            with self.assertRaises(GrokAskError):
                chat_completions(
                    [{"role": "user", "content": "plan"}],
                    user_id="other-user",
                )
            self.assertEqual(len(calls), 2)
            with self.assertRaises(GrokAskError):
                chat_completions(
                    [{"role": "user", "content": "plan"}],
                    user_id="quota-user",
                    force=True,
                )
            self.assertEqual(len(calls), 3)


class AgentTodayQuotaFallback(XaiQuotaHarness):
    def _env_today(self):
        return {
            "FITDASH_SERVICE_TOKEN": "house-secret",
            "FITDASH_SERVICE_LOOPBACK": "0",
            "FITDASH_USER_ID": "sub-quota",
            "TZ": "UTC",
            "RESISTANCE_DASHBOARD_CONFIG_DIR": self._tmp.name,
            "FITDASH_AI_PLANNER_PERSIST": "0",
        }

    def _pull_slot(self):
        return {
            "session_type": "pull",
            "is_rest_day": False,
            "exercises": [],
            "empty": True,
            "next_session_type": "pull",
        }

    def test_prior_plan_fallback_and_backoff(self):
        save_last_good_workout_plan(
            "sub-quota",
            "2020-01-02",
            {
                "session_type": "pull",
                "is_rest_day": False,
                "exercises": [
                    {
                        "name": "DB Row",
                        "prescription": {"sets": 2, "reps": 8, "weight_lbs": 50},
                    }
                ],
            },
        )
        calls = {"n": 0}

        def boom(*_args, **_kwargs):
            calls["n"] += 1
            raise GrokAskError(
                format_quota_message(
                    source="supergrok_session",
                    code="personal-team-blocked:spending-limit",
                ),
                status=403,
                kind="xai_quota",
                xai_code="personal-team-blocked:spending-limit",
                source="supergrok_session",
            )

        loaded = {
            "day": "2026-10-16",
            "catalog": {"exercises": []},
            "sessions": [],
            "goals": {"rotation": ["push", "pull", "legs"]},
            "recovery": {"sparse": True, "score": 80},
            "stamped": self._pull_slot(),
            "next_session_type": "pull",
        }
        patches = [
            mock.patch("api.dashboard._load_sessions", return_value=([], [], "turso")),
            mock.patch(
                "rt_dashboard.google_health.GoogleHealthClient.credentials_present",
                return_value=False,
            ),
            mock.patch(
                "rt_dashboard.hidrate_client.hidrate_bottle_charge",
                return_value={"available": False, "percent": None},
            ),
            mock.patch(
                "rt_dashboard.hidrate_client.hidrate_hydration_samples",
                return_value=[],
            ),
            mock.patch(
                "rt_dashboard.grok_planner.dashboard_plan_slots",
                return_value=({}, self._pull_slot()),
            ),
            mock.patch(
                "rt_dashboard.agent_plan._load_generate_kwargs", return_value=loaded
            ),
            mock.patch(
                "rt_dashboard.grok_ask.resolve_xai_credentials", return_value=dict(CREDS)
            ),
            mock.patch("rt_dashboard.grok_ask.chat_completions", side_effect=boom),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in patches])
        with mock.patch.dict(os.environ, self._env_today(), clear=True):
            status, body = agent_today_body(_token_headers(), "")
            after_first = calls["n"]
            status2, body2 = agent_today_body(_token_headers(), "")
            after_second = calls["n"]
            status3, _body3 = agent_today_body(_token_headers(), "force=1")
        self.assertEqual(status, 200)
        wo = body["today"]["workout"]
        self.assertTrue(wo.get("plan_exercises"))
        self.assertEqual(wo["plan_exercises"][0]["name"], "DB Row")
        self.assertEqual(wo.get("source"), "fallback_last")
        self.assertIn("xai_quota", wo.get("generate_error") or "")
        self.assertTrue((wo.get("generate_error") or "").startswith(QUOTA_TITLE))
        self.assertNotIn("{", wo.get("generate_error") or "")
        self.assertEqual(body["today"]["ai_planner"]["status"], "xai_quota")
        self.assertEqual(body["today"]["ai_planner"]["source"], "supergrok_session")
        self.assertNotIn("token", json.dumps(body["today"]["ai_planner"]))
        self.assertEqual(status2, 200)
        self.assertEqual(after_first, 1)
        self.assertEqual(after_second, 1)
        self.assertEqual(body2["today"]["workout"].get("source"), "fallback_last")
        self.assertEqual(status3, 200)
        self.assertEqual(calls["n"], 2)
        from rt_dashboard.timeutil import local_today_iso

        self.assertIsNone(load_last_good_workout_plan("sub-quota", local_today_iso()))

    def test_catalog_template_when_no_prior_plan(self):
        ctx = {
            "day": "2026-10-16",
            "catalog": {
                "exercises": [
                    {"name": "Goblet Squat", "session_types": ["legs"]},
                ]
            },
            "sessions": [],
            "goals": {"rotation": ["push", "pull", "legs"], "default_hard_sets": 2},
            "recovery": {"sparse": True, "score": 80},
            "stamped": {
                "session_type": "legs",
                "is_rest_day": False,
                "exercises": [],
                "next_session_type": "legs",
            },
            "next_session_type": "legs",
        }

        def boom(*_args, **_kwargs):
            raise GrokAskError(
                format_quota_message(source="xai_api_key", code="spending-limit"),
                status=403,
                kind="xai_quota",
                xai_code="spending-limit",
                source="xai_api_key",
            )

        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials", return_value=dict(KEY_CREDS)
        ), mock.patch("rt_dashboard.grok_ask.chat_completions", side_effect=boom):
            out = ensure_today_grok_plan("sub-quota", day="2026-10-16", context=ctx)
        self.assertTrue(out["ok"])
        workout = out["workout"]
        self.assertEqual(workout.get("source"), "fallback_template")
        self.assertEqual(workout["exercises"][0]["name"], "Goblet Squat")
        self.assertIn("xai_quota", workout.get("generate_error") or "")
        self.assertIsNone(load_last_good_workout_plan("sub-quota", "2026-10-16"))

    def test_equipment_dict_prefers_deterministic_template(self):
        ctx = {
            "day": "2026-10-16",
            "catalog": {
                "exercises": [{"name": "Goblet Squat", "session_types": ["legs"]}]
            },
            "equipment": {"items": []},
            "sessions": [],
            "goals": {"rotation": ["push", "pull", "legs"]},
            "recovery": {"sparse": True, "score": 80},
            "stamped": {
                "session_type": "legs",
                "is_rest_day": False,
                "exercises": [],
            },
        }

        def boom(*_args, **_kwargs):
            raise GrokAskError(
                "blocked",
                status=403,
                kind="xai_quota",
                xai_code="spending-limit",
                source="xai_api_key",
            )

        canned = {
            "session_type": "legs",
            "is_rest_day": False,
            "exercises": [{"name": "Deterministic Press", "sets": 2, "reps": 8}],
        }
        with mock.patch(
            "rt_dashboard.grok_ask.resolve_xai_credentials", return_value=dict(KEY_CREDS)
        ), mock.patch(
            "rt_dashboard.grok_ask.chat_completions", side_effect=boom
        ), mock.patch(
            "rt_dashboard.agent_plan._deterministic_workout", return_value=canned
        ) as det:
            out = ensure_today_grok_plan("sub-quota", day="2026-10-16", context=ctx)
        det.assert_called()
        self.assertEqual(out["workout"]["exercises"][0]["name"], "Deterministic Press")
        self.assertEqual(out["workout"].get("source"), "fallback_template")

    def test_connect_supergrok_stays_empty(self):
        ctx = {
            "day": "2026-10-16",
            "catalog": {
                "exercises": [{"name": "Goblet Squat", "session_types": ["legs"]}]
            },
            "sessions": [],
            "goals": {"rotation": ["push", "pull", "legs"]},
            "recovery": {},
            "stamped": {
                "session_type": "legs",
                "is_rest_day": False,
                "exercises": [],
            },
        }
        with mock.patch(
            "rt_dashboard.agent_plan.generate_grok_plans",
            return_value={
                "ok": False,
                "error": "Connect SuperGrok to generate today's meal/workout plan.",
                "workout": {"session_type": "legs", "exercises": [], "empty": True},
            },
        ):
            out = ensure_today_grok_plan("sub-quota", day="2026-10-16", context=ctx)
        self.assertFalse(out["ok"])
        self.assertIn("Connect SuperGrok", out["error"])
        self.assertEqual(out["workout"].get("exercises") or [], [])


class HealthzPlannerObservation(XaiQuotaHarness):
    def test_vercel_and_pi_healthz_do_not_call_xai(self):
        note_quota(
            "sub-quota",
            source="supergrok_session",
            code="personal-team-blocked:spending-limit",
            message=format_quota_message(
                source="supergrok_session",
                code="personal-team-blocked:spending-limit",
            ),
        )
        with mock.patch("rt_dashboard.grok_ask._post_chat") as posted:
            body = healthz_body()
            self.assertEqual(posted.call_count, 0)
        planner = body["ai_planner"]
        self.assertEqual(
            set(planner),
            {"status", "source", "last_error_code", "since"},
        )
        self.assertEqual(planner["status"], "xai_quota")
        self.assertEqual(planner["source"], "supergrok_session")
        for value in planner.values():
            self.assertNotIn("{", str(value or ""))
        self.assertNotIn("token", json.dumps(planner))

        import server as fitdash_server

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), fitdash_server.DashboardHandler)
        port = int(httpd.server_address[1])
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch("rt_dashboard.grok_ask._post_chat") as posted:
                with urlopen(Request(f"http://127.0.0.1:{port}/api/healthz"), timeout=5) as resp:
                    raw = resp.read().decode("utf-8")
                    pi = json.loads(raw)
                self.assertEqual(posted.call_count, 0)
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(pi["ai_planner"]["status"], "xai_quota")
        self.assertEqual(
            set(pi["ai_planner"]),
            {"status", "source", "last_error_code", "since"},
        )
        self.assertNotIn("session-token", raw)
        for value in pi["ai_planner"].values():
            self.assertNotIn("{", str(value or ""))

    def test_empty_observation_is_unknown(self):
        snap = public_snapshot()
        self.assertEqual(snap["status"], "unknown")
        self.assertEqual(snap["source"], "none")
        self.assertIsNone(snap["last_error_code"])
        self.assertIsNone(snap["since"])


class AskQuotaResponse(XaiQuotaHarness):
    def test_cookie_less_ask_is_401(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = ASK_PY.ask_body({}, {"question": "today?"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")

    def test_quota_ask_is_503_without_raw_json(self):
        user = {"id": "sub-quota", "email": "c@example.com"}
        raw = SPENDING_BODY
        exc = GrokAskError(
            format_quota_message(
                source="supergrok_session",
                code="personal-team-blocked:spending-limit",
            ),
            status=403,
            body=raw,
            kind="xai_quota",
            xai_code="personal-team-blocked:spending-limit",
            source="supergrok_session",
        )
        with mock.patch.object(
            ASK_PY, "signing_secret", return_value="secret"
        ), mock.patch.object(
            ASK_PY, "session_from_headers", return_value=user
        ), mock.patch(
            "rt_dashboard.grok_ask.auth_status", return_value={"ok": True}
        ), mock.patch(
            "api.dashboard.dashboard_body", return_value=(200, {"ok": True})
        ), mock.patch(
            "rt_dashboard.grok_ask.ask_about_dashboard", side_effect=exc
        ):
            status, body = ASK_PY.ask_body(
                {"Cookie": "fitdash_session=x"}, {"question": "today?"}
            )
        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])
        self.assertTrue(str(body["error"]).startswith(QUOTA_TITLE))
        self.assertIn("xai_quota", body["error"])
        self.assertNotIn("{", body["error"])
        self.assertNotIn("Connect SuperGrok", body["error"])
        dumped = json.dumps(body)
        self.assertNotIn(raw, dumped)
        self.assertNotIn("run out of credits", dumped)


class PantryIgnoresQuota(XaiQuotaHarness):
    def test_generate_meal_plan_does_not_call_xai(self):
        note_quota(
            "sub-quota",
            source="xai_api_key",
            code="spending-limit",
            message=format_quota_message(source="xai_api_key", code="spending-limit"),
        )

        def boom(*_args, **_kwargs):
            raise AssertionError("meal plan called xAI")

        with mock.patch("rt_dashboard.grok_ask.chat_completions", side_effect=boom):
            plan = generate_meal_plan(
                {"ingredients": []},
                {"calories": 2100, "protein_g": 160, "carbs_g": 180, "fat_g": 55},
                {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
            )
        self.assertIsInstance(plan, dict)


class TodayCardCopy(unittest.TestCase):
    def test_today_card_uses_text_content_and_retry(self):
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        ask = (ROOT / "static" / "supergrok.js").read_text(encoding="utf-8")
        self.assertIn(QUOTA_TITLE, app)
        self.assertIn("p.textContent = note", app)
        self.assertIn("btn-retry-ai-plan", app)
        self.assertIn('id="btn-retry-ai-plan"', page)
        self.assertIn(QUOTA_TITLE, ask)
        self.assertIn("generatePlansForce", ask)
        self.assertIn("/api/ask/plan?force=1", ask)
        quota_at = ask.index('err.indexOf("AI plan unavailable (xAI credits)")')
        connect_at = ask.index(
            'status.textContent = err || "Connect SuperGrok to generate today\'s meal/workout plan."'
        )
        self.assertLess(quota_at, connect_at)
        self.assertIn('meal.source === "xai_quota"', ask)


if __name__ == "__main__":
    unittest.main()
