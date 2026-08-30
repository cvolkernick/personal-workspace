"""Interest Spectrum: two-lane APR/APY axis, no invent, FCF nudge after essentials."""

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
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.interest_spectrum import (  # noqa: E402
    AGENTIC_FUND_ID,
    BITCOIN_ID,
    EST_CAGR_IDS,
    EST_CAGR_KIND,
    EST_CAGR_LABEL,
    EST_CAGR_NOTE,
    JR_STRCUSX_ID,
    JR_TARGET_LABEL,
    JR_TARGET_PCT,
    LOCKED_FLEET,
    LOCKED_RATE_BY_ID,
    LOCKED_SEED_RATE_BY_ID,
    LOCKED_SEEDS,
    LOCKED_YIELD_SEEDS,
    MORPHO_HY_VAULT_NE_PRODUCT_NOTE,
    MORPHO_HY_VAULT_REF_PATHS,
    RATE_PRECEDENCE,
    RH_MARGIN_BOOK_PATHS,
    RH_MARGIN_ID,
    SEED_TICKS_PCT,
    USDG_GOLD_CAVEAT,
    WELLS_OFF_FCC_ID,
    build_fcf_coach,
    build_interest_spectrum,
    household_overdue_count,
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
    def test_morpho_hy_settings_paths_precede_product_apy(self) -> None:
        spec = next(row for row in LOCKED_YIELD_SEEDS if row["id"] == "morpho_hy")
        self.assertAlmostEqual(spec["rate_pct"], 7.0)
        settings = list(spec["settings_paths"])
        live = list(spec["paths"])
        vault_ref = list(spec.get("vault_ref_paths") or MORPHO_HY_VAULT_REF_PATHS)
        self.assertEqual(settings[0], ("config", "coinbase_manual", "product_apy"))
        self.assertIn(("config", "coinbase_manual", "vault_apy"), settings)
        self.assertIn(("config", "coinbase_manual", "morpho_hy_apy_est"), settings)
        self.assertNotIn(("config", "coinbase_manual", "vault_apy"), live)
        self.assertNotIn(("snapshot", "coinbase_manual", "product_apy"), live)
        self.assertNotIn(("evaluation", "inputs", "vault_apy"), live)
        self.assertNotIn(("evaluation", "inputs", "hy_vault_apy"), live)
        self.assertNotIn(("snapshot", "morpho_hy", "apy_est"), live)
        self.assertNotIn(("snapshot", "morpho_hy", "apy"), live)
        self.assertNotIn(("snapshot", "morpho_hy", "vault_apy"), live)
        self.assertIn(("evaluation", "inputs", "product_apy"), live)
        self.assertIn(("evaluation", "inputs", "vault_apy"), vault_ref)
        self.assertIn("product_apy", spec["notes"])
        self.assertIn("settings", spec["notes"])
        self.assertIn("seed", spec["notes"])
        self.assertIn("vault reference", spec["notes"])
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, spec["notes"])

    def test_morpho_borrow_settings_paths_precede_live_books(self) -> None:
        spec = next(row for row in LOCKED_SEEDS if row["id"] == "morpho_borrow")
        self.assertAlmostEqual(spec["rate_pct"], 5.0)
        settings = list(spec["settings_paths"])
        live = list(spec["paths"])
        self.assertEqual(settings[0], ("config", "coinbase_manual", "variable_apr"))
        self.assertIn(("config", "coinbase_manual", "morpho_borrow_apr"), settings)
        self.assertNotIn(("config", "coinbase_manual", "variable_apr"), live)
        self.assertNotIn(("snapshot", "coinbase_manual", "variable_apr"), live)
        self.assertIn(("evaluation", "inputs", "variable_apr"), live)
        self.assertIn(("snapshot", "morpho_borrow", "apr"), live)
        self.assertIn("settings", spec["notes"])
        self.assertIn("seed", spec["notes"])
        self.assertIn("avgBorrowApy", spec["notes"])
        self.assertEqual(spec["label"], "Coinbase BTC-backed Morpho loan")
        self.assertIn("margin/borrow", spec["detail"])
        self.assertIn("Coinbase BTC-backed Morpho loan", spec["notes"])
        self.assertIn("margin/borrow", spec["notes"])

    def test_rh_margin_settings_paths_precede_live_books(self) -> None:
        spec = next(row for row in LOCKED_SEEDS if row["id"] == RH_MARGIN_ID)
        self.assertAlmostEqual(spec["rate_pct"], 5.0)
        self.assertEqual(spec["kind"], "debt")
        self.assertEqual(spec["label"], "RH margin interest")
        self.assertIn("borrow cost", spec["detail"])
        self.assertIn("$50k", spec["detail"])
        settings = list(spec["settings_paths"])
        live = list(spec["paths"])
        self.assertEqual(settings[0], ("config", "robinhood", "rh_margin_apr"))
        self.assertIn(("config", "robinhood", "margin_apr"), settings)
        self.assertNotIn(("config", "robinhood", "rh_margin_apr"), live)
        self.assertIn(("evaluation", "inputs", "rh_margin_apr"), live)
        self.assertIn(("snapshot", "robinhood", "margin_apr"), live)
        self.assertEqual(list(RH_MARGIN_BOOK_PATHS), live)
        self.assertNotIn(("evaluation", "inputs", "rh_margin_use"), live)
        self.assertNotIn(("evaluation", "inputs", "rh_margin_use_max"), live)
        self.assertNotIn(("config", "policy", "rh_margin_use_max"), live)
        self.assertIn("settings", spec["notes"])
        self.assertIn("seed", spec["notes"])
        self.assertIn("5% up to $50k", spec["notes"])
        self.assertIn("do not invent", spec["notes"])
        self.assertIn("scrape", spec["notes"])

    def test_usdg_hy_settings_paths_precede_live_books(self) -> None:
        spec = next(row for row in LOCKED_YIELD_SEEDS if row["id"] == "usdg_earn")
        self.assertAlmostEqual(spec["rate_pct"], 7.0)
        settings = list(spec["settings_paths"])
        live = list(spec["paths"])
        self.assertEqual(settings[0], ("config", "robinhood", "usdg_earn_apy_est"))
        self.assertIn(("config", "robinhood", "usdg_hy_apy_est"), settings)
        self.assertNotIn(("config", "robinhood", "usdg_earn_apy_est"), live)
        self.assertNotIn(("snapshot", "robinhood", "usdg_earn_apy_est"), live)
        self.assertIn(("evaluation", "inputs", "rh_usdg_earn_apy_est"), live)
        self.assertIn(("snapshot", "usdg_hy", "apy_est"), live)
        self.assertIn("settings", spec["notes"])
        self.assertIn("seed", spec["notes"])
        self.assertIn("Gold", spec["notes"])
        self.assertIn(USDG_GOLD_CAVEAT, spec["notes"])

    def test_bitcoin_settings_paths_precede_generic_expected_return(self) -> None:
        spec = next(row for row in LOCKED_YIELD_SEEDS if row["id"] == BITCOIN_ID)
        self.assertAlmostEqual(spec["rate_pct"], 30.0)
        self.assertTrue(spec["est_cagr"])
        self.assertEqual(spec["rate_kind"], EST_CAGR_KIND)
        self.assertEqual(spec["detail"], EST_CAGR_NOTE)
        settings = list(spec["settings_paths"])
        live = list(spec.get("paths") or ())
        self.assertEqual(settings[0], ("config", "coinbase_manual", "bitcoin_cagr_est"))
        self.assertIn(("config", "coinbase_manual", "btc_cagr_est"), settings)
        self.assertEqual(live, [])
        self.assertNotIn(("evaluation", "inputs", "btc_expected_return"), live)
        self.assertNotIn(("evaluation", "inputs", "assumed_return"), live)
        self.assertNotIn(("snapshot", "coinbase_manual", "btc_assumed_return"), live)
        self.assertNotIn(("evaluation", "inputs", "bitcoin_cagr_apy_est"), settings)
        self.assertIn(EST_CAGR_NOTE, spec["notes"])
        self.assertIn("not cash APR/APY", spec["notes"])
        self.assertIn("settings", spec["notes"])
        self.assertIn("seed", spec["notes"])

    def test_agentic_fund_settings_paths_precede_generic_expected_return(self) -> None:
        spec = next(row for row in LOCKED_YIELD_SEEDS if row["id"] == AGENTIC_FUND_ID)
        self.assertAlmostEqual(spec["rate_pct"], 15.0)
        self.assertTrue(spec["est_cagr"])
        self.assertEqual(spec["rate_kind"], EST_CAGR_KIND)
        self.assertEqual(spec["detail"], EST_CAGR_NOTE)
        settings = list(spec["settings_paths"])
        live = list(spec.get("paths") or ())
        self.assertEqual(settings[0], ("config", "robinhood", "agentic_fund_cagr_est"))
        self.assertIn(("config", "robinhood", "agentic_cagr_est"), settings)
        self.assertEqual(live, [])
        self.assertNotIn(("evaluation", "inputs", "equity_expected_return"), live)
        self.assertNotIn(("evaluation", "inputs", "assumed_return"), live)
        self.assertNotIn(("snapshot", "robinhood", "expected_return"), live)
        self.assertIn(EST_CAGR_NOTE, spec["notes"])
        self.assertIn("not cash APR/APY", spec["notes"])
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
        self.assertEqual(payload["axis"]["debt_lane"], "below")
        self.assertEqual(payload["axis"]["yield_lane"], "above")
        self.assertEqual(payload["axis"]["left"], "0%")
        self.assertGreaterEqual(payload["axis"]["max_pct"], 29)
        self.assertEqual(payload["axis"]["ticks"], list(SEED_TICKS_PCT))
        self.assertTrue(payload["policy"]["apr_apy_only"])
        self.assertTrue(payload["policy"]["est_cagr_exception"])
        self.assertEqual(payload["policy"]["est_cagr_ids"], [BITCOIN_ID, AGENTIC_FUND_ID])
        self.assertFalse(payload["policy"]["equity_btc_assumed_return"])
        self.assertFalse(payload["policy"]["invented_rates"])
        self.assertFalse(payload["policy"]["wells_is_fcc_liability"])
        self.assertFalse(payload["policy"]["wells_on_fcc_spectrum"])
        self.assertFalse(payload["policy"]["chip_size_is_notional"])
        self.assertFalse(payload["coach_wired"])
        self.assertFalse(payload["policy"]["coach_wired"])
        self.assertEqual(payload["policy"]["rate_precedence"], RATE_PRECEDENCE)
        self.assertTrue(payload["policy"]["settings_are_fallback"])
        self.assertFalse(payload["policy"]["morpho_hy_vault_graphql_is_product"])
        self.assertTrue(rates_are_honest(payload))

        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertNotIn(WELLS_OFF_FCC_ID, by_id)
        for row in LOCKED_FLEET:
            chip = by_id[row["id"]]
            self.assertEqual(chip["kind"], "debt")
            self.assertEqual(chip["lane"], "below")
            self.assertAlmostEqual(chip["rate_pct"], row["rate_pct"])
            self.assertEqual(chip["source"], "locked_financing")
            self.assertNotIn("principal_balance", chip)
            self.assertNotIn("account_balance", chip)

        self.assertAlmostEqual(by_id["morpho_borrow"]["rate_pct"], 5.0)
        self.assertTrue(by_id["morpho_borrow"]["approx"])
        self.assertEqual(by_id["morpho_borrow"]["source"], "locked_seed")
        self.assertEqual(by_id["morpho_borrow"]["deep_link"], "index.html#morpho")
        self.assertEqual(by_id["morpho_borrow"]["kind"], "debt")
        self.assertEqual(by_id["morpho_borrow"]["lane"], "below")
        self.assertEqual(by_id["morpho_borrow"]["label"], "Coinbase BTC-backed Morpho loan")
        self.assertIn("margin/borrow", by_id["morpho_borrow"]["detail"])
        self.assertEqual(by_id["morpho_borrow"]["rate_kind"], "APR")

        self.assertAlmostEqual(by_id[RH_MARGIN_ID]["rate_pct"], 5.0)
        self.assertTrue(by_id[RH_MARGIN_ID]["approx"])
        self.assertEqual(by_id[RH_MARGIN_ID]["source"], "locked_seed")
        self.assertEqual(by_id[RH_MARGIN_ID]["kind"], "debt")
        self.assertEqual(by_id[RH_MARGIN_ID]["lane"], "below")
        self.assertEqual(by_id[RH_MARGIN_ID]["label"], "RH margin interest")
        self.assertEqual(by_id[RH_MARGIN_ID]["rate_kind"], "APR")
        self.assertIn("5% up to $50k", by_id[RH_MARGIN_ID]["notes"])
        self.assertEqual(by_id[RH_MARGIN_ID]["deep_link"], "index.html#rh-margin")

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
            self.assertEqual(chip["lane"], "above")
            self.assertAlmostEqual(chip["rate_pct"], row["rate_pct"])
            self.assertEqual(chip["source"], "locked_seed")
            self.assertTrue(chip["approx"])
            self.assertNotIn("principal_balance", chip)
            self.assertNotIn("account_balance", chip)

        self.assertAlmostEqual(by_id["x_money"]["rate_pct"], 6.0)
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 7.0)
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, by_id["morpho_hy"]["notes"])
        self.assertAlmostEqual(by_id["usdg_earn"]["rate_pct"], 7.0)
        self.assertIn("Gold", by_id["usdg_earn"]["notes"])
        self.assertIn("cancel", by_id["usdg_earn"]["notes"].lower())
        self.assertIn(USDG_GOLD_CAVEAT, by_id["usdg_earn"]["notes"])

        btc = by_id[BITCOIN_ID]
        self.assertAlmostEqual(btc["rate_pct"], 30.0)
        self.assertTrue(btc["est_cagr"])
        self.assertEqual(btc["rate_kind"], EST_CAGR_KIND)
        self.assertNotEqual(btc["rate_kind"], "APY")
        self.assertEqual(btc["rate_basis"], EST_CAGR_LABEL)
        self.assertEqual(btc["detail"], EST_CAGR_NOTE)
        self.assertIn(EST_CAGR_NOTE, btc["notes"])
        self.assertEqual(btc["deep_link"], "index.html#bitcoin")
        self.assertNotIn("notional", btc)

        agentic = by_id[AGENTIC_FUND_ID]
        self.assertAlmostEqual(agentic["rate_pct"], 15.0)
        self.assertTrue(agentic["est_cagr"])
        self.assertEqual(agentic["rate_kind"], EST_CAGR_KIND)
        self.assertNotEqual(agentic["rate_kind"], "APY")
        self.assertEqual(agentic["rate_basis"], EST_CAGR_LABEL)
        self.assertEqual(agentic["detail"], EST_CAGR_NOTE)
        self.assertIn(EST_CAGR_NOTE, agentic["notes"])
        self.assertEqual(agentic["deep_link"], "index.html#agentic-fund")
        self.assertNotIn("notional", agentic)
        self.assertNotIn("nvda", {c["id"] for c in payload["chips"]})
        self.assertNotIn("aapl", {c["id"] for c in payload["chips"]})
        self.assertNotIn("googl", {c["id"] for c in payload["chips"]})
        self.assertNotIn("be", {c["id"] for c in payload["chips"]})

        jr = by_id[JR_STRCUSX_ID]
        self.assertEqual(jr["kind"], "yield")
        self.assertEqual(jr["lane"], "above")
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
                        "product_apy": 0.036,
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
        self.assertEqual(by_id["x_money"]["lane"], "above")
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 3.6)
        self.assertEqual(by_id["morpho_hy"]["source"], "books")
        self.assertIn("product_apy", by_id["morpho_hy"]["notes"])
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, by_id["morpho_hy"]["notes"])
        self.assertAlmostEqual(by_id["morpho_hy"]["vault_apy_pct"], 3.6)
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
        self.assertEqual(by_id["morpho_borrow"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["morpho_borrow"]["rate_pct"], 5.0)
        self.assertEqual(by_id[RH_MARGIN_ID]["source"], "locked_seed")
        self.assertAlmostEqual(by_id[RH_MARGIN_ID]["rate_pct"], 5.0)
        self.assertEqual(by_id["morpho_hy"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 7.0)
        self.assertEqual(by_id["usdg_earn"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["usdg_earn"]["rate_pct"], 7.0)
        self.assertEqual(by_id[BITCOIN_ID]["source"], "locked_seed")
        self.assertAlmostEqual(by_id[BITCOIN_ID]["rate_pct"], 30.0)
        self.assertEqual(by_id[AGENTIC_FUND_ID]["source"], "locked_seed")
        self.assertAlmostEqual(by_id[AGENTIC_FUND_ID]["rate_pct"], 15.0)
        for chip in payload["chips"]:
            self.assertIn(chip["kind"], ("debt", "yield"))
            if chip["id"] in EST_CAGR_IDS:
                self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
                self.assertTrue(chip.get("est_cagr"))
            else:
                self.assertIn(chip["rate_kind"], ("APR", "APY"))
                self.assertFalse(chip.get("est_cagr"))
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

    def test_jr_uses_canonical_jr_strcusx_apy_field(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "snapshot": {
                    "solana": {"jr_strcusx_apy": 0.176, "jr_strcusx": 3.3}
                }
            },
            config={},
            x_money={},
            solana={},
        )
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], 17.6)
        self.assertEqual(jr["source"], "books")
        self.assertIn("jr_strcusx_apy", jr["notes"])
        self.assertNotIn("notional", jr)
        self.assertTrue(rates_are_honest(payload))

    def test_jr_uses_solstice_sidecar_kwarg_when_snapshot_omits_it(self) -> None:
        payload = build_interest_spectrum(
            treasury={"snapshot": {"solana": {"jr_strcusx": 3.3}}},
            config={},
            x_money={},
            solana={},
            solstice_jr={
                "jr_strcusx_apy": 0.3473,
                "apy": 0.3473,
                "source": "solstice_onchain",
            },
        )
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], 34.73)
        self.assertEqual(jr["source"], "books")
        self.assertFalse(jr["approx"])
        self.assertNotIn("rate_label", jr)
        self.assertNotIn("notional", jr)
        self.assertGreaterEqual(payload["axis"]["max_pct"], 35)
        self.assertTrue(rates_are_honest(payload))

    def test_jr_disk_path_reads_solstice_sidecar_when_snapshot_omits_it(self) -> None:
        import tempfile

        import treasury.interest_spectrum as spec

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        tre = root / "treasury_latest.json"
        tre.write_text(
            json.dumps({"snapshot": {"solana": {"jr_strcusx": 3.317128}}}),
            encoding="utf-8",
        )
        sidecar = root / "solstice_jr_latest.json"
        sidecar.write_text(
            json.dumps(
                {
                    "jr_strcusx_apy": 0.3473,
                    "apy": 0.3473,
                    "source": "solstice_onchain",
                }
            ),
            encoding="utf-8",
        )
        missing = root / "missing.json"
        prev = {
            "TREASURY_FCC": spec.TREASURY_FCC,
            "TREASURY_SNAP": spec.TREASURY_SNAP,
            "SOLSTICE_JR_SNAPSHOT": spec.SOLSTICE_JR_SNAPSHOT,
            "XM_SNAPSHOT": spec.XM_SNAPSHOT,
            "SOLANA_SNAPSHOT": spec.SOLANA_SNAPSHOT,
            "CONFIG_PATH": spec.CONFIG_PATH,
            "FCC_STUB": spec.FCC_STUB,
        }

        def _restore() -> None:
            for key, val in prev.items():
                setattr(spec, key, val)

        self.addCleanup(_restore)
        spec.TREASURY_FCC = tre
        spec.TREASURY_SNAP = tre
        spec.SOLSTICE_JR_SNAPSHOT = sidecar
        spec.XM_SNAPSHOT = missing
        spec.SOLANA_SNAPSHOT = missing
        spec.CONFIG_PATH = missing
        spec.FCC_STUB = missing

        payload = spec.build_interest_spectrum()
        jr = {c["id"]: c for c in payload["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], 34.73)
        self.assertEqual(jr["source"], "books")
        self.assertFalse(jr["approx"])
        self.assertNotIn("rate_label", jr)
        self.assertNotIn("notional", jr)
        self.assertGreaterEqual(payload["axis"]["max_pct"], 35)
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
        self.assertIn("precedence", usdg["notes"])
        self.assertIn("settings", usdg["notes"])

    def test_usdg_hy_settings_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"robinhood": {"usdg_earn_apy_est": 0.041}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(chip["rate_pct"], 4.1)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("config.robinhood.usdg_earn_apy_est", chip["notes"])
        self.assertIn("settings", chip["notes"].lower())
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_usdg_hy_live_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"rh_usdg_earn_apy_est": 0.0328}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(chip["rate_pct"], 3.28)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("evaluation.inputs.rh_usdg_earn_apy_est", chip["notes"])
        self.assertIn("live", chip["notes"])
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_usdg_hy_live_beats_settings_when_honest(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"rh_usdg_earn_apy_est": 0.0328}},
                "snapshot": {
                    "usdg_hy": {"apy_est": 0.031},
                    "robinhood": {"usdg_earn_apy_est": 0.0328},
                },
            },
            config={"robinhood": {"usdg_earn_apy_est": 0.055}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(chip["rate_pct"], 3.28)
        self.assertEqual(chip["source"], "books")
        self.assertIn("evaluation.inputs.rh_usdg_earn_apy_est", chip["notes"])
        self.assertNotIn("config.robinhood.usdg_earn_apy_est", chip["notes"])
        self.assertIn("live", chip["notes"])
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_usdg_hy_live_beats_dedicated_settings_key(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"rh_usdg_earn_apy_est": 0.033}}},
            config={"robinhood": {"usdg_hy_apy_est": 0.048}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(chip["rate_pct"], 3.3)
        self.assertIn("evaluation.inputs.rh_usdg_earn_apy_est", chip["notes"])
        self.assertNotIn("config.robinhood.usdg_hy_apy_est", chip["notes"])
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])

    def test_usdg_hy_blank_settings_does_not_beat_live(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"rh_usdg_earn_apy_est": 0.0328}}},
            config={"robinhood": {"usdg_earn_apy_est": None, "usdg_hy_apy_est": ""}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(chip["rate_pct"], 3.28)
        self.assertIn("evaluation.inputs.rh_usdg_earn_apy_est", chip["notes"])
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])

    def test_usdg_does_not_invent_post_gold_rate(self) -> None:
        """Gold-cancelled flags must not invent 0% or any other post-Gold APY."""
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {
                    "inputs": {
                        "gold_cancelled": True,
                        "rh_gold": False,
                        "post_gold_apy": 0.0,
                    }
                },
                "snapshot": {"robinhood": {"gold": False, "gold_cancelled": True}},
            },
            config={"robinhood": {"gold": False}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertEqual(chip["source"], "locked_seed")
        self.assertAlmostEqual(chip["rate_pct"], 7.0)
        self.assertTrue(chip["approx"])
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])
        self.assertNotAlmostEqual(chip["rate_pct"], 0.0)
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_borrow_settings_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"coinbase_manual": {"variable_apr": 0.062}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 6.2)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("config.coinbase_manual.variable_apr", chip["notes"])
        self.assertIn("settings", chip["notes"].lower())
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_borrow_live_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"variable_apr": 0.0468}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 4.68)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("evaluation.inputs.variable_apr", chip["notes"])
        self.assertIn("live", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_borrow_live_beats_settings_when_honest(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"variable_apr": 0.0468}},
                "snapshot": {
                    "morpho_borrow": {"apr": 0.044},
                    "coinbase_manual": {"variable_apr": 0.0468},
                },
            },
            config={"coinbase_manual": {"variable_apr": 0.071}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 4.68)
        self.assertEqual(chip["source"], "books")
        self.assertIn("evaluation.inputs.variable_apr", chip["notes"])
        self.assertNotIn("config.coinbase_manual.variable_apr", chip["notes"])
        self.assertIn("live", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_borrow_live_beats_dedicated_settings_key(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"variable_apr": 0.0468}}},
            config={"coinbase_manual": {"morpho_borrow_apr": 0.058}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 4.68)
        self.assertIn("evaluation.inputs.variable_apr", chip["notes"])
        self.assertNotIn("config.coinbase_manual.morpho_borrow_apr", chip["notes"])

    def test_morpho_borrow_blank_settings_does_not_beat_live(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"variable_apr": 0.0468}}},
            config={"coinbase_manual": {"variable_apr": None, "morpho_borrow_apr": ""}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 4.68)
        self.assertIn("evaluation.inputs.variable_apr", chip["notes"])

    def test_morpho_borrow_zero_settings_does_not_paint_zero(self) -> None:
        """Blank/0 manual override must not paint 0% as books (#343)."""
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"variable_apr": 0.0468}}},
            config={"coinbase_manual": {"variable_apr": 0, "morpho_borrow_apr": 0.0}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 4.68)
        self.assertEqual(chip["source"], "books")
        self.assertNotAlmostEqual(chip["rate_pct"], 0.0)
        self.assertIn("evaluation.inputs.variable_apr", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_borrow_zero_settings_falls_to_seed_when_no_live(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {}},
                "snapshot": {"coinbase_manual": {"variable_apr": 0}},
            },
            config={"coinbase_manual": {"variable_apr": 0, "morpho_borrow_apr": "0"}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 5.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertTrue(chip["approx"])
        self.assertNotAlmostEqual(chip["rate_pct"], 0.0)
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_borrow_seed_always_visible_when_no_books(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 5.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertTrue(chip["approx"])
        self.assertIn("seed", chip["notes"].lower())
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})
        self.assertTrue(rates_are_honest(payload))

    def test_rh_margin_settings_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"robinhood": {"rh_margin_apr": 0.062}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 6.2)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertEqual(chip["kind"], "debt")
        self.assertEqual(chip["lane"], "below")
        self.assertIn("config.robinhood.rh_margin_apr", chip["notes"])
        self.assertIn("settings", chip["notes"].lower())
        self.assertIn("5% up to $50k", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_rh_margin_live_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"rh_margin_apr": 0.055}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 5.5)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("evaluation.inputs.rh_margin_apr", chip["notes"])
        self.assertIn("live", chip["notes"])
        self.assertIn("5% up to $50k", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_rh_margin_live_beats_settings_when_honest(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"rh_margin_apr": 0.055}},
                "snapshot": {"robinhood": {"margin_apr": 0.048}},
            },
            config={"robinhood": {"rh_margin_apr": 0.071}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 5.5)
        self.assertEqual(chip["source"], "books")
        self.assertIn("evaluation.inputs.rh_margin_apr", chip["notes"])
        self.assertNotIn("config.robinhood.rh_margin_apr", chip["notes"])
        self.assertIn("live", chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_rh_margin_live_beats_dedicated_settings_key(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"rh_margin_apr": 0.055}}},
            config={"robinhood": {"margin_apr": 0.058}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 5.5)
        self.assertIn("evaluation.inputs.rh_margin_apr", chip["notes"])
        self.assertNotIn("config.robinhood.margin_apr", chip["notes"])

    def test_rh_margin_blank_settings_does_not_beat_live(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"rh_margin_apr": 0.055}}},
            config={"robinhood": {"rh_margin_apr": None, "margin_apr": ""}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 5.5)
        self.assertIn("evaluation.inputs.rh_margin_apr", chip["notes"])

    def test_rh_margin_zero_settings_does_not_paint_zero(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"rh_margin_apr": 0.055}}},
            config={"robinhood": {"rh_margin_apr": 0, "margin_apr": 0.0}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 5.5)
        self.assertEqual(chip["source"], "books")
        self.assertNotAlmostEqual(chip["rate_pct"], 0.0)
        self.assertTrue(rates_are_honest(payload))

    def test_rh_margin_seed_always_visible_when_no_books(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 5.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertTrue(chip["approx"])
        self.assertEqual(chip["kind"], "debt")
        self.assertEqual(chip["lane"], "below")
        self.assertIn("seed", chip["notes"].lower())
        self.assertIn("5% up to $50k", chip["notes"])
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})
        self.assertTrue(rates_are_honest(payload))

    def test_rh_margin_does_not_invent_from_utilization_or_hy(self) -> None:
        """Utilization, USDG yield, and generic rates must not invent RH margin APR."""
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {
                    "inputs": {
                        "rh_margin_use": 0.22,
                        "rh_usdg_earn_apy_est": 0.032,
                        "variable_apr": 0.0468,
                    }
                },
                "snapshot": {
                    "robinhood": {
                        "margin_loan_usd": 12000,
                        "rh_margin_use": 0.18,
                    },
                    "usdg_hy": {"apy_est": 0.031},
                },
            },
            config={"policy": {"rh_margin_use_max": 0.4}, "robinhood": {}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[RH_MARGIN_ID]
        self.assertEqual(chip["source"], "locked_seed")
        self.assertAlmostEqual(chip["rate_pct"], 5.0)
        self.assertTrue(chip["approx"])
        self.assertAlmostEqual(chip["notional"], 12000)
        self.assertEqual(chip["notional_kind"], "principal")
        rates = [c["rate_pct"] for c in payload["placed"] if c["id"] == RH_MARGIN_ID]
        self.assertNotIn(22.0, rates)
        self.assertNotIn(40.0, rates)
        self.assertNotIn(18.0, rates)
        self.assertNotIn(3.2, rates)
        self.assertTrue(rates_are_honest(payload))

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
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_settings_product_apy_key_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"vault_apy": 0.0291}}},
            config={"coinbase_manual": {"product_apy": 0.072}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 7.2)
        self.assertEqual(chip["source"], "books")
        self.assertIn("config.coinbase_manual.product_apy", chip["notes"])
        self.assertNotIn("evaluation.inputs.vault_apy", chip["notes"].split("vault reference")[0])
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])
        self.assertAlmostEqual(chip["vault_apy_pct"], 2.91)
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_vault_graphql_alone_does_not_paint_product_chip(self) -> None:
        """Naked vault_apy / GraphQL ~2.91% must not become books product APY."""
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"vault_apy": 0.0291, "hy_vault_apy": 0.0291}},
                "snapshot": {
                    "morpho_hy": {
                        "apy_est": 0.0291,
                        "apy": 0.0291,
                        "vault_apy": 0.0291,
                        "avg_net_apy": 0.0291,
                        "source": "morpho_graphql",
                    }
                },
            },
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 7.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertTrue(chip["approx"])
        self.assertNotAlmostEqual(chip["rate_pct"], 2.91)
        self.assertNotEqual(chip["source"], "books")
        self.assertAlmostEqual(chip["vault_apy_pct"], 2.91)
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_product_apy_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"product_apy": 0.064, "vault_apy": 0.0291}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 6.4)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertIn("evaluation.inputs.product_apy", chip["notes"])
        self.assertIn("product_apy", chip["notes"])
        self.assertNotIn("evaluation.inputs.vault_apy", chip["notes"].split("vault reference")[0])
        self.assertAlmostEqual(chip["vault_apy_pct"], 2.91)
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_product_apy_beats_settings_when_honest(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {
                    "inputs": {
                        "vault_apy": 0.0291,
                        "hy_vault_apy": 0.031,
                        "product_apy": 0.064,
                    }
                },
                "snapshot": {
                    "morpho_hy": {"apy_est": 0.028, "product_apy": 0.06},
                    "coinbase_manual": {"vault_apy": 0.0291},
                },
            },
            config={"coinbase_manual": {"vault_apy": 0.08}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 6.4)
        self.assertEqual(chip["source"], "books")
        self.assertIn("evaluation.inputs.product_apy", chip["notes"])
        self.assertNotIn("config.coinbase_manual.vault_apy", chip["notes"])
        self.assertNotIn("evaluation.inputs.vault_apy", chip["notes"].split("vault reference")[0])
        self.assertAlmostEqual(chip["vault_apy_pct"], 2.91)
        self.assertNotAlmostEqual(chip["rate_pct"], 2.91)
        self.assertNotAlmostEqual(chip["rate_pct"], 8.0)
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_morpho_hy_product_apy_beats_dedicated_settings_key(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"vault_apy": 0.029, "product_apy": 0.05}}},
            config={"coinbase_manual": {"morpho_hy_apy_est": 0.061}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 5.0)
        self.assertIn("evaluation.inputs.product_apy", chip["notes"])
        self.assertNotIn("config.coinbase_manual.morpho_hy_apy_est", chip["notes"])
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])

    def test_morpho_hy_blank_settings_does_not_beat_product_apy(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"product_apy": 0.063, "vault_apy": 0.0291}}},
            config={
                "coinbase_manual": {
                    "product_apy": None,
                    "vault_apy": None,
                    "morpho_hy_apy_est": "",
                }
            },
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 6.3)
        self.assertEqual(chip["source"], "books")
        self.assertIn("evaluation.inputs.product_apy", chip["notes"])
        self.assertAlmostEqual(chip["rate_pct"], 6.3)
        self.assertNotAlmostEqual(chip["rate_pct"], 2.91)

    def test_morpho_hy_blank_settings_and_naked_vault_falls_to_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"vault_apy": 0.0291}}},
            config={"coinbase_manual": {"vault_apy": None, "morpho_hy_apy_est": ""}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 7.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertAlmostEqual(chip["vault_apy_pct"], 2.91)
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])

    def test_morpho_hy_vault_apy_never_wins_product_chip_after_live_first(self) -> None:
        """Naka AC: vault GraphQL ~2.9% is live but must not paint the One chip."""
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"vault_apy": 0.029, "hy_vault_apy": 0.029}},
                "snapshot": {
                    "morpho_hy": {
                        "vault_apy": 0.029,
                        "apy_est": 0.029,
                        "apy": 0.029,
                        "avg_net_apy": 0.029,
                        "source": "morpho_graphql",
                    }
                },
            },
            config={"coinbase_manual": {"product_apy": 0.08, "vault_apy": 0.08}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 8.0)
        self.assertEqual(chip["source"], "books")
        self.assertIn("config.coinbase_manual.product_apy", chip["notes"])
        self.assertIn("settings", chip["notes"].lower())
        self.assertNotAlmostEqual(chip["rate_pct"], 2.9)
        self.assertAlmostEqual(chip["vault_apy_pct"], 2.9)
        self.assertEqual(chip["vault_rate_kind"], "vault_reference")
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_zero_live_does_not_beat_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {
                    "inputs": {
                        "variable_apr": 0,
                        "rh_margin_apr": 0.0,
                        "rh_usdg_earn_apy_est": 0,
                        "product_apy": 0,
                        "x_money_apy_est": 0,
                    }
                },
                "snapshot": {
                    "morpho_borrow": {"apr": 0},
                    "usdg_hy": {"apy_est": 0},
                    "morpho_hy": {"product_apy": 0, "vault_apy": 0.0291},
                    "x_money": {"apy_est": 0},
                },
            },
            config={},
            x_money={"apy_est": 0},
            solana={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertAlmostEqual(by_id["morpho_borrow"]["rate_pct"], 5.0)
        self.assertEqual(by_id["morpho_borrow"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id[RH_MARGIN_ID]["rate_pct"], 5.0)
        self.assertEqual(by_id[RH_MARGIN_ID]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["usdg_earn"]["rate_pct"], 7.0)
        self.assertEqual(by_id["usdg_earn"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 7.0)
        self.assertEqual(by_id["morpho_hy"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["x_money"]["rate_pct"], 6.0)
        self.assertEqual(by_id["x_money"]["source"], "locked_seed")
        self.assertAlmostEqual(by_id["morpho_hy"]["vault_apy_pct"], 2.91)
        self.assertNotAlmostEqual(by_id["morpho_hy"]["rate_pct"], 0.0)
        self.assertTrue(rates_are_honest(payload))

    def test_zero_live_falls_to_settings_not_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"variable_apr": 0, "product_apy": 0}}},
            config={
                "coinbase_manual": {"variable_apr": 0.062, "product_apy": 0.072},
            },
            x_money={},
            solana={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertAlmostEqual(by_id["morpho_borrow"]["rate_pct"], 6.2)
        self.assertIn("settings", by_id["morpho_borrow"]["notes"].lower())
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 7.2)
        self.assertIn("settings", by_id["morpho_hy"]["notes"].lower())
        self.assertTrue(rates_are_honest(payload))

    def test_jr_zero_or_blocked_live_stays_docs_target(self) -> None:
        zero = build_interest_spectrum(
            treasury={"snapshot": {"solana": {"jr_strcusx_apy": 0}}},
            config={},
            x_money={},
            solana={},
        )
        jr0 = {c["id"]: c for c in zero["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr0["rate_pct"], JR_TARGET_PCT)
        self.assertEqual(jr0["source"], "docs_target")
        self.assertNotAlmostEqual(jr0["rate_pct"], 0.0)

        blocked = build_interest_spectrum(
            treasury={
                "snapshot": {
                    "solstice_jr": {
                        "jr_strcusx_apy": 0.18,
                        "jr_strcusx_apy_error": "source blocked: no public/docs JSON APY",
                    }
                }
            },
            config={},
            x_money={},
            solana={},
        )
        jr = {c["id"]: c for c in blocked["chips"]}[JR_STRCUSX_ID]
        self.assertAlmostEqual(jr["rate_pct"], JR_TARGET_PCT)
        self.assertEqual(jr["source"], "docs_target")
        self.assertEqual(jr["rate_label"], JR_TARGET_LABEL)
        self.assertNotAlmostEqual(jr["rate_pct"], 18.0)
        self.assertTrue(rates_are_honest(blocked))

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
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, chip["notes"])
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})
        self.assertTrue(rates_are_honest(payload))

    def test_bitcoin_seed_always_shows_with_cagr_label(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[BITCOIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 30.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertEqual(chip["lane"], "above")
        self.assertTrue(chip["approx"])
        self.assertTrue(chip["est_cagr"])
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertEqual(chip["rate_basis"], EST_CAGR_LABEL)
        self.assertIn(EST_CAGR_NOTE, chip["notes"])
        self.assertIn("seed", chip["notes"].lower())
        self.assertTrue(payload["policy"]["est_cagr_exception"])
        self.assertTrue(rates_are_honest(payload))

    def test_agentic_fund_seed_always_shows_with_cagr_label(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[AGENTIC_FUND_ID]
        self.assertAlmostEqual(chip["rate_pct"], 15.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertEqual(chip["lane"], "above")
        self.assertTrue(chip["approx"])
        self.assertTrue(chip["est_cagr"])
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertEqual(chip["rate_basis"], EST_CAGR_LABEL)
        self.assertIn(EST_CAGR_NOTE, chip["notes"])
        self.assertIn("seed", chip["notes"].lower())
        self.assertTrue(rates_are_honest(payload))

    def test_bitcoin_settings_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"coinbase_manual": {"bitcoin_cagr_est": 0.22}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[BITCOIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 22.0)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertTrue(chip["est_cagr"])
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertEqual(chip["rate_basis"], EST_CAGR_LABEL)
        self.assertIn("config.coinbase_manual.bitcoin_cagr_est", chip["notes"])
        self.assertIn("settings", chip["notes"].lower())
        self.assertIn(EST_CAGR_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_agentic_fund_settings_beats_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {}}},
            config={"robinhood": {"agentic_fund_cagr_est": 0.18}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[AGENTIC_FUND_ID]
        self.assertAlmostEqual(chip["rate_pct"], 18.0)
        self.assertEqual(chip["source"], "books")
        self.assertFalse(chip["approx"])
        self.assertTrue(chip["est_cagr"])
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertEqual(chip["rate_basis"], EST_CAGR_LABEL)
        self.assertIn("config.robinhood.agentic_fund_cagr_est", chip["notes"])
        self.assertIn("settings", chip["notes"].lower())
        self.assertIn(EST_CAGR_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_bitcoin_does_not_invent_live_return(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {"inputs": {"bitcoin_cagr_apy_est": 0.275, "btc_expected_return": 0.25}},
                "snapshot": {"bitcoin": {"cagr_apy_est": 0.28}, "coinbase_manual": {"btc_assumed_return": 0.33}},
            },
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[BITCOIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 30.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertTrue(chip["est_cagr"])
        self.assertTrue(rates_are_honest(payload))

    def test_agentic_fund_does_not_invent_live_return(self) -> None:
        payload = build_interest_spectrum(
            treasury={
                "evaluation": {
                    "inputs": {"agentic_fund_cagr_apy_est": 0.165, "equity_expected_return": 0.10}
                },
                "snapshot": {"agentic_fund": {"cagr_apy_est": 0.19}, "robinhood": {"expected_return": 0.11}},
            },
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[AGENTIC_FUND_ID]
        self.assertAlmostEqual(chip["rate_pct"], 15.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertTrue(chip["est_cagr"])
        self.assertTrue(rates_are_honest(payload))

    def test_bitcoin_legacy_settings_key_still_overrides_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"bitcoin_cagr_apy_est": 0.275}}},
            config={"coinbase_manual": {"bitcoin_cagr_apy_est": 0.31}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[BITCOIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 31.0)
        self.assertEqual(chip["source"], "books")
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertIn("config.coinbase_manual.bitcoin_cagr_apy_est", chip["notes"])
        self.assertNotIn("evaluation.inputs.bitcoin_cagr_apy_est", chip["notes"])
        self.assertIn(EST_CAGR_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_agentic_fund_legacy_settings_key_still_overrides_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"agentic_fund_cagr_apy_est": 0.165}}},
            config={"robinhood": {"agentic_fund_cagr_apy_est": 0.12}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[AGENTIC_FUND_ID]
        self.assertAlmostEqual(chip["rate_pct"], 12.0)
        self.assertEqual(chip["source"], "books")
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertIn("config.robinhood.agentic_fund_cagr_apy_est", chip["notes"])
        self.assertNotIn("evaluation.inputs.agentic_fund_cagr_apy_est", chip["notes"])
        self.assertIn(EST_CAGR_NOTE, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_bitcoin_blank_settings_stays_on_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"bitcoin_cagr_apy_est": 0.275}}},
            config={"coinbase_manual": {"bitcoin_cagr_est": None, "btc_cagr_est": ""}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[BITCOIN_ID]
        self.assertAlmostEqual(chip["rate_pct"], 30.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertIn(EST_CAGR_NOTE, chip["notes"])

    def test_agentic_fund_blank_settings_stays_on_seed(self) -> None:
        payload = build_interest_spectrum(
            treasury={"evaluation": {"inputs": {"agentic_fund_cagr_apy_est": 0.165}}},
            config={"robinhood": {"agentic_fund_cagr_est": None, "agentic_cagr_est": ""}},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}[AGENTIC_FUND_ID]
        self.assertAlmostEqual(chip["rate_pct"], 15.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertEqual(chip["rate_kind"], EST_CAGR_KIND)
        self.assertIn(EST_CAGR_NOTE, chip["notes"])

    def test_honesty_rejects_est_cagr_chip_presented_as_cash_apy(self) -> None:
        payload = build_interest_spectrum(treasury={}, config={}, x_money={}, solana={})
        by_id = {c["id"]: c for c in payload["chips"]}
        by_id[BITCOIN_ID]["rate_kind"] = "APY"
        self.assertFalse(rates_are_honest(payload))
        by_id[BITCOIN_ID]["rate_kind"] = EST_CAGR_KIND
        by_id[BITCOIN_ID]["est_cagr"] = False
        payload["policy"]["est_cagr_exception"] = False
        self.assertFalse(rates_are_honest(payload))

    def test_cagr_chips_ignore_generic_expected_return_fields(self) -> None:
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
            config={},
            x_money={},
            solana={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertEqual(by_id[BITCOIN_ID]["source"], "locked_seed")
        self.assertAlmostEqual(by_id[BITCOIN_ID]["rate_pct"], 30.0)
        self.assertEqual(by_id[AGENTIC_FUND_ID]["source"], "locked_seed")
        self.assertAlmostEqual(by_id[AGENTIC_FUND_ID]["rate_pct"], 15.0)
        self.assertTrue(by_id[BITCOIN_ID]["est_cagr"])
        self.assertTrue(by_id[AGENTIC_FUND_ID]["est_cagr"])
        self.assertEqual(by_id[BITCOIN_ID]["rate_kind"], EST_CAGR_KIND)
        self.assertEqual(by_id[AGENTIC_FUND_ID]["rate_kind"], EST_CAGR_KIND)
        self.assertFalse(payload["policy"]["equity_btc_assumed_return"])
        self.assertTrue(payload["policy"]["est_cagr_exception"])
        self.assertTrue(rates_are_honest(payload))

    def test_coach_is_not_wired_even_if_stub_has_a_number(self) -> None:
        payload = build_interest_spectrum(
            treasury={},
            config={"interest_spectrum": {"coach_threshold_pct": 5}},
            x_money={},
            stub={"coach_threshold_pct": 4.25},
        )
        self.assertFalse(payload["coach_wired"])
        self.assertFalse(payload["policy"]["coach_wired"])
        self.assertIsNone(payload.get("coach_nudge"))
        self.assertNotIn("coach_threshold_pct", payload)
        self.assertNotIn("coach_threshold_locked", payload)
        self.assertNotIn("coach_threshold_x", payload)


def _exp_tabs(*items: dict) -> dict:
    return {"tabs": {"Essential": {"items": list(items)}}}


def _current_treasury(
    *,
    free_dollar: float = 100.0,
    ltv: float | None = 0.40,
    expenses: dict | None = None,
    extra_snap: dict | None = None,
    extra_inputs: dict | None = None,
) -> dict:
    inputs: dict = {"next_free_dollar": free_dollar}
    if ltv is not None:
        inputs["ltv"] = ltv
    snap = {"expenses": expenses or _exp_tabs()}
    if extra_snap:
        snap.update(extra_snap)
    if extra_inputs:
        inputs.update(extra_inputs)
    return {
        "as_of": "2026-08-27",
        "evaluation": {
            "inputs": inputs,
            "cashflow_allocation": {"next_free_dollar": free_dollar},
        },
        "snapshot": snap,
    }


class TestSpectrumFcfCoach(unittest.TestCase):
    def test_overdue_household_is_display_only(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(
                free_dollar=250,
                ltv=0.40,
                expenses=_exp_tabs(
                    {
                        "item": "One Card",
                        "date": "2026-08-01",
                        "amount_due": 50,
                    },
                    {
                        "item": "Rent",
                        "date": "2026-09-01",
                        "amount_due": 8400,
                    },
                ),
            ),
            config={},
            x_money={},
            solana={},
        )
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(len(payload["chips"]), 8)
        self.assertFalse(payload["coach_wired"])
        self.assertFalse(payload["policy"]["coach_wired"])
        self.assertIsNone(payload.get("coach_nudge"))
        self.assertGreater(payload["coach"]["household_overdue_count"], 0)
        self.assertFalse(payload["coach"]["essentials_current"])
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})
        self.assertTrue(rates_are_honest(payload))
        self.assertNotIn("coach_threshold_pct", payload)

    def test_current_with_free_dollar_zero_is_display_only(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(
                free_dollar=0,
                ltv=0.45,
                expenses=_exp_tabs(
                    {
                        "item": "Santander",
                        "date": "2026-09-05",
                        "amount_due": 773,
                    }
                ),
            ),
            config={},
            x_money={},
            solana={},
        )
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(len(payload["placed"]), 8)
        self.assertTrue(payload["coach"]["essentials_current"])
        self.assertEqual(payload["coach"]["household_overdue_count"], 0)
        self.assertEqual(payload["coach"]["next_free_dollar"], 0)
        self.assertFalse(payload["coach_wired"])
        self.assertFalse(payload["policy"]["coach_wired"])
        self.assertIsNone(payload.get("coach_nudge"))
        self.assertTrue(rates_are_honest(payload))

    def test_current_with_free_dollar_paints_nudge_line(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(free_dollar=120, ltv=0.40),
            config={},
            x_money={},
            solana={},
        )
        self.assertTrue(payload["coach_wired"])
        self.assertTrue(payload["policy"]["coach_wired"])
        nudge = payload["coach_nudge"]
        self.assertIsNotNone(nudge)
        self.assertEqual(nudge["debt_id"], "one_card")
        self.assertEqual(nudge["yield_id"], "morpho_hy")
        self.assertAlmostEqual(nudge["debt_rate_pct"], 29.0)
        self.assertAlmostEqual(nudge["yield_rate_pct"], 7.0)
        self.assertEqual(nudge["line"], "next free dollar: One Card 29% vs HY 7%")
        low = nudge["line"].lower()
        for tok in (
            "mint",
            "overlay",
            "dip",
            "sleeve",
            "cagr",
            "cic",
            "invent",
            "trade",
            "jr",
            "bitcoin",
            "agentic",
        ):
            self.assertNotIn(tok, low)
        ids = {c["id"] for c in payload["chips"]}
        self.assertIn(JR_STRCUSX_ID, ids)
        self.assertIn(BITCOIN_ID, ids)
        self.assertIn(AGENTIC_FUND_ID, ids)
        self.assertNotIn(WELLS_OFF_FCC_ID, ids)
        self.assertTrue(rates_are_honest(payload))
        self.assertNotIn("coach_threshold_pct", payload)

    def test_ltv_alert_is_manage_ping_not_fail_of_current(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(free_dollar=80, ltv=0.45),
            config={},
            x_money={},
            solana={},
        )
        self.assertTrue(payload["coach"]["essentials_current"])
        self.assertTrue(payload["coach_wired"])
        self.assertEqual(
            payload["coach_nudge"]["line"],
            "next free dollar: One Card 29% vs HY 7%",
        )

    def test_ltv_at_max_keeps_display_only(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(free_dollar=80, ltv=0.50),
            config={},
            x_money={},
            solana={},
        )
        self.assertFalse(payload["coach"]["essentials_current"])
        self.assertFalse(payload["coach_wired"])
        self.assertIsNone(payload.get("coach_nudge"))

    def test_wells_overdue_does_not_fail_current(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(
                free_dollar=90,
                ltv=0.40,
                expenses=_exp_tabs(
                    {
                        "item": "Wells Fargo",
                        "date": "2026-08-01",
                        "amount_due": 400,
                    }
                ),
            ),
            config={},
            x_money={},
            solana={},
        )
        self.assertEqual(payload["coach"]["household_overdue_count"], 0)
        self.assertTrue(payload["coach_wired"])

    def test_gold_overdue_fails_current(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(
                free_dollar=90,
                ltv=0.40,
                expenses=_exp_tabs(
                    {
                        "item": "RH Gold",
                        "date": "2026-08-10",
                        "amount_due": 5,
                    }
                ),
            ),
            config={},
            x_money={},
            solana={},
        )
        self.assertGreater(payload["coach"]["household_overdue_count"], 0)
        self.assertFalse(payload["coach_wired"])

    def test_vault_apy_still_not_product_when_nudge_on(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(
                free_dollar=150,
                ltv=0.40,
                extra_inputs={"vault_apy": 0.029, "hy_vault_apy": 0.029, "product_apy": 0.072},
                extra_snap={
                    "morpho_hy": {
                        "vault_apy": 0.029,
                        "apy_est": 0.029,
                        "product_apy": 0.072,
                    }
                },
            ),
            config={},
            x_money={},
            solana={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 7.2)
        self.assertAlmostEqual(by_id["morpho_hy"]["vault_apy_pct"], 2.9)
        self.assertEqual(by_id["morpho_hy"]["vault_rate_kind"], "vault_reference")
        self.assertIn(MORPHO_HY_VAULT_NE_PRODUCT_NOTE, by_id["morpho_hy"]["notes"])
        self.assertTrue(payload["coach_wired"])
        self.assertEqual(payload["coach_nudge"]["yield_id"], "morpho_hy")
        self.assertAlmostEqual(payload["coach_nudge"]["yield_rate_pct"], 7.2)
        self.assertNotIn("2.9", payload["coach_nudge"]["line"])
        self.assertTrue(rates_are_honest(payload))

    def test_live_beats_settings_beats_seed_when_nudge_on(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(
                free_dollar=150,
                ltv=0.40,
                extra_inputs={
                    "product_apy": 0.064,
                    "x_money_apy_est": 0.055,
                    "rh_usdg_earn_apy_est": 0.05,
                },
            ),
            config={"coinbase_manual": {"product_apy": 0.08}},
            x_money={},
            solana={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertAlmostEqual(by_id["morpho_hy"]["rate_pct"], 6.4)
        self.assertIn("evaluation.inputs.product_apy", by_id["morpho_hy"]["notes"])
        self.assertNotIn("config.coinbase_manual.product_apy", by_id["morpho_hy"]["notes"])
        self.assertTrue(payload["coach_wired"])
        self.assertEqual(payload["coach_nudge"]["yield_id"], "morpho_hy")
        self.assertAlmostEqual(payload["coach_nudge"]["yield_rate_pct"], 6.4)
        self.assertIn("6.4%", payload["coach_nudge"]["line"])
        self.assertTrue(rates_are_honest(payload))

    def test_jr_btc_agentic_cannot_win_park_fcf(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(free_dollar=200, ltv=0.40),
            config={},
            x_money={},
            solana={},
        )
        by_id = {c["id"]: c for c in payload["chips"]}
        self.assertGreater(by_id[BITCOIN_ID]["rate_pct"], by_id["morpho_hy"]["rate_pct"])
        self.assertGreater(by_id[JR_STRCUSX_ID]["rate_pct"], by_id["morpho_hy"]["rate_pct"])
        self.assertGreater(by_id[AGENTIC_FUND_ID]["rate_pct"], by_id["x_money"]["rate_pct"])
        self.assertNotIn(payload["coach_nudge"]["yield_id"], (JR_STRCUSX_ID, BITCOIN_ID, AGENTIC_FUND_ID))
        self.assertIn(payload["coach_nudge"]["yield_id"], ("x_money", "morpho_hy", "usdg_earn"))
        coach = build_fcf_coach(payload["chips"], _current_treasury(free_dollar=200, ltv=0.40))
        self.assertNotIn(coach["nudge"]["yield_id"], (JR_STRCUSX_ID, BITCOIN_ID, AGENTIC_FUND_ID))

    def test_usdg_cancelled_does_not_win_park_fcf(self) -> None:
        payload = build_interest_spectrum(
            treasury=_current_treasury(
                free_dollar=200,
                ltv=0.40,
                extra_inputs={"gold_cancelled": True},
                extra_snap={"robinhood": {"gold_cancelled": True, "gold": False}},
            ),
            config={},
            x_money={},
            solana={},
        )
        ids = {c["id"] for c in payload["chips"]}
        self.assertIn("usdg_earn", ids)
        self.assertNotEqual(payload["coach_nudge"]["yield_id"], "usdg_earn")

    def test_household_overdue_count_ignores_due_soon_and_wells(self) -> None:
        tre = _current_treasury(
            expenses=_exp_tabs(
                {"item": "Capital One", "date": "2026-09-10", "amount_due": 373},
                {"item": "Wells Fargo", "date": "2026-08-01", "amount_due": 200},
                {"item": "Groceries", "date": "2026-08-01", "amount_due": 40},
            )
        )
        n, ids = household_overdue_count(
            tre,
            expenses=tre["snapshot"]["expenses"],
            today=date(2026, 8, 27),
        )
        self.assertEqual(n, 0)
        self.assertEqual(ids, [])


class TestInterestSpectrumPage(unittest.TestCase):
    def test_page_is_two_lane_spectrum(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("<h1>Interest Spectrum</h1>", html)
        self.assertIn("Interest Spectrum · FCC", html)
        self.assertIn("APR / APY · FCC", html)
        self.assertIn('data-layout="two_lane"', html)
        self.assertIn("DEBT COST", html)
        self.assertIn("ASSET YIELD", html)
        self.assertIn("Asset yield (above)", html)
        self.assertIn("Debt cost (below)", html)
        self.assertNotIn("Debt cost (above)", html)
        self.assertIn('y="28" fill="#3dd6b0"', html)
        self.assertIn('y="348" fill="#ff8a6b"', html)
        self.assertIn('chip.kind === "yield" ? "above" : "below"', html)
        self.assertIn("0% → ~${maxPct}%", html)
        self.assertIn("~30%", html)
        self.assertIn("/api/interest-spectrum", html)
        self.assertIn('id="nav-fcc"', html)
        self.assertIn("width: 7.4rem", html)
        self.assertIn("Locked yield seeds always show", html)
        self.assertIn("FCC settings manual", html)
        self.assertIn("honest live books", html)
        self.assertIn("settings fallback", html)
        self.assertIn("seed 7%", html)
        self.assertIn("product_apy", html)
        self.assertIn("vault_apy reference only", html)
        self.assertIn("≠ Coinbase One product", html)
        self.assertIn("USDG HY precedence", html)
        self.assertIn("Coinbase BTC-backed Morpho loan", html)
        self.assertIn("margin/borrow", html)
        self.assertIn("RH margin interest", html)
        self.assertIn("5% up to $50k", html)
        self.assertIn("avgBorrowApy", html)
        self.assertIn("seed ~5%", html)
        self.assertIn("Gold-cancel", html)
        self.assertIn("Bitcoin 30%", html)
        self.assertIn("Agentic Fund 15%", html)
        self.assertIn("est. CAGR", html)
        self.assertIn("not cash APR/APY", html)
        self.assertIn("est_cagr", html)
        self.assertIn("rate_basis", html)
        self.assertNotIn("est. CAGR used as APY", html)
        self.assertNotIn("est. CAGR as APY", html)
        self.assertNotIn("Yield venues appear only when", html)
        self.assertNotIn("Coach threshold", html)
        self.assertNotIn("coach X", html)
        self.assertIn('id="coach-nudge"', html)
        self.assertIn("display-only FCF nudge", html)
        self.assertNotIn("no coach wiring", html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn("CIC", html)
        self.assertNotIn("vercel", html.lower())
        self.assertNotIn("buy token", html.lower())
        self.assertNotIn("place order", html.lower())
        self.assertNotIn("mint JR", html)
        self.assertNotIn("sleeve-while-red", html)
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
        self.assertIn('id="bitcoin"', html)
        self.assertIn('id="agentic-fund"', html)
        self.assertIn('id="panel-solana"', html)
        self.assertIn('id="m-btc-cagr"', html)
        self.assertIn('id="m-agentic-cagr"', html)
        self.assertIn('id="rh-margin"', html)
        self.assertIn('id="m-rh-margin-apr"', html)
        self.assertIn("rh_margin_apr", html)
        self.assertNotIn("Coinbase BTC-backed Morpho loan APR override", html)
        self.assertIn("morpho-live", html)
        self.assertIn("RH margin interest APR override", html)
        self.assertIn("bitcoin_cagr_est", html)
        self.assertIn("agentic_fund_cagr_est", html)
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
        self.assertEqual(data.get("axis", {}).get("yield_lane"), "above")
        self.assertEqual(data.get("axis", {}).get("debt_lane"), "below")
        self.assertEqual(data.get("coach_wired"), (data.get("policy") or {}).get("coach_wired"))
        if data.get("coach_wired"):
            self.assertTrue((data.get("coach_nudge") or {}).get("line"))
            self.assertTrue((data.get("policy") or {}).get("coach_wired"))
        else:
            self.assertFalse((data.get("policy") or {}).get("coach_wired"))
        self.assertTrue(rates_are_honest(data))
        ids = {c["id"] for c in data.get("chips") or []}
        self.assertTrue(set(LOCKED_RATE_BY_ID).issubset(ids))
        self.assertTrue(set(LOCKED_SEED_RATE_BY_ID).issubset(ids))
        self.assertIn(JR_STRCUSX_ID, ids)
        self.assertIn(BITCOIN_ID, ids)
        self.assertIn(AGENTIC_FUND_ID, ids)
        self.assertNotIn(WELLS_OFF_FCC_ID, ids)
        for chip in data.get("chips") or []:
            self.assertIn(chip.get("kind"), ("debt", "yield"))
            if chip.get("kind") == "yield":
                self.assertEqual(chip.get("lane"), "above")
            else:
                self.assertEqual(chip.get("lane"), "below")

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
