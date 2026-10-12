#!/usr/bin/env python3
"""Persistent Horizon top nav (Home, Daily Brief, Archive, Model, Directory) on every page.

Links must resolve on Horizon's own origin (served at /) and behind FCC's
/horizon/ lens (X-Forwarded-Prefix + injected <base href="/horizon/">).
Hostnames are placeholders.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "research" / "daily_brief" / "fixtures"
OWN = "https://horizon-host.example:10000"
FCC = "https://fcc-host.example"

from research.daily_brief import store  # noqa: E402
from research.daily_brief.render import NOT_MODEL_LINE  # noqa: E402
from research.horizon import server  # noqa: E402

PAGES = {
    "/": "home",
    "/daily-brief": "daily-brief",
    "/daily-brief/archive": "archive",
    "/daily-brief/2026-10-09/am": "daily-brief",
    "/model": "model",
    "/directory": "directory",
}
EXPECT = {
    "home": "/",
    "daily-brief": "/daily-brief",
    "archive": "/daily-brief/archive",
    "model": "/model",
    "directory": "/directory",
}
_NAV_RE = re.compile(r'<nav id="hz-site-nav"[^>]*>(.*?)</nav>', re.S)
_LINK_RE = re.compile(r'<a href="([^"]*)" data-nav="([a-z-]+)"([^>]*)>')


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _load_fcc_rewrite():
    try:
        spec = importlib.util.spec_from_file_location("fcc_server_hz_nav", ROOT / "financial-command" / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod.rewrite_root_absolute
    except Exception:  # noqa: BLE001
        return None


class TestSiteNav(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        ed = json.loads((FIXTURES / "sample-am.json").read_text(encoding="utf-8"))
        store.publish(copy.deepcopy(ed), root=Path(cls._tmp.name))
        cls._env = mock.patch.dict(os.environ, {"DAILY_BRIEF_DIR": cls._tmp.name})
        cls._env.start()
        # Directory content is prism-local; use a placeholder fixture page.
        dird = Path(cls._tmp.name) / "directory-fixture"
        dird.mkdir()
        (dird / "index.html").write_text(
            "<!doctype html><html><head><title>Fixture</title></head><body><p>Example</p></body></html>",
            encoding="utf-8",
        )
        cls._dir = mock.patch.object(server, "DIRECTORY_DIR", dird)
        cls._dir.start()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.HorizonHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.rewrite = staticmethod(_load_fcc_rewrite() or (lambda b, *_a: b))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls._env.stop()
        cls._dir.stop()
        cls._tmp.cleanup()

    def _get(self, path: str, headers: dict[str, str] | None = None, method: str = "GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def _links(self, html: str) -> dict[str, tuple[str, str]]:
        navs = _NAV_RE.findall(html)
        self.assertEqual(len(navs), 1, "exactly one site nav")
        return {key: (href, rest) for href, key, rest in _LINK_RE.findall(navs[0])}

    def test_every_page_renders_nav(self) -> None:
        for path, active in PAGES.items():
            code, _, body = self._get(path)
            self.assertEqual(code, 200, path)
            html = body.decode("utf-8")
            links = self._links(html)
            self.assertEqual(list(links), ["home", "daily-brief", "archive", "model", "directory"], path)
            for key, (_href, rest) in links.items():
                self.assertEqual("aria-current" in rest, key == active, f"{path} {key}")
            # Nav sits right after <body ...>, before the page content.
            self.assertRegex(html, r"<body\b[^>]*><style>#hz-site-nav", path)

    def test_links_resolve_on_own_origin(self) -> None:
        for path in PAGES:
            links = self._links(self._get(path)[2].decode("utf-8"))
            for key, (href, _rest) in links.items():
                self.assertFalse(href.startswith("/"), f"{path} {key} must be relative: {href}")
                got = urlparse(urljoin(OWN + path, href)).path
                self.assertEqual(got, EXPECT[key], f"{path} {key} -> {href}")

    def test_links_resolve_behind_fcc_lens(self) -> None:
        for path in PAGES:
            raw = self._get(path, {"X-Forwarded-Prefix": "/horizon"})[2]
            html = self.rewrite(raw, "/horizon", "text/html; charset=utf-8").decode("utf-8")
            m = re.search(r'<base href="([^"]+)">', html)
            base = urljoin(FCC + "/horizon" + path, m.group(1)) if m else FCC + "/horizon" + path
            if m:
                self.assertEqual(urlparse(base).path, "/horizon/")
            links = self._links(html)
            for key, (href, _rest) in links.items():
                got = urlparse(urljoin(base, href)).path
                want = "/horizon" + EXPECT[key] if EXPECT[key] != "/" else "/horizon/"
                self.assertEqual(got, want, f"{path} {key} -> {href}")

    def test_head_length_matches_get(self) -> None:
        for path in PAGES:
            _, gh, body = self._get(path)
            code, hh, _ = self._get(path, method="HEAD")
            self.assertEqual(code, 200, path)
            self.assertEqual(int(hh.get("Content-Length")), len(body), path)

    def test_brief_keeps_not_model_label(self) -> None:
        for path in ("/", "/daily-brief", "/daily-brief/2026-10-09/am"):
            html = self._get(path)[2].decode("utf-8")
            self.assertIn(NOT_MODEL_LINE, html, path)

    def test_json_routes_have_no_nav(self) -> None:
        for path in ("/api/health", "/manifest.webmanifest"):
            self.assertNotIn(b"hz-site-nav", self._get(path)[2], path)

    def test_with_site_nav_idempotent(self) -> None:
        page = "<html><head></head><body class=x><p>hi</p></body></html>"
        once = server.with_site_nav(page, "/daily-brief/2026-10-09/pm")
        self.assertEqual(server.with_site_nav(once, "/daily-brief/2026-10-09/pm"), once)
        self.assertIn('href="../../daily-brief"', once)
        self.assertIn('href="../../"', once)


if __name__ == "__main__":
    unittest.main()
