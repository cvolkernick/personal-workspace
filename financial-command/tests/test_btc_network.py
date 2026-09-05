"""FCC Bitcoin tab: network hashrate + difficulty charts."""

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
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FCC = ROOT / "financial-command"
INDEX = FCC / "index.html"
FIXTURE = ROOT / "treasury" / "tests" / "fixtures" / "btc_network_mempool.json"

from treasury.btc_network_sync import normalize_network  # noqa: E402


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_btc_network", FCC / "server.py")
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


def _normalized_fixture() -> dict:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return normalize_network(raw["hashrate"], raw["adjustment"], as_of="2026-09-05T20:00:00+00:00")


class TestBtcNetworkPage(unittest.TestCase):
    def test_bitcoin_tab_has_both_charts(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('id="btc-network-card"', html)
        self.assertIn('data-m-panel="mining"', html)
        self.assertIn(">Bitcoin</span>", html)
        self.assertIn('id="btc-hr-chart"', html)
        self.assertIn('id="btc-diff-chart"', html)
        self.assertIn("/api/btc-network", html)
        self.assertIn("function renderBtcNetwork", html)
        self.assertIn("function btcLineChartSvg", html)
        self.assertIn("bitcoin: \"btc-network-card\"", html)
        # ASIC fleet stays on the same tab
        self.assertIn('id="braiins-card"', html)


class TestBtcNetworkApi(unittest.TestCase):
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
        self.assertIn("btc_network", data.get("features") or [])
        self.assertIn("braiins", data.get("features") or [])

    def test_btc_network_api_serves_normalized_series(self) -> None:
        payload = _normalized_fixture()
        with mock.patch.object(self.mod, "_btc_network_live", return_value=payload):
            code, body = self._get("/api/btc-network")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertGreaterEqual(len(data.get("hashrate") or []), 4)
        self.assertGreaterEqual(len(data.get("difficulty") or []), 3)
        self.assertIn("current_hashrate", data)
        self.assertIn("current_difficulty", data)
        self.assertEqual(data.get("source"), "mempool.space")
