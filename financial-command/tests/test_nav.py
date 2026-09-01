"""FCC nav: Fleet + Horizon are same-host deep-links; Orchestra is gone."""

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

from research.horizon.server import DEFAULT_PORT as HORIZON_PORT  # noqa: E402

FCC = ROOT / "financial-command"
SURFACES = (
    "index.html",
    "capital-flows.html",
    "watchlist.html",
    "interest-spectrum.html",
    "bias-spectrum.html",
)
HARDCODED_IPS = ("192.168.100.98", "100.67.114.2")
PUBLIC_URL_NEEDLES = (
    "vercel.app",
    "vercel.com",
    "horizon.vercel",
    "https://horizon",
    "strategy/horizon.md",
)
ORCHESTRA_NEEDLES = (
    "nav-orchestra",
    "data-open-orchestra",
    "open-orchestra",
    "← Orchestrator",
    "nav-orchestra.js",
    "openOrchestrator",
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
            "target": ad.get("target", ""),
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


def horizon_href(hostname: str) -> str:
    """Same contract as financial-command/nav-horizon.js fccHorizonHref.

    Port is research/horizon/server.py DEFAULT_PORT — not invented here.
    """
    host = hostname or "127.0.0.1"
    return f"http://{host}:{HORIZON_PORT}/"


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
        spectrum = _parse_anchors((FCC / "interest-spectrum.html").read_text(encoding="utf-8"))

        cb = _by_id(index, "nav-coinbase")
        self.assertEqual(cb["text"], "Coinbase")
        self.assertEqual(cb["href"], "https://www.coinbase.com/home")
        self.assertEqual(cb.get("target") or "", "_blank")

        rh = _by_id(index, "nav-robinhood")
        self.assertEqual(rh["text"], "Robinhood")
        self.assertEqual(rh["href"], "https://robinhood.com/")
        self.assertEqual(rh.get("target") or "", "_blank")

        expenses = _by_id(index, "nav-expenses")
        self.assertEqual(expenses["text"], "Expenses")
        self.assertEqual(
            expenses["href"],
            "https://docs.google.com/spreadsheets/d/15ZU7843pTSLSEI0U-taFZ4Qwk3bTQx6cWh2Ex0d7NJQ/edit",
        )
        self.assertEqual(expenses.get("target") or "", "_blank")

        index_html = (FCC / "index.html").read_text(encoding="utf-8")
        h1 = re.search(r"<h1\b.*?</h1>", index_html, re.S)
        self.assertIsNotNone(h1)
        self.assertNotIn("nav-coinbase", h1.group(0))
        self.assertNotIn("nav-robinhood", h1.group(0))
        self.assertNotIn("nav-expenses", h1.group(0))
        self.assertIn('class="broker-links"', index_html)
        cb_at = index_html.find('id="nav-coinbase"')
        rh_at = index_html.find('id="nav-robinhood"')
        exp_at = index_html.find('id="nav-expenses"')
        h1_at = index_html.find("<h1")
        self.assertGreater(h1_at, 0)
        self.assertLess(cb_at, h1_at)
        self.assertLess(rh_at, h1_at)
        self.assertLess(exp_at, h1_at)
        self.assertLess(cb_at, rh_at)
        self.assertLess(rh_at, exp_at)

        self.assertIn('k: "Agentic NAV"', index_html)
        self.assertIn('k: "Morpho LTV"', index_html)
        self.assertIn('k: "CB ONE CARD"', index_html)
        self.assertIn('k: "Liquid USDC"', index_html)
        self.assertIn('k: "Vault USDC"', index_html)
        self.assertIn("kpi-pair", index_html)
        self.assertNotIn('k: "NAV"', index_html)
        self.assertNotIn('k: "LTV"', index_html)
        self.assertNotIn('k: "Card"', index_html)
        self.assertNotIn('k: "USDC"', index_html)

        cf = _by_id(index, "nav-capital-flows")
        self.assertEqual(cf["text"], "Capital Flows")
        self.assertEqual(cf["href"], "capital-flows.html")

        wl = _by_id(index, "nav-watchlist")
        self.assertEqual(wl["text"], "Watchlist")
        self.assertEqual(wl["href"], "watchlist.html")

        spec = _by_id(index, "nav-interest-spectrum")
        self.assertEqual(spec["text"], "Interest Spectrum")
        self.assertEqual(spec["href"], "interest-spectrum.html")

        bias = _by_id(index, "nav-bias-spectrum")
        self.assertEqual(bias["text"], "Bias Spectrum")
        self.assertEqual(bias["href"], "bias-spectrum.html")

        fcc_from_flows = _by_id(flows, "nav-fcc")
        self.assertIn("FCC", fcc_from_flows["text"])
        self.assertEqual(fcc_from_flows["href"], "index.html")
        self.assertEqual(_by_id(flows, "nav-watchlist")["href"], "watchlist.html")
        self.assertEqual(_by_id(flows, "nav-watchlist")["text"], "Watchlist")
        self.assertEqual(_by_id(flows, "nav-interest-spectrum")["href"], "interest-spectrum.html")
        self.assertEqual(_by_id(flows, "nav-bias-spectrum")["href"], "bias-spectrum.html")

        fcc_from_watch = _by_id(watch, "nav-fcc")
        self.assertIn("FCC", fcc_from_watch["text"])
        self.assertEqual(fcc_from_watch["href"], "index.html")
        cf_from_watch = _by_id(watch, "nav-capital-flows")
        self.assertEqual(cf_from_watch["href"], "capital-flows.html")
        self.assertEqual(cf_from_watch["text"], "Capital Flows")
        self.assertEqual(_by_id(watch, "nav-interest-spectrum")["href"], "interest-spectrum.html")
        self.assertEqual(_by_id(watch, "nav-bias-spectrum")["href"], "bias-spectrum.html")

        fcc_from_spec = _by_id(spectrum, "nav-fcc")
        self.assertIn("FCC", fcc_from_spec["text"])
        self.assertEqual(fcc_from_spec["href"], "index.html")
        self.assertEqual(_by_id(spectrum, "nav-capital-flows")["href"], "capital-flows.html")
        self.assertEqual(_by_id(spectrum, "nav-watchlist")["href"], "watchlist.html")
        self.assertEqual(_by_id(spectrum, "nav-bias-spectrum")["href"], "bias-spectrum.html")
        bias_page = _parse_anchors((FCC / "bias-spectrum.html").read_text(encoding="utf-8"))
        self.assertIn("FCC", _by_id(bias_page, "nav-fcc")["text"])
        self.assertEqual(_by_id(bias_page, "nav-fcc")["href"], "index.html")
        self.assertEqual(_by_id(bias_page, "nav-interest-spectrum")["href"], "interest-spectrum.html")

        for name in SURFACES:
            html = (FCC / name).read_text(encoding="utf-8")
            for needle in ORCHESTRA_NEEDLES:
                self.assertNotIn(needle, html, f"{name} still has {needle!r}")

        more_spec = _by_id(index, "link-interest-spectrum-full")
        self.assertEqual(more_spec["href"], "interest-spectrum.html")
        self.assertIn("Open", more_spec["text"])
        self.assertIn('id="interest-spectrum-card"', index_html)
        self.assertIn('id="link-interest-spectrum-full"', index_html)
        # More-tab card must live on the More panel, not a Glance/Cash tab.
        card_idx = index_html.find('id="interest-spectrum-card"')
        self.assertGreater(card_idx, 0)
        self.assertIn('data-m-panel="more"', index_html[card_idx : card_idx + 120])

        more_bias = _by_id(index, "link-bias-spectrum-full")
        self.assertEqual(more_bias["href"], "bias-spectrum.html")
        self.assertIn("Open", more_bias["text"])
        self.assertIn('id="bias-spectrum-card"', index_html)
        bias_card_idx = index_html.find('id="bias-spectrum-card"')
        self.assertGreater(bias_card_idx, 0)
        self.assertIn('data-m-panel="more"', index_html[bias_card_idx : bias_card_idx + 120])

        # Mobile header hides #nav-fleet; the visible path is the top chip + More card.
        fleet_chip = _by_id(index, "link-fleet-chip")
        self.assertEqual(fleet_chip["text"], "Fleet")
        self.assertIn(":8796", fleet_chip["href"])
        self.assertTrue(fleet_chip["href"].startswith("http://"))
        self.assertEqual(fleet_chip.get("target") or "", "")
        chip_at = index_html.find('id="link-fleet-chip"')
        self.assertGreater(chip_at, 0)
        self.assertLess(chip_at, h1_at)
        self.assertIn("data-nav-fleet", index_html[chip_at : chip_at + 180])
        self.assertGreater(chip_at, exp_at)

        more_fleet = _by_id(index, "link-fleet-full")
        self.assertIn("Open", more_fleet["text"])
        self.assertIn(":8796", more_fleet["href"])
        self.assertIn('id="fleet-card"', index_html)
        fleet_card_idx = index_html.find('id="fleet-card"')
        self.assertGreater(fleet_card_idx, 0)
        self.assertIn('data-m-panel="more"', index_html[fleet_card_idx : fleet_card_idx + 120])
        full_at = index_html.find('id="link-fleet-full"')
        self.assertIn("data-nav-fleet", index_html[full_at : full_at + 180])

        footer_fleet = _by_id(index, "link-fleet-footer")
        self.assertEqual(footer_fleet["text"], "Fleet")
        self.assertIn(":8796", footer_fleet["href"])

        spec_html = (FCC / "interest-spectrum.html").read_text(encoding="utf-8")
        # Mobile cull hides satellite siblings, never the FCC back-link.
        self.assertIn("#nav-horizon", spec_html)
        self.assertIn("#nav-watchlist", spec_html)
        self.assertRegex(
            spec_html,
            r"#nav-horizon,\s*#nav-capital-flows,\s*#nav-watchlist,\s*#nav-bias-spectrum,\s*#nav-fleet",
        )
        self.assertNotRegex(
            spec_html,
            r"#nav-fcc\s*[,\{]",
        )
        footer_fcc = [a for a in spectrum if a["href"] == "index.html"]
        self.assertGreaterEqual(len(footer_fcc), 2, "header + footer FCC back-links")

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

    def test_live_entry_fleet_href_is_page_host_not_loopback(self) -> None:
        """Live AC: FCC / Fleet href after JS is same-host :8796.

        Not 127.0.0.1 unless the page host is localhost / 127.0.0.1.
        Phone / LAN / Tailscale entry uses window.location.hostname.
        """
        self.assertEqual(fleet_href("prism-gateway"), "http://prism-gateway:8796/")
        self.assertEqual(fleet_href("192.168.100.98"), "http://192.168.100.98:8796/")
        self.assertEqual(fleet_href("100.67.114.2"), "http://100.67.114.2:8796/")
        self.assertNotEqual(fleet_href("prism-gateway"), "http://127.0.0.1:8796/")
        self.assertEqual(fleet_href("127.0.0.1"), "http://127.0.0.1:8796/")
        self.assertEqual(fleet_href("localhost"), "http://localhost:8796/")


class TestFccNavHorizon(unittest.TestCase):
    def test_horizon_bind_comes_from_research_horizon_server(self) -> None:
        src = (ROOT / "research" / "horizon" / "server.py").read_text(encoding="utf-8")
        self.assertRegex(src, r"(?m)^DEFAULT_PORT\s*=\s*8795\s*$")
        self.assertEqual(HORIZON_PORT, 8795)
        self.assertNotEqual(HORIZON_PORT, 8791)  # seasonal plan, not Horizon Macro

    def test_nav_contains_horizon_link_on_every_fcc_surface(self) -> None:
        port_token = f":{HORIZON_PORT}"
        for name in SURFACES:
            html = (FCC / name).read_text(encoding="utf-8")
            anchors = _parse_anchors(html)
            hz = _by_id(anchors, "nav-horizon")
            self.assertEqual(hz["text"], "Horizon", name)
            self.assertNotIn(str(HORIZON_PORT), hz["text"], name)
            self.assertNotRegex(hz["title"], r"\d{4}", name)
            self.assertIn(port_token, hz["href"], name)
            self.assertTrue(
                hz["href"].startswith("http://"),
                f"{name} Horizon href must be an origin, not a relative FCC path",
            )
            self.assertNotIn("<iframe", html.lower(), name)
            self.assertIn("nav-horizon.js", html, name)
            self.assertNotIn("nav-orchestra.js", html, name)
            for ip in HARDCODED_IPS:
                self.assertNotIn(ip, hz["href"], name)
                self.assertNotIn(ip, html, name)
            for needle in PUBLIC_URL_NEEDLES:
                self.assertNotIn(needle, hz["href"], name)
                self.assertNotIn(needle, html, name)

    def test_horizon_href_uses_current_hostname_and_server_port(self) -> None:
        js = (FCC / "nav-horizon.js").read_text(encoding="utf-8")
        self.assertIn("location.hostname", js)
        self.assertIn(f"HORIZON_PORT = {HORIZON_PORT}", js)
        self.assertIn("fccHorizonHref", js)
        self.assertNotIn("<iframe", js.lower())
        self.assertNotIn("vercel", js.lower())
        for ip in HARDCODED_IPS:
            self.assertNotIn(ip, js)
        for needle in PUBLIC_URL_NEEDLES:
            self.assertNotIn(needle, js)

        self.assertEqual(horizon_href("192.168.100.98"), f"http://192.168.100.98:{HORIZON_PORT}/")
        self.assertEqual(horizon_href("100.67.114.2"), f"http://100.67.114.2:{HORIZON_PORT}/")
        self.assertEqual(horizon_href("prism-gateway"), f"http://prism-gateway:{HORIZON_PORT}/")
        self.assertEqual(horizon_href("127.0.0.1"), f"http://127.0.0.1:{HORIZON_PORT}/")
        self.assertEqual(horizon_href(""), f"http://127.0.0.1:{HORIZON_PORT}/")
        self.assertNotEqual(horizon_href("prism-gateway"), "http://127.0.0.1:8795/")

        self.assertRegex(
            js,
            r"""["']http://["']\s*\+\s*host\s*\+\s*["']:["']\s*\+\s*HORIZON_PORT\s*\+\s*["']/["']""",
        )

    def test_orchestra_controls_are_gone_from_fcc(self) -> None:
        self.assertFalse((FCC / "nav-orchestra.js").is_file())
        server = (FCC / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("/api/open-orchestra", server)
        self.assertNotIn("ensure_orchestra", server)
        self.assertNotIn("ORCHESTRA_PORT", server)
        for name in SURFACES:
            html = (FCC / name).read_text(encoding="utf-8")
            for needle in ORCHESTRA_NEEDLES:
                self.assertNotIn(needle, html, f"{name} still has {needle!r}")

    def test_auto_fleet_nav_still_present(self) -> None:
        for name in SURFACES:
            html = (FCC / name).read_text(encoding="utf-8")
            fleet = _by_id(_parse_anchors(html), "nav-fleet")
            self.assertEqual(fleet["text"], "Fleet", name)
            self.assertIn("nav-fleet.js", html, name)

    def test_mobile_cull_still_hides_header_fleet_not_chip(self) -> None:
        """≤720px hides #nav-fleet; top chip / More card stay visible."""
        index_html = (FCC / "index.html").read_text(encoding="utf-8")
        self.assertRegex(
            index_html,
            r"#nav-horizon,\s*#nav-capital-flows,\s*#nav-interest-spectrum,\s*#nav-bias-spectrum,\s*#nav-fleet",
        )
        self.assertNotRegex(index_html, r"#link-fleet-chip\s*[,\{]")
        self.assertNotRegex(index_html, r"#link-fleet-full\s*[,\{]")
        self.assertNotRegex(index_html, r"#fleet-card\s*[,\{]")
        self.assertIn("Watchlist | Refresh", index_html)


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


class TestRootNavJsRemap(unittest.TestCase):
    """GET /nav-*.js must serve financial-command siblings (favicon-style remap)."""

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
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_root_nav_fleet_js_is_fcc_sibling(self) -> None:
        expected = (FCC / "nav-fleet.js").read_bytes()
        code, body = self._get("/nav-fleet.js")
        self.assertEqual(code, 200)
        self.assertEqual(body, expected)
        prefixed, prefixed_body = self._get("/financial-command/nav-fleet.js")
        self.assertEqual(prefixed, 200)
        self.assertEqual(prefixed_body, expected)

    def test_root_nav_horizon_js_is_fcc_sibling(self) -> None:
        expected = (FCC / "nav-horizon.js").read_bytes()
        code, body = self._get("/nav-horizon.js")
        self.assertEqual(code, 200)
        self.assertEqual(body, expected)
        prefixed, prefixed_body = self._get("/financial-command/nav-horizon.js")
        self.assertEqual(prefixed, 200)
        self.assertEqual(prefixed_body, expected)

    def test_root_nav_orchestra_js_is_gone(self) -> None:
        code, _ = self._get("/nav-orchestra.js")
        self.assertEqual(code, 404)
        prefixed, _ = self._get("/financial-command/nav-orchestra.js")
        self.assertEqual(prefixed, 404)

    def test_root_missing_js_stays_404(self) -> None:
        code, _ = self._get("/no-such-nav.js")
        self.assertEqual(code, 404)

    def test_entry_and_prefixed_html_still_work(self) -> None:
        root_code, root_body = self._get("/")
        self.assertEqual(root_code, 200)
        self.assertIn(b'id="nav-fleet"', root_body)
        self.assertIn(b"nav-fleet.js", root_body)
        self.assertIn(b'id="nav-horizon"', root_body)
        self.assertIn(b"nav-horizon.js", root_body)
        self.assertNotIn(b"nav-orchestra", root_body)
        self.assertNotIn(b"Orchestrator", root_body)
        html_code, html_body = self._get("/financial-command/index.html")
        self.assertEqual(html_code, 200)
        self.assertIn(b'id="nav-fleet"', html_body)
        self.assertIn(b'id="nav-horizon"', html_body)

    def test_open_orchestra_api_is_gone(self) -> None:
        get_code, _ = self._get("/api/open-orchestra")
        self.assertIn(get_code, (404, 405))
        url = f"http://127.0.0.1:{self.port}/api/open-orchestra"
        req = urllib.request.Request(url, data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                post_code = resp.status
        except urllib.error.HTTPError as exc:
            post_code = exc.code
        self.assertEqual(post_code, 404)

    def test_remap_helper_only_fcc_js_basenames(self) -> None:
        remap = self.mod._root_fcc_js_remap
        self.assertEqual(remap("/nav-fleet.js"), "/financial-command/nav-fleet.js")
        self.assertEqual(remap("/nav-horizon.js"), "/financial-command/nav-horizon.js")
        self.assertIsNone(remap("/nav-orchestra.js"))
        self.assertIsNone(remap("/financial-command/nav-fleet.js"))
        self.assertIsNone(remap("/no-such-nav.js"))
        self.assertIsNone(remap("/../nav-fleet.js"))
        self.assertIsNone(remap("/"))
        self.assertIsNone(remap("/interest-spectrum.html"))

    def test_remap_helper_origin_html_siblings(self) -> None:
        remap = self.mod._root_fcc_file_remap
        self.assertEqual(remap("/interest-spectrum.html"), "/financial-command/interest-spectrum.html")
        self.assertEqual(remap("/bias-spectrum.html"), "/financial-command/bias-spectrum.html")
        self.assertEqual(remap("/watchlist.html"), "/financial-command/watchlist.html")
        self.assertEqual(remap("/capital-flows.html"), "/financial-command/capital-flows.html")
        self.assertEqual(remap("/bias-spectrum"), "/financial-command/bias-spectrum.html")
        self.assertEqual(remap("/interest-spectrum"), "/financial-command/interest-spectrum.html")
        self.assertIsNone(remap("/financial-command/bias-spectrum.html"))
        self.assertIsNone(remap("/../bias-spectrum.html"))
        self.assertIsNone(remap("/treasury"))
        self.assertIsNone(remap("/no-such-page.html"))


if __name__ == "__main__":
    unittest.main()
