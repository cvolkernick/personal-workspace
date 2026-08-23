"""Interest Spectrum: no invented rates, APR/APY only, viewable FCC page."""

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
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.interest_spectrum import (  # noqa: E402
    LOCKED_FLEET,
    LOCKED_RATE_BY_ID,
    build_interest_spectrum,
    rates_are_honest,
)

FCC = ROOT / "financial-command"
PAGE = FCC / "interest-spectrum.html"
STUB = FCC / "interest-spectrum.json"

# Rates that must never appear unless they come from locked/books fields.
INVENTED = (29.0, 29.99, 7.0, 0.07, 5.0)
EQUITY_BTC_NEEDLES = (
    "expected-return axis",
    "assumed-return axis",
    "equity return axis",
    "btc return axis",
    "appreciation axis",
)


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


class TestInterestSpectrumBuilder(unittest.TestCase):
    def test_locked_fleet_chips_without_inventing_book_yields(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            stub={"coach_threshold_pct": None},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["title"], "Interest Spectrum")
        self.assertEqual(payload["brand"], "FCC")
        self.assertTrue(payload["policy"]["apr_apy_only"])
        self.assertFalse(payload["policy"]["equity_btc_assumed_return"])
        self.assertFalse(payload["policy"]["invented_rates"])
        self.assertFalse(payload["policy"]["wells_is_fcc_liability"])
        self.assertIsNone(payload["coach_threshold_pct"])
        self.assertFalse(payload["coach_threshold_locked"])
        self.assertTrue(rates_are_honest(payload))

        by_id = {c["id"]: c for c in payload["chips"]}
        for row in LOCKED_FLEET:
            chip = by_id[row["id"]]
            self.assertEqual(chip["kind"], "debt")
            self.assertEqual(chip["rate_kind"], "APR")
            self.assertAlmostEqual(chip["rate_pct"], row["rate_pct"])
            self.assertEqual(chip["source"], "locked_financing")
            self.assertNotIn("principal_balance", chip)
            self.assertNotIn("account_balance", chip)

        wells = by_id["m3-2020"]
        self.assertFalse(wells["fcc_liability"])
        self.assertEqual(wells["role"], "metadata")
        self.assertIn("not a FCC liability", wells["notes"])

        rivian = by_id["r1s-2023"]
        self.assertAlmostEqual(rivian["rate_pct"], 0.0)
        self.assertEqual(rivian["monthly_payment"], 1350)

        unknown = {c["id"]: c for c in payload["unknown"]}
        self.assertIn("morpho_borrow", unknown)
        self.assertIn("morpho_hy", unknown)
        self.assertIn("x_money", unknown)
        self.assertIn("usdg_earn", unknown)
        for chip in unknown.values():
            self.assertIsNone(chip["rate_pct"])
            self.assertEqual(chip["source"], "unknown")
            self.assertIn("unknown", (chip.get("notes") or "").lower())

        placed_ids = {c["id"] for c in payload["placed"]}
        self.assertEqual(placed_ids, set(LOCKED_RATE_BY_ID))

    def test_books_apr_used_only_when_present(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"variable_apr": 0.0487}},
                "snapshot": {"x_money": {"apy_est": 0.04}},
            },
            config={"robinhood": {"usdg_earn_apy_est": 0.032}},
            x_money={},
            stub={"coach_threshold_pct": None},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertAlmostEqual(by_id["morpho_borrow"]["rate_pct"], 4.87)
        self.assertEqual(by_id["morpho_borrow"]["kind"], "debt")
        self.assertEqual(by_id["morpho_borrow"]["source"], "books")
        self.assertAlmostEqual(by_id["x_money"]["rate_pct"], 4.0)
        self.assertEqual(by_id["x_money"]["kind"], "yield")
        self.assertAlmostEqual(by_id["usdg_earn"]["rate_pct"], 3.2)
        self.assertIsNone(by_id["morpho_hy"]["rate_pct"])
        self.assertTrue(rates_are_honest(payload))

    def test_no_invent_ignores_equity_btc_assumed_returns(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {
                    "inputs": {
                        "btc_expected_return": 0.25,
                        "equity_expected_return": 0.10,
                        "assumed_return": 0.12,
                        "appreciation_pct": 8,
                    }
                },
                "snapshot": {
                    "robinhood": {"expected_return": 0.11},
                    "coinbase_manual": {"btc_assumed_return": 0.3},
                },
            },
            config={"interest_spectrum": {"coach_threshold_pct": None}},
            x_money={"apy_est": None},
            stub={"coach_threshold_pct": None},
        )
        for chip in payload["chips"]:
            if chip["source"] != "locked_financing":
                self.assertIsNone(chip["rate_pct"], chip)
            self.assertIn(chip["kind"], ("debt", "yield"))
            self.assertIn(chip["rate_kind"], ("APR", "APY"))
        rates = [c["rate_pct"] for c in payload["placed"]]
        for banned in INVENTED:
            self.assertNotIn(banned, rates)
        self.assertNotIn(25.0, rates)
        self.assertNotIn(10.0, rates)
        self.assertNotIn(12.0, rates)
        self.assertNotIn(8.0, rates)
        self.assertTrue(rates_are_honest(payload))

    def test_usdg_settings_placeholder_is_not_a_default(self) -> None:
        """index.html pre-fills 0.07 in the form — spectrum must not adopt that."""
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"robinhood": {}},
            x_money={},
            stub={},
        )
        usdg = next(c for c in payload["chips"] if c["id"] == "usdg_earn")
        self.assertIsNone(usdg["rate_pct"])
        self.assertNotAlmostEqual(usdg.get("rate_pct") or -1, 7.0)

    def test_coach_threshold_stays_blank_without_lock(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            stub={"coach_threshold_pct": None, "notes": "blank"},
        )
        self.assertIsNone(payload["coach_threshold_pct"])
        locked = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            stub={"coach_threshold_pct": 4.25},
        )
        self.assertAlmostEqual(locked["coach_threshold_pct"], 4.25)
        self.assertTrue(locked["coach_threshold_locked"])


