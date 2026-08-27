"""Payout-outlook inference for FCC Mining bar / ETA."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.braiins_sync import _infer_payout_outlook  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
