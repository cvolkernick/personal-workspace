"""Bias spectrum: held book % vs watchlist consider share. No invented targets."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.bias_spectrum import build_bias_spectrum  # noqa: E402


def _fm() -> dict:
    return {
        "ok": True,
        "as_of": "2026-09-01T17:00:00+00:00",
        "analysis": {
            "ok": True,
            "nav_usd": 250.0,
            "equity_market_value_usd": 200.0,
            "weights_of_deployed": {
                "btc_digital_credit": 0.4,
                "stocks_growth": 0.6,
            },
            "targets": {
                "btc_digital_credit_pct": 0.4,
                "stocks_growth_pct": 0.6,
                "band_pct": 0.05,
            },
            "positions": [
                {
                    "symbol": "TSLA",
                    "quantity": 1,
                    "market_value": 80.0,
                    "sleeve": "stocks_growth",
                },
                {
                    "symbol": "MSTR",
                    "quantity": 1,
                    "market_value": 40.0,
                    "sleeve": "btc_digital_credit",
                },
                {
                    "symbol": "BE",
                    "quantity": 1,
                    "market_value": 80.0,
                    "sleeve": "stocks_growth",
                },
            ],
            "watchlist": {
                "entries": [
                    {
                        "symbol": "BE",
                        "name": "Bloom",
                        "priority": "high",
                        "status": "ready",
                        "sleeve_if_owned": "stocks_growth",
                    },
                    {
                        "symbol": "NVDA",
                        "name": "NVIDIA",
                        "priority": "high",
                        "status": "ready",
                        "sleeve_if_owned": "stocks_growth",
                    },
                    {
                        "symbol": "PLTR",
                        "name": "Palantir",
                        "priority": "medium",
                        "status": "ready",
                        "sleeve_if_owned": "stocks_growth",
                    },
                    {
                        "symbol": "EVGO",
                        "name": "EVgo",
                        "priority": "low",
                        "status": "ready",
                        "sleeve_if_owned": "stocks_growth",
                    },
                ]
            },
        },
    }


class TestBiasSpectrumBuilder(unittest.TestCase):
    def test_held_is_pct_of_deployed_equity(self) -> None:
        payload = build_bias_spectrum(fund_manager=_fm(), treasury={})
        self.assertTrue(payload["ok"])
        by_id = {c["id"]: c for c in payload["chips"]}
        tsla = by_id["held-TSLA"]
        self.assertEqual(tsla["kind"], "held")
        self.assertEqual(tsla["lane"], "above")
        self.assertAlmostEqual(tsla["weight_pct"], 40.0)
        self.assertEqual(tsla["weight_basis"], "pct_of_deployed_equity")
        self.assertEqual(tsla["source"], "books")
        self.assertAlmostEqual(by_id["held-MSTR"]["weight_pct"], 20.0)
        self.assertAlmostEqual(by_id["held-BE"]["weight_pct"], 40.0)

    def test_held_watchlist_names_do_not_double_plot_as_consider(self) -> None:
        payload = build_bias_spectrum(fund_manager=_fm(), treasury={})
        ids = {c["id"] for c in payload["chips"]}
        self.assertIn("held-BE", ids)
        self.assertNotIn("consider-BE", ids)
        self.assertIn("consider-NVDA", ids)

    def test_consider_share_from_priority_not_capital(self) -> None:
        payload = build_bias_spectrum(fund_manager=_fm(), treasury={})
        by_id = {c["id"]: c for c in payload["chips"]}
        # Unheld: NVDA high=3, PLTR med=2, EVGO low=1 → total 6
        self.assertAlmostEqual(by_id["consider-NVDA"]["weight_pct"], 50.0)
        self.assertAlmostEqual(by_id["consider-PLTR"]["weight_pct"], 33.33)
        self.assertAlmostEqual(by_id["consider-EVGO"]["weight_pct"], 16.67)
        for cid in ("consider-NVDA", "consider-PLTR", "consider-EVGO"):
            chip = by_id[cid]
            self.assertEqual(chip["kind"], "consider")
            self.assertEqual(chip["lane"], "below")
            self.assertEqual(chip["weight_basis"], "priority_share")
            self.assertFalse(chip["held"])
            self.assertIn("not capital", chip["notes"])

    def test_does_not_invent_per_name_targets(self) -> None:
        payload = build_bias_spectrum(fund_manager=_fm(), treasury={})
        self.assertFalse(payload["policy"]["invented_targets"])
        self.assertTrue(payload["policy"]["held_is_book_weight"])
        self.assertTrue(payload["policy"]["consider_is_priority_share"])
        self.assertTrue(payload["policy"]["sleeve_targets_are_legend_only"])
        self.assertFalse(payload["policy"]["private_watchlist_on_axis"])
        self.assertFalse(payload["policy"]["apr_apy_axis"])
        for chip in payload["chips"]:
            self.assertNotIn("target_pct", chip)
            self.assertNotIn("target_weight", chip)

    def test_zero_or_blank_market_value_is_not_a_held_chip(self) -> None:
        fm = _fm()
        fm["analysis"]["positions"].append(
            {"symbol": "CASHY", "quantity": 1, "market_value": 0, "sleeve": "other"}
        )
        payload = build_bias_spectrum(fund_manager=fm, treasury={})
        ids = {c["id"] for c in payload["chips"]}
        self.assertNotIn("held-CASHY", ids)

    def test_empty_analysis_is_ok_with_error_not_fake_weights(self) -> None:
        payload = build_bias_spectrum(fund_manager={"ok": False}, treasury={})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chips"], [])
        self.assertTrue(payload.get("error"))
        self.assertNotIn("APR", payload["title"])

    def test_axis_is_two_lane_weight_not_apr(self) -> None:
        payload = build_bias_spectrum(fund_manager=_fm(), treasury={})
        axis = payload["axis"]
        self.assertEqual(axis["layout"], "two_lane")
        self.assertEqual(axis["held_lane"], "above")
        self.assertEqual(axis["consider_lane"], "below")
        self.assertEqual(axis["unit"], "relative_weight_pct")
        self.assertGreaterEqual(axis["max_pct"], 40.0)
        self.assertEqual(payload["held_count"], 3)
        self.assertEqual(payload["consider_count"], 3)


if __name__ == "__main__":
    unittest.main()
