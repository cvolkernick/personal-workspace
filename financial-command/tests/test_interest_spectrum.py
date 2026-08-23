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
    JR_STRCUSX_ID,
    JR_TARGET_LABEL,
    JR_TARGET_PCT,
    LOCKED_FLEET,
    LOCKED_RATE_BY_ID,
    LOCKED_SEED_RATE_BY_ID,
    LOCKED_YIELD_SEEDS,
    SEED_TICKS_PCT,
    USDG_GOLD_CAVEAT,
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
    def test_morpho_hy_settings_paths_precede_live_books(self) -> None:
        spec = next(row for row in LOCKED_YIELD_SEEDS if row["id"] == "morpho_hy")
        self.assertAlmostEqual(spec["rate_pct"], 7.0)
        settings = list(spec["settings_paths"])
        live = list(spec["paths"])
        self.assertEqual(settings[0], ("config", "coinbase_manual", "vault_apy"))
        self.assertIn(("config", "coinbase_manual", "morpho_hy_apy_est"), settings)
        self.assertNotIn(("config", "coinbase_manual", "vault_apy"), live)
        self.assertIn(("evaluation", "inputs", "vault_apy"), live)
        self.assertIn("settings", spec["notes"])
        self.assertIn("seed", spec["notes"])

    def test_two_lane_axis_and_locked_seeds(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            solana={},
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

        for row in LOCKED_YIELD_SEEDS:
            chip = by_id[row["id"]]
            self.assertEqual(chip["kind"], "yield")
            self.assertEqual(chip["lane"], "below")
            self.assertAlmostEqual(chip["rate_pct"], row["rate_pct"])
            self.assertEqual(chip["source"], "locked_seed")
            self.assertTrue(chip["approx"])
            self.assertNotIn("principal_balance", chip)
            self.assertNotIn("account_balance", chip)

        self.assertAlmostEqual(by_id["x_money"]["rate_pct"], 6.0)
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 7.0)
        self.assertAlmostEqual(by_id["usdg_earn"]["rate_pct"], 7.0)
        self.assertIn("Gold", by_id["usdg_earn"]["notes"])
        self.assertIn("cancel", by_id["usdg_earn"]["notes"].lower())
        self.assertIn(USDG_GOLD_CAVEAT, by_id["usdg_earn"]["notes"])

        jr = by_id[JR_STRCUSX_ID]
        self.assertEqual(jr["kind"], "yield")
        self.assertEqual(jr["lane"], "below")
        self.assertAlmostEqual(jr["rate_pct"], JR_TARGET_PCT)
        self.assertEqual(jr["rate_label"], JR_TARGET_LABEL)
        self.assertEqual(jr["source"], "docs_target")
        self.assertTrue(jr["approx"])
        self.assertFalse(jr.get("counts_toward_hy"))
        self.assertFalse(jr.get("counts_toward_ltv_defense"))
        self.assertNotIn("notional", jr)
        self.assertIn("docs.solstice.finance", jr["notes"])
        self.assertIn("solstice.finance/vaults/strcusx", jr["notes"])
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
        self.assertEqual(by_id["usdg_earn"]["source"], "books")
        self.assertIn("Gold", by_id["usdg_earn"]["notes"])
        self.assertIn("cancel", by_id["usdg_earn"]["notes"].lower())
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
            solana={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertEqual(by_id["x_money"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["x_money"]["rate_pct"], 6.0)
        self.assertEqual(by_id["morpho_hy"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 7.0)
        self.assertEqual(by_id["usdg_earn"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["usdg_earn"]["rate_pct"], 7.0)
        for chip in payload["chips"]:
            self.assertIn(chip["kind"], ("debt", "yield"))
            self.assertIn(chip["rate_kind"], ("APR", "APY"))
            self.assertIn(chip["source"], ("locked_financing", "locked_seed", "books", "docs_target"))
        rates = [c["rate_pct"] for c in payload["placed"]]
        self.assertNotIn(25.0, rates)
        self.assertNotIn(12.0, rates)
        self.assertNotIn(8.0, rates)
        self.assertNotIn(11.0, rates)
        self.assertTrue(rates_are_honest(payload))

    def test_jr_target_when_no_live_solstice_does_not_invent_balance(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "snapshot": {
                    "solana": {
                        "jr_strcusx": 3.317128,
                        "jr_strcusx_usd": 3.40,
                        "jr_strcusx_usd_price": 1.0255,
                    }
                }
            },
            config={},
            x_money={},
            solana={},
        )
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], 20.0)
        self.assertEqual(jr["rate_label"], "~20% target")
        self.assertEqual(jr["source"], "docs_target")
        self.assertTrue(jr["approx"])
        self.assertFalse(jr.get("counts_toward_hy"))
        self.assertFalse(jr.get("counts_toward_ltv_defense"))
        self.assertNotIn("notional", jr)
        self.assertIn("docs.solstice.finance", jr["notes"])
        self.assertIn("solstice.finance/vaults/strcusx", jr["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_jr_uses_live_solstice_when_already_on_books(self) -> None:
        payload = build_interest_spectrum(
            treasury={"snapshot": {"solana": {"solstice_apy": 0.184, "jr_strcusx": 3.3}}},
            config={},
            x_money={},
        )
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], 18.4)
        self.assertEqual(jr["source"], "books")
        self.assertFalse(jr["approx"])
        self.assertNotIn("rate_label", jr)
        self.assertNotIn("notional", jr)
        self.assertFalse(jr.get("counts_toward_hy"))
        self.assertTrue(rates_are_honest(payload))

    def test_usdg_seed_always_shows_with_gold_caveat(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"robinhood": {}},
            x_money={},
            solana={},
        )
        usdg = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(usdg["rate_pct"], 7.0)
        self.assertEqual(usdg["source"], "locked_seed")
        self.assertIn("Gold", usdg["notes"])
        self.assertIn("cancel", usdg["notes"].lower())
        self.assertIn("do not invent a post-Gold rate", usdg["notes"])

    def test_morpho_hy_settings_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"coinbase_manual": {"vault_apy": 0.055}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 5.5)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("config.coinbase_manual.vault_apy", chip["notes"])
        self.assertIn("settings", chip["notes"].lower())
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_live_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"vault_apy": 0.0291}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 2.91)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("evaluation.inputs.vault_apy", chip["notes"])
        self.assertIn("live", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_settings_beats_live_when_set(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"vault_apy": 0.0291, "hy_vault_apy": 0.031}},
                "snapshot": {
                    "morpho_hy": {"apy_est": 0.028},
                    "coinbase_manual": {"vault_apy": 0.0291},
                },
            },
            config={"coinbase_manual": {"vault_apy": 0.08}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 8.0)
        self.assertEqual(chip["source"], "books")
        self.assertIn("config.coinbase_manual.vault_apy", chip["notes"])
        self.assertNotIn("evaluation.inputs.vault_apy", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_dedicated_settings_key_beats_live(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"vault_apy": 0.029}}},
            config={"coinbase_manual": {"morpho_hy_apy_est": 0.061}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 6.1)
        self.assertIn("config.coinbase_manual.morpho_hy_apy_est", chip["notes"])

    def test_morpho_hy_blank_settings_does_not_beat_live(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"vault_apy": 0.0291}}},
            config={"coinbase_manual": {"vault_apy": None, "morpho_hy_apy_est": ""}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 2.91)
        self.assertIn("evaluation.inputs.vault_apy", chip["notes"])

    def test_morpho_hy_seed_always_visible_when_no_books(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 7.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertTrue(chip["approx"])
        self.assertIn("seed", chip["notes"].lower())
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})
        self.assertTrue(rates_are_honest(payload))

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
        self.assertIn("Locked yield seeds always show", html)
        self.assertIn("FCC settings manual", html)
        self.assertIn("seed 7%", html)
        self.assertNotIn("Yield venues appear only when", html)
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
        self.assertIn('id="panel-solana"', html)
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
        self.assertIn(JR_STRCUSX_ID, ids)
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
