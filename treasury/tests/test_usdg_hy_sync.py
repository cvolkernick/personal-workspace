"""USDG HY GraphQL poller: soft-fail, no invent, no HTML scrape, no post-Gold invent."""

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
    USDG_GOLD_CAVEAT,
    WELLS_OFF_FCC_ID,
    build_interest_spectrum,
    rates_are_honest,
)
from treasury.policy import evaluate_treasury  # noqa: E402
from treasury.usdg_hy_sync import (  # noqa: E402
    ROBINHOOD_CHAIN_ID,
    SPECTRUM_SEED_APY,
    STEAKHOUSE_USDG_VAULT,
    fetch_usdg_hy,
    fetch_usdg_hy_apy,
)

LIVE_FRACTION = 0.03284664799933121


def _graphql_ok(apy: float = LIVE_FRACTION) -> dict:
    return {
        "data": {
            "vaultV2ByAddress": {
                "address": STEAKHOUSE_USDG_VAULT,
                "name": "Steakhouse USDG",
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


class TestFetchSoftFail(unittest.TestCase):
    def test_live_success_does_not_write_seed(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok()))
        row, err = fetch_usdg_hy_apy(opener=opener)
        self.assertIsNone(err)
        assert row is not None
        self.assertEqual(row["source"], "morpho_graphql")
        self.assertEqual(row["product"], "Steakhouse USDG")
        self.assertEqual(row["chain_id"], ROBINHOOD_CHAIN_ID)
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
            path = Path(td) / "usdg_hy_latest.json"
            row = fetch_usdg_hy(
                prefer_live=True,
                prior=prior,
                snapshot=path,
                opener=opener,
            )
        self.assertAlmostEqual(row["apy"], 0.031)
        self.assertAlmostEqual(row["apy_est"], 0.031)
        self.assertIn("live_error", row)
        self.assertNotAlmostEqual(row["apy"], SPECTRUM_SEED_APY)
        self.assertNotAlmostEqual(row["apy"], 0.0)
        self.assertFalse(path.is_file())

    def test_soft_fail_without_prior_is_empty_not_seed_or_post_gold(self) -> None:
        opener = mock.Mock(side_effect=OSError("offline"))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "usdg_hy_latest.json"
            row = fetch_usdg_hy(
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
            treasury={"snapshot": {"usdg_hy": row}, "evaluation": {"inputs": {}}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(chip["rate_pct"], 7.0)
        self.assertEqual(chip["source"], "locked_seed")
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])
        self.assertTrue(rates_are_honest(payload))
        self.assertNotIn(WELLS_OFF_FCC_ID, {c["id"] for c in payload["chips"]})

    def test_html_response_soft_fails_to_prior(self) -> None:
        opener = mock.Mock(
            return_value=_FakeResp(
                "<html>Robinhood Earn estimated 7% APY</html>",
                content_type="text/html",
            )
        )
        prior = {"apy_est": 0.033, "source": "morpho_graphql"}
        with tempfile.TemporaryDirectory() as td:
            row = fetch_usdg_hy(
                prefer_live=True,
                prior=prior,
                snapshot=Path(td) / "uh.json",
                opener=opener,
            )
        self.assertAlmostEqual(row["apy"], 0.033)
        self.assertIn("live_error", row)
        self.assertNotAlmostEqual(row["apy"], SPECTRUM_SEED_APY)

    def test_offline_uses_prior_sidecar_not_seed(self) -> None:
        prior = {"apy": 0.027, "apy_est": 0.027, "source": "morpho_graphql"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "uh.json"
            path.write_text(json.dumps(prior), encoding="utf-8")
            row = fetch_usdg_hy(prefer_live=False, snapshot=path)
        self.assertAlmostEqual(row["apy"], 0.027)
        self.assertNotAlmostEqual(row["apy"], SPECTRUM_SEED_APY)

    def test_live_writes_sidecar_used_as_spectrum_books(self) -> None:
        opener = mock.Mock(return_value=_FakeResp(_graphql_ok(0.0328)))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "uh.json"
            row = fetch_usdg_hy(
                prefer_live=True,
                prior=None,
                snapshot=path,
                opener=opener,
            )
            self.assertTrue(path.is_file())
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(saved["apy_est"], 0.0328)
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 0, "source": "empty"},
                "coinbase_manual": {},
                "usdg_hy": row,
                "robinhood": {},
            }
        )
        self.assertAlmostEqual(ev["inputs"]["rh_usdg_earn_apy_est"], 0.0328)
        payload = build_interest_spectrum(
            treasury={"evaluation": ev, "snapshot": {"usdg_hy": row}},
            config={},
            x_money={},
            solana={},
        )
        chip = {c["id"]: c for c in payload["chips"]}["usdg_earn"]
        self.assertAlmostEqual(chip["rate_pct"], 3.28)
        self.assertEqual(chip["source"], "books")
        self.assertIn(USDG_GOLD_CAVEAT, chip["notes"])
        self.assertTrue(rates_are_honest(payload))

    def test_evaluate_does_not_copy_settings_into_live_apy(self) -> None:
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 0, "source": "empty"},
                "coinbase_manual": {},
                "usdg_hy": {},
                "robinhood": {"usdg_earn_apy_est": 0.08, "gold": False},
            }
        )
        self.assertIsNone(ev["inputs"].get("rh_usdg_earn_apy_est"))

    def test_evaluate_does_not_invent_post_gold_zero(self) -> None:
        ev = evaluate_treasury(
            {
                "coinbase": {"liquid_usdc": 0, "source": "empty"},
                "coinbase_manual": {},
                "usdg_hy": {},
                "robinhood": {"gold": False, "gold_cancelled": True},
            }
        )
        self.assertIsNone(ev["inputs"].get("rh_usdg_earn_apy_est"))


if __name__ == "__main__":
    unittest.main()
