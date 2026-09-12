"""Payout-outlook inference for FCC Mining bar / ETA."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.braiins_sync import (  # noqa: E402
    _compact_payouts,
    _infer_payout_outlook,
    _stamp_payout_prices,
    finalize_payout_prices,
)


class TestPayoutOutlook(unittest.TestCase):
    def test_config_override_0_005_wins_over_empty_history(self):
        out = _infer_payout_outlook(
            {},
            balance_btc=0.0039601,
            daily_reward_avg_btc=0.00018867,
            threshold_override=0.005,
        )
        self.assertEqual(out["threshold_btc"], 0.005)
        self.assertEqual(out["threshold_source"], "config")
        self.assertAlmostEqual(out["progress_pct"], 79.2, places=1)
        self.assertAlmostEqual(out["remaining_btc"], 0.0010399, places=7)
        self.assertAlmostEqual(out["days_to_threshold_est"], 5.5, places=1)

    def test_stale_0_01_override_would_stretch_eta(self):
        """Regression: 2026-08-05 0.01 override must not linger after UI revert."""
        stale = _infer_payout_outlook(
            {},
            balance_btc=0.0039601,
            daily_reward_avg_btc=0.00018867,
            threshold_override=0.01,
        )
        live = _infer_payout_outlook(
            {},
            balance_btc=0.0039601,
            daily_reward_avg_btc=0.00018867,
            threshold_override=0.005,
        )
        self.assertEqual(stale["threshold_btc"], 0.01)
        self.assertLess(live["days_to_threshold_est"], stale["days_to_threshold_est"])
        self.assertGreater(live["progress_pct"], stale["progress_pct"])

    def test_repo_config_is_0_005(self):
        cfg = json.loads((ROOT / "treasury" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["braiins"]["payout_threshold_btc"], 0.005)
        flows = json.loads(
            (ROOT / "investment" / "capital_flows.json").read_text(encoding="utf-8")
        )
        asics = next(s for s in flows["income_sources"] if s["id"] == "asics")
        self.assertEqual(asics["payout_threshold_btc"], 0.005)
        self.assertEqual(flows["integrations"]["braiins_pool"]["payout_threshold_btc"], 0.005)


class TestPayoutHistory(unittest.TestCase):
    RAW = {
        "onchain": [
            {
                "status": "confirmed",
                "amount_sats": 509546,
                "resolved_at_ts": 1788316825,
                "tx_id": "abc",
                "destination": "3K9pyUufLpazzUC1NTD16JkwCouQmF3TLg",
                "trigger_type": "triggered",
            }
        ],
        "lightning": [],
    }

    def test_compact_omits_destination(self) -> None:
        rows = _compact_payouts(self.RAW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tx_id"], "abc")
        self.assertAlmostEqual(rows[0]["amount_btc"], 0.00509546)
        self.assertEqual(rows[0]["at"], "2026-09-02T02:40:25+00:00")
        self.assertNotIn("destination", rows[0])
        self.assertNotIn("destination_redacted", rows[0])

    def test_stamp_preserves_previous_price(self) -> None:
        compact = _compact_payouts(self.RAW)
        first = _stamp_payout_prices(compact, previous=[], price=80000.0)
        self.assertEqual(first[0]["usd_price_at_payout"], 80000.0)
        self.assertEqual(first[0]["usd_at_payout"], 407.64)
        second = _stamp_payout_prices(
            compact, previous=first, price=90000.0
        )
        self.assertEqual(second[0]["usd_price_at_payout"], 80000.0)
        self.assertEqual(second[0]["usd_at_payout"], 407.64)

    def test_finalize_skips_when_payouts_missing(self) -> None:
        snap = {"ok": True, "payouts_error": "HTTP 500"}
        out = finalize_payout_prices(snap, previous={}, price=80000.0)
        self.assertNotIn("payouts", out)

    def test_new_payout_gets_current_price(self) -> None:
        prev = [
            {
                "tx_id": "old",
                "amount_btc": 0.005,
                "usd_price_at_payout": 70000.0,
            }
        ]
        new_rows = [
            {"tx_id": "old", "amount_btc": 0.005},
            {"tx_id": "new", "amount_btc": 0.01},
        ]
        stamped = _stamp_payout_prices(new_rows, previous=prev, price=80000.0)
        by_tx = {r["tx_id"]: r for r in stamped}
        self.assertEqual(by_tx["old"]["usd_price_at_payout"], 70000.0)
        self.assertEqual(by_tx["new"]["usd_price_at_payout"], 80000.0)
        self.assertEqual(by_tx["new"]["usd_at_payout"], 800.0)


if __name__ == "__main__":
    unittest.main()
