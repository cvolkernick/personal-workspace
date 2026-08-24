"""Morpho borrow GraphQL poller: soft-fail, no invent, no HTML scrape."""

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

from treasury.interest_spectrum import (  # noqa: E402
    WELLS_OFF_FCC_ID,
    build_interest_spectrum,
    rates_are_honest,
)
from treasury.morpho_borrow_sync import (  # noqa: E402
    BASE_CHAIN_ID,
    CBBTC_USDC_BASE_MARKET,
    FIELD_NAME,
    SPECTRUM_SEED_APR,
    fetch_morpho_borrow,
    fetch_morpho_borrow_apr,
    parse_market_borrow_apy,
)
from treasury.morpho_hy_sync import normalize_apy_fraction  # noqa: E402
from treasury.policy import evaluate_treasury  # noqa: E402

LIVE_FRACTION = 0.04678863865786198


def _graphql_ok(apr: float = LIVE_FRACTION) -> dict:
    return {
        "data": {
            "marketById": {
                "marketId": CBBTC_USDC_BASE_MARKET,
                "lltv": "860000000000000000",
                "loanAsset": {
                    "symbol": "USDC",
                    "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                },
                "collateralAsset": {
                    "symbol": "cbBTC",
                    "address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
                },
                "state": {"avgBorrowApy": apr, "borrowApy": apr},
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


class TestParseMarketBorrow(unittest.TestCase):
    def test_avg_borrow_apy_fraction(self) -> None:
        apr, err, market = parse_market_borrow_apy(_graphql_ok())
        self.assertIsNone(err)
        self.assertAlmostEqual(apr, LIVE_FRACTION)
        self.assertEqual(market.get("marketId"), CBBTC_USDC_BASE_MARKET)
        self.assertNotAlmostEqual(apr, SPECTRUM_SEED_APR)

    def test_rejects_html_scrape(self) -> None:
        apr, err, _market = parse_market_borrow_apy(
            "<!DOCTYPE html><html><body>Coinbase Borrow 5%</body></html>"
        )
        self.assertIsNone(apr)
        self.assertIn("HTML", err or "")

    def test_rejects_missing_and_graphql_errors(self) -> None:
        apr, err, _ = parse_market_borrow_apy({"data": {"marketById": None}})
        self.assertIsNone(apr)
        self.assertIn("missing", (err or "").lower())
        apr, err, _ = parse_market_borrow_apy({"errors": [{"message": "boom"}]})
        self.assertIsNone(apr)
        self.assertIn("errors", (err or "").lower())
        apr, err, _ = parse_market_borrow_apy(
            {"data": {"marketById": {"state": {"avgBorrowApy": None}}}}
        )
        self.assertIsNone(apr)

    def test_normalize_rejects_junk_does_not_invent_seed(self) -> None:
        self.assertIsNone(normalize_apy_fraction(None))
        self.assertIsNone(normalize_apy_fraction(""))
        self.assertIsNone(normalize_apy_fraction("nope"))
        self.assertIsNone(normalize_apy_fraction(-0.01))
        self.assertIsNone(normalize_apy_fraction(250))
        self.assertAlmostEqual(normalize_apy_fraction(4.68), 0.0468)
        self.assertAlmostEqual(normalize_apy_fraction(0.0468), 0.0468)


class TestFetchSoftFail(unittest.TestCase):
    def test_live_success_does_not_write_seed(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok()))
        row, err = fetch_morpho_borrow_apr(opener=opener)
        self.assertIsNone(err)
        assert row is not None
        self.assertEqual(row["source"], "morpho_graphql")
        self.assertEqual(row["market_id"], CBBTC_USDC_BASE_MARKET)
        self.assertEqual(row["chain_id"], BASE_CHAIN_ID)
        self.assertEqual(row["field"], FIELD_NAME)
        self.assertAlmostEqual(row["apr"], LIVE_FRACTION)
        self.assertAlmostEqual(row["variable_apr"], LIVE_FRACTION)
        self.assertNotAlmostEqual(row["apr"], SPECTRUM_SEED_APR)
        opener.assert_called_once()

    def test_soft_fail_keeps_prior_does_not_invent(self) -> None:
        prior = {
            "source": "morpho_graphql",
            "apr": 0.0487,
            "variable_apr": 0.0487,
            "as_of": "2026-08-22T00:00:00+00:00",
        }
        opener = mock.Mock(side_effect=TimeoutError("timed out"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "morpho_borrow_latest.json"
            row = fetch_morpho_borrow(
                prefer_live=True,
                prior=prior,
                snapshot=path,
                opener=opener,
            )
        self.assertAlmostEqual(row["apr"], 0.0487)
        self.assertAlmostEqual(row["variable_apr"], 0.0487)
        self.assertIn("live_error", row)
        self.assertNotAlmostEqual(row["apr"], SPECTRUM_SEED_APR)
        self.assertFalse(path.is_file())

    def test_soft_fail_without_prior_is_empty_not_seed(self) -> None:
        opener = mock.Mock(side_effect=OSError("offline"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "morpho_borrow_latest.json"
            row = fetch_morpho_borrow(
                prefer_live=True,
                prior=None,
                snapshot=path,
                opener=opener,
            )
        self.assertIsNone(row.get("apr"))
        self.assertIsNone(row.get("variable_apr"))
        self.assertEqual(row.get("source"), "empty")
        self.assertIn("live_error", row)
        payload = build_interest_spectrum(
            treasury={"snapshot": {"morpho_borrow": row}, "evaluation": {"inputs": {}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 5.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertTrue(rates_are_honest(payload))
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})

    def test_html_response_soft_fails_to_prior(self) -> None:
        opener = mock.Mock(
            return_value=_FakeResp(
                "<html>Coinbase Borrow APR 5%</html>",
                content_type="text/html",
            )
        )
        prior = {"apr": 0.044, "variable_apr": 0.044, "source": "morpho_graphql"}
        with tempfile.TemporaryDirectory() as td:
            row = fetch_morpho_borrow(
                prefer_live=True,
                prior=prior,
                snapshot=Path(td) / "mb.json",
                opener=opener,
            )
        self.assertAlmostEqual(row["apr"], 0.044)
        self.assertIn("live_error", row)

    def test_offline_uses_prior_sidecar_not_seed(self) -> None:
        prior = {"apr": 0.047, "variable_apr": 0.047, "source": "morpho_graphql"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mb.json"
            path.write_text(json.dumps(prior), encoding="utf-8")
            row = fetch_morpho_borrow(prefer_live=False, snapshot=path)
        self.assertAlmostEqual(row["apr"], 0.047)
        self.assertNotAlmostEqual(row["apr"], SPECTRUM_SEED_APR)

    def test_live_writes_sidecar_used_as_spectrum_books(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok(0.0468)))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mb.json"
            row = fetch_morpho_borrow(
                prefer_live=True,
                prior=None,
                snapshot=path,
                opener=opener,
            )
            self.assertTrue(path.is_file())
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(saved["apr"], 0.0468)
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 0, "source": "empty"},
                "coinbase_manual": {},
                "morpho_borrow": row,
                "robinhood": {},
            }
        )
        self.assertAlmostEqual(ev["inputs"]["variable_apr"], 0.0468)
        payload = build_interest_spectrum(
            treasury={"evaluation": ev, "snapshot": {"morpho_borrow": row}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_borrow"]
        self.assertAlmostEqual(chip["rate_pct"], 4.68)
        self.assertEqual(chip["source"], "books")
        self.assertTrue(rates_are_honest(payload))

    def test_evaluate_does_not_copy_settings_into_variable_apr(self) -> None:
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 0, "source": "empty"},
                "coinbase_manual": {"variable_apr": 0.08, "morpho_borrow_apr": 0.09},
                "morpho_borrow": {},
                "robinhood": {},
            }
        )
        self.assertIsNone(ev["inputs"].get("variable_apr"))


if __name__ == "__main__":
    unittest.main()
