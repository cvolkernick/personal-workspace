#!/usr/bin/env python3
"""Auto Fleet header: FCC same-host deep-link; no Orchestra."""

from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard_endpoints import _DEFAULT_SERVICES  # noqa: E402

FLEET = Path(__file__).resolve().parents[1]
ENDPOINTS = ROOT / "deploy" / "endpoints.json"
HARDCODED_IPS = ("192.168.100.98", "100.67.114.2")
PUBLIC_URL_NEEDLES = (
    "vercel.app",
    "vercel.com",
    "horizon.vercel",
    "https://horizon",
    "strategy/horizon.md",
)
ORCHESTRA_NEEDLES = (
    "link-orchestra",
    "nav-orchestra",
    "data-open-orchestra",
    "open-orchestra",
    "openOrchestrator",
    "Orchestrator",
    "Orchestra",
)


def _fcc_bind() -> tuple[int, str]:
    """Existing FCC bind from deploy/endpoints.json — do not invent."""
    data = json.loads(ENDPOINTS.read_text(encoding="utf-8"))
    svc = (data.get("services") or {}).get("financial-command") or {}
    port = int(svc["port"])
    path = str(svc["path"])
    defaults = _DEFAULT_SERVICES["financial-command"]
    if port != int(defaults["port"]) or path != str(defaults["path"]):
        raise AssertionError(
            "deploy/endpoints.json financial-command bind drifted from "
            f"dashboard_endpoints._DEFAULT_SERVICES: {port} {path!r}"
        )
    return port, path


