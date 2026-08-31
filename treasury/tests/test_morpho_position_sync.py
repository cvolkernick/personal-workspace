"""Morpho wallet position poller: GraphQL parse, overlay, no Settings fallback."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import save_config  # noqa: E402
from treasury.morpho_borrow_sync import CBBTC_USDC_BASE_MARKET  # noqa: E402
from treasury.morpho_position_sync import (  # noqa: E402
    DEFAULT_LLTV,
    DEFAULT_WALLET,
    fetch_morpho_position,
    liquidation_price_btc_usd,
    overlay_manual_with_position,
    overlay_morpho_position_onto_treasury,
    parse_lltv,
    parse_price_variation,
    parse_user_position,
    rewrite_honest_liquidation_price,
)
from treasury.policy import evaluate_treasury  # noqa: E402

LIVE_COLLATERAL_RAW = 358918
LIVE_BORROW_RAW = 122943394
LIVE_COLLATERAL_USD = 280.1704280540971
LIVE_BORROW_USD = 122.94404229168993
LIVE_HEALTH = 1.959719794355175
LIVE_VARIATION = -0.4897229681098162

# 8/30 prism sidecar (Naka FAIL after #438). Invented liq is principal/(coll×0.86).
SIDECAR_830_PRINCIPAL = 122.94
SIDECAR_830_COLLATERAL_BTC = 0.00358918
SIDECAR_830_MARK = 78569.42
SIDECAR_830_VARIATION = -0.4927
SIDECAR_830_INVENT = 39829.47
SIDECAR_830_HONEST = SIDECAR_830_MARK * (1.0 + SIDECAR_830_VARIATION)


def _sidecar_830() -> dict:
    return {
        "source": "morpho_graphql",
        "wallet": DEFAULT_WALLET,
        "as_of": "2026-08-30T12:25:00+00:00",
        "loan_principal_usdc": SIDECAR_830_PRINCIPAL,
        "collateral_btc": SIDECAR_830_COLLATERAL_BTC,
        "collateral_btc_usd": SIDECAR_830_MARK * SIDECAR_830_COLLATERAL_BTC,
        "ltv": SIDECAR_830_PRINCIPAL / (SIDECAR_830_MARK * SIDECAR_830_COLLATERAL_BTC),
        "lltv": 0.86,
        "health_factor": 1.96,
        "price_variation_to_liquidation": SIDECAR_830_VARIATION,
        "liquidation_price_btc_usd": SIDECAR_830_INVENT,
    }


def _graphql_ok() -> dict:
    return {
        "data": {
            "userByAddress": {
                "address": DEFAULT_WALLET,
                "marketPositions": [
                    {
                        "healthFactor": LIVE_HEALTH,
                        "priceVariationToLiquidationPrice": -0.4897229681098162,
                        "market": {
                            "marketId": CBBTC_USDC_BASE_MARKET,
                            "lltv": "860000000000000000",
                            "loanAsset": {"symbol": "USDC", "decimals": 6},
                            "collateralAsset": {"symbol": "cbBTC", "decimals": 8},
                            "state": {
                                "avgBorrowApy": 0.04919972459904276,
                                "borrowApy": 0.04919748744110636,
                            },
                        },
                        "state": {
                            "collateral": LIVE_COLLATERAL_RAW,
                            "collateralUsd": LIVE_COLLATERAL_USD,
                            "borrowAssets": LIVE_BORROW_RAW,
                            "borrowAssetsUsd": LIVE_BORROW_USD,
                            "borrowShares": 110053942516293,
                        },
                    }
                ],
            }
        }
    }


class _FakeResp:
    def __init__(self, payload, content_type: str = "application/json"):
        if isinstance(payload, dict):
            raw = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = payload
        self._raw = raw
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestParsePosition(unittest.TestCase):
    def test_live_payload_matches_onchain_books(self) -> None:
        row, err = parse_user_position(
            _graphql_ok(), wallet=DEFAULT_WALLET, market_id=CBBTC_USDC_BASE_MARKET
        )
        self.assertIsNone(err)
        assert row is not None
        self.assertEqual(row["source"], "morpho_graphql")
        self.assertEqual(row["wallet"], DEFAULT_WALLET)
        self.assertAlmostEqual(row["collateral_btc"], LIVE_COLLATERAL_RAW / 1e8)
        self.assertAlmostEqual(row["collateral_btc_usd"], LIVE_COLLATERAL_USD)
        self.assertAlmostEqual(row["loan_principal_usdc"], LIVE_BORROW_USD)
        self.assertAlmostEqual(row["ltv"], LIVE_BORROW_USD / LIVE_COLLATERAL_USD)
        self.assertAlmostEqual(row["lltv"], 0.86)
        self.assertAlmostEqual(row["health_factor"], LIVE_HEALTH)
        expected_liq = liquidation_price_btc_usd(
            collateral_usd=LIVE_COLLATERAL_USD,
            collateral_btc=LIVE_COLLATERAL_RAW / 1e8,
            price_variation_to_liquidation=LIVE_VARIATION,
        )
        self.assertAlmostEqual(row["liquidation_price_btc_usd"], expected_liq)
        invented_lltv = LIVE_BORROW_USD / ((LIVE_COLLATERAL_RAW / 1e8) * 0.86)
        self.assertNotAlmostEqual(
            row["liquidation_price_btc_usd"], invented_lltv, places=1
        )
        self.assertAlmostEqual(row["variable_apr"], 0.04919972459904276)
        self.assertLess(row["ltv"], 0.45)
        self.assertAlmostEqual(row["price_variation_to_liquidation"], LIVE_VARIATION)

    def test_rejects_html_and_errors(self) -> None:
        row, err = parse_user_position(
            "<!DOCTYPE html><html>loan</html>", wallet=DEFAULT_WALLET
        )
        self.assertIsNone(row)
        self.assertIn("HTML", err or "")
        row, err = parse_user_position({"errors": [{"message": "boom"}]}, wallet="0x1")
        self.assertIsNone(row)
        self.assertIn("errors", (err or "").lower())

    def test_empty_markets_is_zero_loan_not_error(self) -> None:
        payload = {"data": {"userByAddress": {"address": DEFAULT_WALLET, "marketPositions": []}}}
        row, err = parse_user_position(payload, wallet=DEFAULT_WALLET)
        self.assertIsNone(err)
        assert row is not None
        self.assertEqual(row["loan_principal_usdc"], 0.0)
        self.assertEqual(row["collateral_btc"], 0.0)
        self.assertEqual(row["ltv"], 0.0)

    def test_lltv_wad_and_fraction(self) -> None:
        self.assertAlmostEqual(parse_lltv("860000000000000000"), 0.86)
        self.assertAlmostEqual(parse_lltv(0.86), 0.86)
        self.assertAlmostEqual(parse_lltv(None), DEFAULT_LLTV)

    def test_liq_from_graphql_variation_not_lltv_invent(self) -> None:
        mark = LIVE_COLLATERAL_USD / (LIVE_COLLATERAL_RAW / 1e8)
        honest = mark * (1.0 + LIVE_VARIATION)
        got = liquidation_price_btc_usd(
            collateral_usd=LIVE_COLLATERAL_USD,
            collateral_btc=LIVE_COLLATERAL_RAW / 1e8,
            price_variation_to_liquidation=LIVE_VARIATION,
        )
        self.assertAlmostEqual(got, honest)
        invented = LIVE_BORROW_USD / ((LIVE_COLLATERAL_RAW / 1e8) * 0.86)
        self.assertNotAlmostEqual(got, invented, places=1)
        lltv_times_spot = 0.86 * mark
        self.assertGreater(abs(got - lltv_times_spot), 1000)

    def test_liq_null_when_variation_missing(self) -> None:
        payload = _graphql_ok()
        del payload["data"]["userByAddress"]["marketPositions"][0][
            "priceVariationToLiquidationPrice"
        ]
        row, err = parse_user_position(
            payload, wallet=DEFAULT_WALLET, market_id=CBBTC_USDC_BASE_MARKET
        )
        self.assertIsNone(err)
        assert row is not None
        self.assertIsNone(row["liquidation_price_btc_usd"])
        self.assertGreater(row["loan_principal_usdc"], 0)
        self.assertGreater(row["collateral_btc"], 0)

    def test_liq_never_invented_from_ltv_times_collateral(self) -> None:
        self.assertIsNone(
            liquidation_price_btc_usd(
                collateral_usd=LIVE_COLLATERAL_USD,
                collateral_btc=LIVE_COLLATERAL_RAW / 1e8,
                price_variation_to_liquidation=None,
            )
        )
        self.assertAlmostEqual(parse_price_variation(LIVE_VARIATION), LIVE_VARIATION)
        self.assertAlmostEqual(parse_price_variation(-0.4897229681098162 * 1e18), LIVE_VARIATION)


class TestFetchSoftFail(unittest.TestCase):
    def test_live_writes_sidecar(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok()))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pos.json"
            row = fetch_morpho_position(
                prefer_live=True, prior=None, snapshot=path, opener=opener
            )
            self.assertTrue(path.is_file())
        self.assertEqual(row["source"], "morpho_graphql")
        self.assertAlmostEqual(row["loan_principal_usdc"], LIVE_BORROW_USD)

    def test_soft_fail_keeps_prior_not_settings(self) -> None:
        prior = {
            "source": "morpho_graphql",
            "loan_principal_usdc": 122.94,
            "collateral_btc_usd": 280.17,
            "ltv": 0.4388,
        }
        opener = mock.Mock(side_effect=TimeoutError("timed out"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pos.json"
            row = fetch_morpho_position(
                prefer_live=True,
                prior=prior,
                snapshot=path,
                opener=opener,
            )
        self.assertAlmostEqual(row["loan_principal_usdc"], 122.94)
        self.assertIn("live_error", row)
        self.assertFalse(path.is_file())

    def test_html_response_soft_fails_to_prior(self) -> None:
        opener = mock.Mock(
            return_value=_FakeResp("<html>Borrow</html>", content_type="text/html")
        )
        prior = {
            "source": "morpho_graphql",
            "loan_principal_usdc": 100.0,
            "collateral_btc_usd": 250.0,
            "ltv": 0.4,
        }
        with tempfile.TemporaryDirectory() as td:
            row = fetch_morpho_position(
                prefer_live=True,
                prior=prior,
                snapshot=Path(td) / "pos.json",
                opener=opener,
            )
        self.assertAlmostEqual(row["loan_principal_usdc"], 100.0)
        self.assertIn("live_error", row)


class TestOverlayAndPolicy(unittest.TestCase):
    def test_overlay_replaces_stale_settings(self) -> None:
        pos, _ = parse_user_position(_graphql_ok(), wallet=DEFAULT_WALLET)
        man = overlay_manual_with_position(
            {
                "loan_principal_usdc": "166.99",
                "collateral_btc_usd": "365.28",
                "ltv": ".4571",
                "vault_usdc": "147.43",
            },
            pos,
        )
        self.assertAlmostEqual(man["loan_principal_usdc"], LIVE_BORROW_USD)
        self.assertAlmostEqual(man["collateral_btc_usd"], LIVE_COLLATERAL_USD)
        self.assertAlmostEqual(man["ltv"], LIVE_BORROW_USD / LIVE_COLLATERAL_USD)
        self.assertEqual(man["vault_usdc"], "147.43")
        self.assertEqual(man["morpho_wallet"], DEFAULT_WALLET)

    def test_evaluate_prefers_morpho_position_over_settings(self) -> None:
        pos, _ = parse_user_position(_graphql_ok(), wallet=DEFAULT_WALLET)
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 100, "source": "live"},
                "coinbase_manual": {
                    "loan_principal_usdc": 166.99,
                    "collateral_btc_usd": 365.28,
                    "ltv": 0.4571,
                    "vault_usdc": 147.43,
                    "card_balance": 10,
                    "card_available_credit": 490,
                },
                "morpho_position": pos,
                "one_card": {"source": "ynab", "card_balance": 10},
                "robinhood": {
                    "buying_power": 1000,
                    "cash": 10,
                    "equity_value": 5000,
                    "source": "live",
                },
            }
        )
        self.assertAlmostEqual(ev["inputs"]["loan_principal_usdc"], LIVE_BORROW_USD)
        self.assertAlmostEqual(ev["inputs"]["collateral_btc_usd"], LIVE_COLLATERAL_USD)
        self.assertAlmostEqual(ev["inputs"]["ltv"], LIVE_BORROW_USD / LIVE_COLLATERAL_USD)
        self.assertIsNotNone(ev["inputs"]["liquidation_price_btc_usd"])
        self.assertAlmostEqual(
            ev["inputs"]["liquidation_price_btc_usd"], pos["liquidation_price_btc_usd"]
        )
        invented = LIVE_BORROW_USD / ((LIVE_COLLATERAL_RAW / 1e8) * 0.86)
        self.assertNotAlmostEqual(
            ev["inputs"]["liquidation_price_btc_usd"], invented, places=1
        )
        self.assertEqual(ev["stress"]["coinbase_ltv"], "green")
        self.assertNotIn("loan_principal_usdc", ev["data_quality"]["missing_manual_fields"])
        self.assertNotIn("ltv", ev["data_quality"]["missing_manual_fields"])

    def test_evaluate_keeps_liq_key_null_without_invent(self) -> None:
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 100, "btc_usd_price": 78000, "source": "live"},
                "coinbase_manual": {"vault_usdc": 147.43, "card_balance": 10},
                "one_card": {"source": "ynab", "card_balance": 10},
                "robinhood": {
                    "buying_power": 1000,
                    "cash": 10,
                    "equity_value": 5000,
                    "source": "live",
                },
            }
        )
        self.assertIn("liquidation_price_btc_usd", ev["inputs"])
        self.assertIsNone(ev["inputs"]["liquidation_price_btc_usd"])

    def test_overlay_restores_position_and_inputs_from_graphql(self) -> None:
        pos, err = parse_user_position(_graphql_ok(), wallet=DEFAULT_WALLET)
        self.assertIsNone(err)
        treasury = {
            "snapshot": {"coinbase_manual": {"vault_usdc": "47.44"}},
            "evaluation": {"inputs": {"btc_usd_price": 78000}},
        }
        with tempfile.TemporaryDirectory() as td:
            out = overlay_morpho_position_onto_treasury(
                treasury,
                prefer_live=False,
                prior=pos,
                snapshot=Path(td) / "pos.json",
            )
        self.assertEqual(out["snapshot"]["morpho_position"]["source"], "morpho_graphql")
        self.assertAlmostEqual(
            out["snapshot"]["morpho_position"]["liquidation_price_btc_usd"],
            pos["liquidation_price_btc_usd"],
        )
        self.assertAlmostEqual(
            out["evaluation"]["inputs"]["liquidation_price_btc_usd"],
            pos["liquidation_price_btc_usd"],
        )
        self.assertAlmostEqual(out["snapshot"]["coinbase_manual"]["loan_principal_usdc"], LIVE_BORROW_USD)
        self.assertEqual(out["snapshot"]["coinbase_manual"]["vault_usdc"], "47.44")

    def test_overlay_leaves_liq_null_when_graphql_misses(self) -> None:
        treasury = {
            "snapshot": {"coinbase_manual": {}},
            "evaluation": {"inputs": {"btc_usd_price": 78000}},
        }
        with tempfile.TemporaryDirectory() as td:
            out = overlay_morpho_position_onto_treasury(
                treasury,
                prefer_live=False,
                prior={"source": "empty"},
                snapshot=Path(td) / "pos.json",
            )
        self.assertIn("liquidation_price_btc_usd", out["evaluation"]["inputs"])
        self.assertIsNone(out["evaluation"]["inputs"]["liquidation_price_btc_usd"])

    def test_stale_830_sidecar_must_not_win_over_live_graphql(self) -> None:
        """GET default (prefer_live unset) must not paint the 8/30 LLTV invent."""
        live_honest = liquidation_price_btc_usd(
            collateral_usd=LIVE_COLLATERAL_USD,
            collateral_btc=LIVE_COLLATERAL_RAW / 1e8,
            price_variation_to_liquidation=LIVE_VARIATION,
        )
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok()))
        treasury = {
            "snapshot": {"coinbase_manual": {"vault_usdc": "47.44"}},
            "evaluation": {"inputs": {"btc_usd_price": 78000}},
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "morpho_position_latest.json"
            path.write_text(json.dumps(_sidecar_830()), encoding="utf-8")
            out = overlay_morpho_position_onto_treasury(
                treasury,
                prior=_sidecar_830(),
                snapshot=path,
                opener=opener,
            )
            rewritten = json.loads(path.read_text(encoding="utf-8"))
        opener.assert_called()
        painted = out["evaluation"]["inputs"]["liquidation_price_btc_usd"]
        self.assertNotAlmostEqual(painted, SIDECAR_830_INVENT, places=1)
        self.assertAlmostEqual(painted, live_honest)
        self.assertAlmostEqual(
            out["snapshot"]["morpho_position"]["liquidation_price_btc_usd"],
            live_honest,
        )
        invented_live = LIVE_BORROW_USD / ((LIVE_COLLATERAL_RAW / 1e8) * 0.86)
        self.assertNotAlmostEqual(painted, invented_live, places=1)
        self.assertAlmostEqual(rewritten["liquidation_price_btc_usd"], live_honest)

    def test_stale_composite_position_still_hits_live_graphql(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok()))
        live_honest = liquidation_price_btc_usd(
            collateral_usd=LIVE_COLLATERAL_USD,
            collateral_btc=LIVE_COLLATERAL_RAW / 1e8,
            price_variation_to_liquidation=LIVE_VARIATION,
        )
        treasury = {
            "snapshot": {"morpho_position": _sidecar_830()},
            "evaluation": {"inputs": {"btc_usd_price": 78000}},
        }
        with tempfile.TemporaryDirectory() as td:
            out = overlay_morpho_position_onto_treasury(
                treasury,
                snapshot=Path(td) / "pos.json",
                opener=opener,
            )
        opener.assert_called()
        painted = out["evaluation"]["inputs"]["liquidation_price_btc_usd"]
        self.assertNotAlmostEqual(painted, SIDECAR_830_INVENT, places=1)
        self.assertAlmostEqual(painted, live_honest)

    def test_stale_830_sidecar_lltv_invent_rewritten_when_live_misses(self) -> None:
        opener = mock.Mock(side_effect=TimeoutError("timed out"))
        treasury = {
            "snapshot": {},
            "evaluation": {"inputs": {"btc_usd_price": 78000}},
        }
        with tempfile.TemporaryDirectory() as td:
            out = overlay_morpho_position_onto_treasury(
                treasury,
                prior=_sidecar_830(),
                snapshot=Path(td) / "pos.json",
                opener=opener,
            )
        painted = out["evaluation"]["inputs"]["liquidation_price_btc_usd"]
        self.assertNotAlmostEqual(painted, SIDECAR_830_INVENT, places=1)
        self.assertAlmostEqual(painted, SIDECAR_830_HONEST, places=2)
        self.assertIsNone(
            rewrite_honest_liquidation_price(
                {
                    "source": "morpho_graphql",
                    "loan_principal_usdc": SIDECAR_830_PRINCIPAL,
                    "collateral_btc": SIDECAR_830_COLLATERAL_BTC,
                    "collateral_btc_usd": SIDECAR_830_MARK * SIDECAR_830_COLLATERAL_BTC,
                    "ltv": 0.44,
                    "liquidation_price_btc_usd": SIDECAR_830_INVENT,
                }
            )["liquidation_price_btc_usd"]
        )

    def test_save_config_strips_loan_keys_keeps_wallet(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "policy": {},
                        "morpho": {"wallet": DEFAULT_WALLET},
                        "coinbase_manual": {
                            "loan_principal_usdc": "166.99",
                            "collateral_btc_usd": "365.28",
                            "ltv": "0.45",
                            "variable_apr": "0.05",
                            "vault_usdc": "47.44",
                        },
                        "robinhood": {},
                        "ynab": {},
                    }
                ),
                encoding="utf-8",
            )
            save_config(
                {"coinbase_manual": {"vault_usdc": "50", "loan_principal_usdc": "1"}},
                path=path,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("loan_principal_usdc", saved["coinbase_manual"])
        self.assertNotIn("ltv", saved["coinbase_manual"])
        self.assertNotIn("variable_apr", saved["coinbase_manual"])
        self.assertEqual(saved["coinbase_manual"]["vault_usdc"], "50")
        self.assertEqual(saved["morpho"]["wallet"], DEFAULT_WALLET)


if __name__ == "__main__":
    unittest.main()
