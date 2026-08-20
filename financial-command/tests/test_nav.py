"""FCC nav: Fleet is a same-host deep-link, not an embed."""

from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"
SURFACES = ("index.html", "capital-flows.html", "watchlist.html")
HARDCODED_IPS = ("192.168.100.98", "100.67.114.2")


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


def fleet_href(hostname: str) -> str:
    """Same contract as financial-command/nav-fleet.js fccFleetHref."""
    host = hostname or "127.0.0.1"
    return f"http://{host}:8796/"


class TestFccNavFleet(unittest.TestCase):
    def test_nav_contains_fleet_link_on_every_fcc_surface(self) -> None:
        for name in SURFACES:
            html = (FCC / name).read_text(encoding="utf-8")
            anchors = _parse_anchors(html)
            fleet = _by_id(anchors, "nav-fleet")
            self.assertEqual(fleet["text"], "Fleet", name)
            self.assertIn(":8796", fleet["href"], name)
            self.assertTrue(
                fleet["href"].startswith("http://"),
                f"{name} Fleet href must be an origin, not a relative FCC path",
            )
            self.assertNotIn("<iframe", html.lower(), name)
            self.assertIn("nav-fleet.js", html, name)
            for ip in HARDCODED_IPS:
                self.assertNotIn(ip, fleet["href"], name)
                self.assertNotIn(ip, html, name)

    def test_fleet_href_uses_current_hostname_port_8796(self) -> None:
        js = (FCC / "nav-fleet.js").read_text(encoding="utf-8")
        self.assertIn("location.hostname", js)
        self.assertIn("FLEET_PORT = 8796", js)
        self.assertIn("fccFleetHref", js)
        self.assertNotIn("<iframe", js.lower())
        for ip in HARDCODED_IPS:
            self.assertNotIn(ip, js)

        self.assertEqual(fleet_href("192.168.100.98"), "http://192.168.100.98:8796/")
        self.assertEqual(fleet_href("100.67.114.2"), "http://100.67.114.2:8796/")
        self.assertEqual(fleet_href("prism-gateway"), "http://prism-gateway:8796/")
        self.assertEqual(fleet_href("127.0.0.1"), "http://127.0.0.1:8796/")
        self.assertEqual(fleet_href(""), "http://127.0.0.1:8796/")

        # JS builder must concatenate hostname + :8796 (not a baked-in IP).
        self.assertRegex(
            js,
            r"""["']http://["']\s*\+\s*host\s*\+\s*["']:["']\s*\+\s*FLEET_PORT\s*\+\s*["']/["']""",
        )

    def test_existing_fcc_capital_flows_watchlist_still_work(self) -> None:
        index = _parse_anchors((FCC / "index.html").read_text(encoding="utf-8"))
        flows = _parse_anchors((FCC / "capital-flows.html").read_text(encoding="utf-8"))
        watch = _parse_anchors((FCC / "watchlist.html").read_text(encoding="utf-8"))

        cf = _by_id(index, "nav-capital-flows")
        self.assertEqual(cf["text"], "Capital Flows")
        self.assertEqual(cf["href"], "capital-flows.html")

        wl = _by_id(index, "nav-watchlist")
        self.assertEqual(wl["text"], "Watchlist")
        self.assertEqual(wl["href"], "watchlist.html")

        fcc_from_flows = _by_id(flows, "nav-fcc")
        self.assertIn("FCC", fcc_from_flows["text"])
        self.assertEqual(fcc_from_flows["href"], "index.html")
        self.assertEqual(_by_id(flows, "nav-watchlist")["href"], "watchlist.html")
        self.assertEqual(_by_id(flows, "nav-watchlist")["text"], "Watchlist")

        fcc_from_watch = _by_id(watch, "nav-fcc")
        self.assertIn("FCC", fcc_from_watch["text"])
        self.assertEqual(fcc_from_watch["href"], "index.html")
        cf_from_watch = _by_id(watch, "nav-capital-flows")
        self.assertEqual(cf_from_watch["href"], "capital-flows.html")
        self.assertEqual(cf_from_watch["text"], "Capital Flows")

        index_html = (FCC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="nav-orchestra"', index_html)
        self.assertIn("← Orchestrator", index_html)

    def test_orchestra_and_horizon_have_no_fleet_nav(self) -> None:
        orch = (ROOT / "orchestra" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('id: "fleet"', orch)
        self.assertNotIn('short: "Fleet"', orch)
        self.assertNotIn(":8796", orch)

        horizon = (ROOT / "research" / "horizon" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn(":8796", horizon)
        self.assertNotIn('id="nav-fleet"', horizon)

        fitdash = (ROOT / "resistance-dashboard" / "static" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(":8796", fitdash)
        self.assertNotIn('id="nav-fleet"', fitdash)


if __name__ == "__main__":
    unittest.main()
