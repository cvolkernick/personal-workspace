#!/usr/bin/env python3
"""Auto Fleet header: FCC same-host deep-link; Turo opens turo.com; no Orchestra."""

from __future__ import annotations

import importlib.util
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

FLEET = Path(__file__).resolve().parents[1]
FCC_SERVER = ROOT / "financial-command" / "server.py"
FCC_PORT = 8000
FCC_PATH = "/financial-command/index.html"
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
    """Existing FCC bind — financial-command/server.py --port default. Do not invent."""
    src = FCC_SERVER.read_text(encoding="utf-8")
    m = re.search(r'add_argument\("--port".*default=(\d+)', src)
    if not m:
        raise AssertionError("financial-command/server.py has no --port default")
    port = int(m.group(1))
    if port != FCC_PORT:
        raise AssertionError(f"FCC --port default drifted: {port} != {FCC_PORT}")
    return port, FCC_PATH


def fcc_href(
    hostname: str | None = None,
    *,
    pathname: str = "/",
    protocol: str = "http:",
    loc_hostname: str = "127.0.0.1",
) -> str:
    """Same contract as auto-fleet/nav-fcc.js fleetFccHref."""
    port, path = _fcc_bind()
    if hostname:
        return f"http://{hostname}:{port}{path}"
    host = loc_hostname or "127.0.0.1"
    on_lens = pathname == "/fleet" or pathname.startswith("/fleet/")
    on_ts = host.lower().endswith(".ts.net")
    if on_lens or protocol == "https:" or on_ts:
        if protocol == "https:" or on_ts:
            return f"https://{host}{path}"
        return path
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
            "target": ad.get("target", ""),
            "rel": ad.get("rel", ""),
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

    def test_fcc_href_lan_uses_page_host(self) -> None:
        js = (FLEET / "nav-fcc.js").read_text(encoding="utf-8")
        port, path = _fcc_bind()
        self.assertIn("loc.hostname", js)
        self.assertIn("loc.pathname", js)
        self.assertIn("loc.protocol", js)
        self.assertIn("global.location", js)
        self.assertIn(f"FCC_PORT = {port}", js)
        self.assertIn(f'FCC_PATH = "{path}"', js)
        self.assertIn("fleetFccHref", js)
        self.assertIn(".ts.net", js)
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
        self.assertEqual(
            fcc_href(pathname="/", loc_hostname="192.168.100.98"),
            f"http://192.168.100.98:{port}{path}",
        )
        self.assertNotEqual(
            fcc_href("prism-gateway"), f"http://127.0.0.1:{port}{path}"
        )

    def test_fcc_href_same_origin_lens_and_tailnet_https(self) -> None:
        port, path = _fcc_bind()
        del port
        self.assertEqual(
            fcc_href(pathname="/fleet/", loc_hostname="127.0.0.1"),
            path,
        )
        self.assertEqual(
            fcc_href(pathname="/fleet", loc_hostname="192.168.100.98"),
            path,
        )
        self.assertEqual(
            fcc_href(
                pathname="/fleet/",
                protocol="https:",
                loc_hostname="prism-gateway.tailb1085a.ts.net",
            ),
            "https://prism-gateway.tailb1085a.ts.net" + path,
        )
        self.assertEqual(
            fcc_href(
                pathname="/",
                protocol="http:",
                loc_hostname="prism-gateway.tailb1085a.ts.net",
            ),
            "https://prism-gateway.tailb1085a.ts.net" + path,
        )
        js = (FLEET / "nav-fcc.js").read_text(encoding="utf-8")
        self.assertIn('path.indexOf("/fleet/")', js)
        self.assertRegex(js, r"protocol === [\"']https:[\"']")

    def test_nav_fcc_js_avoids_proxy_rewrite_triggers(self) -> None:
        """FCC's /fleet/ proxy rewrites href="/…" and fetch("/…") (#680)."""
        js = (FLEET / "nav-fcc.js").read_bytes()
        self.assertNotRegex(js, rb'(?:src|href|action)\s*=\s*["\']/')
        self.assertNotRegex(js, rb'fetch\(\s*["\']/')
        self.assertIn(b'FCC_PATH = "/financial-command/index.html"', js)

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

    def test_header_has_turo_open_link(self) -> None:
        html = (FLEET / "index.html").read_text(encoding="utf-8")
        header = _header_html(html)
        turo = _by_id(_parse_anchors(header), "nav-turo")
        self.assertEqual(turo["text"], "Turo")
        self.assertEqual(turo["href"], "https://turo.com")
        self.assertEqual(turo["title"], "Open Turo")
        self.assertEqual(turo["target"], "_blank")
        self.assertIn("noopener", turo["rel"].split())
        self.assertIn("noreferrer", turo["rel"].split())
        self.assertNotIn("/drivers/", turo["href"])
        self.assertNotIn("/vehicle/", turo["href"])
        fcc_at = header.find('id="nav-fcc"')
        refresh_at = header.find('id="btn-refresh"')
        turo_at = header.find('id="nav-turo"')
        self.assertGreater(fcc_at, 0)
        self.assertGreater(refresh_at, fcc_at)
        self.assertGreater(turo_at, refresh_at)

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
        self.assertIn(b'id="nav-turo"', body)
        self.assertIn(b'href="https://turo.com"', body)
        self.assertIn(b">Turo<", body)
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
