"""FCC planned vs YNAB strip is display-only; coach / Spectrum stay unchanged."""

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
INDEX = FCC / "index.html"
SPECTRUM = FCC / "interest-spectrum.html"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_planned_actual", FCC / "server.py")
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


class TestPlannedActualPage(unittest.TestCase):
    def test_strip_is_display_only_and_not_over_painted(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('id="planned-actual-card"', html)
        self.assertIn('id="planned-actual-strip"', html)
        self.assertIn("/api/planned-actual", html)
        self.assertIn("display only", html.lower())
        self.assertIn("flag-two-charge", html)
        self.assertIn("flag-cadence-lump", html)
        self.assertIn("flag-off-book", html)
        self.assertIn("flag-payment-shaped", html)
        self.assertIn("two-charge, cadence-lump, and payment-shaped", html)
        self.assertIn("Essential (legacy Personal), Fleet, and Collateral", html)
        self.assertIn("self-send", html)
        self.assertIn("nvolkern@gmail.com", html)
        self.assertIn("2026-09-11", html)
        self.assertIn("type=send", html)
        self.assertNotIn("overspend", html.lower())
        # two-charge / cadence-lump must not use the red due style
        css = html.split("flag-two-charge")[1].split("flag-off-book")[0]
        self.assertNotIn("var(--red)", css)
        self.assertNotIn("#ff6b6b", css)
        self.assertNotIn("due-red", css)
        self.assertNotIn("vercel", html.lower())
        visible = html
        # no new port copy in the strip card
        card = html.split('id="planned-actual-card"')[1].split('id="actions-card"')[0]
        self.assertNotIn(":8000", card)
        self.assertNotIn(":8795", card)
        self.assertNotIn(":8796", card)

    def test_spectrum_page_coach_copy_unchanged(self) -> None:
        html = SPECTRUM.read_text(encoding="utf-8")
        self.assertIn('id="coach-nudge"', html)
        self.assertIn("display-only FCF nudge", html)
        self.assertNotIn("planned-actual", html)
        self.assertNotIn("cadence-lump", html)


class TestPlannedActualApi(unittest.TestCase):
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
        for row in data.get("rows") or []:
            self.assertIn(
                row.get("flag"),
                ("on", "not-yet", "two-charge", "cadence-lump", "off-book From", "payment-shaped"),
            )
            self.assertNotIn(row.get("flag"), ("over", "under", "overspend"))
        standing = data.get("standing_sends") or []
        self.assertEqual(len(standing), 3)
        kinds = {s.get("kind") for s in standing}
        self.assertEqual(kinds, {"thais", "rent", "jr_self_send"})

    def test_health_lists_feature(self) -> None:
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("planned_actual", data.get("features") or [])
        self.assertIn("interest_spectrum", data.get("features") or [])
        self.assertIn("coach", data.get("features") or [])

    def test_spectrum_coach_wired_unchanged(self) -> None:
        code, body = self._get("/api/interest-spectrum")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data.get("coach_wired"), (data.get("policy") or {}).get("coach_wired"))
        self.assertNotIn("planned_actual", data)
        self.assertNotIn("cadence-lump", json.dumps(data))

    def test_coach_api_has_no_flag_strip_fields(self) -> None:
        code, body = self._get("/api/coach")
        self.assertIn(code, (200, 500))
        data = json.loads(body.decode("utf-8"))
        blob = json.dumps(data).lower()
        self.assertNotIn("cadence-lump", blob)
        self.assertNotIn("two-charge", blob)
        self.assertNotIn("off-book from", blob)
        self.assertNotIn("planned_actual", blob)


if __name__ == "__main__":
    unittest.main()
