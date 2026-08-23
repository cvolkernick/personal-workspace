"""Public FitDash quests use the Google login session. No GOOGLE_TASKS_* on Vercel."""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from api.auth.session_util import (
    CALENDAR_EVENTS_SCOPE,
    COOKIE_SOFT_LIMIT,
    LOGIN_SCOPES,
    SESSION_COOKIE,
    TASKS_SCOPE,
    granted_scope_from_tokens,
    make_session,
    read_session,
    read_session_google,
    session_has_calendar_scope,
    session_has_tasks_scope,
    session_set_cookie,
)
from api.auth.status import auth_status_body
from api.workout._util import daily_tasks_body, daily_tasks_complete_body
from rt_dashboard.auth_login import LOGIN_SCOPES as PI_LOGIN_SCOPES
from rt_dashboard.gtasks_bridge import credentials_status as bridge_status
from rt_dashboard.gtasks_bridge import load_google_tasks
from rt_dashboard.gtasks_session import (
    FITNESS_LIST_NOT_FOUND,
    MISSING_TASKS_SCOPE,
    TASKS_API_NOT_ENABLED,
    TOKEN_REFRESH_FAILED,
    bound_session_google,
    classify_google_http_error,
    credentials_status,
    ensure_access_token,
)
from rt_dashboard.daily_plan_tasks import (
    PlannedGroup,
    PlannedItem,
    _hydrate_ids_from_listed,
)

ROOT = Path(__file__).resolve().parents[1]


def _identity_headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _tasks_headers(**extra):
    payload = {
        "id": "sub-1",
        "email": "c@example.com",
        "display_name": "Chris",
        "refresh_token": "1//rt-sess",
        "access_token": "ya29.sess",
        "scope": f"openid email profile {TASKS_SCOPE}",
        "expires_in": 3600,
    }
    payload.update(extra)
    token = make_session(payload)
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _cookie_headers(payload: dict) -> dict:
    from api.auth.session_util import _sign

    token = _sign(payload)
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _board():
    return {
        "date": "2026-08-20",
        "actions": [{"kind": "training", "text": "Train today", "id": "t"}],
        "workout": {"is_rest_day": True, "exercises": []},
        "meal": {"meals": [], "items": []},
        "purchases": [],
    }


def _dashboard_ok(board=None):
    return (
        200,
        {
            "coach": {"today": board or _board()},
            "meta": {"local_today": "2026-08-20"},
        },
    )


def _fake_lists_and_create():
    created = []

    def fake_request(google, method, url, body=None, query=None, _retried=False):
        if url.endswith("/users/@me/lists"):
            return {"items": [{"id": "L1", "title": "Fitness"}]}
        if method == "GET" and url.endswith("/tasks"):
            return {"items": []}
        if method == "POST" and url.endswith("/tasks"):
            tid = f"t{len(created) + 1}"
            created.append((tid, (body or {}).get("title"), query))
            return {
                "id": tid,
                "title": (body or {}).get("title"),
                "status": "needsAction",
            }
        if method == "GET" and "/tasks/" in url:
            tid = url.rsplit("/", 1)[-1]
            return {"id": tid, "title": "leaf", "status": "needsAction"}
        if method == "PUT":
            return {**(body or {}), "status": "completed"}
        return {}

    return fake_request, created


def _clear_gt_env(env: dict) -> dict:
    for key in (
        "GOOGLE_TASKS_TOKEN_JSON",
        "GOOGLE_TASKS_REFRESH_TOKEN",
        "GOOGLE_TASKS_CLIENT_ID",
        "GOOGLE_TASKS_CLIENT_SECRET",
        "GOOGLE_TASKS_CONFIG_DIR",
    ):
        env.pop(key, None)
    return env


def _honest(err: str) -> None:
    text = err or ""
    assert "Tasks permission" in text, text
    assert "google_tasks.py not found" not in text
    assert "/var/projects-dashboard" not in text
    assert "GOOGLE_TASKS_" not in text
    assert "token.json" not in text
    assert "8787" not in text
    assert "8788" not in text


