"""Bitcoin network hashrate + difficulty snapshot (mempool.space)."""

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

from treasury.btc_network_sync import (  # noqa: E402
    HASHRATE_URL,
    WINDOW,
    btc_network_payload,
    format_difficulty,
    format_hashrate_hs,
    normalize_network,
    write_btc_network_snapshot,
)

FIXTURE = ROOT / "treasury" / "tests" / "fixtures" / "btc_network_mempool.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestFormatters(unittest.TestCase):
    def test_hashrate_eh(self):
        self.assertEqual(format_hashrate_hs(927200699635722500000), "927.2 EH/s")

    def test_difficulty_t(self):
        self.assertEqual(format_difficulty(125807076547197.5), "125.8 T")

    def test_empty(self):
        self.assertEqual(format_hashrate_hs(None), "—")
        self.assertEqual(format_difficulty(""), "—")


class TestNormalize(unittest.TestCase):
    def test_fixture_series_and_current(self):
        raw = _fixture()
        out = normalize_network(raw["hashrate"], raw["adjustment"], as_of="2026-09-05T20:00:00+00:00")
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "mempool.space")
        self.assertEqual(out["window"], WINDOW)
        self.assertTrue(HASHRATE_URL.endswith("/all"))
        self.assertEqual(len(out["hashrate"]), 4)
        self.assertEqual(len(out["difficulty"]), 3)
        self.assertEqual(out["hashrate"][0]["t"], 1693958400)
        self.assertEqual(out["difficulty"][-1]["height"], 963648)
        self.assertAlmostEqual(out["current_hashrate"], 927200699635722500000, delta=1e6)
        self.assertAlmostEqual(out["current_difficulty"], 125807076547197.5, places=1)
        self.assertEqual(out["hashrate_label"], "927.2 EH/s")
        self.assertEqual(out["difficulty_label"], "125.8 T")
        adj = out["adjustment"]
        self.assertEqual(adj["remaining_blocks"], 3)
        self.assertAlmostEqual(adj["difficulty_change_pct"], 1.268, places=3)
        self.assertTrue(str(adj["estimated_retarget_at"]).startswith("2026-"))

    def test_rejects_empty_object(self):
        out = normalize_network({})
        self.assertFalse(out["ok"])
        self.assertIn("no hashrate or difficulty", out["error"])

    def test_rejects_non_object(self):
        out = normalize_network([])
        self.assertFalse(out["ok"])


class TestPayloadCache(unittest.TestCase):
    def test_fresh_snapshot_skips_network(self):
        raw = _fixture()
        snap = normalize_network(raw["hashrate"], raw["adjustment"])
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "btc_network_latest.json"
            write_btc_network_snapshot(snap, path)
            with mock.patch("treasury.btc_network_sync.fetch_btc_network") as fetch:
                out = btc_network_payload(refresh_if_stale=True, path=path)
                fetch.assert_not_called()
            self.assertTrue(out["ok"])
            self.assertEqual(len(out["hashrate"]), 4)

    def test_missing_without_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing.json"
            out = btc_network_payload(refresh_if_stale=False, path=path)
            self.assertFalse(out["ok"])
            self.assertEqual(out["status"], "missing")

    def test_short_window_snapshot_refetches(self):
        raw = _fixture()
        snap = normalize_network(raw["hashrate"], raw["adjustment"])
        snap["window"] = "3y"
        live = dict(snap)
        live["window"] = WINDOW
        live["hashrate_label"] = "refetched"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "btc_network_latest.json"
            write_btc_network_snapshot(snap, path)
            with mock.patch(
                "treasury.btc_network_sync.fetch_btc_network", return_value=live
            ) as fetch:
                out = btc_network_payload(refresh_if_stale=True, path=path)
                fetch.assert_called_once()
            self.assertEqual(out["window"], WINDOW)
            self.assertEqual(out["hashrate_label"], "refetched")


if __name__ == "__main__":
    unittest.main()
