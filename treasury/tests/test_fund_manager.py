"""Tests for agentic fund manager policy + sleeve weights."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unittest import mock

from treasury.fund_manager import (  # noqa: E402
    analyze_agentic_book,
    append_decision,
    load_decision_log,
    load_fund_policy,
    load_watchlist,
    notify_if_needed,
    rules_based_review,
    sleeve_for_symbol,
    watchlist_summary,
)


class TestFundPolicy(unittest.TestCase):
    def test_policy_v1_autopilot_agentic_only(self):
        p = load_fund_policy()
        self.assertEqual(p["account"]["scope"], "agentic_only")
        self.assertFalse(p["approval"]["require_user_confirm"])
        self.assertIsNone(p["limits"]["max_single_order_notional_usd"])
        self.assertAlmostEqual(p["targets"]["btc_digital_credit_pct"], 0.4)
        self.assertAlmostEqual(p["targets"]["stocks_growth_pct"], 0.6)
        self.assertIn("BITA", p["sleeves"]["btc_digital_credit"]["symbols"])
        self.assertNotIn("BITA", p["sleeves"]["stocks_growth"]["symbols"])
        self.assertIn("TSLA", p["sleeves"]["stocks_growth"]["symbols"])
        self.assertEqual(p["cadence"]["reviews_per_day"], 1)
        self.assertIsNone(p["cadence"].get("max_trades_per_day"))
        self.assertIsNone(p["cadence"].get("max_trades_per_review"))
        self.assertFalse(p["approval"].get("require_owner_feedback", False))
        self.assertEqual(p["rationale"].get("owner_intervention"), "optional")
        self.assertTrue(p["automation"].get("unattended"))
        self.assertTrue(p["team"]["enabled"])
        self.assertTrue(p["rationale"]["required_on_every_decision"])
        self.assertTrue(p["team"]["roles"]["executor"]["writes_orders"])
        self.assertFalse(p["team"]["roles"]["critic"]["writes_orders"])
        # Uniform research/rotate — size-invariant deploys
        proc = p.get("process") or {}
        self.assertTrue(proc.get("uniform_for_all_nav"))
        self.assertTrue(proc.get("size_invariant"))
        self.assertTrue((proc.get("research_rotate") or {}).get("required"))
        self.assertTrue((proc.get("research_rotate") or {}).get("forbid_held_only_default"))
        self.assertEqual(p["rationale"].get("owner_feedback_timing"), "after_pass")

    def test_sleeve_tags(self):
        p = load_fund_policy()
        self.assertEqual(sleeve_for_symbol("BITA", p), "btc_digital_credit")
        self.assertEqual(sleeve_for_symbol("mstr", p), "btc_digital_credit")
        self.assertEqual(sleeve_for_symbol("TSLA", p), "stocks_growth")
        self.assertEqual(sleeve_for_symbol("XYZ", p), "other")
        # Watchlist energy candidate maps to stocks sleeve if sized
        self.assertEqual(sleeve_for_symbol("BE", p), "stocks_growth")

    def test_watchlist_be_energy(self):
        wl = load_watchlist()
        symbols = {
            (e.get("symbol") or "").upper() for e in (wl.get("entries") or [])
        }
        self.assertIn("BE", symbols)
        self.assertFalse((wl.get("policy") or {}).get("auto_buy", True))
        p = load_fund_policy()
        self.assertEqual((p.get("watchlist") or {}).get("path"), "investment/watchlist.json")
        summary = watchlist_summary(p)
        self.assertIn("BE", summary["symbols"])
        self.assertGreaterEqual(summary["count"], 1)

    def test_watchlist_nvda_open_weight_note(self):
        """NVDA thesis note mentions open-source/open-weight; not a held or buy claim."""
        wl = load_watchlist()
        nvda = next(
            (
                e
                for e in (wl.get("entries") or [])
                if (e.get("symbol") or "").upper() == "NVDA"
            ),
            None,
        )
        self.assertIsNotNone(nvda, "NVDA public watchlist entry missing")
        self.assertEqual(nvda.get("status"), "ready")
        self.assertNotEqual((nvda.get("status") or "").lower(), "buy")
        self.assertNotEqual((nvda.get("status") or "").lower(), "held")
        self.assertFalse(bool(nvda.get("held")))
        thesis_notes = nvda.get("thesis_notes")
        if isinstance(thesis_notes, list):
            extra = " ".join(str(x) for x in thesis_notes)
        else:
            extra = str(thesis_notes or "")
        blob = " ".join(
            [
                str(nvda.get("thesis_fit") or ""),
                str(nvda.get("notes") or ""),
                extra,
            ]
        ).lower()
        self.assertIn("open-source", blob)
        self.assertIn("open-weight", blob)
        self.assertIn("not held", blob)
        p = load_fund_policy()
        core = ((p.get("allowlist") or {}).get("core") or [])
        self.assertNotIn("NVDA", [str(s).upper() for s in core])

    def test_book_channel_map_v0_enums_and_anchors(self):
        """Meridian nest: load fixture, lock enums, assert NVDA/MSTR may_change."""
        path = ROOT / "investment" / "book_channel_map.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema"), "fcc_book_channel_map_v0")
        self.assertEqual(data.get("owner"), "nakatoshi")
        self.assertEqual(data.get("consumer"), "meridian")
        self.assertFalse(data.get("stale"))
        self.assertLessEqual(float(data.get("confidence")), 0.50)
        self.assertTrue(data.get("as_of"))
        notes = str(data.get("notes") or "")
        self.assertIn("may_change=none", notes)
        self.assertIn("as_of missing", notes)
        self.assertIn("empty capital list", notes.lower())
        self.assertIn("No FCC marks on Horizon", notes)

        kinds = {"global", "held", "watch"}
        may_change = {
            "ltv_manage",
            "card_refi",
            "residual_freeze",
            "sleeve_watch",
            "none",
        }
        book_channels = {
            "btc_morpho_ltv",
            "usdc_cash",
            "rh_sleeve",
            "strc_jr",
            "none",
        }
        urgencies = {"watch", "this_week", "immediate", "structural"}

        items = data.get("items") or []
        self.assertIsInstance(items, list)
        by_kind = {"global": 0, "held": 0, "watch": 0}
        by_id = {}
        for item in items:
            self.assertIsInstance(item, dict)
            self.assertTrue(item.get("id"))
            self.assertIn(item.get("kind"), kinds)
            self.assertIn(item.get("may_change"), may_change)
            self.assertIn(item.get("book_channel"), book_channels)
            self.assertIsInstance(item.get("does_not"), str)
            self.assertNotIsInstance(item.get("does_not"), list)
            self.assertTrue(item.get("as_of"))
            self.assertIsInstance(item.get("stale"), bool)
            self.assertLessEqual(float(item.get("confidence")), 0.50)
            if "urgency" in item:
                self.assertIn(item.get("urgency"), urgencies)
            by_kind[item["kind"]] += 1
            by_id[item["id"]] = item

        self.assertEqual(by_kind["global"], 6)
        self.assertEqual(by_kind["held"], 7)
        self.assertEqual(by_kind["watch"], 7)
        self.assertEqual(by_id["NVDA"]["may_change"], "none")
        self.assertEqual(by_id["NVDA"]["kind"], "watch")
        self.assertEqual(by_id["MSTR"]["may_change"], "sleeve_watch")
        self.assertEqual(by_id["MSTR"]["kind"], "held")


class TestDecisionLog(unittest.TestCase):
    def test_append_and_load(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            append_decision(
                {
                    "kind": "hold",
                    "summary": "test hold",
                    "rationale": {"why_now": "in band"},
                    "team_votes": {"risk": {"vote": "ok", "note": "fine"}},
                    "actions": [],
                },
                path=p,
                also_journal=False,
            )
            rows = load_decision_log(path=p, limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "hold")


class TestRulesReview(unittest.TestCase):
    def test_hold_when_in_band_zero_cash(self):
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 0,
                "buying_power": 0,
                "total_value": 100,
                "positions": [
                    {"symbol": "MSTR", "quantity": 1, "average_buy_price": 40},
                    {"symbol": "TSLA", "quantity": 1, "average_buy_price": 60},
                ],
            }
        }
        fm = rules_based_review(rh_snapshot=rh, log=False)
        rr = fm["rules_review"]
        self.assertFalse(rr["need_llm"])
        self.assertEqual(rr["outcome"], "hold")

    def test_hold_when_dust_below_min_trade(self):
        # Dust < min_trade ($1) must not wake the team / ntfy every 15m
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 0.09,
                "buying_power": 0.09,
                "total_value": 100.09,
                "positions": [
                    {"symbol": "MSTR", "quantity": 1, "average_buy_price": 40},
                    {"symbol": "TSLA", "quantity": 1, "average_buy_price": 60},
                ],
            }
        }
        fm = rules_based_review(rh_snapshot=rh, log=False)
        rr = fm["rules_review"]
        self.assertFalse(rr["need_llm"])
        self.assertEqual(rr["outcome"], "hold")
        self.assertTrue(rr.get("dust_capital"))
        self.assertLess(rr["deployable_usd"], rr["min_trade_usd"])

    def test_need_llm_when_spendable_cash_or_bp(self):
        # Spendable free capital (≥ min_trade) triggers deploy path
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 5.0,
                "buying_power": 5.0,
                "total_value": 105,
                "positions": [
                    {"symbol": "MSTR", "quantity": 1, "average_buy_price": 40},
                    {"symbol": "TSLA", "quantity": 1, "average_buy_price": 60},
                ],
            }
        }
        fm = rules_based_review(rh_snapshot=rh, log=False)
        self.assertTrue(fm["rules_review"]["need_llm"])
        self.assertEqual(fm["rules_review"]["kind"], "deploy")

    def test_need_llm_when_cash_idle(self):
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 50,
                "buying_power": 50,
                "total_value": 50,
                "positions": [],
            }
        }
        fm = rules_based_review(rh_snapshot=rh, log=False)
        self.assertTrue(fm["rules_review"]["need_llm"])


class TestNotifyIfNeeded(unittest.TestCase):
    def test_quiet_on_hold_even_with_force(self):
        out = notify_if_needed(
            decision_or_review={"kind": "hold", "summary": "team HOLD"},
            treasury_eval={},
            force=True,
        )
        self.assertTrue(out.get("ok"))
        self.assertFalse(out.get("notified"))
        self.assertIn("hold", (out.get("reason") or "").lower())

    def test_stale_rh_cooldown(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path

        from treasury import fund_manager as fm

        stale_eval = {
            "data_quality": {
                "stale": ["robinhood snapshot is old"],
                "warnings": [],
            }
        }
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "ntfy_stale_rh_state.json"
            with mock.patch.object(fm, "NTFY_STALE_RH_STATE", state), mock.patch.object(
                fm, "load_config", return_value={"notifications": {"enabled": True, "stale_rh_cooldown_hours": 6}}
            ), mock.patch("urllib.request.urlopen") as urlopen:
                resp = mock.MagicMock()
                resp.status = 200
                resp.__enter__.return_value = resp
                resp.__exit__.return_value = None
                urlopen.return_value = resp

                first = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                )
                self.assertTrue(first.get("notified"), first)
                self.assertEqual(urlopen.call_count, 1)

                # Immediate re-notify should be suppressed by cooldown
                second = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                )
                self.assertFalse(second.get("notified"), second)
                self.assertIn("cooldown", second.get("reason") or "")
                self.assertEqual(urlopen.call_count, 1)

                # force bypasses cooldown
                third = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                    force=True,
                )
                self.assertTrue(third.get("notified"), third)
                self.assertEqual(urlopen.call_count, 2)


class TestAnalyze(unittest.TestCase):
    def test_all_cash_hints_deploy(self):
        p = load_fund_policy()
        rh = {
            "agentic": {
                "account_number": "674601752",
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 8.37,
                "buying_power": 8.37,
                "total_value": 8.37,
                "positions": [],
            }
        }
        a = analyze_agentic_book(rh, p)
        self.assertTrue(a["ok"])
        self.assertAlmostEqual(a["nav_usd"], 8.37)
        self.assertTrue(a["fair_game"])
        self.assertFalse(a["approval"]["require_user_confirm"])
        self.assertIsNone(a["approval"]["max_single_order_notional_usd"])
        self.assertTrue(any("cash" in h.lower() or "Deploy" in h for h in a["manager_hints"]))

    def test_deployed_weights(self):
        p = load_fund_policy()
        rh = {
            "agentic": {
                "account_number_last4": "1752",
                "agentic_allowed": True,
                "cash": 0,
                "buying_power": 0,
                "total_value": 100,
                "positions": [
                    {"symbol": "MSTR", "quantity": 1, "average_buy_price": 40},
                    {"symbol": "TSLA", "quantity": 1, "average_buy_price": 60},
                ],
            }
        }
        a = analyze_agentic_book(rh, p)
        self.assertTrue(a["ok"])
        self.assertAlmostEqual(a["weights_of_deployed"]["btc_digital_credit"], 0.4, places=2)
        self.assertAlmostEqual(a["weights_of_deployed"]["stocks_growth"], 0.6, places=2)


if __name__ == "__main__":
    unittest.main()