class LoginScopeAndCookie(unittest.TestCase):
    def test_fitdash_login_requests_tasks_scope(self):
        self.assertIn(TASKS_SCOPE, LOGIN_SCOPES)
        self.assertIn(TASKS_SCOPE, PI_LOGIN_SCOPES)
        self.assertIn(CALENDAR_EVENTS_SCOPE, LOGIN_SCOPES)
        self.assertIn(CALENDAR_EVENTS_SCOPE, PI_LOGIN_SCOPES)
        self.assertEqual(list(LOGIN_SCOPES), list(PI_LOGIN_SCOPES))
        start = (ROOT / "api" / "auth" / "google" / "start.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("LOGIN_SCOPES", start)

    def test_session_identity_does_not_leak_tokens(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret", "GOOGLE_CLIENT_ID": "cid"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {
                    "id": "sub-1",
                    "email": "c@example.com",
                    "display_name": "Chris",
                    "refresh_token": "1//secret-rt",
                    "access_token": "ya29.secret",
                    "scope": TASKS_SCOPE,
                }
            )
            user = read_session(token)
            self.assertEqual(user["id"], "sub-1")
            dumped = json.dumps(user)
            self.assertNotIn("1//secret-rt", dumped)
            self.assertNotIn("ya29.secret", dumped)
            self.assertNotIn("refresh_token", dumped)
            google = read_session_google(token)
            self.assertEqual(google["refresh_token"], "1//secret-rt")
            self.assertTrue(session_has_tasks_scope(google))
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            status = auth_status_body(headers)
            self.assertTrue(status["authenticated"])
            self.assertNotIn("1//secret-rt", json.dumps(status))


class SessionCredentials(unittest.TestCase):
    def test_session_with_tasks_scope_is_ok_without_env_token(self):
        env = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"}
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            google = {
                "refresh_token": "1//rt-sess",
                "access_token": "ya29.sess",
                "scope": f"openid {TASKS_SCOPE}",
            }
            with bound_session_google(google):
                status = credentials_status()
                bridge = bridge_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["source"], "session")
        self.assertTrue(bridge["ok"])
        self.assertEqual(bridge["source"], "session")
        self.assertNotIn("1//rt-sess", json.dumps(status))

    def test_missing_scope_is_honest(self):
        google = {
            "refresh_token": "1//rt-sess",
            "access_token": "ya29.sess",
            "scope": "openid email profile",
        }
        with bound_session_google(google):
            status = credentials_status()
            bridge = bridge_status()
        self.assertFalse(status["ok"])
        self.assertFalse(bridge["ok"])
        _honest(status.get("error") or "")
        _honest(bridge.get("error") or "")
        self.assertEqual(status["source"], "session")


