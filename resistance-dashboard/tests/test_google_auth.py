"""Unit tests for in-app Google Health OAuth helpers."""

from __future__ import annotations

import unittest
from unittest import mock

from rt_dashboard.google_auth import (
    REDIRECT_URI,
    SCOPES,
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
        self.assertNotIn("calendar", url.lower())
        self.assertTrue(
            all("calendar" not in s for s in SCOPES),
            msg="Health-only connect must stay Calendar-free",
        )

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


class RemotePublicUrlAuthTests(unittest.TestCase):
    def test_start_auth_flow_uses_login_when_public_url_remote(self):
        import os
        from rt_dashboard import google_auth as ga

        prev = os.environ.get("FITDASH_PUBLIC_URL")
        try:
            os.environ["FITDASH_PUBLIC_URL"] = "https://prism-gateway.tailb1085a.ts.net"
            out = ga.start_auth_flow(force=True)
            self.assertTrue(out.get("ok"))
            self.assertEqual(out.get("status"), "use_login")
            self.assertTrue(out.get("use_same_window"))
            self.assertEqual(out.get("auth_url"), "/api/auth/google/start")
            self.assertIn("/api/auth/google/callback", out.get("redirect_uri") or "")
            self.assertNotIn("8788", out.get("redirect_uri") or "")
        finally:
            if prev is None:
                os.environ.pop("FITDASH_PUBLIC_URL", None)
            else:
                os.environ["FITDASH_PUBLIC_URL"] = prev

