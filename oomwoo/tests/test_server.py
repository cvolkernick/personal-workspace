"""HTTP surface — fixture mode, no live GitHub."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "oomwoo"
SERVER = PKG / "server.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "status.json"


def _http(method: str, url: str, timeout: float = 8.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


class OomwooServerTests(unittest.TestCase):
    def test_health_status_and_index(self) -> None:
        port = 18798
        proc = subprocess.Popen(
            [
                sys.executable,
                str(SERVER),
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
                "--no-browser",
                "--fixture",
                str(FIXTURE),
            ],
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.time() + 10
            last_err: Exception | None = None
            health: dict = {}
            while time.time() < deadline:
                try:
                    code, raw = _http("GET", f"{base}/api/health")
                    health = json.loads(raw) if raw else {}
                    if code == 200 and health.get("ok"):
                        break
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    time.sleep(0.1)
            else:
                err = (proc.stderr.read() if proc.stderr else "") or str(last_err)
                self.fail(f"server did not become ready: {err}")

            self.assertEqual(health.get("service"), "oomwoo")
            self.assertEqual(health.get("port"), port)
            self.assertTrue(health.get("fixture"))

            code, raw = _http("GET", f"{base}/api/status")
            self.assertEqual(code, 200, raw)
            payload = json.loads(raw)
            self.assertTrue(payload.get("ok"))
            self.assertEqual(payload["hub"]["stars"], 10039)
            self.assertEqual(payload["progress"]["deliverables_done"], 4)
            self.assertTrue(payload["modules"])
            self.assertTrue(payload["issues"])

            code, page = _http("GET", f"{base}/")
            self.assertEqual(code, 200)
            self.assertIn("OOMWOO", page)
            self.assertIn("/api/status", page)
            self.assertIn("makerspet/oomwoo", page)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_index_tracks_modules_not_finance(self) -> None:
        html = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn("function renderModules", html)
        self.assertIn("Ready to start", html)
        self.assertNotIn("combined_monthly", html)
        self.assertNotIn("FitDash", html)


if __name__ == "__main__":
    unittest.main()