class VercelNeverReadsFileToken(unittest.TestCase):
    def test_vercel_bridge_ignores_file_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "token.json"
            token_path.write_text(
                json.dumps(
                    {
                        "refresh_token": "1//from-file",
                        "client_id": "file-cid",
                        "client_secret": "file-cs",
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "VERCEL": "1",
                "GOOGLE_CLIENT_ID": "cid",
                "GOOGLE_CLIENT_SECRET": "sec",
                "GOOGLE_TASKS_CONFIG_DIR": tmp,
            }
            with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
                os.environ["GOOGLE_TASKS_CONFIG_DIR"] = tmp
                status = bridge_status()
                gt = load_google_tasks()
                gt_status = gt.credentials_status()
                with self.assertRaises(RuntimeError) as ctx:
                    gt._load_token_blob()
        self.assertFalse(status["ok"])
        _honest(status.get("error") or "")
        _honest(str(ctx.exception))
        self.assertNotEqual(status.get("source"), "file")
        self.assertNotEqual(gt_status.get("source"), "file")
        self.assertNotIn("1//from-file", json.dumps(gt_status))

    def test_no_env_token_required_when_session_has_scope(self):
        env = {
            "VERCEL": "1",
            "GOOGLE_CLIENT_ID": "cid",
            "GOOGLE_CLIENT_SECRET": "sec",
        }
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            with bound_session_google(
                {
                    "refresh_token": "1//rt-sess",
                    "access_token": "ya29.sess",
                    "scope": TASKS_SCOPE,
                }
            ):
                status = bridge_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["source"], "session")


class DailyTasksUsesSession(unittest.TestCase):
    def test_identity_only_session_is_honest_missing_scope(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret", "GOOGLE_CLIENT_ID": "cid"}
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            with mock.patch(
                "api.dashboard.dashboard_body",
                return_value=(
                    200,
                    {
                        "coach": {
                            "today": {
                                "date": "2026-08-20",
                                "workout": {"is_rest_day": True, "exercises": []},
                                "meal": {"meals": [], "items": []},
                                "purchases": [],
                                "actions": [
                                    {"kind": "training", "text": "Train", "id": "t"}
                                ],
                            }
                        },
                        "meta": {"local_today": "2026-08-20"},
                    },
                ),
            ):
                status, body = daily_tasks_body(_identity_headers())
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        _honest(body.get("error") or "")
        daily = body["daily_tasks"]
        self.assertFalse(daily.get("ok"))
        _honest(daily.get("error") or "")
        self.assertIsNone(daily.get("list_id"))
        for g in daily.get("groups") or []:
            for item in g.get("items") or []:
                self.assertIsNone(item.get("task_id"))

    def test_session_with_tasks_scope_lists_and_completes(self):
        created = []

        def fake_request(google, method, url, body=None, query=None):
            if url.endswith("/users/@me/lists"):
                return {"items": [{"id": "L1", "title": "Fitness"}]}
            if method == "GET" and url.endswith("/tasks"):
                return {"items": []}
            if method == "POST" and url.endswith("/tasks"):
                tid = f"t{len(created) + 1}"
                created.append((tid, (body or {}).get("title"), query))
                return {
                    "id": tid,
                    "title": (body or {}).get("title"),
                    "status": "needsAction",
                }
            if method == "GET" and "/tasks/" in url:
                tid = url.rsplit("/", 1)[-1]
                return {"id": tid, "title": "leaf", "status": "needsAction"}
            if method == "PUT":
                return {**(body or {}), "status": "completed"}
            return {}

        env = {
            "VERCEL": "1",
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "GOOGLE_CLIENT_ID": "cid",
        }
        board = {
            "date": "2026-08-20",
            "actions": [{"kind": "training", "text": "Train today", "id": "t"}],
            "workout": {"is_rest_day": True, "exercises": []},
            "meal": {"meals": [], "items": []},
            "purchases": [],
        }
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            with mock.patch(
                "rt_dashboard.gtasks_session._request", side_effect=fake_request
            ):
                with tempfile.TemporaryDirectory() as tmp:
                    with mock.patch.dict(
                        os.environ,
                        {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp},
                        clear=False,
                    ), mock.patch(
                        "api.dashboard.dashboard_body",
                        return_value=(
                            200,
                            {
                                "coach": {"today": board},
                                "meta": {"local_today": "2026-08-20"},
                            },
                        ),
                    ):
                        status, body = daily_tasks_body(_tasks_headers())
                        complete_status, complete_body = daily_tasks_complete_body(
                            _tasks_headers(),
                            {"list_id": "L1", "task_id": "t1", "completed": True},
                        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        daily = body["daily_tasks"]
        self.assertEqual(daily.get("source"), "google_tasks")
        self.assertEqual(daily.get("list_id"), "L1")
        item = daily["groups"][0]["items"][0]
        self.assertTrue(item.get("task_id"))
        self.assertEqual(item.get("list_id"), "L1")
        self.assertTrue(created)
        self.assertEqual(complete_status, 200)
        self.assertTrue(complete_body["ok"])

    def test_complete_missing_scope_is_honest(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret", "GOOGLE_CLIENT_ID": "cid"}
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            status, body = daily_tasks_complete_body(
                _identity_headers(),
                {"list_id": "L1", "task_id": "t1", "completed": True},
            )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        _honest(body.get("error") or "")


class HonestGoogleErrors(unittest.TestCase):
    def test_tokens_without_sc_are_ok_and_attach_ids(self):
        fake_request, created = _fake_lists_and_create()
        env = {
            "VERCEL": "1",
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "GOOGLE_CLIENT_ID": "cid",
        }
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            headers = _cookie_headers(
                {
                    "sub": "sub-1",
                    "email": "c@example.com",
                    "name": "Chris",
                    "exp": int(time.time()) + 3600,
                    "rt": "1//rt-sess",
                    "at": "ya29.sess",
                }
            )
            google = read_session_google(headers["Cookie"].split("=", 1)[1])
            self.assertFalse(session_has_tasks_scope(google))
            with bound_session_google(google):
                status = credentials_status()
            self.assertTrue(status["ok"], status)
            self.assertEqual(status["source"], "session")
            with mock.patch(
                "rt_dashboard.gtasks_session._request", side_effect=fake_request
            ):
                with tempfile.TemporaryDirectory() as tmp:
                    with mock.patch.dict(
                        os.environ,
                        {"RESISTANCE_DASHBOARD_CONFIG_DIR": tmp},
                        clear=False,
                    ), mock.patch(
                        "api.dashboard.dashboard_body",
                        return_value=_dashboard_ok(),
                    ):
                        code, body = daily_tasks_body(headers)
                        complete_status, complete_body = daily_tasks_complete_body(
                            headers,
                            {"list_id": "L1", "task_id": "t1", "completed": True},
                        )
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"], body)
        daily = body["daily_tasks"]
        self.assertEqual(daily.get("source"), "google_tasks")
        item = daily["groups"][0]["items"][0]
        self.assertTrue(item.get("task_id"))
        self.assertEqual(item.get("list_id"), "L1")
        self.assertTrue(created)
        self.assertEqual(complete_status, 200)
        self.assertTrue(complete_body["ok"])

    def test_api_not_enabled_is_not_missing_scope(self):
        body = json.dumps(
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "Google Tasks API has not been used in project 894450316497 "
                    "before or it is disabled. Enable it by visiting "
                    "https://console.developers.google.com/apis/api/tasks.googleapis.com/"
                    "overview?project=894450316497",
                    "details": [{"reason": "ACCESS_NOT_CONFIGURED"}],
                }
            }
        )
        err = classify_google_http_error(403, body, during="tasks")
        self.assertEqual(err, TASKS_API_NOT_ENABLED)
        self.assertNotEqual(err, MISSING_TASKS_SCOPE)
        env = {
            "VERCEL": "1",
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "GOOGLE_CLIENT_ID": "cid",
        }

        def boom(*_a, **_k):
            raise RuntimeError(TASKS_API_NOT_ENABLED)

        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            with mock.patch("rt_dashboard.gtasks_session._request", side_effect=boom):
                with mock.patch(
                    "api.dashboard.dashboard_body", return_value=_dashboard_ok()
                ):
                    code, payload = daily_tasks_body(_tasks_headers())
        self.assertEqual(code, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload.get("error"), TASKS_API_NOT_ENABLED)
        self.assertNotEqual(payload.get("error"), MISSING_TASKS_SCOPE)
        daily = payload["daily_tasks"]
        self.assertIsNone(daily.get("list_id"))
        for g in daily.get("groups") or []:
            for item in g.get("items") or []:
                self.assertIsNone(item.get("task_id"))

    def test_refresh_failed_is_not_missing_scope(self):
        err = classify_google_http_error(
            400, '{"error":"invalid_grant"}', during="refresh"
        )
        self.assertEqual(err, TOKEN_REFRESH_FAILED)
        self.assertNotEqual(err, MISSING_TASKS_SCOPE)
        fp = io.BytesIO(b'{"error":"invalid_grant","error_description":"revoked"}')
        http_err = urllib.error.HTTPError(
            "https://oauth2.googleapis.com/token",
            400,
            "Bad Request",
            {},
            fp,
        )
        google = {
            "refresh_token": "1//rt-sess",
            "access_token": "",
            "scope": TASKS_SCOPE,
            "access_expires_at": 0,
        }
        env = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"}
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            with mock.patch(
                "urllib.request.urlopen", side_effect=http_err
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    ensure_access_token(google)
        self.assertEqual(str(ctx.exception), TOKEN_REFRESH_FAILED)
        self.assertNotEqual(str(ctx.exception), MISSING_TASKS_SCOPE)

    def test_generic_403_is_not_missing_scope(self):
        err = classify_google_http_error(403, '{"error":{"status":"PERMISSION_DENIED"}}')
        self.assertNotEqual(err, MISSING_TASKS_SCOPE)
        self.assertIn("403", err)

    def test_insufficient_scopes_stays_missing_scope(self):
        err = classify_google_http_error(
            403, '{"error":{"message":"Request had insufficient authentication scopes."}}'
        )
        self.assertEqual(err, MISSING_TASKS_SCOPE)

    def test_no_fitness_list_is_distinct(self):
        def fake_request(google, method, url, body=None, query=None, _retried=False):
            if url.endswith("/users/@me/lists"):
                return {"items": [{"id": "X", "title": "Work"}]}
            return {}

        env = {
            "VERCEL": "1",
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "GOOGLE_CLIENT_ID": "cid",
        }
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            with mock.patch(
                "rt_dashboard.gtasks_session._request", side_effect=fake_request
            ):
                with mock.patch(
                    "api.dashboard.dashboard_body", return_value=_dashboard_ok()
                ):
                    code, payload = daily_tasks_body(_tasks_headers())
        self.assertEqual(code, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload.get("error"), FITNESS_LIST_NOT_FOUND)
        self.assertNotEqual(payload.get("error"), MISSING_TASKS_SCOPE)
        self.assertIsNone((payload.get("daily_tasks") or {}).get("list_id"))


class CompactSessionCookie(unittest.TestCase):
    def test_refresh_session_stays_under_browser_limit(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret", "GOOGLE_CLIENT_ID": "cid"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {
                    "id": "100000000000000000000",
                    "email": "cvolkern@gmail.com",
                    "display_name": "Chris Volkernick",
                    "refresh_token": "1//" + "r" * 140,
                    "access_token": "ya29." + "A" * 2500,
                    "scope": " ".join(LOGIN_SCOPES),
                    "expires_in": 3599,
                }
            )
            google = read_session_google(token)
        self.assertLess(len(SESSION_COOKIE) + 1 + len(token), COOKIE_SOFT_LIMIT)
        self.assertLess(len(session_set_cookie(token)), 4096)
        self.assertTrue(google["refresh_token"].startswith("1//"))
        self.assertFalse(google["access_token"])
        self.assertTrue(session_has_tasks_scope(google))
        self.assertTrue(session_has_calendar_scope(google))
        self.assertNotIn("1//", json.dumps(read_session(token)))

    def test_plus_encoded_scope_counts_as_tasks(self):
        encoded = "openid+email+" + TASKS_SCOPE.replace("/", "%2F")
        self.assertTrue(session_has_tasks_scope({"scope": encoded}))
        self.assertEqual(granted_scope_from_tokens(""), " ".join(LOGIN_SCOPES))
        self.assertIn(TASKS_SCOPE, granted_scope_from_tokens(""))


class HydrateExistingTasks(unittest.TestCase):
    def test_hydrate_reuses_listed_titles(self):
        planned = [
            PlannedGroup(
                group="training",
                title="Training",
                items=[
                    PlannedItem(
                        group="training", slug="train-today", title="Train today"
                    )
                ],
            )
        ]
        listed = {
            "ok": True,
            "tasks": [
                {
                    "id": "p1",
                    "title": "Training",
                    "due": "2026-08-20T00:00:00.000Z",
                },
                {
                    "id": "c1",
                    "title": "Train today",
                    "parent": "p1",
                    "due": "2026-08-20T00:00:00.000Z",
                },
            ],
        }
        ids = _hydrate_ids_from_listed({}, planned, listed, "2026-08-20")
        self.assertEqual(ids["training|group"], "p1")
        self.assertEqual(ids["training|train-today"], "c1")


class NoNewFunction(unittest.TestCase):
    def test_hobby_function_count_stays_at_12(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])
        raw = (ROOT / "vercel.json").read_text(encoding="utf-8")
        self.assertNotIn("api/daily-tasks.py", raw)
        self.assertEqual(
            json.loads(raw).get("ignoreCommand"),
            "python3 scripts/vercel_ignore.py || exit 1",
        )


class VercelDeadFilePath(unittest.TestCase):
    def test_vercel_daily_tasks_never_reports_py_not_found(self):
        env = {
            "VERCEL": "1",
            "GOOGLE_CLIENT_SECRET": "test-secret",
            "GOOGLE_CLIENT_ID": "cid",
        }
        with mock.patch.dict(os.environ, _clear_gt_env(dict(env)), clear=True):
            with mock.patch(
                "api.dashboard.dashboard_body", return_value=_dashboard_ok()
            ):
                _code, body = daily_tasks_body(_identity_headers())
        blob = json.dumps(body)
        self.assertNotIn("google_tasks.py not found", blob)
        self.assertNotIn("/var/projects-dashboard", blob)
        self.assertNotIn("GOOGLE_TASKS_", blob)
        _honest(body.get("error") or "")


if __name__ == "__main__":
    unittest.main()
