"""Unit tests for in-app Google Health OAuth helpers."""

from __future__ import annotations

import unittest
from unittest import mock

from rt_dashboard.google_auth import (
    REDIRECT_URI,
    build_auth_url,
    auth_flow_status,
)


class TestGoogleAuth(unittest.TestCase):
    def test_build_auth_url_includes_offline_consent(self):
        url = build_auth_url("client-123.apps.googleusercontent.com")
        self.assertIn("accounts.google.com", url)
        self.assertIn("client_id=client-123", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("redirect_uri=", url)
        self.assertTrue(
            "127.0.0.1" in url and ("8788" in url),
            msg=f"redirect port missing in {url}",
        )
        self.assertIn("googlehealth.nutrition.readonly", url.replace("%2F", "/"))

    def test_auth_status_without_tokens(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            # Clear google vars
            import os

            for k in (
                "GOOGLE_CLIENT_ID",
                "GOOGLE_CLIENT_SECRET",
                "GOOGLE_REFRESH_TOKEN",
                "GOOGLE_ACCESS_TOKEN",
            ):
                os.environ.pop(k, None)
            st = auth_flow_status()
            self.assertTrue(st.get("ok"))
            self.assertFalse(st.get("credentials_present"))
            self.assertFalse(st.get("token_ok"))


if __name__ == "__main__":
    unittest.main()
