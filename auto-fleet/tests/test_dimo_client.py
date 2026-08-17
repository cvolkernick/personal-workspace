"""DIMO stub + injectable live path. No network."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import dimo_client  # noqa: E402


class DimoClientTests(unittest.TestCase):
    def tearDown(self) -> None:
        dimo_client._FETCH = None

    def test_missing_env_is_unconfigured(self) -> None:
        unit = {"id": "m3-2022"}
        out = dimo_client.dimo_for_unit(unit, env={})
        self.assertEqual(out["status"], "unconfigured")
        self.assertIsNone(out["odometer"])
        self.assertIsNone(out["range"])
        self.assertIsNone(out["last_seen"])

    def test_configured_without_token_is_unconfigured(self) -> None:
        env = {
            "DIMO_CLIENT_ID": "0xabc",
            "DIMO_DOMAIN": "https://example.invalid",
            "DIMO_API_KEY": "secret",
        }
        out = dimo_client.dimo_for_unit({"id": "m3-2022"}, env=env)
        self.assertEqual(out["status"], "unconfigured")
        self.assertIn("token", (out.get("error") or "").lower())

    def test_fetch_ok_via_injectable(self) -> None:
        env = {
            "DIMO_CLIENT_ID": "0xabc",
            "DIMO_DOMAIN": "https://example.invalid",
            "DIMO_API_KEY": "secret",
            "DIMO_TOKEN_M3_2022": "42",
        }

        def fake(token_id: int, _env):
            self.assertEqual(token_id, 42)
            return {
                "last_seen": "2026-08-17T12:00:00Z",
                "odometer": 44120,
                "range": 210,
                "location": {"latitude": 35.2, "longitude": -80.8},
            }

        dimo_client._FETCH = fake
        out = dimo_client.dimo_for_unit({"id": "m3-2022"}, env=env)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["odometer"], 44120)
        self.assertEqual(out["range"], 210)
        self.assertEqual(out["last_seen"], "2026-08-17T12:00:00Z")
        self.assertEqual(out["location"]["latitude"], 35.2)

    def test_fetch_error_does_not_raise(self) -> None:
        env = {
            "DIMO_CLIENT_ID": "0xabc",
            "DIMO_DOMAIN": "https://example.invalid",
            "DIMO_API_KEY": "secret",
            "DIMO_VEHICLE_TOKENS": '{"m3-2022": 7}',
        }

        def boom(token_id: int, _env):
            raise RuntimeError("telemetry 401")

        dimo_client._FETCH = boom
        out = dimo_client.dimo_for_unit({"id": "m3-2022"}, env=env)
        self.assertEqual(out["status"], "error")
        self.assertIn("401", out.get("error") or "")
        self.assertIsNone(out["odometer"])

    def test_is_configured_requires_id_domain_and_secret(self) -> None:
        self.assertFalse(dimo_client.is_configured({}))
        self.assertFalse(
            dimo_client.is_configured(
                {"DIMO_CLIENT_ID": "x", "DIMO_DOMAIN": "y"}
            )
        )
        self.assertTrue(
            dimo_client.is_configured(
                {
                    "DIMO_CLIENT_ID": "x",
                    "DIMO_DOMAIN": "y",
                    "DIMO_DEVELOPER_JWT": "jwt",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
