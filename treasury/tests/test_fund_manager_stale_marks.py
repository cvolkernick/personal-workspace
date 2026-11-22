"""Offline writers keep the last market marks and never price a book at cost."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from treasury.fund_manager import (
    QUOTE_ENV_KEYS,
    apply_broker_quote_env,
    evaluate_fund_manager,
    load_fund_policy,
    snapshot_has_market_marks,
    write_fund_manager_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]


def _book():
    return {
        "agentic": {
            "account_number_last4": "1752",
            "agentic_allowed": True,
            "cash": 0.18,
            "buying_power": 0.18,
            "total_value": 435.49,
            "positions": [
                {"symbol": "MARA", "quantity": 0.712517, "average_buy_price": 11.23},
                {"symbol": "TSLA", "quantity": 1, "average_buy_price": 100},
            ],
        }
    }


def _prior():
    return {
        "as_of": "2026-11-21T21:00:00+00:00",
        "analysis": {
            "as_of": "2026-11-21T21:00:00+00:00",
            "valuation": "market_value",
            "positions": [
                {
                    "symbol": "MARA",
                    "quantity": 0.712517,
                    "market_price": 6.495,
                    "market_value": round(0.712517 * 6.495, 4),
                    "valuation": "market_value",
                },
                {
                    "symbol": "TSLA",
                    "quantity": 1,
                    "market_price": 200.0,
                    "market_value": 200.0,
                    "valuation": "market_value",
                },
            ],
        },
    }


class TestCarryForwardMarks(unittest.TestCase):
    def test_no_client_keeps_prior_prices_and_flags_stale(self):
        fm = evaluate_fund_manager(
            rh_snapshot=_book(),
            policy=load_fund_policy(),
            quotes={},
            prior_snapshot=_prior(),
        )
        rows = {p["symbol"]: p for p in fm["analysis"]["positions"]}
        self.assertAlmostEqual(rows["MARA"]["market_price"], 6.495, places=4)
        self.assertAlmostEqual(rows["MARA"]["market_value"], 0.712517 * 6.495, places=4)
        self.assertNotAlmostEqual(rows["MARA"]["market_value"], 0.712517 * 11.23, places=2)
        self.assertAlmostEqual(rows["TSLA"]["market_price"], 200.0, places=4)
        self.assertNotAlmostEqual(rows["TSLA"]["market_value"], 100.0, places=2)
        self.assertTrue(rows["MARA"]["price_stale"])
        self.assertEqual(rows["MARA"]["price_as_of"], "2026-11-21T21:00:00+00:00")
        self.assertTrue(fm["analysis"]["price_stale"])
        self.assertEqual(fm["analysis"]["price_as_of"], "2026-11-21T21:00:00+00:00")
        self.assertEqual(fm["analysis"]["valuation"], "carried_mark")
        weights = fm["analysis"]["weights_of_deployed"]
        self.assertIsNotNone(weights["btc_digital_credit"])
        self.assertIsNotNone(weights["stocks_growth"])
        self.assertNotIn("11.23", json.dumps(rows["MARA"]))

    def test_cost_basis_snapshot_is_not_a_price(self):
        prior = {
            "analysis": {
                "as_of": "2026-11-21T21:00:00+00:00",
                "positions": [
                    {
                        "symbol": "MARA",
                        "quantity": 0.712517,
                        "average_buy_price": 11.23,
                        "market_price": 11.23,
                        "market_value": round(0.712517 * 11.23, 4),
                        "valuation": "cost_basis_estimate",
                    }
                ],
            }
        }
        fm = evaluate_fund_manager(
            rh_snapshot=_book(),
            policy=load_fund_policy(),
            quotes={},
            prior_snapshot=prior,
        )
        mara = next(p for p in fm["analysis"]["positions"] if p["symbol"] == "MARA")
        self.assertIsNone(mara["market_value"])
        self.assertIsNone(mara["market_price"])
        self.assertNotEqual(mara.get("valuation"), "carried_mark")
        self.assertFalse(snapshot_has_market_marks(prior))

    def test_no_prior_priced_snapshot_stays_price_missing(self):
        fm = evaluate_fund_manager(
            rh_snapshot=_book(),
            policy=load_fund_policy(),
            quotes={},
            prior_snapshot={},
        )
        mara = next(p for p in fm["analysis"]["positions"] if p["symbol"] == "MARA")
        self.assertIsNone(mara["market_value"])
        self.assertIsNone(mara["market_price"])
        self.assertIsNone(fm["analysis"]["weights_of_deployed"]["stocks_growth"])
        self.assertNotIn("price_stale", mara)

    def test_priced_snapshot_is_not_replaced_by_an_unpriced_one(self):
        import tempfile

        priced = evaluate_fund_manager(
            rh_snapshot=_book(),
            policy=load_fund_policy(),
            quotes={},
            prior_snapshot=_prior(),
        )
        unpriced = evaluate_fund_manager(
            rh_snapshot=_book(),
            policy=load_fund_policy(),
            quotes={},
            prior_snapshot={},
        )
        self.assertTrue(snapshot_has_market_marks(priced))
        self.assertFalse(snapshot_has_market_marks(unpriced))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fund_manager_latest.json"
            write_fund_manager_snapshot(priced, path)
            write_fund_manager_snapshot(unpriced, path)
            kept = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(kept["analysis"]["price_stale"])
        self.assertAlmostEqual(kept["analysis"]["positions"][0]["market_price"], 6.495, places=4)
        self.assertNotEqual(kept["analysis"]["valuation"], "cost_basis_estimate")

    def test_stale_as_of_stays_on_the_last_real_quote(self):
        prior = _prior()
        prior["analysis"]["price_stale"] = True
        prior["analysis"]["price_as_of"] = "2026-11-20T15:00:00+00:00"
        prior["as_of"] = "2026-11-21T21:00:00+00:00"
        prior["analysis"]["as_of"] = "2026-11-21T21:00:00+00:00"
        fm = evaluate_fund_manager(
            rh_snapshot=_book(),
            policy=load_fund_policy(),
            quotes={},
            prior_snapshot=prior,
        )
        self.assertEqual(fm["analysis"]["price_as_of"], "2026-11-20T15:00:00+00:00")


class TestBrokerQuoteEnv(unittest.TestCase):
    def test_env_file_matches_bp_poll_drop_in_and_writers(self):
        env = (ROOT / "treasury" / "deploy" / "fm-broker-quote.env").read_text(encoding="utf-8")
        self.assertIn("FM_BROKER_ADAPTER=treasury.fund_manager_broker:submit", env)
        self.assertIn("FM_BROKER_CLIENT=grok_mcp", env)
        self.assertIn("FM_BROKER_GROK_BIN=/home/prism-agent/.grok/bin/grok", env)
        runbook = (ROOT / "investment" / "FUND_MANAGER_RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("FM_BROKER_ADAPTER=treasury.fund_manager_broker:submit", runbook)
        self.assertIn("FM_BROKER_CLIENT=grok_mcp", runbook)
        self.assertIn("FM_BROKER_GROK_BIN=/home/prism-agent/.grok/bin/grok", runbook)
        for rel in (
            "treasury/rh_refresh.sh",
            "treasury/braiins_refresh.sh",
            "treasury/coinbase_price_refresh.sh",
            "treasury/cb_solana_refresh.sh",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("broker_quote_env.sh", text, rel)
        for rel in (
            "treasury/deploy/solana-refresh.service",
            "treasury/deploy/braiins-refresh.service",
            "treasury/deploy/coinbase-price-refresh.service",
            "treasury/deploy/rh-refresh.service",
            "treasury/deploy/drop-ins/user/solana-refresh.service.d/20-broker-quote.conf",
            "treasury/deploy/drop-ins/user/financial-command.service.d/20-broker-quote.conf",
            "treasury/deploy/drop-ins/system/braiins-refresh.service.d/20-broker-quote.conf",
            "treasury/deploy/drop-ins/system/coinbase-price-refresh.service.d/20-broker-quote.conf",
            "treasury/deploy/drop-ins/system/rh-refresh.service.d/20-broker-quote.conf",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("fm-broker-quote.env", text, rel)
        helper = (ROOT / "treasury/deploy/broker_quote_env.sh").read_text(encoding="utf-8")
        self.assertIn("fm-broker-quote.env", helper)
        self.assertNotIn("order_gate", helper)
        self.assertNotIn("order_gate", env)

    def test_apply_fills_unset_vars_and_does_not_override(self):
        path = ROOT / "treasury" / "deploy" / "fm-broker-quote.env"
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "", "FM_BROKER_CLIENT": "keep-me"}, clear=False):
            for key in QUOTE_ENV_KEYS:
                if key != "FM_BROKER_CLIENT":
                    os.environ.pop(key, None)
            apply_broker_quote_env(path)
            self.assertEqual(os.environ["FM_BROKER_CLIENT"], "keep-me")
            self.assertEqual(os.environ["FM_BROKER_ADAPTER"], "treasury.fund_manager_broker:submit")
            self.assertEqual(os.environ["FM_BROKER_GROK_BIN"], "/home/prism-agent/.grok/bin/grok")
            os.environ.pop("FM_BROKER_ADAPTER", None)
            os.environ.pop("FM_BROKER_GROK_BIN", None)


if __name__ == "__main__":
    unittest.main()
