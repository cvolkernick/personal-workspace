"""Unit tests for shared remote_backend helper + representative frontend proxy.

Exercises shipped resolve/load/forward/annotate logic and boots a real
orchestra frontend process with --backend against a minimal HTTP backend.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote_backend as rb  # noqa: E402

ORCHESTRA_SERVER = ROOT / "orchestra" / "server.py"


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 8.0):
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


class ResolveBackendTests(unittest.TestCase):
    def test_local_wins(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "backend.json"
            cfg.write_text(
                json.dumps({"url": "http://mesh-host:8790", "label": "pi"}),
                encoding="utf-8",
            )
            url, label = rb.resolve_backend(
                local=True, backend="http://other:1", config_path=cfg
            )
            self.assertIsNone(url)
            self.assertEqual(label, "")

    def test_cli_backend_accepts_mesh_hostname(self) -> None:
        url, label = rb.resolve_backend(
            local=False, backend="http://prism-gateway:8790", config_path=None
        )
        self.assertEqual(url, "http://prism-gateway:8790")
        self.assertEqual(label, "prism-gateway")

    def test_config_file_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "backend.json"
            cfg.write_text(
                json.dumps(
                    {
                        "url": "http://100.64.0.12:8780/",
                        "label": "tailscale-pi",
                    }
                ),
                encoding="utf-8",
            )
            url, label = rb.resolve_backend(local=False, backend=None, config_path=cfg)
            self.assertEqual(url, "http://100.64.0.12:8780")
            self.assertEqual(label, "tailscale-pi")

    def test_empty_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "missing.json"
            url, label = rb.resolve_backend(local=False, backend=None, config_path=cfg)
            self.assertIsNone(url)
            self.assertEqual(label, "")

    def test_reject_relative_url(self) -> None:
        with self.assertRaises(ValueError):
            rb.normalize_base_url("not-a-url")

    def test_is_api_path(self) -> None:
        self.assertTrue(rb.is_api_path("/api/health"))
        self.assertTrue(rb.is_api_path("/api/health?x=1"))
        self.assertFalse(rb.is_api_path("/index.html"))
        self.assertFalse(rb.is_api_path("/"))


class ForwardAndAnnotateTests(unittest.TestCase):
    def test_forward_api_and_annotate_health(self) -> None:
        port = _free_port()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: ANN001
                return

            def do_GET(self) -> None:  # noqa: N802
                body = json.dumps({"ok": True, "service": "fake-backend"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            code, data, ctype = rb.forward_api(
                f"http://127.0.0.1:{port}", "/api/health", "GET"
            )
            self.assertEqual(code, 200)
            self.assertIn("json", ctype)
            payload = json.loads(data.decode())
            self.assertTrue(payload.get("ok"))
            self.assertEqual(payload.get("service"), "fake-backend")

            annotated = rb.annotate_health_json(
                data,
                backend_url=f"http://127.0.0.1:{port}",
                backend_label="test",
                frontend="http://127.0.0.1:9/",
            )
            a = json.loads(annotated.decode())
            self.assertTrue(a.get("proxy"))
            self.assertEqual(a.get("backend_label"), "test")
            self.assertEqual(a.get("frontend"), "http://127.0.0.1:9/")
            self.assertEqual(a.get("service"), "fake-backend")
        finally:
            httpd.shutdown()


class OrchestraProxyLaunchTests(unittest.TestCase):
    """Ship path: orchestra frontend --backend → temporary real backend."""

    def _run_once(self) -> dict:
        backend_port = _free_port()
        frontend_port = _free_port()

        class Backend(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: ANN001
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0] == "/api/health":
                    body = json.dumps(
                        {
                            "ok": True,
                            "service": "orchestra",
                            "workspace": "/pi/personal-workspace",
                        }
                    ).encode()
                else:
                    body = json.dumps({"ok": False, "error": "unexpected"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        backend = ThreadingHTTPServer(("127.0.0.1", backend_port), Backend)
        bt = threading.Thread(target=backend.serve_forever, daemon=True)
        bt.start()

        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        frontend = subprocess.Popen(
            [
                sys.executable,
                str(ORCHESTRA_SERVER),
                "--port",
                str(frontend_port),
                "--host",
                "127.0.0.1",
                "--backend",
                f"http://127.0.0.1:{backend_port}",
                "--no-browser",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.time() + 12
            last_err = ""
            while time.time() < deadline:
                try:
                    code, payload = _http_json(
                        "GET", f"http://127.0.0.1:{frontend_port}/api/health"
                    )
                    if code == 200 and payload.get("ok"):
                        return payload
                    last_err = f"code={code} payload={payload}"
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)
                time.sleep(0.1)
            err = frontend.stderr.read() if frontend.stderr else ""
            self.fail(f"frontend proxy not ready: {last_err}\nstderr={err}")
        finally:
            frontend.terminate()
            try:
                frontend.wait(timeout=3)
            except subprocess.TimeoutExpired:
                frontend.kill()
            backend.shutdown()

    def test_orchestra_proxy_health_twice(self) -> None:
        first = self._run_once()
        self.assertTrue(first.get("ok"))
        self.assertEqual(first.get("service"), "orchestra")
        self.assertTrue(first.get("proxy"), msg=first)
        self.assertIn("backend", first)
        self.assertIn("/pi/personal-workspace", str(first.get("workspace", "")))

        second = self._run_once()
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("proxy"))
        self.assertEqual(second.get("service"), "orchestra")


if __name__ == "__main__":
    unittest.main()
