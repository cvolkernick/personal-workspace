"""Position dossier: stance + research for one consider-set ticker."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.position_dossier import build_position_dossier  # noqa: E402


def _policy() -> dict:
    return {
        "as_of": "2026-09-01",
        "targets": {
            "btc_digital_credit_pct": 0.4,
            "stocks_growth_pct": 0.6,
        },
        "allowlist": {"core": ["MSTR", "STRC", "SATA", "TSLA"]},
        "sleeves": {
            "btc_digital_credit": {
                "target_pct": 0.4,
                "symbols": ["MSTR", "STRC", "SATA"],
                "watchlist_symbols": ["STRK"],
                "sub_sleeves": {
                    "digital_credit": {
                        "preferred_core": ["STRC", "SATA"],
                        "notes": "Small bias inside the 40% stack toward STRC/SATA.",
                    }
                },
            },
            "stocks_growth": {
                "target_pct": 0.6,
                "symbols": ["TSLA"],
                "watchlist_symbols": ["NVDA", "BE"],
            },
        },
    }


def _watchlist() -> dict:
    return {
        "entries": [
            {
                "symbol": "BE",
                "name": "Bloom Energy",
                "priority": "high",
                "status": "ready",
                "sleeve_if_owned": "stocks_growth",
                "thesis_fit": "AI power equipment",
                "last_verdict": "ready_consider_no_size",
                "notes": "Fuel-cost overlay. Not pass.",
                "last_deep_dive": "2026-08-31",
                "last_deep_dive_path": "investment/research/BE_deep_dive.md",
            },
            {
                "symbol": "NVDA",
                "name": "NVIDIA",
                "priority": "high",
                "status": "ready",
                "sleeve_if_owned": "stocks_growth",
            },
            {
                "symbol": "STRK",
                "name": "Strike Pref",
                "priority": "high",
                "status": "ready",
                "sleeve_if_owned": "btc_digital_credit",
                "last_verdict": "ready_consider_no_size_until_free_capital",
            },
        ]
    }


def _fm() -> dict:
    return {
        "ok": True,
        "as_of": "2026-09-01T00:00:00Z",
        "analysis": {
            "ok": True,
            "nav_usd": 200.0,
            "equity_market_value_usd": 200.0,
            "positions": [
                {
                    "symbol": "STRC",
                    "quantity": 1,
                    "market_value": 6.0,
                    "sleeve": "btc_digital_credit",
                },
                {
                    "symbol": "TSLA",
                    "quantity": 1,
                    "market_value": 80.0,
                    "sleeve": "stocks_growth",
                },
            ],
        },
    }


def _dossier(symbol: str) -> dict:
    return build_position_dossier(
        symbol,
        fund_manager=_fm(),
        treasury={},
        policy=_policy(),
        watchlist=_watchlist(),
    )


class TestPositionDossier(unittest.TestCase):
    def test_invalid_symbol(self) -> None:
        bad = build_position_dossier("../etc/passwd")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "invalid symbol")

    def test_strc_is_preferred_core_even_without_dive(self) -> None:
        data = _dossier("STRC")
        self.assertTrue(data["ok"])
        self.assertEqual(data["symbol"], "STRC")
        self.assertTrue(data["in_consider_set"])
        st = data["stance"]
        self.assertEqual(st["role"], "preferred_core")
        self.assertTrue(st["preferred_core"])
        self.assertTrue(st["held"])
        self.assertEqual(st["sleeve"], "btc_digital_credit")
        self.assertFalse(st["auto_buy"])
        self.assertIn("Preferred-core", st["headline"])
        self.assertIn("next dollar", st["picture"])
        self.assertTrue(any("STRC" in x for x in data["policy_excerpts"]))
        self.assertEqual(data["chip"]["deep_link"], "position.html?symbol=STRC")

    def test_be_watchlist_stance(self) -> None:
        data = _dossier("BE")
        self.assertTrue(data["ok"])
        self.assertTrue(data["in_consider_set"])
        st = data["stance"]
        self.assertEqual(st["role"], "watch_high")
        self.assertFalse(st["core_allowlist"])
        self.assertEqual(data["watchlist"]["last_verdict"], "ready_consider_no_size")
        self.assertIn("AI power", data["watchlist"]["thesis_fit"])

    def test_unknown_name_is_ok_not_crash(self) -> None:
        data = _dossier("ZZZZ")
        self.assertTrue(data["ok"])
        self.assertFalse(data["in_consider_set"])
        self.assertEqual(data["stance"]["role"], None)

    def test_jr_strcusx_files_do_not_hijack_strc(self) -> None:
        data = _dossier("STRC")
        for hit in data["related"]:
            name = Path(str(hit.get("path") or "")).name.upper()
            self.assertFalse(name.startswith("JR_"), name)
            self.assertNotIn("STRCUSX", name)

    def test_related_skips_other_tickers_dives(self) -> None:
        data = _dossier("STRC")
        for hit in data["related"]:
            name = Path(str(hit.get("path") or "")).name.upper()
            if name.endswith("_DEEP_DIVE.MD"):
                self.assertTrue(name.startswith("STRC_"), name)


if __name__ == "__main__":
    unittest.main()
