"""Interest Spectrum: two-lane APR/APY axis, no invent, no coach wiring."""

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
    LOCKED_SEED_RATE_BY_ID,
    SEED_TICKS_PCT,
    WELLS_OFF_FCC_ID,
    build_interest_spectrum,
    rates_are_honest,
)

FCC = ROOT / "financial-command"
PAGE = FCC / "interest-spectrum.html"
STUB = FCC / "interest-spectrum.json"
INDEX = FCC / "index.html"

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
    def test_two_lane_axis_and_locked_seeds(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            stub={"coach_threshold_pct": 5},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["title"], "Interest Spectrum")
        self.assertEqual(payload["brand"], "FCC")
        self.assertEqual(payload["axis"]["layout"], "two_lane")
        self.assertEqual(payload["axis"]["debt_lane"], "above")
        self.assertEqual(payload["axis"]["yield_lane"], "below")
        self.assertEqual(payload["axis"]["left"], "0%")
        self.assertGreaterEqual(payload["axis"]["max_pct"], 29)
        self.assertEqual(payload["axis"]["ticks"], list(SEED_TICKS_PCT))
        self.assertTrue(payload["policy"]["apr_apy_only"])
        self.assertFalse(payload["policy"]["equity_btc_assumed_return"])
        self.assertFalse(payload["policy"]["invented_rates"])
        self.assertFalse(payload["policy"]["wells_is_fcc_liability"])
        self.assertFalse(payload["policy"]["wells_on_fcc_spectrum"])
        self.assertFalse(payload["policy"]["chip_size_is_notional"])
        self.assertFalse(payload["coach_wired"])
        self.assertFalse(payload["policy"]["coach_wired"])
        self.assertTrue(rates_are_honest(payload))

        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertNotIn(WELLS_OFF_FCC_ID, by_id)
        for row in LOCKED_FLEET:
            chip = by_id[row["id"]]
            self.assertEqual(chip["kind"], "debt")
            self.assertEqual(chip["lane"], "above")
            self.assertAlmostEqual(chip["rate_pct"], row["rate_pct"])
            self.assertEqual(chip["source"], "locked_financing")
            self.assertNotIn("principal_balance", chip)
            self.assertNotIn("account_balance", chip)

        self.assertAlmostEqual(by_id["morpho_borrow"]["rate_pct"], 5.0)
        self.assertTrue(by_id["morpho_borrow"]["approx"])
        self.assertEqual(by_id["morpho_borrow"]["source"], "locked_seed")
        self.assertEqual(by_id["morpho_borrow"]["deep_link"], "index.html#morpho")

        self.assertAlmostEqual(by_id["one_card"]["rate_pct"], 29.0)
        self.assertTrue(by_id["one_card"]["approx"])
        self.assertEqual(by_id["one_card"]["deep_link"], "index.html#one-card")

        self.assertAlmostEqual(by_id["r1s-2023"]["rate_pct"], 0.0)
        self.assertEqual(by_id["r1s-2023"]["venue"], "Rivian")
        self.assertEqual(by_id["r1s-2023"]["monthly_payment"], 1350)
        self.assertEqual(by_id["r1s-2023"]["deep_link"], "fleet")

        self.assertNotIn("x_money", by_id)
        self.assertNotIn("morpho_hy", by_id)
        self.assertNotIn("usdg_earn", by_id)
        self.assertEqual(payload["unknown"], [])

    def test_books_apr_overrides_morpho_seed_and_plots_yield(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {
                    "inputs": {
                        "variable_apr": 0.0487,
                        "vault_apy": 0.036,
                        "card_balance": 462.2,
                        "loan_principal_usdc": 1200,
                    }
                },
                "snapshot": {"x_money": {"apy_est": 0.04, "cash": 178.14}},
            },
            config={"robinhood": {"usdg_earn_apy_est": 0.032}},
            x_money={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertAlmostEqual(by_id["morpho_borrow"]["rate_pct"], 4.87)
        self.assertEqual(by_id["morpho_borrow"]["source"], "books")
        self.assertFalse(by_id["morpho_borrow"]["approx"])
        self.assertAlmostEqual(by_id["morpho_borrow"]["notional"], 1200)
        self.assertAlmostEqual(by_id["x_money"]["rate_pct"], 4.0)
        self.assertEqual(by_id["x_money"]["kind"], "yield")
        self.assertEqual(by_id["x_money"]["lane"], "below")
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 3.6)
        self.assertAlmostEqual(by_id["usdg_earn"]["rate_pct"], 3.2)
        self.assertAlmostEqual(by_id["one_card"]["notional"], 462.2)
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
            x_money={"apy_est": None},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertNotIn("x_money", by_id)
        self.assertNotIn("morpho_hy", by_id)
        for chip in payload["chips"]:
            self.assertIn(chip["kind"], ("debt", "yield"))
            self.assertIn(chip["rate_kind"], ("APR", "APY"))
            self.assertIn(chip["source"], ("locked_financing", "locked_seed", "books"))
        rates = [c["rate_pct"] for c in payload["placed"]]
        self.assertNotIn(25.0, rates)
        self.assertNotIn(12.0, rates)
        self.assertNotIn(8.0, rates)
        self.assertNotIn(7.0, rates)
        self.assertTrue(rates_are_honest(payload))

    def test_usdg_settings_placeholder_is_not_a_default(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"robinhood": {}},
            x_money={},
        )
        ids = {c["id"] for c in payload["chips"]}
        self.assertNotIn("usdg_earn", ids)

    def test_coach_is_not_wired_even_if_stub_has_a_number(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={"interest_spectrum": {"coach_threshold_pct": 5}},
            x_money={},
            stub={"coach_threshold_pct": 4.25},
        )
        self.assertFalse(payload["coach_wired"])
        self.assertNotIn("coach_threshold_pct", payload)
        self.assertNotIn("coach_threshold_locked", payload)


