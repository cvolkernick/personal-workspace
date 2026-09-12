"""Cash Streams mining-unknown copy: real payout-feed error, Pi-timer topology (#687)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.cash_streams import (  # noqa: E402
    BRAIINS_PRODUCER_HINT,
    braiins_unknown_error,
    mining_from_snapshots,
)

AS_OF = "2026-09-11T12:00:00+00:00"
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
START = date(2026, 6, 13)
END = date(2026, 9, 11)
FORBIDDEN = (
    "python3 treasury/braiins_sync.py",
    "run braiins_sync",
    "launchctl",
    "systemctl",
)


def _write_snaps(root: Path, *, braiins=None, coinbase=None) -> None:
    snap = root / "treasury" / "snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    if braiins is not None:
        (snap / "braiins_latest.json").write_text(
            json.dumps(braiins), encoding="utf-8"
        )
    if coinbase is not None:
        (snap / "coinbase_latest.json").write_text(
            json.dumps(coinbase), encoding="utf-8"
        )


def _mine(root: Path):
    return mining_from_snapshots(start=START, end=END, root=root, now=NOW)


def _assert_no_terminal(test: unittest.TestCase, error: str) -> None:
    test.assertTrue(error)
    for needle in FORBIDDEN:
        test.assertNotIn(needle, error)
    test.assertIn(BRAIINS_PRODUCER_HINT, error)
    test.assertIn("braiins-refresh.timer", error)
    test.assertNotIn("python3 treasury/", error)


class TestBraiinsUnknownErrorCopy(unittest.TestCase):
    def test_payouts_error_is_named_in_copy(self) -> None:
        err = braiins_unknown_error(
            "payouts_missing",
            {
                "ok": True,
                "as_of": AS_OF,
                "payouts_error": "HTTP 429 rate limited",
                "partial_errors": {
                    "payouts": "HTTP 429 rate limited",
                    "rewards": "timeout",
                },
            },
        )
        self.assertIn("HTTP 429 rate limited", err)
        self.assertIn("payouts API: HTTP 429 rate limited", err)
        self.assertIn("rewards: timeout", err)
        self.assertIn(AS_OF, err)
        _assert_no_terminal(self, err)

    def test_partial_errors_when_payouts_error_absent(self) -> None:
        err = braiins_unknown_error(
            "payouts_missing",
            {
                "ok": True,
                "as_of": AS_OF,
                "partial_errors": {"payouts": "pool payouts timeout"},
            },
        )
        self.assertIn("pool payouts timeout", err)
        self.assertNotIn("payouts API:", err)
        _assert_no_terminal(self, err)

    def test_kinds_are_distinct(self) -> None:
        missing = braiins_unknown_error("missing_file")
        ok_false = braiins_unknown_error(
            "ok_false", {"ok": False, "error": "No Braiins Pool token"}
        )
        no_payouts = braiins_unknown_error(
            "payouts_missing", {"ok": True, "as_of": AS_OF}
        )
        stale = braiins_unknown_error("stale", {"ok": True, "as_of": AS_OF})
        self.assertIn("no braiins_latest.json", missing)
        self.assertIn("No Braiins Pool token", ok_false)
        self.assertIn("no payouts list", no_payouts)
        self.assertIn("stale", stale)
        self.assertNotEqual(missing, ok_false)
        self.assertNotEqual(ok_false, no_payouts)
        self.assertNotEqual(no_payouts, stale)
        for err in (missing, ok_false, no_payouts, stale):
            _assert_no_terminal(self, err)


class TestMiningFromSnapshotsUnknown(unittest.TestCase):
    def test_snapshot_with_payouts_error_surfaces_api_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": AS_OF,
                    "payouts_error": "HTTP 500 from pool.braiins.com",
                    "last_payout_btc": 0.005,
                },
                coinbase={"as_of": AS_OF, "btc_usd_price": 100000.0},
            )
            got = _mine(root)
        self.assertEqual(got["status"], "unknown")
        self.assertIsNone(got["usd"])
        self.assertIn("HTTP 500 from pool.braiins.com", got["error"])
        self.assertIn("payout history missing", got["error"])
        _assert_no_terminal(self, got["error"])

    def test_snapshot_with_partial_errors_without_payouts_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": AS_OF,
                    "partial_errors": {"payouts": "pool payouts timeout"},
                    "last_payout_btc": 0.005,
                },
                coinbase={"as_of": AS_OF, "btc_usd_price": 100000.0},
            )
            got = _mine(root)
        self.assertEqual(got["status"], "unknown")
        self.assertIn("pool payouts timeout", got["error"])
        self.assertNotIn("payouts API:", got["error"])
        _assert_no_terminal(self, got["error"])

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            got = _mine(Path(tmp))
        self.assertEqual(got["status"], "unknown")
        self.assertIn("no braiins_latest.json", got["error"])
        _assert_no_terminal(self, got["error"])

    def test_ok_false_surfaces_snapshot_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": False,
                    "as_of": AS_OF,
                    "error": "No Braiins Pool token",
                },
            )
            got = _mine(root)
        self.assertEqual(got["status"], "unknown")
        self.assertIn("No Braiins Pool token", got["error"])
        _assert_no_terminal(self, got["error"])

    def test_stale_names_as_of_and_pi_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": "2026-09-10T00:00:00+00:00",
                    "payouts": [],
                },
                coinbase={"as_of": AS_OF, "btc_usd_price": 100000.0},
            )
            got = _mine(root)
        self.assertEqual(got["status"], "unknown")
        self.assertIn("stale", got["error"])
        self.assertIn("2026-09-10T00:00:00+00:00", got["error"])
        self.assertIn("every 4h", got["error"])
        _assert_no_terminal(self, got["error"])

    def test_ok_true_without_payouts_or_payouts_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_snaps(
                root,
                braiins={
                    "ok": True,
                    "as_of": AS_OF,
                    "last_payout_btc": 0.005,
                },
            )
            got = _mine(root)
        self.assertEqual(got["status"], "unknown")
        self.assertIn("no payouts list", got["error"])
        self.assertNotIn("payouts API:", got["error"])
        _assert_no_terminal(self, got["error"])


if __name__ == "__main__":
    unittest.main()
