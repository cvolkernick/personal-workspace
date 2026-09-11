"""Position dossier page + API."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FCC = ROOT / "financial-command"
PAGE = FCC / "position.html"
BIAS = FCC / "bias-spectrum.html"
WATCH = FCC / "watchlist.html"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_position", FCC / "server.py")
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


class TestPositionDossierPage(unittest.TestCase):
    def test_page_is_ticker_dossier(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("Current stance", html)
        self.assertIn("Where it fits", html)
        self.assertIn("Deep dive", html)
        self.assertIn("Related knowledge", html)
        self.assertIn("/api/position?symbol=", html)
        self.assertIn("symbolFromLocation", html)
        self.assertIn('id="nav-bias-spectrum"', html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn("pwa.js", html)
        self.assertIn('rel="manifest"', html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("place order", html.lower())
        self.assertNotIn("CIC", html)

    def test_bias_chips_link_to_dossier(self) -> None:
        html = BIAS.read_text(encoding="utf-8")
        self.assertIn("position.html?symbol=", html)
        self.assertIn("position dossier", html.lower())
        self.assertNotIn('chip.deep_link || "watchlist.html"', html)

    def test_watchlist_hash_redirects_to_dossier(self) -> None:
        html = WATCH.read_text(encoding="utf-8")
        self.assertIn("redirectHashToDossier", html)
        self.assertIn("position.html?symbol=", html)
        self.assertIn("Open dossier", html)


class TestPositionDossierApi(unittest.TestCase):
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

    def test_health_lists_feature(self) -> None:
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("position_dossier", data.get("features") or [])

    def test_invalid_symbol_400(self) -> None:
        code, body = self._get("/api/position?symbol=" + urllib.parse.quote("../x"))
        self.assertEqual(code, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data.get("ok"))

    def test_api_and_pretty_url(self) -> None:
        code, body = self._get("/api/position?symbol=STRC")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("symbol"), "STRC")
        self.assertIn("stance", data)
        self.assertIn("deep_dive", data)
        self.assertIn("larger_picture", data)
        self.assertFalse(data["larger_picture"].get("auto_buy"))
        if data.get("in_consider_set"):
            self.assertEqual(data["stance"].get("role"), "preferred_core")
            self.assertEqual(data["stance"].get("sleeve"), "btc_digital_credit")

        page_code, page_body = self._get("/financial-command/position?symbol=STRC")
        self.assertEqual(page_code, 200)
        self.assertIn(b"Current stance", page_body)
        self.assertIn(b'id="dossier"', page_body)

        origin_pretty, origin_body = self._get("/position?symbol=STRC")
        self.assertEqual(origin_pretty, 200)
        self.assertEqual(origin_body, page_body)

        nested_code, nested_body = self._get("/financial-command/position/STRC")
        self.assertEqual(nested_code, 200)
        self.assertEqual(nested_body, page_body)

        origin_nested, origin_nested_body = self._get("/position/STRC")
        self.assertEqual(origin_nested, 200)
        self.assertEqual(origin_nested_body, page_body)

        bias_code, bias_body = self._get("/api/bias-spectrum")
        self.assertEqual(bias_code, 200)
        bias = json.loads(bias_body.decode("utf-8"))
        chips = {c.get("symbol"): c for c in bias.get("chips") or []}
        if "STRC" in chips:
            self.assertEqual(chips["STRC"].get("deep_link"), "position.html?symbol=STRC")


if __name__ == "__main__":
    unittest.main()
