import os
import unittest
from unittest import mock

from api.auth.session_util import (
    PROD_PUBLIC_URL,
    SESSION_COOKIE,
    make_session,
    make_state,
    missing_oauth_env,
    public_base_url,
    read_session,
    redirect_uri,
    verify_state,
)
from api.auth.status import auth_status_body
from api.dashboard import dashboard_body
from api.healthz import healthz_body


class VercelPreviewHealthz(unittest.TestCase):
    def test_ok_preview_role(self):
        body = healthz_body()
        self.assertTrue(body["ok"])
        self.assertEqual(body["service"], "fitdash")
        self.assertEqual(body["role"], "vercel-preview")


class VercelPreviewAuthStatus(unittest.TestCase):
    def test_honest_logged_out(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            body = auth_status_body({})
            self.assertTrue(body["ok"])
            self.assertFalse(body["authenticated"])
            self.assertIsNone(body["user"])
            self.assertEqual(body["oauth"], "unproven")
            self.assertIn("GOOGLE_CLIENT_ID", body["missing"])

    def test_signed_session_roundtrip(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret", "GOOGLE_CLIENT_ID": "cid"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            user = read_session(token)
            self.assertEqual(user["id"], "sub-1")
            self.assertEqual(user["email"], "c@example.com")
            self.assertTrue(verify_state(make_state()))
            self.assertFalse(verify_state("tampered"))

    def test_missing_env_lists_keys(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            missing = missing_oauth_env()
            self.assertEqual(
                missing,
                ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "FITDASH_PUBLIC_URL"],
            )

    def test_production_public_url_uses_prod_alias_not_preview_host(self):
        stale = (
            "https://fitdash-git-feat-fitdash-vercel-adapter-cvolkernick.vercel.app"
        )
        env = {
            "VERCEL_ENV": "production",
            "FITDASH_PUBLIC_URL": stale,
            "VERCEL_URL": "fitdash-git-feat-fitdash-vercel-adapter-cvolkernick.vercel.app",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(public_base_url(), PROD_PUBLIC_URL)
            self.assertEqual(
                redirect_uri(),
                f"{PROD_PUBLIC_URL}/api/auth/google/callback",
            )
            self.assertNotIn("-git-", public_base_url())

    def test_production_honors_non_preview_explicit_url(self):
        env = {
            "VERCEL_ENV": "production",
            "FITDASH_PUBLIC_URL": "https://fitdash-cvolkernick.vercel.app",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(public_base_url(), PROD_PUBLIC_URL)

    def test_production_uses_platform_production_url_when_set(self):
        env = {
            "VERCEL_ENV": "production",
            "VERCEL_PROJECT_PRODUCTION_URL": "fitdash-cvolkernick.vercel.app",
            "VERCEL_URL": "fitdash-abc123-cvolkernick.vercel.app",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(public_base_url(), PROD_PUBLIC_URL)

    def test_production_does_not_require_fitdash_public_url_env(self):
        env = {
            "GOOGLE_CLIENT_ID": "cid",
            "GOOGLE_CLIENT_SECRET": "sec",
            "VERCEL_ENV": "production",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(missing_oauth_env(), [])
            self.assertEqual(public_base_url(), PROD_PUBLIC_URL)

    def test_preview_still_uses_vercel_url(self):
        env = {
            "VERCEL_ENV": "preview",
            "VERCEL_URL": "fitdash-git-feat-other-cvolkernick.vercel.app",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                public_base_url(),
                "https://fitdash-git-feat-other-cvolkernick.vercel.app",
            )


class VercelPreviewDashboard(unittest.TestCase):
    def test_missing_cookie_is_401_json_not_404(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = dashboard_body({})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("sessions", body)

    def test_invalid_cookie_is_401(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            status, body = dashboard_body({"Cookie": f"{SESSION_COOKIE}=tampered"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
