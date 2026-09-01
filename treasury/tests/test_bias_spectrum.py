"""Bias spectrum: new-money consider-share, not current book weight."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.bias_spectrum import build_bias_spectrum  # noqa: E402


def _policy() -> dict:
    return {
        "as_of": "2026-09-01",
        "targets": {
            "btc_digital_credit_pct": 0.4,
            "stocks_growth_pct": 0.6,
            "band_pct": 0.05,
        },
        "allowlist": {"core": ["MSTR", "STRC", "SATA", "TSLA"]},
        "sleeves": {
            "btc_digital_credit": {
                "target_pct": 0.4,
                "symbols": ["MSTR", "STRC", "SATA"],
                "watchlist_symbols": ["STRK"],
                "sub_sleeves": {
                    "digital_credit": {"preferred_core": ["STRC", "SATA"]},
                },
            },
            "stocks_growth": {
                "target_pct": 0.6,
                "symbols": ["TSLA"],
                "watchlist_symbols": ["NVDA", "BE", "PLTR"],
            },
            "energy_opportunistic": {
                "target_pct": None,
                "symbols": [],
                "watchlist_symbols": ["BE"],
            },
        },
    }


def _watchlist() -> dict:
    return {
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
                "symbol": "STRK",
                "name": "Strike Pref",
                "priority": "high",
                "status": "ready",
                "sleeve_if_owned": "btc_digital_credit",
            },
            {
                "symbol": "EVGO",
                "name": "EVgo",
                "priority": "low",
                "status": "monitor",
                "sleeve_if_owned": "stocks_growth",
            },
        ]
    }


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
        },
    }


def _build() -> dict:
    return build_bias_spectrum(
        fund_manager=_fm(),
        treasury={},
        policy=_policy(),
        watchlist=_watchlist(),
    )


class TestBiasSpectrumBuilder(unittest.TestCase):
    def test_axis_is_new_money_not_book_weight(self) -> None:
        payload = _build()
        self.assertTrue(payload["ok"])
        by_id = {c["id"]: c for c in payload["chips"]}
        tsla = by_id["bias-TSLA"]
        # Book is 80/200 = 40%. Axis must not be that.
        self.assertEqual(tsla["book_pct"], 40.0)
        self.assertNotAlmostEqual(tsla["weight_pct"], 40.0)
        self.assertEqual(tsla["weight_basis"], "new_money_consider_share")
        self.assertEqual(tsla["role"], "core")
        self.assertTrue(tsla["held"])
        self.assertEqual(tsla["lane"], "below")
        self.assertEqual(tsla["kind"], "held")
        # Stocks sleeve scores: TSLA 4, NVDA 3, BE 3, PLTR 2 = 12
        # TSLA = 4/12 * 60% = 20
        self.assertAlmostEqual(tsla["weight_pct"], 20.0)

    def test_held_watchlist_stays_on_consider_set(self) -> None:
        payload = _build()
        ids = {c["id"] for c in payload["chips"]}
        self.assertIn("bias-BE", ids)
        self.assertNotIn("held-BE", ids)
        self.assertNotIn("consider-BE", ids)
        be = {c["id"]: c for c in payload["chips"]}["bias-BE"]
        self.assertTrue(be["held"])
        self.assertEqual(be["role"], "watch_high")
        self.assertEqual(be["lane"], "below")
        # BE = 3/12 * 60% = 15
        self.assertAlmostEqual(be["weight_pct"], 15.0)

    def test_preferred_core_outranks_other_btc_core(self) -> None:
        payload = _build()
        by_id = {c["id"]: c for c in payload["chips"]}
        strc = by_id["bias-STRC"]
        mstr = by_id["bias-MSTR"]
        strk = by_id["bias-STRK"]
        self.assertEqual(strc["role"], "preferred_core")
        self.assertEqual(mstr["role"], "core")
        self.assertEqual(strk["role"], "watch_high")
        self.assertEqual(strc["lane"], "above")
        self.assertEqual(mstr["lane"], "above")
        self.assertEqual(strk["lane"], "above")
        # BTC scores: STRC 5, SATA 5, MSTR 4, STRK 3 = 17
        self.assertAlmostEqual(strc["weight_pct"], round(100.0 * 0.4 * 5 / 17, 2))
        self.assertAlmostEqual(mstr["weight_pct"], round(100.0 * 0.4 * 4 / 17, 2))
        self.assertAlmostEqual(strk["weight_pct"], round(100.0 * 0.4 * 3 / 17, 2))
        self.assertGreater(strc["weight_pct"], mstr["weight_pct"])
        self.assertFalse(strc["held"])
        self.assertTrue(mstr["held"])
        self.assertEqual(mstr["book_pct"], 20.0)

    def test_monitor_watchlist_is_off_axis(self) -> None:
        payload = _build()
        ids = {c["id"] for c in payload["chips"]}
        self.assertNotIn("bias-EVGO", ids)

    def test_sleeve_budgets_sum_to_one_hundred(self) -> None:
        payload = _build()
        total = sum(float(c["weight_pct"]) for c in payload["chips"])
        self.assertAlmostEqual(total, 100.0, places=1)
        btc = sum(float(c["weight_pct"]) for c in payload["chips"] if c["lane"] == "above")
        stocks = sum(float(c["weight_pct"]) for c in payload["chips"] if c["lane"] == "below")
        self.assertAlmostEqual(btc, 40.0, places=1)
        self.assertAlmostEqual(stocks, 60.0, places=1)

    def test_does_not_invent_per_name_targets(self) -> None:
        payload = _build()
        self.assertFalse(payload["policy"]["invented_targets"])
        self.assertFalse(payload["policy"]["held_is_book_weight"])
        self.assertTrue(payload["policy"]["axis_is_new_money_consider_share"])
        self.assertTrue(payload["policy"]["book_pct_is_annotation"])
        self.assertTrue(payload["policy"]["sleeve_targets_are_new_money_budget"])
        self.assertTrue(payload["policy"]["forbid_held_only"])
        self.assertFalse(payload["policy"]["private_watchlist_on_axis"])
        self.assertFalse(payload["policy"]["apr_apy_axis"])
        for chip in payload["chips"]:
            self.assertNotIn("target_pct", chip)
            self.assertNotIn("target_weight", chip)

    def test_zero_market_value_is_not_held(self) -> None:
        fm = _fm()
        fm["analysis"]["positions"].append(
            {"symbol": "CASHY", "quantity": 1, "market_value": 0, "sleeve": "other"}
        )
        payload = build_bias_spectrum(
            fund_manager=fm, treasury={}, policy=_policy(), watchlist=_watchlist()
        )
        ids = {c["id"] for c in payload["chips"]}
        self.assertNotIn("bias-CASHY", ids)

    def test_empty_policy_does_not_fake_weights(self) -> None:
        payload = build_bias_spectrum(
            fund_manager={"ok": False},
            treasury={},
            policy={"allowlist": {"core": []}, "sleeves": {}},
            watchlist={"entries": []},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chips"], [])
        self.assertTrue(payload.get("error"))
        self.assertNotIn("APR", payload["title"])

    def test_axis_is_two_lane_new_money_not_apr(self) -> None:
        payload = _build()
        axis = payload["axis"]
        self.assertEqual(axis["layout"], "two_lane")
        self.assertEqual(axis["btc_lane"], "above")
        self.assertEqual(axis["stocks_lane"], "below")
        self.assertEqual(axis["unit"], "new_money_consider_share_pct")
        self.assertGreaterEqual(axis["max_pct"], 20.0)
        self.assertGreaterEqual(payload["held_count"], 1)
        self.assertGreaterEqual(payload["consider_count"], 1)
        self.assertEqual(payload["btc_count"], 4)
        self.assertEqual(payload["stocks_count"], 4)


if __name__ == "__main__":
    unittest.main()