class TestInterestSpectrumPage(unittest.TestCase):
    def test_page_is_two_lane_spectrum(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Interest Spectrum</h1>", html)
        self.assertIn("Interest Spectrum · FCC", html)
        self.assertIn("APR / APY · FCC", html)
        self.assertIn('data-layout="two_lane"', html)
        self.assertIn("DEBT COST", html)
        self.assertIn("ASSET YIELD", html)
        self.assertIn("0% → ~30%", html)
        self.assertIn("/api/interest-spectrum", html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn("width: 7.4rem", html)
        self.assertNotIn("Coach threshold", html)
        self.assertNotIn("coach X", html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("CIC", html)
        self.assertNotIn("vercel", html.lower())
        self.assertNotIn("buy token", html.lower())
        self.assertNotIn("place order", html.lower())
        lower = html.lower()
        for needle in EQUITY_BTC_NEEDLES:
            self.assertNotIn(needle, lower)
        visible = re.sub(r"<script[\s\S]*?</script>", "", html)
        visible = re.sub(r'href="[^"]+"', "", visible)
        self.assertNotIn(":8000", visible)
        self.assertNotIn(":8796", visible)

    def test_stub_file_is_unwired_coach(self) -> None:
        stub = json.loads(STUB.read_text(encoding="utf-8"))
        self.assertIsNone(stub.get("coach_threshold_pct"))
        self.assertFalse(stub.get("coach_wired"))
        self.assertEqual(stub.get("title"), "Interest Spectrum")

    def test_fcc_index_has_spectrum_deep_link_targets(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('id="one-card"', html)
        self.assertIn('id="morpho"', html)
        self.assertIn('id="hy"', html)
        self.assertIn('id="x-money"', html)
        self.assertIn("openFccDeepLink", html)
        self.assertIn("interest-spectrum.html", html)


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
        self.assertEqual(data.get("axis", {}).get("layout"), "two_lane")
        self.assertFalse(data.get("coach_wired"))
        self.assertTrue(rates_are_honest(data))
        ids = {c["id"] for c in data.get("chips") or []}
        self.assertTrue(set(LOCKED_RATE_BY_ID).issubset(ids))
        self.assertTrue(set(LOCKED_SEED_RATE_BY_ID).issubset(ids))
        self.assertNotIn(WELLS_OFF_FCC_ID, ids)
        for chip in data.get("chips") or []:
            self.assertIn(chip.get("kind"), ("debt", "yield"))
            self.assertIn(chip.get("lane"), ("above", "below"))

        page_code, page_body = self._get("/financial-command/interest-spectrum")
        self.assertEqual(page_code, 200)
        self.assertIn(b"<h1>Interest Spectrum</h1>", page_body)
        self.assertIn(b"data-layout=\"two_lane\"", page_body)

    def test_health_lists_feature(self) -> None:
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("interest_spectrum", data.get("features") or [])


if __name__ == "__main__":
    unittest.main()
