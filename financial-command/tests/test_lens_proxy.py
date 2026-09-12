"""Same-origin /fleet and /horizon reverse-proxy (#680)."""

from __future__ import annotations

import http.client
import importlib.util
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FCC = ROOT / "financial-command"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_lens", FCC / "server.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


class _Upstream(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._send(200, "application/json", b'{"ok":true,"service":"lens-up"}')
            return
        if path in ("/", "/index.html"):
            html = (
                b"<!doctype html><head><title>up</title></head>"
                b'<script src="/app.js"></script>'
                b'<script>fetch("/api/health")</script>'
            )
            self._send(200, "text/html; charset=utf-8", html)
            return
        if path == "/app.js":
            self._send(200, "text/javascript", b'fetch("/api/fleet")')
            return
        self._send(404, "text/plain", b"missing")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        _ = self.rfile.read(length) if length else b""
        if self.path.split("?", 1)[0] == "/api/refresh":
            self._send(200, "application/json", b'{"ok":true,"refreshed":true}')
            return
        self._send(404, "text/plain", b"missing")


class TestRewriteHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()

    def test_lens_proxy_spec(self) -> None:
        spec = self.mod.lens_proxy_spec
        self.assertEqual(spec("/fleet")[0], "/fleet")
        self.assertEqual(spec("/fleet/")[1], "/")
        self.assertEqual(spec("/fleet/api/health")[1], "/api/health")
        self.assertEqual(spec("/horizon/api/dashboard")[1], "/api/dashboard")
        self.assertIsNone(spec("/api/treasury"))
        self.assertIsNone(spec("/fleets"))

    def test_rewrite_html_and_js_root_absolute(self) -> None:
        html = b'<!doctype html><head></head><script src="/app.js"></script>'
        out = self.mod.rewrite_root_absolute(html, "/fleet", "text/html")
        self.assertIn(b'src="/fleet/app.js"', out)
        self.assertIn(b'<base href="/fleet/">', out)
        js = b'fetch("/api/health")'
        js_out = self.mod.rewrite_root_absolute(js, "/fleet", "text/javascript")
        self.assertEqual(js_out, b'fetch("/fleet/api/health")')
        already = b'fetch("/fleet/api/health")'
        self.assertEqual(
            self.mod.rewrite_root_absolute(already, "/fleet", "text/javascript"),
            already,
        )

    def test_rewrite_location(self) -> None:
        self.assertEqual(
            self.mod.rewrite_location("http://127.0.0.1:8796/api/health", "/fleet", "127.0.0.1", 8796),
            "/fleet/api/health",
        )
        self.assertEqual(
            self.mod.rewrite_location("/index.html", "/horizon", "127.0.0.1", 8795),
            "/horizon/index.html",
        )


class TestLensProxyHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()
        cls.up_port = _free_port()
        cls.up = ThreadingHTTPServer(("127.0.0.1", cls.up_port), _Upstream)
        cls.up_thread = threading.Thread(target=cls.up.serve_forever, daemon=True)
        cls.up_thread.start()
        cls._patch = mock.patch.dict(
            cls.mod.LENS_UPSTREAMS,
            {"/fleet": ("127.0.0.1", cls.up_port), "/horizon": ("127.0.0.1", cls.up_port)},
        )
        cls._patch.start()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), cls.mod.FCCHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.up.shutdown()
        cls.up.server_close()
        cls.up_thread.join(timeout=5)
        cls._patch.stop()

    def _get(self, path: str) -> tuple[int, dict, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()

    def test_slash_redirect(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/fleet")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 302)
            self.assertEqual(resp.getheader("Location"), "/fleet/")
            resp.read()
        finally:
            conn.close()

    def test_html_rewrites_nested_assets_and_they_load(self) -> None:
        code, _, body = self._get("/fleet/")
        self.assertEqual(code, 200)
        self.assertIn(b'src="/fleet/app.js"', body)
        self.assertIn(b'fetch("/fleet/api/health")', body)
        self.assertIn(b'<base href="/fleet/">', body)
        js_code, _, js_body = self._get("/fleet/app.js")
        self.assertEqual(js_code, 200)
        self.assertIn(b'fetch("/fleet/api/fleet")', js_body)
        api_code, _, api_body = self._get("/fleet/api/health")
        self.assertEqual(api_code, 200)
        self.assertTrue(json.loads(api_body.decode("utf-8")).get("ok"))

    def test_horizon_post_refresh(self) -> None:
        url = f"http://127.0.0.1:{self.port}/horizon/api/refresh"
        req = urllib.request.Request(url, data=b'{"offline":true}', method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data.get("ok"))

    def test_upstream_down_is_502(self) -> None:
        with mock.patch.dict(self.mod.LENS_UPSTREAMS, {"/fleet": ("127.0.0.1", 1)}):
            code, _, body = self._get("/fleet/")
        self.assertEqual(code, 502)
        self.assertIn(b"unreachable", body)


if __name__ == "__main__":
    unittest.main()
