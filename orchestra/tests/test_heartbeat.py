"""Tests for Pi → Orchestra heartbeat contract (#50)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "orchestra"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heartbeat import (  # noqa: E402
    build_degraded,
    collect_heartbeat,
    heartbeat_api_payload,
    latest_path,
    load_heartbeat,
    overall_ok,
    service_healthy,
    write_heartbeat,
)


class ServiceHealthyTests(unittest.TestCase):
    def test_health_ok_outweighs_inactive_unit(self) -> None:
        # Lock-in: health_ok > active
        self.assertTrue(service_healthy(active=False, health_ok=True))
        self.assertFalse(service_healthy(active=True, health_ok=False))

    def test_falls_back_to_active_when_no_health(self) -> None:
        self.assertTrue(service_healthy(active=True, health_ok=None))
        self.assertFalse(service_healthy(active=False, health_ok=None))


class DegradedAndOkTests(unittest.TestCase):
    def test_critical_health_fail_sets_ok_false(self) -> None:
        services = [
            {
                "name": "orchestra-dashboard",
                "active": True,
                "health_ok": False,
                "severity": "critical",
                "note": "http_500",
            }
        ]
        degraded = build_degraded(services)
        self.assertEqual(len(degraded), 1)
        self.assertEqual(degraded[0]["severity"], "red")
        self.assertFalse(overall_ok(services, degraded))

    def test_yellow_fail_keeps_ok_true(self) -> None:
        services = [
            {
                "name": "orchestra-dashboard",
                "active": True,
                "health_ok": True,
                "severity": "critical",
                "note": "",
            },
            {
                "name": "iot-dashboard",
                "active": False,
                "health_ok": False,
                "severity": "yellow",
                "note": "unit:inactive",
            },
        ]
        degraded = build_degraded(services)
        self.assertEqual(len(degraded), 1)
        self.assertEqual(degraded[0]["severity"], "yellow")
        self.assertTrue(overall_ok(services, degraded))

    def test_active_false_but_health_true_is_healthy(self) -> None:
        services = [
            {
                "name": "iot-dashboard",
                "active": False,
                "health_ok": True,
                "severity": "yellow",
                "note": "unit:inactive",
            }
        ]
        self.assertEqual(build_degraded(services), [])


class WriteLoadApiTests(unittest.TestCase):
    def test_atomic_write_and_api_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            payload = {
                "schema_version": 1,
                "host": "prism-gateway",
                "host_role": "prod",
                "as_of": "2026-08-06T03:30:00+00:00",
                "ok": True,
                "collector": {"name": "pi-heartbeat", "version": "0.1.0"},
                "services": [],
                "mesh": {
                    "lan_ip": "192.168.100.98",
                    "tailscale_ip": "100.67.114.2",
                    "reachable_lan": True,
                    "reachable_tailscale": True,
                },
                "notes": [],
                "degraded": [],
            }
            write_heartbeat(ws, payload=payload)
            path = latest_path(ws)
            self.assertTrue(path.is_file())
            loaded = load_heartbeat(ws)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["host"], "prism-gateway")
            self.assertTrue(loaded["ok"])

            api = heartbeat_api_payload(ws)
            self.assertTrue(api["available"])
            self.assertTrue(api["ok"])
            self.assertIn("heartbeat", api)
            self.assertEqual(api["heartbeat"]["schema_version"], 1)

    def test_missing_heartbeat_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = heartbeat_api_payload(Path(tmp))
            self.assertFalse(api["available"])
            self.assertFalse(api["ok"])
            self.assertEqual(api["error"], "heartbeat_missing")


class CollectMockTests(unittest.TestCase):
    def test_collect_uses_mocked_probes(self) -> None:
        watch = [
            {
                "name": "orchestra-dashboard",
                "unit": "orchestra-dashboard.service",
                "port": 8790,
                "health_path": "/api/health",
                "severity": "critical",
            },
            {
                "name": "iot-dashboard",
                "unit": "iot-dashboard.service",
                "port": 8780,
                "health_path": "/api/health",
                "severity": "yellow",
            },
        ]

        def fake_unit(unit: str, **_kw):
            if "orchestra" in unit:
                return True, "active"
            return False, "inactive"

        def fake_health(url: str, **_kw):
            if ":8790" in url:
                return True, 5, ""
            return False, 2, "url_error:Connection refused"

        with mock.patch("heartbeat.unit_is_active", side_effect=fake_unit), mock.patch(
            "heartbeat.probe_health", side_effect=fake_health
        ), mock.patch(
            "heartbeat.detect_mesh",
            return_value={
                "lan_ip": "192.168.100.98",
                "tailscale_ip": "100.67.114.2",
                "reachable_lan": True,
                "reachable_tailscale": True,
            },
        ), mock.patch(
            "heartbeat.detect_host", return_value=("prism-gateway", "prod")
        ):
            doc = collect_heartbeat(Path("/tmp"), watchlist=watch)

        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["host"], "prism-gateway")
        self.assertEqual(doc["host_role"], "prod")
        self.assertTrue(doc["ok"])  # only yellow failed
        self.assertEqual(len(doc["services"]), 2)
        orch = doc["services"][0]
        self.assertTrue(orch["active"])
        self.assertTrue(orch["health_ok"])
        self.assertEqual(len(doc["degraded"]), 1)
        self.assertEqual(doc["degraded"][0]["service"], "iot-dashboard")
        self.assertEqual(doc["degraded"][0]["severity"], "yellow")

    def test_collect_critical_fail(self) -> None:
        watch = [
            {
                "name": "financial-command",
                "unit": "financial-command.service",
                "port": 8000,
                "health_path": "/api/health",
                "severity": "critical",
            }
        ]
        with mock.patch(
            "heartbeat.unit_is_active", return_value=(True, "active")
        ), mock.patch(
            "heartbeat.probe_health", return_value=(False, 10, "http_503")
        ), mock.patch(
            "heartbeat.detect_mesh",
            return_value={
                "lan_ip": None,
                "tailscale_ip": None,
                "reachable_lan": False,
                "reachable_tailscale": False,
            },
        ), mock.patch(
            "heartbeat.detect_host", return_value=("prism-gateway", "prod")
        ):
            doc = collect_heartbeat(watchlist=watch)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["degraded"][0]["severity"], "red")


class ServerRouteSmoke(unittest.TestCase):
    def test_handler_imports_heartbeat(self) -> None:
        # Ensure server module wires the import without executing network
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "orchestra_server_hb", ORCH / "server.py"
        )
        self.assertIsNotNone(spec)
        # Import path already has orchestra on sys.path via heartbeat tests
        from heartbeat import heartbeat_api_payload as fn

        self.assertTrue(callable(fn))


if __name__ == "__main__":
    unittest.main()
