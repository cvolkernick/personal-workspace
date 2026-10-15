"""FCC Settings placeholders in treasury/config.json stay present and null-safe.

``coinbase_manual.bitcoin_cagr_est``, ``robinhood.agentic_fund_cagr_est`` and
``robinhood.rh_margin_apr`` are read by ``treasury/interest_spectrum.py``
(``settings_paths``) and by FCC ``index.html`` Settings prefill/save. A null
placeholder must keep each chip on its locked seed (no invented rate).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.interest_spectrum import (  # noqa: E402
    AGENTIC_FUND_ID,
    BITCOIN_ID,
    RH_MARGIN_ID,
    build_interest_spectrum,
)

CONFIG = ROOT / "treasury" / "config.json"
PLACEHOLDERS = (
    ("coinbase_manual", "bitcoin_cagr_est"),
    ("robinhood", "agentic_fund_cagr_est"),
    ("robinhood", "rh_margin_apr"),
)


class TestConfigSettingsPlaceholders(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_placeholders_present(self) -> None:
        for section, key in PLACEHOLDERS:
            with self.subTest(path=f"{section}.{key}"):
                self.assertIn(key, self.cfg[section])

    def test_placeholders_are_read_by_interest_spectrum(self) -> None:
        import treasury.interest_spectrum as isp

        src = Path(isp.__file__).read_text(encoding="utf-8")
        for section, key in PLACEHOLDERS:
            with self.subTest(path=f"{section}.{key}"):
                self.assertIn(f'("config", "{section}", "{key}")', src)

    def test_null_placeholders_keep_locked_seeds(self) -> None:
        cfg = {
            "coinbase_manual": {"bitcoin_cagr_est": None},
            "robinhood": {"agentic_fund_cagr_est": None, "rh_margin_apr": None},
        }
        payload = build_interest_spectrum(
            treasury={}, config=cfg, x_money={}, solana={}
        )
        chips = {c["id"]: c for c in payload["chips"]}
        self.assertAlmostEqual(chips[BITCOIN_ID]["rate_pct"], 30.0)
        self.assertEqual(chips[BITCOIN_ID]["source"], "locked_seed")
        self.assertAlmostEqual(chips[AGENTIC_FUND_ID]["rate_pct"], 15.0)
        self.assertEqual(chips[AGENTIC_FUND_ID]["source"], "locked_seed")
        self.assertAlmostEqual(chips[RH_MARGIN_ID]["rate_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()