def fcc_href(hostname: str) -> str:
    """Same contract as auto-fleet/nav-fcc.js fleetFccHref."""
    host = hostname or "127.0.0.1"
    port, path = _fcc_bind()
    return f"http://{host}:{port}{path}"


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict[str, str]] = []
        self._cur: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        ad = {k: (v or "") for k, v in attrs}
        self._cur = {
            "id": ad.get("id", ""),
            "href": ad.get("href", ""),
            "title": ad.get("title", ""),
            "text": "",
        }

    def handle_data(self, data: str) -> None:
        if self._cur is not None:
            self._cur["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._cur is not None:
            self._cur["text"] = re.sub(r"\s+", " ", self._cur["text"]).strip()
            self.anchors.append(self._cur)
            self._cur = None


def _parse_anchors(html: str) -> list[dict[str, str]]:
    p = _AnchorParser()
    p.feed(html)
    return p.anchors


def _header_html(html: str) -> str:
    m = re.search(r"<header\b[^>]*>.*?</header>", html, flags=re.I | re.S)
    if not m:
        raise AssertionError("Auto Fleet index.html has no <header>")
    return m.group(0)


def _by_id(anchors: list[dict[str, str]], aid: str) -> dict[str, str]:
    for a in anchors:
        if a["id"] == aid:
            return a
    raise AssertionError(f"missing anchor id={aid!r}")


class TestFleetHeader(unittest.TestCase):
    def test_fcc_bind_comes_from_endpoints_json(self) -> None:
        port, path = _fcc_bind()
        self.assertEqual(port, 8000)
        self.assertEqual(path, "/financial-command/index.html")

    def test_header_has_fcc_same_host_deep_link(self) -> None:
        html = (FLEET / "index.html").read_text(encoding="utf-8")
        header = _header_html(html)
        port, path = _fcc_bind()
        fcc = _by_id(_parse_anchors(header), "nav-fcc")
        self.assertEqual(fcc["text"], "FCC")
        self.assertNotIn(str(port), fcc["text"])
        self.assertNotRegex(fcc["title"], r"\d{4}")
        self.assertIn(f":{port}", fcc["href"])
        self.assertTrue(fcc["href"].endswith(path), fcc["href"])
        self.assertTrue(fcc["href"].startswith("http://"), fcc["href"])
        self.assertIn("nav-fcc.js", html)
        self.assertNotIn("<iframe", html.lower())
        for ip in HARDCODED_IPS:
            self.assertNotIn(ip, fcc["href"])
            self.assertNotIn(ip, header)
        for needle in PUBLIC_URL_NEEDLES:
            self.assertNotIn(needle, fcc["href"])
            self.assertNotIn(needle, header)
            self.assertNotIn(needle, html)

    def test_fcc_href_uses_page_host(self) -> None:
        js = (FLEET / "nav-fcc.js").read_text(encoding="utf-8")
        port, path = _fcc_bind()
        self.assertIn("location.hostname", js)
        self.assertIn(f"FCC_PORT = {port}", js)
        self.assertIn(f'FCC_PATH = "{path}"', js)
        self.assertIn("fleetFccHref", js)
        self.assertNotIn("<iframe", js.lower())
        self.assertNotIn("vercel", js.lower())
        self.assertNotIn("orchestra", js.lower())
        for ip in HARDCODED_IPS:
            self.assertNotIn(ip, js)
        for needle in PUBLIC_URL_NEEDLES:
            self.assertNotIn(needle, js)

        self.assertEqual(
            fcc_href("192.168.100.98"),
            f"http://192.168.100.98:{port}{path}",
        )
        self.assertEqual(fcc_href("100.67.114.2"), f"http://100.67.114.2:{port}{path}")
        self.assertEqual(fcc_href("prism-gateway"), f"http://prism-gateway:{port}{path}")
        self.assertEqual(fcc_href("127.0.0.1"), f"http://127.0.0.1:{port}{path}")
        self.assertEqual(fcc_href(""), f"http://127.0.0.1:{port}{path}")
        self.assertNotEqual(fcc_href("prism-gateway"), f"http://127.0.0.1:{port}{path}")

        self.assertRegex(
            js,
            r"""["']http://["']\s*\+\s*host\s*\+\s*["']:["']\s*\+\s*FCC_PORT\s*\+\s*FCC_PATH""",
        )

    def test_header_has_no_orchestra(self) -> None:
        html = (FLEET / "index.html").read_text(encoding="utf-8")
        header = _header_html(html)
        js = (FLEET / "nav-fcc.js").read_text(encoding="utf-8")
        for needle in ORCHESTRA_NEEDLES:
            self.assertNotIn(needle, header, f"header still has {needle!r}")
            self.assertNotIn(needle, html, f"index.html still has {needle!r}")
            self.assertNotIn(needle, js, f"nav-fcc.js still has {needle!r}")
        self.assertNotIn(":8790", header)
        self.assertNotIn(":8790", html)

    def test_refresh_remains(self) -> None:
        html = (FLEET / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btn-refresh"', html)
        self.assertIn("Auto Fleet", html)

    def test_header_visible_copy_has_no_port_number(self) -> None:
        header = _header_html((FLEET / "index.html").read_text(encoding="utf-8"))
        visible = re.sub(r"<[^>]+>", " ", header)
        visible = re.sub(r"\s+", " ", visible)
        self.assertNotRegex(visible, r"\bport\s+\d+")
        self.assertNotIn("8796", visible)
        self.assertNotIn("8000", visible)
        self.assertNotIn("8790", visible)


def _load_fleet_server():
    spec = importlib.util.spec_from_file_location("auto_fleet_server", FLEET / "server.py")
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


class TestFleetHeaderLive(unittest.TestCase):
    """Served index + nav-fcc.js keep the header contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fleet_server()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), cls.mod.AutoFleetHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _get(self, path: str) -> tuple[int, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_served_index_has_fcc_not_orchestra(self) -> None:
        code, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn(b'id="nav-fcc"', body)
        self.assertIn(b">FCC<", body)
        self.assertIn(b"nav-fcc.js", body)
        self.assertIn(b'id="btn-refresh"', body)
        header = _header_html(body.decode("utf-8"))
        for needle in ORCHESTRA_NEEDLES:
            self.assertNotIn(needle, header, f"served header still has {needle!r}")

    def test_nav_fcc_js_is_fleet_sibling(self) -> None:
        expected = (FLEET / "nav-fcc.js").read_bytes()
        code, body = self._get("/nav-fcc.js")
        self.assertEqual(code, 200)
        self.assertEqual(body, expected)


if __name__ == "__main__":
    unittest.main()
