"""Restore Interest Spectrum + Coinbase/RH/Fleet links on work/treasury FCC."""

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

from treasury.interest_spectrum import build_interest_spectrum  # noqa: E402

FCC = ROOT / "financial-command"
INDEX = FCC / "index.html"
SURFACES = (
    "index.html",
    "capital-flows.html",
    "cash-streams.html",
    "runway.html",
    "watchlist.html",
    "interest-spectrum.html",
)


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


def _by_id(anchors: list[dict[str, str]], aid: str) -> dict[str, str]:
    for a in anchors:
        if a["id"] == aid:
            return a
    raise AssertionError(f"missing anchor id={aid!r}")


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server", FCC / "server.py")
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


class TestBrokerAndSpectrumNav(unittest.TestCase):
    def test_index_broker_pills(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        anchors = _parse_anchors(html)
        cb = _by_id(anchors, "nav-coinbase")
        rh = _by_id(anchors, "nav-robinhood")
        exp = _by_id(anchors, "nav-expenses")
        fleet = _by_id(anchors, "link-fleet-chip")
        self.assertEqual(cb["text"], "Coinbase")
        self.assertIn("coinbase.com", cb["href"])
        self.assertEqual(rh["text"], "Robinhood")
        self.assertIn("robinhood.com", rh["href"])
        self.assertEqual(exp["text"], "Expenses")
        self.assertIn("docs.google.com/spreadsheets", exp["href"])
        self.assertEqual(fleet["text"], "Fleet")
        self.assertEqual(fleet["href"], "/fleet/")
        self.assertIn('data-nav-fleet', html)
        self.assertIn("nav-fleet.js", html)
        self.assertIn("broker-links", html)

    def test_interest_spectrum_nav_on_fcc_surfaces(self) -> None:
        for name in SURFACES:
            html = (FCC / name).read_text(encoding="utf-8")
            self.assertIn("nav-fleet.js", html, name)
            self.assertIn('id="nav-fleet"', html, name)
            if name == "interest-spectrum.html":
                self.assertIn("<h1>Interest Spectrum</h1>", html)
                continue
            self.assertIn("interest-spectrum.html", html, name)
            self.assertIn('id="nav-interest-spectrum"', html, name)

    def test_more_panel_has_spectrum_and_fleet(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('id="interest-spectrum-card"', html)
        self.assertIn('id="fleet-card"', html)
        self.assertIn("APR / APY", html)


class TestInterestSpectrumApi(unittest.TestCase):
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

    def _get(self, path: str) -> tuple[int, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_health_lists_interest_spectrum(self) -> None:
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("interest_spectrum", data.get("features") or [])

    def test_api_and_page(self) -> None:
        payload = build_interest_spectrum()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("title"), "Interest Spectrum")
        self.assertFalse((payload.get("policy") or {}).get("invented_rates"))

        code, body = self._get("/api/interest-spectrum")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("axis", {}).get("layout"), "two_lane")
        self.assertGreaterEqual(len(data.get("chips") or []), 1)

        page_code, page_body = self._get("/financial-command/interest-spectrum")
        self.assertEqual(page_code, 200)
        self.assertIn(b"<h1>Interest Spectrum</h1>", page_body)

        html_code, _html = self._get("/financial-command/interest-spectrum.html")
        self.assertEqual(html_code, 200)


if __name__ == "__main__":
    unittest.main()
