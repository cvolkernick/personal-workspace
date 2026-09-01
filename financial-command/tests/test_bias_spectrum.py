"""Bias Spectrum page + API: two-lane relative weights, no invented targets."""

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
    def test_page_is_two_lane_weight_spectrum(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Bias Spectrum</h1>", html)
        self.assertIn("Bias Spectrum · FCC", html)
        self.assertIn("Relative weight · FCC", html)
        self.assertIn('data-layout="two_lane"', html)
        self.assertIn("HELD BOOK", html)
        self.assertIn("WATCHLIST CONSIDER", html)
        self.assertIn("Held book (above)", html)
        self.assertIn("Watchlist consider (below)", html)
        self.assertIn("/api/bias-spectrum", html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn('id="nav-fleet"', html)
        self.assertIn("nav-fleet.js", html)
        self.assertIn("high=3", html)
        self.assertIn("not capital", html)
        self.assertIn("No invented per-name targets", html)
        self.assertIn("legend only", html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("place order", html.lower())
        self.assertNotIn("mint JR", html)
        self.assertNotIn("CIC", html)
        self.assertNotIn("vercel", html.lower())
        self.assertIn("chip.kind === \"held\"", html)

    def test_fcc_index_has_bias_deep_link(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("bias-spectrum.html", html)
        self.assertIn('id="nav-bias-spectrum"', html)
        self.assertIn('id="bias-spectrum-card"', html)
        self.assertIn('id="link-bias-spectrum-full"', html)


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
        self.assertEqual(data.get("axis", {}).get("held_lane"), "above")
        self.assertEqual(data.get("axis", {}).get("consider_lane"), "below")
        self.assertEqual(data.get("axis", {}).get("unit"), "relative_weight_pct")
        self.assertFalse((data.get("policy") or {}).get("invented_targets"))
        for chip in data.get("chips") or []:
            self.assertIn(chip.get("kind"), ("held", "consider"))
            if chip.get("kind") == "held":
                self.assertEqual(chip.get("lane"), "above")
            else:
                self.assertEqual(chip.get("lane"), "below")

        page_code, page_body = self._get("/financial-command/bias-spectrum")
        self.assertEqual(page_code, 200)
        self.assertIn(b"<h1>Bias Spectrum</h1>", page_body)
        self.assertIn(b'data-layout="two_lane"', page_body)

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


if __name__ == "__main__":
    unittest.main()
