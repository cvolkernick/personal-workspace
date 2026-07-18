"""HTTP dashboard tests — real server process, fake transport via env path.

Uses a temp bulbs.json and the shipped server entry point. Control path is
exercised through the real API; network is avoided by not requesting live
status against real IPs for control assertions that use config-only listing.
For control, we hit the real execute_control path with a dry registry IP that
will fail network — instead we test listing/health/presets schema and control
request validation, plus success shape when using injectable unit path in
test_control. This module gates the HTTP surface.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "iot" / "server.py"


def _http_json(
    method: str, url: str, body: dict | None = None, timeout: float = 8.0
) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw}
        return e.code, payload


class IoTDashboardServerTests(unittest.TestCase):
    def test_health_devices_presets_control_validation(self) -> None:
        bulbs = {
            "entryway1": {"ip": "192.168.100.106", "mac": "6c2990089296"},
            "entryway2": {"ip": "192.168.100.118", "mac": "6c2990d5075a"},
            "entryway3": {"ip": "192.168.100.184", "mac": "6c29904e244e"},
            "entryway4": {"ip": "192.168.100.207", "mac": "6c29903d3195"},
        }
        with tempfile.TemporaryDirectory() as td:
            bulbs_path = Path(td) / "bulbs.json"
            bulbs_path.write_text(json.dumps(bulbs), encoding="utf-8")
            port = 18780
            env = {**os.environ, "PYTHONPATH": str(ROOT)}
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(SERVER),
                    "--port",
                    str(port),
                    "--host",
                    "127.0.0.1",
                    "--bulbs",
                    str(bulbs_path),
                    "--no-browser",
                ],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            base = f"http://127.0.0.1:{port}"
            try:
                deadline = time.time() + 10
                last_err: Exception | None = None
                while time.time() < deadline:
                    try:
                        code, health = _http_json("GET", f"{base}/api/health")
                        if code == 200 and health.get("ok"):
                            break
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        time.sleep(0.1)
                else:
                    err = (proc.stderr.read() if proc.stderr else "") or str(last_err)
                    self.fail(f"server did not become ready: {err}")

                self.assertEqual(health.get("service"), "iot")
                self.assertIn("registry", health)
                self.assertEqual(health["registry"]["count"], 4)
                self.assertIn("entryway1", health["registry"]["names"])
                # Bound port must match --port, not hard-coded DEFAULT_PORT
                self.assertEqual(health.get("port"), port)

                code, devices = _http_json("GET", f"{base}/api/devices")
                self.assertEqual(code, 200, devices)
                self.assertTrue(devices.get("ok"))
                self.assertEqual(devices.get("count"), 4)
                ids = {d["id"] for d in devices["devices"]}
                self.assertEqual(
                    ids, {"entryway1", "entryway2", "entryway3", "entryway4"}
                )
                self.assertIn("presets", devices)
                self.assertIn("cyan", devices["presets"])
                for d in devices["devices"]:
                    self.assertIn("ip", d)
                    self.assertIn("mac", d)
                    self.assertEqual(d.get("source"), "config")

                code, presets = _http_json("GET", f"{base}/api/presets")
                self.assertEqual(code, 200)
                self.assertIn("off", presets.get("presets") or [])

                # Validation: missing fields
                code, bad = _http_json("POST", f"{base}/api/control", {})
                self.assertEqual(code, 400)
                self.assertFalse(bad.get("ok"))

                # Unknown device — pure path, no network needed for intent fail
                code, unk = _http_json(
                    "POST",
                    f"{base}/api/control",
                    {"target": "does-not-exist", "color": "red"},
                )
                self.assertIn(code, (400, 200))
                self.assertFalse(unk.get("ok"))
                self.assertIn("unknown", (unk.get("error") or "").lower())

                # Discovery-style wiz-{ip} must resolve on the real control path
                # (uses live transport → network may fail; intent must not be "unknown")
                code, wiz = _http_json(
                    "POST",
                    f"{base}/api/control",
                    {"target": "wiz-127.0.0.1", "color": "off"},
                )
                self.assertNotIn(
                    "unknown device",
                    (wiz.get("error") or "").lower(),
                    msg=f"discovery id rejected: {wiz}",
                )
                # Should have attempted at least one target result (ok or network fail)
                self.assertTrue(
                    wiz.get("results") or wiz.get("ok") is False and "unknown" not in (wiz.get("error") or "").lower(),
                    wiz,
                )
                if wiz.get("results"):
                    self.assertEqual(wiz["results"][0].get("ip") or "127.0.0.1", "127.0.0.1")

                # index.html served — control targets prefer IP for discovery devices
                req = urllib.request.Request(f"{base}/")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8")
                self.assertIn("IoT Control", html)
                self.assertIn("data-color", html)
                self.assertIn("/api/control", html)
                self.assertIn("controlTargetForDevice", html)
                self.assertIn("data-control-target", html)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
