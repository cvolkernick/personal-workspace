"""FCC /horizon/* -> Horizon's own origin when HORIZON_PUBLIC_ORIGIN is set.

FCC's manifest and root worker use scope "/", so Horizon under /horizon/ on the
same origin can only open "in FCC". Horizon on its own Tailscale Serve port is
a separate origin and installs as its own app. Hostnames are placeholders.
"""

from __future__ import annotations

import http.client
import importlib.util
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import prism_public  # noqa: E402

FCC = ROOT / "financial-command"
HZ = "https://fcc-host.unit.ts.net:10000"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_hz_redirect", FCC / "server.py")
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

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send(b'{"ok":true,"proxied":"' + self.path.encode() + b'"}')

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length) if length else b""
        self._send(b'{"ok":true,"posted":true}')


class TestHorizonPublicOrigin(unittest.TestCase):
    def test_valid_origins(self) -> None:
        f = prism_public.horizon_public_origin
        self.assertEqual(f({"HORIZON_PUBLIC_ORIGIN": HZ}), HZ)
        self.assertEqual(f({"HORIZON_PUBLIC_ORIGIN": HZ + "/"}), HZ)
        self.assertEqual(
            f({"HORIZON_PUBLIC_ORIGIN": "https://HZ.unit.ts.net:8443"}), "https://hz.unit.ts.net:8443"
        )
        self.assertEqual(f({"HORIZON_PUBLIC_ORIGIN": "https://hz.unit.ts.net"}), "https://hz.unit.ts.net")

    def test_invalid_origins_are_off(self) -> None:
        f = prism_public.horizon_public_origin
        for raw in (
            "",
            "http://fcc-host.unit.ts.net:10000",
            "https://fcc-host.unit.ts.net:8795",
            "https://fcc-host.unit.ts.net:10000/horizon",
            "https://fcc-host.unit.ts.net:10000/?x=1",
            "https://user@fcc-host.unit.ts.net:10000",
            "https://prism.example.ts.net:10000",
            "https://127.0.0.1:10000",
            "fcc-host.unit.ts.net:10000",
        ):
            self.assertEqual(f({"HORIZON_PUBLIC_ORIGIN": raw}), "", raw)

    def test_same_origin_as_fcc_refused(self) -> None:
        env = {"HORIZON_PUBLIC_ORIGIN": "https://fcc.unit.ts.net", "PRISM_PUBLIC_BASE": "https://fcc.unit.ts.net"}
        self.assertEqual(prism_public.horizon_public_origin(env), "")


class TestRedirectTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()

    def test_path_and_query_kept(self) -> None:
        t = self.mod.horizon_redirect_target
        self.assertEqual(t("/horizon", "GET", HZ), HZ + "/")
        self.assertEqual(t("/horizon/", "GET", HZ), HZ + "/")
        self.assertEqual(t("/horizon/daily-brief", "GET", HZ), HZ + "/daily-brief")
        self.assertEqual(t("/horizon/daily-brief/2026-10-09/am?x=1&y=2", "HEAD", HZ), HZ + "/daily-brief/2026-10-09/am?x=1&y=2")
        self.assertEqual(t("/horizon/daily-brief/archive", "GET", HZ), HZ + "/daily-brief/archive")
        self.assertEqual(t("/horizon/model?tab=world", "GET", HZ), HZ + "/model?tab=world")
        self.assertEqual(t("/horizon/manifest.webmanifest", "GET", HZ), HZ + "/manifest.webmanifest")

    def test_not_redirected(self) -> None:
        t = self.mod.horizon_redirect_target
        self.assertIsNone(t("/horizon/daily-brief", "GET", ""))
        self.assertIsNone(t("/horizon/api/health", "GET", HZ))
        self.assertIsNone(t("/horizon/api/refresh", "POST", HZ))
        self.assertIsNone(t("/horizon/daily-brief", "POST", HZ))
        self.assertIsNone(t("/horizon/sw.js", "GET", HZ))
        self.assertIsNone(t("/horizonx", "GET", HZ))
        self.assertIsNone(t("/fleet/", "GET", HZ))
        self.assertIsNone(t("/", "GET", HZ))


class TestFccServesRedirect(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()
        cls.up_port = _free_port()
        cls.up = ThreadingHTTPServer(("127.0.0.1", cls.up_port), _Upstream)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls._patch = mock.patch.dict(cls.mod.LENS_UPSTREAMS, {"/horizon": ("127.0.0.1", cls.up_port)})
        cls._patch.start()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), cls.mod.FCCHandler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.up.shutdown()
        cls.up.server_close()
        cls._patch.stop()

    def _req(self, method: str, path: str, body: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, resp.getheader("Location"), resp.getheader("Content-Type") or "", data

    def test_unset_keeps_proxy(self) -> None:
        with mock.patch.dict(os.environ, {"HORIZON_PUBLIC_ORIGIN": ""}):
            code, loc, _, body = self._req("GET", "/horizon/daily-brief?x=1")
        self.assertEqual(code, 200)
        self.assertIsNone(loc)
        self.assertIn(b'"proxied":"/daily-brief?x=1"', body)

    def test_set_redirects_get_and_head(self) -> None:
        with mock.patch.dict(os.environ, {"HORIZON_PUBLIC_ORIGIN": HZ}):
            for method in ("GET", "HEAD"):
                code, loc, _, _ = self._req(method, "/horizon/daily-brief/2026-10-09/pm?src=fcc")
                self.assertEqual(code, 302, method)
                self.assertEqual(loc, HZ + "/daily-brief/2026-10-09/pm?src=fcc", method)
            code, loc, _, _ = self._req("GET", "/horizon")
            self.assertEqual((code, loc), (302, HZ + "/"))

    def test_set_still_proxies_api_and_post(self) -> None:
        with mock.patch.dict(os.environ, {"HORIZON_PUBLIC_ORIGIN": HZ}):
            code, loc, _, body = self._req("GET", "/horizon/api/health")
            self.assertEqual(code, 200)
            self.assertIsNone(loc)
            code, _, _, body = self._req("POST", "/horizon/api/refresh", b"{}")
            self.assertEqual(code, 200)
            self.assertIn(b'"posted":true', body)

    def test_set_serves_retiring_worker(self) -> None:
        with mock.patch.dict(os.environ, {"HORIZON_PUBLIC_ORIGIN": HZ}):
            code, loc, ctype, body = self._req("GET", "/horizon/sw.js")
        self.assertEqual(code, 200)
        self.assertIsNone(loc)
        self.assertIn("javascript", ctype)
        self.assertIn(b"self.registration.unregister()", body)
        self.assertIn(b'"horizon-shell-"', body)

    def test_brief_redirect_chain_lands_on_horizon_origin(self) -> None:
        with mock.patch.dict(os.environ, {"HORIZON_PUBLIC_ORIGIN": HZ, "BRIEF_BASE_URL": ""}):
            code, loc, _, _ = self._req("GET", "/brief/2026-10-09/am")
            self.assertEqual(code, 302)
            self.assertEqual(loc, "/horizon/daily-brief/2026-10-09/am")
            code, loc, _, _ = self._req("GET", loc)
            self.assertEqual((code, loc), (302, HZ + "/daily-brief/2026-10-09/am"))


if __name__ == "__main__":
    unittest.main()
