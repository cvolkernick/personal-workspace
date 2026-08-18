import unittest

from api.healthz import healthz_body


class VercelPreviewHealthz(unittest.TestCase):
    def test_ok_preview_role(self):
        body = healthz_body()
        self.assertTrue(body["ok"])
        self.assertEqual(body["service"], "fitdash")
        self.assertEqual(body["role"], "vercel-preview")
        self.assertNotEqual(body["role"], "prod")
