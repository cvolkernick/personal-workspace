"""FCC PWA shell: origin-scoped standalone app like FitDash."""

from __future__ import annotations

import importlib.util
import json
import socket
import struct
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FCC = ROOT / "financial-command"
SURFACES = (
    "index.html",
    "capital-flows.html",
    "watchlist.html",
    "interest-spectrum.html",
    "bias-spectrum.html",
    "position.html",
)


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_pwa", FCC / "server.py")
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


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


class TestFccPwaFiles(unittest.TestCase):
    def test_manifest_is_standalone_origin_scope(self) -> None:
        raw = (FCC / "manifest.webmanifest").read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["scope"], "/")
        self.assertEqual(data.get("id"), "/")
        self.assertFalse(data["start_url"].startswith("/api/"))
        self.assertEqual(data["theme_color"], "#0b0f14")
        self.assertEqual(data["background_color"], "#0b0f14")
        sizes = {icon["sizes"]: icon["src"] for icon in data["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        self.assertTrue(sizes["192x192"].startswith("/"))
        self.assertTrue(sizes["512x512"].startswith("/"))
        self.assertFalse(sizes["192x192"].startswith("/api/"))

    def test_server_accepts_systemd_local_flag(self) -> None:
        src = (FCC / "server.py").read_text(encoding="utf-8")
        self.assertIn('"--local"', src)

    def test_sw_never_caches_api_or_json(self) -> None:
        src = (FCC / "sw.js").read_text(encoding="utf-8")
        self.assertIn('url.pathname.startsWith("/api/")', src)
        self.assertIn('url.pathname.endsWith(".json")', src)
        self.assertNotIn("treasury_latest.json", src)
        precache = src.split("const PRECACHE = [", 1)[1].split("];", 1)[0]
        self.assertNotIn(".json", precache)
        self.assertNotIn("/api/", precache)

    def test_html_surfaces_link_manifest_and_register_sw(self) -> None:
        pwa = (FCC / "pwa.js").read_text(encoding="utf-8")
        self.assertIn("navigator.serviceWorker.register", pwa)
        self.assertIn('"/sw.js"', pwa)
        self.assertNotIn('"/financial-command/sw.js"', pwa)
        for name in SURFACES:
            html = (FCC / name).read_text(encoding="utf-8")
            self.assertIn('rel="manifest"', html, name)
            self.assertIn('href="/manifest.webmanifest"', html, name)
            self.assertNotIn("/financial-command/manifest.webmanifest", html, name)
            self.assertIn("pwa.js", html, name)
            self.assertNotIn('navigator.serviceWorker.register("/")', html, name)


class TestFccPwaHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), cls.mod.FCCHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _get(self, path: str) -> tuple[int, str, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, ctype, resp.read()
        except urllib.error.HTTPError as exc:
            ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
            return exc.code, ctype, exc.read()

    def _head(self, path: str) -> tuple[int, str, int]:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                ctype = resp.headers.get("Content-Type", "")
                clen = int(resp.headers.get("Content-Length") or 0)
                return resp.status, ctype, clen
        except urllib.error.HTTPError as exc:
            ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
            clen = int(exc.headers.get("Content-Length") or 0) if exc.headers else 0
            return exc.code, ctype, clen

    def test_prefixed_manifest_is_json_standalone(self) -> None:
        code, ctype, body = self._get("/financial-command/manifest.webmanifest")
        self.assertEqual(code, 200)
        self.assertTrue(
            "json" in ctype.lower() or "manifest" in ctype.lower(),
            ctype,
        )
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["scope"], "/")

    def test_prefixed_sw_js_skips_api(self) -> None:
        code, _, body = self._get("/financial-command/sw.js")
        self.assertEqual(code, 200)
        src = body.decode("utf-8")
        self.assertIn('url.pathname.startsWith("/api/")', src)

    def test_manifest_icon_urls_are_pngs(self) -> None:
        _, _, body = self._get("/financial-command/manifest.webmanifest")
        data = json.loads(body.decode("utf-8"))
        for icon in data["icons"]:
            code, ctype, raw = self._get(icon["src"])
            self.assertEqual(code, 200, icon["src"])
            self.assertIn("image/png", ctype.lower(), icon["src"])
            width, height = _png_size(raw)
            declared = icon["sizes"].split("x")
            self.assertEqual(width, int(declared[0]), icon)
            self.assertEqual(height, int(declared[1]), icon)

    def test_root_pwa_aliases(self) -> None:
        for path in (
            "/",
            "/manifest.webmanifest",
            "/sw.js",
            "/icon-192.png",
            "/icon-512.png",
            "/pwa.js",
            "/nav-fleet.js",
            "/nav-horizon.js",
            "/favicon.svg",
            "/favicon.ico",
            "/favicon-32.png",
            "/apple-touch-icon.png",
            "/interest-spectrum.html",
            "/bias-spectrum.html",
            "/watchlist.html",
            "/capital-flows.html",
            "/interest-spectrum",
            "/bias-spectrum",
        ):
            code, _, _ = self._get(path)
            self.assertEqual(code, 200, path)
        man_code, _, man_root = self._get("/manifest.webmanifest")
        _, _, man_pref = self._get("/financial-command/manifest.webmanifest")
        self.assertEqual(man_code, 200)
        self.assertEqual(man_root, man_pref)
        sw_code, ctype, sw_root = self._get("/sw.js")
        _, _, sw_pref = self._get("/financial-command/sw.js")
        self.assertEqual(sw_code, 200)
        self.assertEqual(sw_root, sw_pref)
        self.assertIn("javascript", ctype.lower())

    def test_entry_html_exposes_manifest_and_pwa_script(self) -> None:
        for path in ("/", "/financial-command/"):
            code, _, body = self._get(path)
            self.assertEqual(code, 200, path)
            self.assertIn(b'rel="manifest"', body)
            self.assertIn(b'href="/manifest.webmanifest"', body)
            self.assertIn(b"pwa.js", body)
        self.assertIn(b'navigator.serviceWorker.register("/sw.js")', (FCC / "pwa.js").read_bytes())

    def test_sw_allowed_header_is_origin(self) -> None:
        url = f"http://127.0.0.1:{self.port}/sw.js"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.headers.get("Service-Worker-Allowed"), "/")

    def test_root_pwa_aliases_head_matches_get(self) -> None:
        """Android A2HS probes HEAD. FitDash HEAD==GET; FCC HEAD used to 404."""
        for path in (
            "/",
            "/manifest.webmanifest",
            "/sw.js",
            "/icon-192.png",
            "/icon-512.png",
            "/pwa.js",
            "/nav-fleet.js",
            "/nav-horizon.js",
            "/favicon.svg",
            "/favicon.ico",
            "/favicon-32.png",
            "/apple-touch-icon.png",
            "/interest-spectrum.html",
            "/bias-spectrum.html",
            "/watchlist.html",
            "/capital-flows.html",
        ):
            get_code, get_ctype, get_body = self._get(path)
            head_code, head_ctype, head_len = self._head(path)
            self.assertEqual(get_code, 200, path)
            self.assertEqual(head_code, 200, f"HEAD {path}")
            self.assertEqual(head_len, len(get_body), f"HEAD Content-Length {path}")
            self.assertEqual(
                (head_ctype or "").split(";")[0].strip().lower(),
                (get_ctype or "").split(";")[0].strip().lower(),
                path,
            )
        head_code, _, head_len = self._head("/")
        get_code, _, get_body = self._get("/")
        self.assertEqual(head_code, 200)
        self.assertEqual(get_code, 200)
        self.assertEqual(head_len, len(get_body))
        self.assertGreater(head_len, 10_000)

    def test_head_sw_allowed_header_is_origin(self) -> None:
        url = f"http://127.0.0.1:{self.port}/sw.js"
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Service-Worker-Allowed"), "/")


if __name__ == "__main__":
    unittest.main()
