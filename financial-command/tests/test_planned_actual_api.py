"""FCC planned-actual + send-book APIs are display-only and do not 404."""

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

FCC = ROOT / "financial-command"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location(
        "fcc_server_planned_actual", FCC / "server.py"
    )
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


class TestPlannedActualAndSendBookApi(unittest.TestCase):
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

    def test_planned_actual_api_display_only(self) -> None:
        code, body = self._get("/api/planned-actual")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("display_only"))
        self.assertFalse(data.get("coach_wired"))
        self.assertFalse(data.get("spectrum_trigger"))
        standing = data.get("standing_sends") or []
        self.assertEqual(len(standing), 3)
        kinds = {s.get("kind") for s in standing}
        self.assertEqual(kinds, {"thais", "rent", "jr_self_send"})
        by_kind = {s["kind"]: s for s in standing}
        self.assertEqual(by_kind["thais"]["amount"], 900.0)
        self.assertEqual(by_kind["jr_self_send"]["amount"], 35.0)

    def test_coinbase_sends_api_is_read_only_book(self) -> None:
        code, body = self._get("/api/coinbase_sends")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("display_only"))
        self.assertIn("transactions", data)
        self.assertIsInstance(data.get("transactions"), list)
        blob = json.dumps(data).lower()
        self.assertNotIn("cdp-api-key", blob)
        self.assertNotIn("api_secret", blob)

    def test_health_lists_feature(self) -> None:
        code, body = self._get("/api/fcc-identity")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        features = data.get("features") or []
        self.assertIn("planned_actual", features)
        self.assertIn("coinbase_sends", features)
        self.assertIn("coach", features)


if __name__ == "__main__":
    unittest.main()