class TestInterestSpectrumPage(unittest.TestCase):
    def test_page_is_viewable_spectrum_not_spreadsheet(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Interest Spectrum</h1>", html)
        self.assertIn("Interest Spectrum · FCC", html)
        self.assertIn("APR / APY · FCC", html)
        self.assertIn('id="spectrum"', html)
        self.assertIn("COST OF DEBT (APR)", html)
        self.assertIn("YIELD (APY)", html)
        self.assertIn("/api/interest-spectrum", html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn('id="nav-capital-flows"', html)
        self.assertIn('id="nav-watchlist"', html)
        self.assertIn('id="nav-fleet"', html)
        self.assertIn("rate unknown", html)
        self.assertIn("Coach threshold X", html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("CIC", html)
        self.assertNotIn("vercel", html.lower())
        lower = html.lower()
        for needle in EQUITY_BTC_NEEDLES:
            self.assertNotIn(needle, lower)
        # Page name / copy: no instructional port numbers (Fleet href is JS-rewritten).
        visible = re.sub(r"<script[\s\S]*?</script>", "", html)
        visible = re.sub(r'href="[^"]+"', "", visible)
        self.assertNotIn(":8000", visible)
        self.assertNotIn(":8796", visible)
        self.assertNotIn("port 8000", visible.lower())
        self.assertNotIn("port 8796", visible.lower())

    def test_stub_file_is_blank_threshold(self) -> None:
        stub = json.loads(STUB.read_text(encoding="utf-8"))
        self.assertIsNone(stub.get("coach_threshold_pct"))
        self.assertEqual(stub.get("title"), "Interest Spectrum")
        self.assertEqual(stub.get("brand"), "FCC")


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
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_api_and_pretty_url(self) -> None:
        code, body = self._get("/api/interest-spectrum")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("title"), "Interest Spectrum")
        self.assertTrue(rates_are_honest(data))
        ids = {c["id"] for c in data.get("chips") or []}
        self.assertTrue(set(LOCKED_RATE_BY_ID).issubset(ids))
        for chip in data.get("chips") or []:
            self.assertIn(chip.get("kind"), ("debt", "yield"))
            if chip.get("rate_pct") is not None:
                self.assertIn(chip.get("rate_kind"), ("APR", "APY"))

        page_code, page_body = self._get("/financial-command/interest-spectrum")
        self.assertEqual(page_code, 200)
        self.assertIn(b"<h1>Interest Spectrum</h1>", page_body)
        slash_code, slash_body = self._get("/financial-command/interest-spectrum/")
        self.assertEqual(slash_code, 200)
        self.assertIn(b"Interest Spectrum", slash_body)

    def test_health_lists_feature(self) -> None:
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("interest_spectrum", data.get("features") or [])


if __name__ == "__main__":
    unittest.main()
