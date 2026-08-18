import unittest

from api.auth.status import auth_status_body
from api.healthz import healthz_body


class VercelPreviewHealthz(unittest.TestCase):
    def test_ok_preview_role(self):
        body = healthz_body()
        self.assertTrue(body["ok"])
        self.assertEqual(body["service"], "fitdash")
        self.assertEqual(body["role"], "vercel-preview")
        self.assertNotEqual(body["role"], "prod")


class VercelPreviewAuthStatus(unittest.TestCase):
    def test_honest_logged_out(self):
        body = auth_status_body()
        self.assertTrue(body["ok"])
        self.assertFalse(body["authenticated"])
        self.assertTrue(body["auth_required"])
        self.assertIsNone(body["user"])
        self.assertIsNone(body["oauth_redirect_uri"])
        self.assertFalse(body["master_key_ready"])
        self.assertEqual(body["role"], "vercel-preview")
        self.assertEqual(body["oauth"], "unproven")
