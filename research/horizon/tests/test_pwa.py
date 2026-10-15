#!/usr/bin/env python3
"""Horizon install shell: manifest, icons, service worker, head tags.

Mirrors FCC's PWA pattern with Horizon-owned files. Checks the shell works
both at the root (:8795) and behind FCC's /horizon lens prefix. Hostnames
here are placeholders.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HORIZON = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "research" / "daily_brief" / "fixtures"
ORIGIN = "https://horizon-host.example"

from research.daily_brief import store  # noqa: E402
from research.horizon import server  # noqa: E402

PAGES = ("/", "/model", "/daily-brief", "/daily-brief/archive", "/daily-brief/2026-10-09/am")
ICON_SIZES = {
    "/icon-192.png": 192,
    "/icon-512.png": 512,
    "/icon-maskable-192.png": 192,
    "/icon-maskable-512.png": 512,
    "/apple-touch-icon.png": 180,
    "/favicon-32.png": 32,
}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _png_size(raw: bytes) -> tuple[int, int]:
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", raw[16:24])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class TestInstallShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        ed = json.loads((FIXTURES / "sample-am.json").read_text(encoding="utf-8"))
        store.publish(copy.deepcopy(ed), root=Path(cls._tmp.name))
        cls._env = mock.patch.dict(os.environ, {"DAILY_BRIEF_DIR": cls._tmp.name})
        cls._env.start()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.HorizonHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls._env.stop()
        cls._tmp.cleanup()

    def _req(self, path: str, method: str = "GET") -> tuple[int, Message, bytes]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        try:
            with _OPENER.open(req, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    # -- manifest -----------------------------------------------------------
    def test_manifest_served_valid_json(self) -> None:
        code, headers, body = self._req("/manifest.webmanifest")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "application/manifest+json")
        self.assertEqual(headers.get("Cache-Control"), "no-cache")
        m = json.loads(body)
        self.assertEqual(m["name"], "Horizon")
        self.assertEqual(m["short_name"], "Horizon")
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["theme_color"], "#060a10")
        self.assertEqual(m["background_color"], "#060a10")
        for key in ("start_url", "scope", "id", "icons"):
            self.assertIn(key, m)

    def test_manifest_theme_matches_base_css(self) -> None:
        css_bg = re.search(r"--bg:\s*(#[0-9a-fA-F]{6})", (HORIZON / "index.html").read_text(encoding="utf-8"))
        self.assertIsNotNone(css_bg)
        m = json.loads((HORIZON / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual(m["theme_color"].lower(), css_bg.group(1).lower())

    def test_manifest_urls_are_prefix_aware(self) -> None:
        m = json.loads(self._req("/manifest.webmanifest")[2])
        # Relative URLs: resolved against the manifest URL, so they follow the lens prefix.
        for prefix in ("", "/horizon"):
            murl = f"{ORIGIN}{prefix}/manifest.webmanifest"
            self.assertEqual(urljoin(murl, m["start_url"]), f"{ORIGIN}{prefix}/")
            self.assertEqual(urljoin(murl, m["scope"]), f"{ORIGIN}{prefix}/")
            for icon in m["icons"]:
                self.assertFalse(icon["src"].startswith("/"), icon["src"])
                self.assertTrue(urljoin(murl, icon["src"]).startswith(f"{ORIGIN}{prefix}/"))
        # id resolves against the origin; must not collide with FCC's id "/".
        self.assertNotIn(m["id"], ("/", "./", ""))
        self.assertEqual(urljoin(ORIGIN + "/", m["id"]), ORIGIN + "/horizon")

    def test_manifest_icons_cover_any_and_maskable(self) -> None:
        m = json.loads(self._req("/manifest.webmanifest")[2])
        have = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
        for want in (("192x192", "any"), ("512x512", "any"), ("192x192", "maskable"), ("512x512", "maskable")):
            self.assertIn(want, have)
        for icon in m["icons"]:
            code, headers, raw = self._req("/" + icon["src"])
            self.assertEqual(code, 200, icon["src"])
            self.assertEqual(headers.get("Content-Type"), "image/png")
            w, h = _png_size(raw)
            self.assertEqual(f"{w}x{h}", icon["sizes"], icon["src"])

    # -- icons ----------------------------------------------------------------
    def test_icons_200_png(self) -> None:
        for path, size in ICON_SIZES.items():
            for method in ("GET", "HEAD"):
                code, headers, raw = self._req(path, method)
                self.assertEqual(code, 200, (path, method))
                self.assertEqual(headers.get("Content-Type"), "image/png", path)
                if method == "GET":
                    self.assertEqual(_png_size(raw), (size, size), path)

    def test_apple_touch_aliases(self) -> None:
        want = self._req("/apple-touch-icon.png")[2]
        for path in ("/apple-touch-icon-precomposed.png", "/apple-touch-icon-180x180.png"):
            code, headers, raw = self._req(path)
            self.assertEqual(code, 200, path)
            self.assertEqual(headers.get("Content-Type"), "image/png")
            self.assertEqual(raw, want)

    def test_unknown_png_still_404(self) -> None:
        self.assertEqual(self._req("/no-such-icon.png")[0], 404)
        self.assertIsNone(server.pwa_icon("../server.py"))

    # -- service worker -------------------------------------------------------
    def test_sw_served_as_javascript(self) -> None:
        for path in ("/sw.js", "/pwa.js"):
            code, headers, body = self._req(path)
            self.assertEqual(code, 200, path)
            self.assertTrue(headers.get("Content-Type", "").startswith("text/javascript"), path)
            self.assertEqual(headers.get("Cache-Control"), "no-cache", path)
            self.assertEqual(body, (HORIZON / path.lstrip("/")).read_bytes())

    def test_sw_never_caches_live_data(self) -> None:
        sw = (HORIZON / "sw.js").read_text(encoding="utf-8")
        for live in ('"api/"', '"data/"', '"daily-brief/api/"', '".json"'):
            self.assertIn(live, sw)
        shell = json.loads("[" + re.search(r"const SHELL = \[(.*?)\];", sw, re.S).group(1).rstrip().rstrip(",") + "]")
        for entry in shell:
            self.assertFalse(entry.startswith("/"), entry)
            self.assertFalse(entry.startswith(("api/", "data/")) or entry.endswith(".json"), entry)
            self.assertNotIn(entry, ("", "./", "model", "daily-brief"))  # no HTML in the cache
            self.assertEqual(self._req("/" + entry)[0], 200, entry)
        self.assertIn('req.mode === "navigate"', sw)

    # -- pages ------------------------------------------------------------------
    def test_pages_link_manifest_and_touch_icon(self) -> None:
        for page in PAGES:
            code, _, body = self._req(page)
            self.assertEqual(code, 200, page)
            html = body.decode("utf-8")
            head = html.split("</head>", 1)[0]
            self.assertEqual(head.count('rel="manifest"'), 1, page)
            self.assertIn('rel="apple-touch-icon"', head, page)
            self.assertIn('name="apple-mobile-web-app-capable" content="yes"', head, page)
            self.assertIn('name="apple-mobile-web-app-title" content="Horizon"', head, page)
            self.assertIn('name="theme-color" content="#060a10"', head, page)
            self.assertRegex(head, r'<script src="/?pwa\.js" defer>', page)
            href = re.search(r'<link rel="manifest" href="([^"]+)"', head).group(1)
            self.assertEqual(urljoin(f"http://127.0.0.1:{self.port}{page}", href), f"http://127.0.0.1:{self.port}/manifest.webmanifest", page)

    def test_daily_brief_json_untouched(self) -> None:
        code, headers, body = self._req("/daily-brief/api/latest")
        self.assertEqual(code, 200)
        self.assertNotIn(b"manifest", body)
        json.loads(body)

    def test_with_pwa_head_idempotent(self) -> None:
        html = "<html><head><title>x</title></head><body></body></html>"
        once = server.with_pwa_head(html)
        self.assertEqual(server.with_pwa_head(once), once)
        self.assertIn('rel="manifest"', once)


def _load_fcc_rewrite():
    try:
        spec = importlib.util.spec_from_file_location("fcc_server_hz_pwa", ROOT / "financial-command" / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod.rewrite_root_absolute
    except Exception:  # noqa: BLE001
        return None


class TestBehindFccLens(unittest.TestCase):
    """Run Horizon's HTML/JS through FCC's /horizon rewrite (financial-command/server.py)."""

    @classmethod
    def setUpClass(cls) -> None:
        fn = _load_fcc_rewrite()
        cls.rewrite = staticmethod(fn) if fn else None

    def setUp(self) -> None:
        if self.rewrite is None:
            self.skipTest("FCC server not importable here")

    def test_brief_head_tags_land_under_prefix(self) -> None:
        page = "<!doctype html><html><head><title>x</title></head><body></body></html>"
        out = self.rewrite(server.with_pwa_head(page).encode(), "/horizon", "text/html; charset=utf-8").decode()
        self.assertIn('href="/horizon/manifest.webmanifest"', out)
        self.assertIn('href="/horizon/apple-touch-icon.png"', out)
        self.assertIn('src="/horizon/pwa.js"', out)

    def test_model_head_tags_resolve_under_prefix(self) -> None:
        out = self.rewrite((HORIZON / "index.html").read_bytes(), "/horizon", "text/html").decode()
        base = re.search(r'<base href="([^"]+)">', out).group(1)
        href = re.search(r'<link rel="manifest" href="([^"]+)"', out).group(1)
        self.assertEqual(urljoin(urljoin(ORIGIN + "/horizon/model", base), href), ORIGIN + "/horizon/manifest.webmanifest")

    def test_worker_scripts_pass_through_unchanged(self) -> None:
        for name in ("sw.js", "pwa.js"):
            raw = (HORIZON / name).read_bytes()
            self.assertEqual(self.rewrite(raw, "/horizon", "text/javascript; charset=utf-8"), raw, name)


if __name__ == "__main__":
    unittest.main()
