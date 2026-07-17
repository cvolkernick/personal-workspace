"""Tests for the time-allocator HTTP dashboard (real server + real store)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "holistic" / "server.py"


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 5.0) -> tuple[int, dict]:
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


class DashboardServerTests(unittest.TestCase):
    def test_api_seed_add_remove_allocate_via_live_server(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "dash.json"
            # Bind ephemeral port via OS: start on 0 not supported by argparse easily —
            # pick a high port and retry once if busy.
            port = 18770
            env = {**dict(**__import__("os").environ), "PYTHONPATH": str(ROOT)}
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(SERVER),
                    "--port",
                    str(port),
                    "--host",
                    "127.0.0.1",
                    "--data",
                    str(data),
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
                deadline = time.time() + 8
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

                code, state = _http_json("POST", f"{base}/api/seed", {})
                self.assertEqual(code, 200, state)
                self.assertGreaterEqual(len(state.get("targets") or []), 4)
                self.assertIn("plan", state)
                self.assertTrue(data.is_file())
                plan_ids = {b["id"] for b in (state.get("plan") or {}).get("blocks") or []}
                self.assertIn("sleep", plan_ids)
                self.assertIn("lyft", plan_ids)

                code, state = _http_json(
                    "POST",
                    f"{base}/api/add",
                    {
                        "title": "Dashboard item",
                        "kind": "task",
                        "priority": 7,
                        "id": "dash-1",
                        "minutes": 45,
                    },
                )
                self.assertEqual(code, 200, state)
                ids = {it["id"] for it in state["items"]}
                self.assertIn("dash-1", ids)

                code, state = _http_json(
                    "POST",
                    f"{base}/api/add",
                    {"title": "Temp", "id": "temp-rm", "minutes": 10},
                )
                self.assertEqual(code, 200, state)
                code, state = _http_json("POST", f"{base}/api/remove", {"key": "temp-rm"})
                self.assertEqual(code, 200, state)
                ids = {it["id"] for it in state["items"]}
                self.assertNotIn("temp-rm", ids)
                self.assertIn("dash-1", ids)

                code, state = _http_json("POST", f"{base}/api/allocate", {"total": 300})
                self.assertEqual(code, 200, state)
                self.assertEqual(state["total_minutes"], 300)

                code, state = _http_json(
                    "POST", f"{base}/api/log", {"target_id": "sleep", "value": 8.0}
                )
                self.assertEqual(code, 200, state)

                code, state = _http_json("GET", f"{base}/api/state")
                self.assertEqual(code, 200, state)
                self.assertEqual(state["total_minutes"], 300)
                self.assertIn("dash-1", {it["id"] for it in state["items"]})
                self.assertTrue(any(k["id"] == "sleep" for k in state.get("kpi_status") or []))

                # index.html served
                req = urllib.request.Request(f"{base}/")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8")
                    self.assertEqual(resp.status, 200)
                    self.assertIn("Time Allocator", html)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
