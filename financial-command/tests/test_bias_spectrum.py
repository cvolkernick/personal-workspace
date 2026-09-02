"""Bias Spectrum page + API: new-money consider-share, not book weight."""

from __future__ import annotations

import importlib.util
import json
import socket
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

from treasury.bias_spectrum import build_bias_spectrum  # noqa: E402

FCC = ROOT / "financial-command"
PAGE = FCC / "bias-spectrum.html"
INDEX = FCC / "index.html"


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


class TestBiasSpectrumPage(unittest.TestCase):
    def test_page_is_two_lane_new_money_spectrum(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Bias Spectrum</h1>", html)
        self.assertIn("Bias Spectrum · FCC", html)
        self.assertIn("New-money consider-share · FCC", html)
        self.assertIn('data-layout="two_lane"', html)
        self.assertIn("BTC / DIGITAL CREDIT", html)
        self.assertIn("STOCKS / GROWTH", html)
        self.assertIn("new-money consider-share", html)
        self.assertIn("not current book weight", html)
        self.assertIn("/api/bias-spectrum", html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn('id="nav-fleet"', html)
        self.assertIn("nav-fleet.js", html)
        self.assertIn("high=3", html)
        self.assertIn("Not an order ticket", html)
        self.assertIn("not live NAV or sleeve targets", html)
        self.assertIn("preferred-core", html)
        self.assertIn("position.html?symbol=", html)
        self.assertNotIn("HELD BOOK", html)
        self.assertNotIn("WATCHLIST CONSIDER", html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("place order", html.lower())
        self.assertNotIn("mint JR", html)
        self.assertNotIn("CIC", html)
        self.assertNotIn("vercel", html.lower())
        self.assertIn('chip.lane === "above"', html)
        self.assertIn("toFixed(2)", html)
        self.assertNotIn("Math.abs(p.x - x) < 86", html)

    def test_fcc_index_has_bias_deep_link(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("bias-spectrum.html", html)
        self.assertIn('id="nav-bias-spectrum"', html)
        self.assertIn('id="bias-spectrum-card"', html)
        self.assertIn('id="link-bias-spectrum-full"', html)
        self.assertIn("not current book weight", html)


class TestBiasSpectrumApi(unittest.TestCase):
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

    def test_api_and_pretty_url(self) -> None:
        code, body = self._get("/api/bias-spectrum")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("axis", {}).get("layout"), "two_lane")
        self.assertEqual(data.get("axis", {}).get("btc_lane"), "above")
        self.assertEqual(data.get("axis", {}).get("stocks_lane"), "below")
        self.assertEqual(data.get("axis", {}).get("unit"), "new_money_consider_share_pct")
        pol = data.get("policy") or {}
        self.assertFalse(pol.get("invented_targets"))
        self.assertTrue(pol.get("axis_is_new_money_consider_share"))
        self.assertFalse(pol.get("held_is_book_weight"))
        self.assertTrue(pol.get("consider_share_stamps_are_not_nav_targets"))
        self.assertTrue(pol.get("consider_share_stamps_are_not_sleeve_targets"))
        self.assertTrue(pol.get("consider_share_stamps_are_not_orders"))
        for chip in data.get("chips") or []:
            self.assertIn(chip.get("kind"), ("held", "consider"))
            if chip.get("sleeve") == "btc_digital_credit":
                self.assertEqual(chip.get("lane"), "above")
            elif chip.get("sleeve") == "stocks_growth":
                self.assertEqual(chip.get("lane"), "below")
            if chip.get("held"):
                self.assertEqual(chip.get("kind"), "held")
            else:
                self.assertEqual(chip.get("kind"), "consider")
            self.assertIn(
                chip.get("weight_basis"),
                ("new_money_consider_share", "consider_share_stamp"),
            )
            self.assertNotIn("target_pct", chip)
            self.assertNotIn("target_weight", chip)
            if chip.get("consider_share_stamp"):
                self.assertEqual(chip.get("weight_basis"), "consider_share_stamp")
                self.assertIn(chip.get("symbol"), ("TSLA", "SPCX"))
                self.assertAlmostEqual(float(chip["weight_pct"]), 15.0)
                self.assertIn("NOT a live NAV", chip.get("notes") or "")

        page_code, page_body = self._get("/financial-command/bias-spectrum")
        self.assertEqual(page_code, 200)
        self.assertIn(b"<h1>Bias Spectrum</h1>", page_body)
        self.assertIn(b'data-layout="two_lane"', page_body)
        self.assertIn(b"new-money consider-share", page_body)

        origin_pretty, origin_body = self._get("/bias-spectrum")
        self.assertEqual(origin_pretty, 200)
        self.assertEqual(origin_body, page_body)
        origin_html, origin_html_body = self._get("/bias-spectrum.html")
        self.assertEqual(origin_html, 200)
        self.assertEqual(origin_html_body, page_body)

        symbols = {c.get("symbol") for c in data.get("chips") or []}
        if "STRC" in symbols:
            strc = next(c for c in data["chips"] if c["symbol"] == "STRC")
            self.assertEqual(strc.get("role"), "preferred_core")
            self.assertEqual(strc.get("sleeve"), "btc_digital_credit")
            self.assertEqual(strc.get("lane"), "above")

    def test_health_lists_feature(self) -> None:
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("bias_spectrum", data.get("features") or [])
        self.assertIn("interest_spectrum", data.get("features") or [])

    def test_builder_matches_api_shape(self) -> None:
        payload = build_bias_spectrum(fund_manager={"ok": False}, treasury={})
        self.assertEqual(payload["title"], "Bias Spectrum")
        self.assertEqual(payload["axis"]["layout"], "two_lane")
        self.assertEqual(payload["axis"]["unit"], "new_money_consider_share_pct")


if __name__ == "__main__":
    unittest.main()
