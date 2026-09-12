"""Pi BTC/USD price producer (#695): public spot, merge, no dual-write."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import fetch_btc_usd_price  # noqa: E402
from treasury.coinbase_price_sync import (  # noqa: E402
    merge_btc_usd_price,
    refresh_coinbase_price_snapshot,
)


class _FakeResp:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestFetchBtcUsdPrice(unittest.TestCase):
    def test_parses_v2_spot_payload(self) -> None:
        body = json.dumps({"data": {"amount": "77251.005", "base": "BTC", "currency": "USD"}})
        with mock.patch("treasury.adapters.urllib.request.urlopen", return_value=_FakeResp(body)):
            price, err = fetch_btc_usd_price()
        self.assertIsNone(err)
        self.assertAlmostEqual(price or 0.0, 77251.005)

    def test_http_error_does_not_invent_price(self) -> None:
        err_obj = HTTPError(
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            503,
            "unavailable",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b"nope"),
        )
        with mock.patch("treasury.adapters.urllib.request.urlopen", side_effect=err_obj):
            price, err = fetch_btc_usd_price()
        self.assertIsNone(price)
        self.assertIn("HTTP 503", err or "")

    def test_url_error(self) -> None:
        with mock.patch(
            "treasury.adapters.urllib.request.urlopen",
            side_effect=URLError("dns"),
        ):
            price, err = fetch_btc_usd_price()
        self.assertIsNone(price)
        self.assertIn("URL error", err or "")

    def test_rejects_non_positive(self) -> None:
        body = json.dumps({"data": {"amount": "0"}})
        with mock.patch("treasury.adapters.urllib.request.urlopen", return_value=_FakeResp(body)):
            price, err = fetch_btc_usd_price()
        self.assertIsNone(price)
        self.assertIn("non-positive", err or "")


class TestMergeBtcUsdPrice(unittest.TestCase):
    def test_preserves_balance_keys_and_recomputes_usd(self) -> None:
        existing = {
            "source": "snapshot",
            "as_of": "2026-09-09T13:05:37+00:00",
            "liquid_usdc": 106.05,
            "liquid_btc": 0.01,
            "btc_usd_price": 79571.99,
            "liquid_btc_usd": 795.72,
            "by_currency": {"USDC": 106.05, "BTC": 0.01},
            "account_count": 19,
            "btc_price_error": "coinbase CLI not found",
        }
        out = merge_btc_usd_price(
            existing, 77251.005, as_of="2026-09-12T22:00:00+00:00"
        )
        self.assertEqual(out["source"], "live")
        self.assertEqual(out["as_of"], "2026-09-12T22:00:00+00:00")
        self.assertAlmostEqual(out["btc_usd_price"], 77251.005)
        self.assertAlmostEqual(out["liquid_usdc"], 106.05)
        self.assertAlmostEqual(out["liquid_btc"], 0.01)
        self.assertAlmostEqual(out["liquid_btc_usd"], 0.01 * 77251.005)
        self.assertEqual(out["account_count"], 19)
        self.assertEqual(out["by_currency"]["USDC"], 106.05)
        self.assertNotIn("btc_price_error", out)

    def test_empty_file_still_writes_price(self) -> None:
        out = merge_btc_usd_price(None, 100000.0, as_of="2026-09-12T22:00:00+00:00")
        self.assertEqual(out["btc_usd_price"], 100000.0)
        self.assertEqual(out["as_of"], "2026-09-12T22:00:00+00:00")
        self.assertNotIn("liquid_btc", out)


class TestRefreshSnapshot(unittest.TestCase):
    def test_success_writes_and_keeps_balances(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "coinbase_latest.json"
            path.write_text(
                json.dumps(
                    {
                        "source": "live",
                        "as_of": "2026-09-09T13:05:37+00:00",
                        "liquid_usdc": 50.0,
                        "liquid_btc": 0.002,
                        "btc_usd_price": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "treasury.coinbase_price_sync.fetch_btc_usd_price",
                return_value=(80000.0, None),
            ):
                report = refresh_coinbase_price_snapshot(path=path)
            self.assertTrue(report["ok"])
            disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertAlmostEqual(disk["btc_usd_price"], 80000.0)
            self.assertAlmostEqual(disk["liquid_usdc"], 50.0)
            self.assertNotEqual(disk["as_of"], "2026-09-09T13:05:37+00:00")

    def test_failure_leaves_as_of_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "coinbase_latest.json"
            before = {
                "source": "live",
                "as_of": "2026-09-09T13:05:37+00:00",
                "btc_usd_price": 79571.99,
                "liquid_usdc": 50.0,
            }
            path.write_text(json.dumps(before), encoding="utf-8")
            with mock.patch(
                "treasury.coinbase_price_sync.fetch_btc_usd_price",
                return_value=(None, "HTTP 503"),
            ):
                report = refresh_coinbase_price_snapshot(path=path)
            self.assertFalse(report["ok"])
            disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(disk["as_of"], "2026-09-09T13:05:37+00:00")
            self.assertAlmostEqual(disk["btc_usd_price"], 79571.99)


if __name__ == "__main__":
    unittest.main()
