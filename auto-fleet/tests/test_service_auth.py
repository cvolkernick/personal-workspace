"""Service-token / loopback auth for Auto Fleet agent-read (#295)."""

from __future__ import annotations

import os
import sys
import unittest
from typing import Any, Dict, Optional

PKG = __import__("pathlib").Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import service_auth  # noqa: E402


class _Headers:
    def __init__(self, data: Optional[Dict[str, str]] = None) -> None:
        self._data = {str(k): str(v) for k, v in (data or {}).items()}

    def get(self, key: str, default: Any = None) -> Any:
        for cand in (key, key.lower(), key.title()):
            if cand in self._data:
                return self._data[cand]
        lower = {k.lower(): v for k, v in self._data.items()}
        return lower.get(str(key).lower(), default)


class ServiceAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "AUTO_FLEET_SERVICE_TOKEN",
                "AUTO_FLEET_SERVICE_LOOPBACK",
                "VERCEL",
            )
        }

    def tearDown(self) -> None:
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def _set_env(self, *, token: str = "", loopback: str = "1") -> None:
        os.environ.pop("VERCEL", None)
        if token:
            os.environ["AUTO_FLEET_SERVICE_TOKEN"] = token
        else:
            os.environ.pop("AUTO_FLEET_SERVICE_TOKEN", None)
        os.environ["AUTO_FLEET_SERVICE_LOOPBACK"] = loopback

    def test_loopback_allowed_by_default(self) -> None:
        self._set_env(token="", loopback="1")
        self.assertTrue(service_auth.service_auth_ok(_Headers(), "127.0.0.1"))
        self.assertTrue(service_auth.service_auth_ok(_Headers(), "::1"))
        self.assertTrue(service_auth.service_auth_ok(_Headers(), "localhost"))

    def test_lan_and_tailscale_denied_without_token(self) -> None:
        self._set_env(token="", loopback="1")
        self.assertFalse(service_auth.service_auth_ok(_Headers(), "192.168.100.5"))
        self.assertFalse(service_auth.service_auth_ok(_Headers(), "100.67.114.2"))

    def test_loopback_denied_when_disabled(self) -> None:
        self._set_env(token="", loopback="0")
        self.assertFalse(service_auth.service_auth_ok(_Headers(), "127.0.0.1"))

    def test_matching_token_allows_non_loopback(self) -> None:
        self._set_env(token="house-secret", loopback="0")
        bearer = _Headers({"Authorization": "Bearer house-secret"})
        custom = _Headers({"X-Auto-Fleet-Service-Token": "house-secret"})
        wrong = _Headers({"Authorization": "Bearer nope"})
        self.assertTrue(service_auth.service_auth_ok(bearer, "192.168.100.5"))
        self.assertTrue(service_auth.service_auth_ok(custom, "10.0.0.8"))
        self.assertFalse(service_auth.service_auth_ok(wrong, "192.168.100.5"))
        self.assertFalse(service_auth.service_auth_ok(_Headers(), "192.168.100.5"))

    def test_vercel_requires_token_even_on_loopback(self) -> None:
        self._set_env(token="house-secret", loopback="1")
        os.environ["VERCEL"] = "1"
        self.assertFalse(service_auth.service_auth_ok(_Headers(), "127.0.0.1"))
        ok = _Headers({"Authorization": "Bearer house-secret"})
        self.assertTrue(service_auth.service_auth_ok(ok, "127.0.0.1"))

    def test_denied_shape(self) -> None:
        body = service_auth.service_auth_denied("agents")
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "auth_required")
        self.assertNotIn("house-secret", str(body))


if __name__ == "__main__":
    unittest.main()
