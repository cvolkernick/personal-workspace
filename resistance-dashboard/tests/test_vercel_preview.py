import os
import unittest
from unittest import mock

from api.auth.session_util import (
    make_session,
    make_state,
    missing_oauth_env,
    read_session,
    verify_state,
)
from api.auth.status import auth_status_body
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
