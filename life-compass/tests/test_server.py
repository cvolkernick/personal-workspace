"""Private dashboard HTTP: five sections, bind default, no CORS, robots."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parent
SERVER = PKG / "server.py"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from engine import empty_state, run_sweep  # noqa: E402
from store import save_state  # noqa: E402

NOW = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)


def _quiet_snapshot() -> dict:
    return {
        "as_of": "2026-09-19T14:00:00+00:00",
        "fitness": {"wired": True, "days_since_resistance": 1, "as_of": "2026-09-19T14:00:00+00:00"},
        "financial": {
            "wired": True,
            "as_of": "2026-09-19T14:00:00+00:00",
            "bills": [],
            "categories": [],
            "payoff": [],
        },
        "operations": {
            "as_of": "2026-09-19T14:00:00+00:00",
            "turo_trips": [],
            "training": [],
            "invoice_ready": {"wired": True, "unassigned": []},
        },
        "radar": {"candidates": [{"title": "Offer", "why": "emerging", "source": "github"}]},
    }


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def _http(method: str, url: str, body: dict | None = None, timeout: float = 5.0):
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
            headers = dict(resp.headers.items())
            payload = json.loads(raw) if raw and "application/json" in (resp.headers.get("Content-Type") or "") else raw
            return resp.status, payload, headers
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = raw
        return e.code, payload, dict(e.headers.items()) if e.headers else {}


class TestServerDefaults(unittest.TestCase):
    def test_argparse_default_host_is_loopback(self) -> None:
        # Importing server should not bind. Parse the same defaults.
        parser = argparse.ArgumentParser()
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8793)
        args = parser.parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8793)

    def test_server_module_default_host(self) -> None:
        import server as svc

        self.assertEqual(svc.DEFAULT_HOST, "127.0.0.1")
        self.assertEqual(svc.DEFAULT_PORT, 8793)


class TestLiveServer(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.state_path = Path(self.td.name) / "state.json"
        state, _, _ = run_sweep(empty_state(), _quiet_snapshot(), now=NOW)
        save_state(state, self.state_path)
        self.port = _free_port()
        env = {**dict(**__import__("os").environ), "PYTHONPATH": str(PKG) + ":" + str(ROOT)}
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(SERVER),
                "--port",
                str(self.port),
                "--host",
                "127.0.0.1",
                "--state",
                str(self.state_path),
                "--no-browser",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 8
        last = None
        while time.time() < deadline:
            try:
                code, health, _ = _http("GET", f"{self.base}/api/health")
                if code == 200 and health.get("ok"):
                    return
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.1)
        err = ""
        if self.proc.stderr:
            try:
                err = self.proc.stderr.read()
            except Exception:
                err = str(last)
        self.tearDown()
        self.fail(f"server not ready: {err or last}")

    def tearDown(self) -> None:
        if getattr(self, "proc", None) and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "td", None):
            self.td.cleanup()

    def test_health_private_flag(self) -> None:
        code, health, headers = _http("GET", f"{self.base}/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(health.get("private"))
        self.assertEqual(health.get("service"), "life-compass")
        header_l = {k.lower(): v for k, v in headers.items()}
        self.assertNotIn("access-control-allow-origin", header_l)
        self.assertIn("no-store", (header_l.get("cache-control") or "").lower())
        self.assertIn("noindex", (header_l.get("x-robots-tag") or "").lower())

    def test_options_not_cors(self) -> None:
        code, _payload, headers = _http("OPTIONS", f"{self.base}/api/state")
        self.assertEqual(code, 403)
        header_l = {k.lower(): v for k, v in headers.items()}
        self.assertNotIn("access-control-allow-origin", header_l)

    def test_robots_disallow(self) -> None:
        code, body, _ = _http("GET", f"{self.base}/robots.txt")
        self.assertEqual(code, 200)
        self.assertIn("Disallow: /", body)

    def test_state_five_sections(self) -> None:
        code, data, _ = _http("GET", f"{self.base}/api/state")
        self.assertEqual(code, 200)
        for key in ("one_thing", "goals", "operations", "radar", "watchlist"):
            self.assertIn(key, data)
        self.assertTrue(data["watchlist"])
        self.assertEqual(data["goal"]["id"], "goal_bf47e52a74b7")
        self.assertEqual(data["one_thing"]["kind"], "all-quiet")

    def test_one_thing_and_missing(self) -> None:
        code, ot, _ = _http("GET", f"{self.base}/api/one-thing")
        self.assertEqual(code, 200)
        self.assertEqual(ot["kind"], "all-quiet")
        code, miss, _ = _http("GET", f"{self.base}/api/missing")
        self.assertEqual(code, 200)
        self.assertFalse(miss["missing"])

    def test_html_noindex(self) -> None:
        req = urllib.request.Request(self.base + "/")
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode("utf-8")
            self.assertIn("noindex", html)
            self.assertIn("Your one thing", html)
            self.assertIn("Watchlist", html)

    def test_radar_promote_and_dismiss(self) -> None:
        code, data, _ = _http("GET", f"{self.base}/api/state")
        self.assertEqual(code, 200)
        cands = data["radar"]["candidates"]
        self.assertTrue(cands)
        cid = cands[0]["id"]
        code, promoted, _ = _http("POST", f"{self.base}/api/radar/promote", {"id": cid})
        self.assertEqual(code, 200)
        self.assertTrue(promoted.get("promoted_target"))
        # Re-sweep with same candidate should not list it as new after promote
        # (promote removes it from candidates; fingerprint is not dismissed so
        # a new sweep would re-add — promote is a tracked target, not a hide.
        # Dismiss hides.)
        snap = _quiet_snapshot()
        snap["radar"] = {
            "candidates": [{"title": "Other", "why": "why-other", "source": "github"}]
        }
        code, swept, _ = _http("POST", f"{self.base}/api/sweep", {"snapshot": snap})
        self.assertEqual(code, 200)
        cid2 = swept["radar"]["candidates"][0]["id"]
        code, dismissed, _ = _http("POST", f"{self.base}/api/radar/dismiss", {"id": cid2})
        self.assertEqual(code, 200)
        self.assertTrue(dismissed["radar"]["dismissed"])


if __name__ == "__main__":
    unittest.main()
