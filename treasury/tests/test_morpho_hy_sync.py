"""Morpho HY GraphQL poller: soft-fail, no invent, no HTML scrape."""

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
from treasury.morpho_hy_sync import (  # noqa: E402
    SPECTRUM_SEED_APY,
    STEAKHOUSE_HY_USDC_VAULT,
    fetch_morpho_hy,
    fetch_morpho_hy_apy,
    normalize_apy_fraction,
    parse_vault_v2_apy,
)
from treasury.policy import evaluate_treasury  # noqa: E402

LIVE_FRACTION = 0.029125068560828957


def _graphql_ok(apy: float = LIVE_FRACTION) -> dict:
    return {
        "data": {
            "vaultV2ByAddress": {
                "address": STEAKHOUSE_HY_USDC_VAULT,
                "name": "Steakhouse High Yield USDC Edition",
                "avgNetApy": apy,
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


class TestParseVaultV2(unittest.TestCase):
    def test_avg_net_apy_fraction(self) -> None:
        apy, err, vault = parse_vault_v2_apy(_graphql_ok())
        self.assertIsNone(err)
        self.assertAlmostEqual(apy, LIVE_FRACTION)
        self.assertEqual(vault.get("name"), "Steakhouse High Yield USDC Edition")
        self.assertNotAlmostEqual(apy, SPECTRUM_SEED_APY)

    def test_rejects_html_scrape(self) -> None:
        apy, err, _vault = parse_vault_v2_apy(
            "<!DOCTYPE html><html><body>Coinbase High Yield 7%</body></html>"
        )
        self.assertIsNone(apy)
        self.assertIn("HTML", err or "")

    def test_rejects_missing_and_graphql_errors(self) -> None:
        apy, err, _ = parse_vault_v2_apy({"data": {"vaultV2ByAddress": None}})
        self.assertIsNone(apy)
        self.assertIn("missing", (err or "").lower())
        apy, err, _ = parse_vault_v2_apy({"errors": [{"message": "boom"}]})
        self.assertIsNone(apy)
        self.assertIn("errors", (err or "").lower())
        apy, err, _ = parse_vault_v2_apy(
            {"data": {"vaultV2ByAddress": {"avgNetApy": None}}}
        )
        self.assertIsNone(apy)

    def test_normalize_rejects_junk_does_not_invent_seed(self) -> None:
        self.assertIsNone(normalize_apy_fraction(None))
        self.assertIsNone(normalize_apy_fraction(""))
        self.assertIsNone(normalize_apy_fraction("nope"))
        self.assertIsNone(normalize_apy_fraction(-0.01))
        self.assertIsNone(normalize_apy_fraction(250))
        self.assertAlmostEqual(normalize_apy_fraction(2.91), 0.0291)
        self.assertAlmostEqual(normalize_apy_fraction(0.0291), 0.0291)


class TestFetchSoftFail(unittest.TestCase):
    def test_live_success_does_not_write_seed(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok()))
        row, err = fetch_morpho_hy_apy(opener=opener)
        self.assertIsNone(err)
        assert row is not None
        self.assertEqual(row["source"], "morpho_graphql")
        self.assertAlmostEqual(row["apy"], LIVE_FRACTION)
        self.assertAlmostEqual(row["apy_est"], LIVE_FRACTION)
        self.assertNotAlmostEqual(row["apy"], SPECTRUM_SEED_APY)
        opener.assert_called_once()

    def test_soft_fail_keeps_prior_does_not_invent(self) -> None:
        prior = {
            "source": "morpho_graphql",
            "apy": 0.031,
            "apy_est": 0.031,
            "as_of": "2026-08-22T00:00:00+00:00",
        }
        opener = mock.Mock(side_effect=TimeoutError("timed out"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "morpho_hy_latest.json"
            row = fetch_morpho_hy(
                prefer_live=True,
                prior=prior,
                snapshot=path,
                opener=opener,
            )
        self.assertAlmostEqual(row["apy"], 0.031)
        self.assertAlmostEqual(row["apy_est"], 0.031)
        self.assertIn("live_error", row)
        self.assertNotAlmostEqual(row["apy"], SPECTRUM_SEED_APY)
        self.assertFalse(path.is_file())

    def test_soft_fail_without_prior_is_empty_not_seed(self) -> None:
        opener = mock.Mock(side_effect=OSError("offline"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "morpho_hy_latest.json"
            row = fetch_morpho_hy(
                prefer_live=True,
                prior=None,
                snapshot=path,
                opener=opener,
            )
        self.assertIsNone(row.get("apy"))
        self.assertIsNone(row.get("apy_est"))
        self.assertEqual(row.get("source"), "empty")
        self.assertIn("live_error", row)
        payload = build_interest_spectrum(
            treasury={"snapshot": {"morpho_hy": row}, "evaluation": {"inputs": {}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 7.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertTrue(rates_are_honest(payload))
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})

    def test_html_response_soft_fails_to_prior(self) -> None:
        opener = mock.Mock(
            return_value=_FakeResp(
                "<html>Coinbase One High Yield 7%</html>",
                content_type="text/html",
            )
        )
        prior = {"apy_est": 0.033, "source": "morpho_graphql"}
        with tempfile.TemporaryDirectory() as td:
            row = fetch_morpho_hy(
                prefer_live=True,
                prior=prior,
                snapshot=Path(td) / "mh.json",
                opener=opener,
            )
        self.assertAlmostEqual(row["apy"], 0.033)
        self.assertIn("live_error", row)

    def test_offline_uses_prior_sidecar_not_seed(self) -> None:
        prior = {"apy": 0.027, "apy_est": 0.027, "source": "morpho_graphql"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mh.json"
            path.write_text(json.dumps(prior), encoding="utf-8")
            row = fetch_morpho_hy(prefer_live=False, snapshot=path)
        self.assertAlmostEqual(row["apy"], 0.027)
        self.assertNotAlmostEqual(row["apy"], SPECTRUM_SEED_APY)

    def test_live_writes_sidecar_used_as_spectrum_books(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok(0.0291)))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mh.json"
            row = fetch_morpho_hy(
                prefer_live=True,
                prior=None,
                snapshot=path,
                opener=opener,
            )
            self.assertTrue(path.is_file())
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(saved["apy_est"], 0.0291)
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 0, "source": "empty"},
                "coinbase_manual": {},
                "morpho_hy": row,
                "robinhood": {},
            }
        )
        self.assertAlmostEqual(ev["inputs"]["vault_apy"], 0.0291)
        payload = build_interest_spectrum(
            treasury={"evaluation": ev, "snapshot": {"morpho_hy": row}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["morpho_hy"]
        self.assertAlmostEqual(chip["rate_pct"], 2.91)
        self.assertEqual(chip["source"], "books")
        self.assertTrue(rates_are_honest(payload))

    def test_evaluate_does_not_copy_settings_into_vault_apy(self) -> None:
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 0, "source": "empty"},
                "coinbase_manual": {"vault_apy": 0.08},
                "morpho_hy": {},
                "robinhood": {},
            }
        )
        self.assertIsNone(ev["inputs"].get("vault_apy"))


if __name__ == "__main__":
    unittest.main()
