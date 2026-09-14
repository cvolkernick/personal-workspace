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
    DECISION_GITHUB_MARKER,
    _decision_already_on_github,
    _is_rh_brokerage_stale_msg,
    analyze_agentic_book,
    append_decision,
    format_decision_github_body,
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
        entries = { (e.get("symbol") or "").upper(): e for e in (wl.get("entries") or []) }
        self.assertIn("BE", entries)
        self.assertEqual(entries["BE"].get("status"), "pass")
        self.assertFalse((wl.get("policy") or {}).get("auto_buy", True))
        p = load_fund_policy()
        self.assertEqual((p.get("watchlist") or {}).get("path"), "investment/watchlist.json")
        self.assertIn("BE", [str(s).upper() for s in ((p.get("guardrails") or {}).get("blocked_symbols") or [])])
        self.assertNotIn("BE", [str(s).upper() for s in ((p.get("sleeves") or {}).get("energy_opportunistic") or {}).get("watchlist_symbols") or []])
        summary = watchlist_summary(p)
        self.assertIn("BE", summary["symbols"])
        self.assertGreaterEqual(summary["count"], 1)

    def test_watchlist_nvda_open_weight_note(self):
        """NVDA thesis note mentions open-source/open-weight; ready+held, not core."""
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
        self.assertIn("still held", blob)
        p = load_fund_policy()
        core = ((p.get("allowlist") or {}).get("core") or [])
        self.assertNotIn("NVDA", [str(s).upper() for s in core])

    def test_investment_sot_no_contradiction(self):
        """#618: sleeve lists, watchlist, pins, and blocked BE stay aligned."""
        p = load_fund_policy()
        wl = load_watchlist()
        entries = { (e.get("symbol") or "").upper(): e for e in (wl.get("entries") or []) }
        stocks_wl = [
            str(s).upper()
            for s in ((p.get("sleeves") or {}).get("stocks_growth") or {}).get("watchlist_symbols") or []
        ]
        energy_wl = [
            str(s).upper()
            for s in ((p.get("sleeves") or {}).get("energy_opportunistic") or {}).get("watchlist_symbols") or []
        ]
        core = [str(s).upper() for s in ((p.get("allowlist") or {}).get("core") or [])]
        for sym in ("GOOGL", "AAPL", "NVDA", "PLTR", "AMZN", "EVGO", "RKLB"):
            self.assertIn(sym, stocks_wl)
            self.assertEqual(entries[sym].get("status"), "ready")
        for sym in ("CCJ", "BWXT"):
            self.assertIn(sym, energy_wl)
            self.assertEqual(entries[sym].get("status"), "ready")
        self.assertNotIn("BE", stocks_wl + energy_wl + core)
        pins_path = ROOT / "investment" / "consider_share.json"
        pins = json.loads(pins_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted((pins.get("pins") or {}).keys()), ["SPCX", "TSLA"])
        self.assertAlmostEqual(float((pins.get("pins") or {})["TSLA"]), 15.0)
        self.assertAlmostEqual(float((pins.get("pins") or {})["SPCX"]), 15.0)

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
        notes_lc = notes.lower()
        self.assertIn("may_change=none", notes)
        self.assertIn("as_of missing", notes)
        self.assertIn("empty capital list", notes_lc)
        self.assertIn("no fcc marks on horizon", notes_lc)

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
                also_github=False,
            )
            rows = load_decision_log(path=p, limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "hold")

    def test_format_includes_required_what_why_fields(self):
        body = format_decision_github_body(
            {
                "as_of": "2026-09-14T14:00:00+00:00",
                "kind": "deploy",
                "summary": "Buy MSTR $25",
                "nav_usd": 320.04,
                "buying_power_usd": 25.04,
                "weights_before": {"btc_digital_credit": 0.4, "stocks_growth": 0.6},
                "weights_after_expected": {
                    "btc_digital_credit": 0.41,
                    "stocks_growth": 0.59,
                },
                "rationale": {
                    "why_now": "Idle cash above min_trade",
                    "why_not_alternatives": "TSLA already at sleeve cap",
                    "thesis_tags": ["btc_proxy", "rules_engine"],
                },
                "team_votes": {
                    "scout": {"vote": "observe", "note": "NAV $320 BP $25"},
                    "thesis": {"vote": "ok", "note": "MSTR"},
                    "risk": {"vote": "ok", "note": "size $25"},
                    "critic": {"vote": "ok", "note": "not held-only"},
                    "executor": {"vote": "place", "note": "MCP buy"},
                },
                "actions": [
                    {
                        "symbol": "MSTR",
                        "side": "buy",
                        "notional_usd": 25.04,
                        "status": "filled",
                        "order_id": "abc",
                    }
                ],
            }
        )
        self.assertIn(DECISION_GITHUB_MARKER, body)
        for needle in (
            "**kind:** `deploy`",
            "**summary:** Buy MSTR $25",
            "why_now",
            "Idle cash above min_trade",
            "why_not_alternatives",
            "TSLA already at sleeve cap",
            "thesis_tags",
            "team_votes",
            "**executor:**",
            "BUY MSTR $25.04",
            "nav_usd",
            "buying_power_usd",
            "weights_before",
            "weights_after",
        ):
            self.assertIn(needle, body)

    def test_append_posts_full_decision_to_github(self):
        import os
        import tempfile
        from pathlib import Path

        captured: dict = {}

        def fake_post(title, text, *, dry_run=False, as_markdown=False):
            captured["title"] = title
            captured["text"] = text
            captured["as_markdown"] = as_markdown
            captured["dry_run"] = dry_run
            return {"ok": True, "posted": True, "issue": "701", "status": 201}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            with mock.patch(
                "treasury.fund_manager.post_ops_github", side_effect=fake_post
            ), mock.patch.dict(
                os.environ, {"FM_DECISION_GITHUB_IN_TEST": "1"}, clear=False
            ):
                append_decision(
                    {
                        "kind": "hold",
                        "summary": "Rules HOLD in band",
                        "nav_usd": 297.70,
                        "buying_power_usd": 0.03,
                        "rationale": {
                            "why_now": "Scheduled daily review",
                            "why_not_alternatives": "In-band + low cash",
                            "thesis_tags": ["rules_engine"],
                        },
                        "team_votes": {
                            "risk": {"vote": "ok", "note": "no trade"}
                        },
                        "actions": [],
                        "weights_before": {
                            "btc_digital_credit": 0.406,
                            "stocks_growth": 0.594,
                        },
                    },
                    path=p,
                    also_journal=False,
                )
            rows = load_decision_log(path=p, limit=1)
        self.assertTrue(captured.get("as_markdown"), captured)
        self.assertFalse(captured.get("dry_run"))
        self.assertIn("fund-manager hold", captured.get("title") or "")
        self.assertIn(DECISION_GITHUB_MARKER, captured.get("text") or "")
        self.assertIn("Scheduled daily review", captured.get("text") or "")
        self.assertIn("In-band + low cash", captured.get("text") or "")
        self.assertIn('"nav_usd": 297.7', captured.get("text") or "")
        self.assertEqual(len(rows), 1)
        gh = rows[0].get("_github") or {}
        self.assertTrue(gh.get("posted"), rows[0])
        self.assertTrue(_decision_already_on_github(rows[0]))

    def test_jsonl_logged_without_posted_is_not_on_github(self):
        self.assertFalse(
            _decision_already_on_github(
                {
                    "as_of": "2026-09-14T14:00:00+00:00",
                    "kind": "deploy",
                    "schema_version": 1,
                    "rationale": {"why_now": "free capital"},
                    "team_votes": {"risk": {"vote": "review", "note": "size"}},
                    "rules_review": {"logged": True},
                }
            )
        )
        self.assertFalse(
            _decision_already_on_github(
                {
                    "kind": "hold",
                    "_github": {"ok": False, "posted": False, "skipped": "no-github-token"},
                }
            )
        )
        self.assertTrue(
            _decision_already_on_github(
                {"kind": "deploy", "_github": {"ok": True, "posted": True, "issue": "701"}}
            )
        )
        self.assertTrue(
            _decision_already_on_github(
                {
                    "kind": "hold",
                    "rules_review": {
                        "logged": True,
                        "github": {"posted": True, "issue": "701"},
                    },
                }
            )
        )

    def test_notify_falls_back_when_jsonl_logged_but_not_posted(self):
        with mock.patch(
            "treasury.fund_manager.post_ops_github",
            return_value={"ok": True, "posted": True, "issue": "701", "status": 201},
        ) as post:
            out = notify_if_needed(
                decision_or_review={
                    "as_of": "2026-09-14T14:00:00+00:00",
                    "kind": "deploy",
                    "outcome": "need_llm",
                    "summary": "idle cash",
                    "schema_version": 1,
                    "rationale": {"why_now": "free capital"},
                    "team_votes": {"risk": {"vote": "review", "note": "size"}},
                    "rules_review": {"logged": True, "need_llm": True},
                },
                treasury_eval={},
            )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("notified"), out)
        self.assertEqual(post.call_count, 1)
        self.assertNotIn("already on #701", out.get("reason") or "")

    def test_notify_skips_duplicate_body_only_when_github_posted(self):
        with mock.patch("treasury.fund_manager.post_ops_github") as post:
            out = notify_if_needed(
                decision_or_review={
                    "kind": "deploy",
                    "outcome": "need_llm",
                    "summary": "idle cash",
                    "_github": {"ok": True, "posted": True, "issue": "701"},
                },
                treasury_eval={},
            )
        self.assertTrue(out.get("ok"), out)
        self.assertFalse(out.get("notified"), out)
        self.assertIn("already on #701", out.get("reason") or "")
        self.assertEqual(post.call_count, 0)

    def test_notify_considers_stale_rh_after_posted_decision(self):
        import os
        import tempfile
        from pathlib import Path

        from treasury import fund_manager as fm

        stale_eval = {
            "data_quality": {
                "stale": ["robinhood snapshot is old"],
                "warnings": [],
            }
        }
        posts: list[tuple] = []

        def fake_post(title, text, *, dry_run=False, as_markdown=False):
            posts.append((title, text, as_markdown))
            return {"ok": True, "posted": True, "issue": "701", "status": 201}

        with tempfile.TemporaryDirectory() as td:
            snap = Path(td)
            with mock.patch.object(fm, "SNAPSHOTS_DIR", snap), mock.patch.object(
                fm, "NTFY_STALE_RH_STATE", snap / "ntfy_stale_rh_state.json"
            ), mock.patch.object(
                fm,
                "load_config",
                return_value={
                    "notifications": {"enabled": True, "stale_rh_cooldown_hours": 6}
                },
            ), mock.patch.dict(
                os.environ, {"GITHUB_TOKEN": "ghs_test", "PI_OPS_ALERT_ISSUE": "701"}, clear=False
            ), mock.patch(
                "treasury.fund_manager.post_ops_github", side_effect=fake_post
            ):
                first = notify_if_needed(
                    decision_or_review={
                        "kind": "deploy",
                        "outcome": "need_llm",
                        "summary": "idle cash",
                        "_github": {"ok": True, "posted": True, "issue": "701"},
                    },
                    treasury_eval=stale_eval,
                )
                self.assertTrue(first.get("notified"), first)
                self.assertTrue(first.get("stale_only"), first)
                self.assertEqual(len(posts), 1)
                self.assertIn("stale RH", posts[0][0])
                self.assertFalse(posts[0][2])

                second = notify_if_needed(
                    decision_or_review={
                        "kind": "deploy",
                        "outcome": "need_llm",
                        "summary": "idle cash",
                        "_github": {"ok": True, "posted": True, "issue": "701"},
                    },
                    treasury_eval=stale_eval,
                )
        self.assertFalse(second.get("notified"), second)
        self.assertIn("cooldown", second.get("reason") or "")
        self.assertEqual(len(posts), 1)


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
        # Dust < min_trade ($1) must not wake the team / #701 every 15m
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

    def _in_band_rh(self):
        return {
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

    def test_hold_dedup_retries_when_github_not_posted(self):
        import os
        import tempfile
        from pathlib import Path

        from treasury import fund_manager as fm

        policy = dict(load_fund_policy())
        policy["live"] = True
        posts: list[dict] = []

        def fake_post(title, text, *, dry_run=False, as_markdown=False):
            posts.append({"title": title})
            return {"ok": False, "posted": False, "skipped": "no-github-token"}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            journal = Path(td) / "journal.md"
            with mock.patch.object(fm, "DECISIONS_PATH", p), mock.patch.object(
                fm, "JOURNAL_PATH", journal
            ), mock.patch(
                "treasury.fund_manager.post_ops_github", side_effect=fake_post
            ), mock.patch.dict(
                os.environ, {"FM_DECISION_GITHUB_IN_TEST": "1"}, clear=False
            ):
                first = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                second = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                third = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                rows = load_decision_log(path=p, limit=5)
        self.assertTrue(first["rules_review"]["logged"])
        self.assertFalse(second["rules_review"]["logged"])
        self.assertFalse(third["rules_review"]["logged"])
        self.assertEqual(len(posts), 3)
        self.assertEqual(len(rows), 1)
        self.assertFalse((rows[0].get("_github") or {}).get("posted"))
        self.assertEqual((rows[0].get("_github") or {}).get("skipped"), "no-github-token")

    def test_hold_retry_success_patches_same_jsonl_row(self):
        import os
        import tempfile
        from pathlib import Path

        from treasury import fund_manager as fm

        policy = dict(load_fund_policy())
        policy["live"] = True
        posts: list[dict] = []

        def fake_post(title, text, *, dry_run=False, as_markdown=False):
            posts.append({"title": title})
            if len(posts) == 1:
                return {"ok": False, "posted": False, "skipped": "no-github-token"}
            return {"ok": True, "posted": True, "issue": "701", "status": 201}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            journal = Path(td) / "journal.md"
            with mock.patch.object(fm, "DECISIONS_PATH", p), mock.patch.object(
                fm, "JOURNAL_PATH", journal
            ), mock.patch(
                "treasury.fund_manager.post_ops_github", side_effect=fake_post
            ), mock.patch.dict(
                os.environ, {"FM_DECISION_GITHUB_IN_TEST": "1"}, clear=False
            ):
                first = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                second = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                third = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                rows = load_decision_log(path=p, limit=5)
        self.assertTrue(first["rules_review"]["logged"])
        self.assertFalse(second["rules_review"]["logged"])
        self.assertFalse(third["rules_review"]["logged"])
        self.assertTrue((second.get("rules_review") or {}).get("github", {}).get("posted"))
        self.assertEqual(len(posts), 2)
        self.assertEqual(len(rows), 1)
        self.assertTrue((rows[0].get("_github") or {}).get("posted"))

    def test_fm_decision_github_zero_skips_post(self):
        import os
        import tempfile
        from pathlib import Path

        from treasury import fund_manager as fm

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            with mock.patch(
                "treasury.fund_manager.post_ops_github"
            ) as post, mock.patch.dict(
                os.environ,
                {"FM_DECISION_GITHUB": "0", "FM_DECISION_GITHUB_IN_TEST": "1"},
                clear=False,
            ):
                append_decision(
                    {"kind": "hold", "summary": "local only"},
                    path=p,
                    also_journal=False,
                )
                rows = load_decision_log(path=p, limit=1)
        self.assertEqual(post.call_count, 0)
        self.assertEqual((rows[0].get("_github") or {}).get("skipped"), "disabled")
        self.assertFalse(_decision_already_on_github(rows[0]))

    def test_hold_dedup_skips_only_after_github_posted(self):
        import os
        import tempfile
        from pathlib import Path

        from treasury import fund_manager as fm

        policy = dict(load_fund_policy())
        policy["live"] = True
        posts: list[dict] = []

        def fake_post(title, text, *, dry_run=False, as_markdown=False):
            posts.append({"title": title})
            return {"ok": True, "posted": True, "issue": "701", "status": 201}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            journal = Path(td) / "journal.md"
            with mock.patch.object(fm, "DECISIONS_PATH", p), mock.patch.object(
                fm, "JOURNAL_PATH", journal
            ), mock.patch(
                "treasury.fund_manager.post_ops_github", side_effect=fake_post
            ), mock.patch.dict(
                os.environ, {"FM_DECISION_GITHUB_IN_TEST": "1"}, clear=False
            ):
                first = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                second = rules_based_review(
                    rh_snapshot=self._in_band_rh(), policy=policy, log=True
                )
                rows = load_decision_log(path=p, limit=5)
        self.assertTrue(first["rules_review"]["logged"])
        self.assertFalse(second["rules_review"]["logged"])
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(rows), 1)
        self.assertTrue((rows[0].get("_github") or {}).get("posted"))


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

    def test_rh_checking_stale_does_not_page(self):
        from treasury import fund_manager as fm

        leftover = {
            "data_quality": {
                "stale": [
                    "one_card data 17.8h old (>6.0h)",
                    "rh_checking data 17.8h old (>6.0h)",
                    "x_money data 17.8h old (>6.0h)",
                ],
                "warnings": [],
            }
        }
        self.assertFalse(_is_rh_brokerage_stale_msg("rh_checking data 17.8h old (>6.0h)"))
        self.assertTrue(_is_rh_brokerage_stale_msg("robinhood snapshot is old"))
        with mock.patch("urllib.request.urlopen") as urlopen:
            out = notify_if_needed(
                decision_or_review={"kind": "hold", "outcome": "hold"},
                treasury_eval=leftover,
            )
        self.assertFalse(out.get("notified"), out)
        self.assertEqual(urlopen.call_count, 0)

    def test_stale_rh_cooldown(self):
        import os
        import tempfile
        from pathlib import Path

        from treasury import fund_manager as fm

        stale_eval = {
            "data_quality": {
                "stale": ["robinhood snapshot is old"],
                "warnings": [],
            }
        }
        urls: list[str] = []

        def fake_urlopen(req, timeout=15):
            urls.append(req.full_url)
            resp = mock.MagicMock()
            resp.status = 201
            resp.getcode.return_value = 201
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        with tempfile.TemporaryDirectory() as td:
            snap = Path(td)
            state = snap / "ntfy_stale_rh_state.json"
            env = {
                "GITHUB_TOKEN": "ghs_test",
                "PI_OPS_ALERT_ISSUE": "701",
                "FCC_ALERT_KILL_SWITCH": "",
            }
            with mock.patch.object(fm, "NTFY_STALE_RH_STATE", state), mock.patch.object(
                fm, "SNAPSHOTS_DIR", snap
            ), mock.patch.object(
                fm, "load_config", return_value={"notifications": {"enabled": True, "stale_rh_cooldown_hours": 6}}
            ), mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "urllib.request.urlopen", side_effect=fake_urlopen
            ):
                first = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                )
                self.assertTrue(first.get("notified"), first)
                self.assertFalse(first.get("page"), first)
                self.assertTrue((first.get("github") or {}).get("posted"), first)
                self.assertEqual(len(urls), 1)
                self.assertIn("api.github.com", urls[0])
                self.assertFalse(any("ntfy.sh" in u for u in urls), urls)

                # Immediate re-notify should be suppressed by cooldown
                second = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                )
                self.assertFalse(second.get("notified"), second)
                self.assertIn("cooldown", second.get("reason") or "")
                self.assertEqual(len(urls), 1)

                # force bypasses cooldown
                third = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                    force=True,
                )
                self.assertTrue(third.get("notified"), third)
                self.assertEqual(len(urls), 2)
                self.assertTrue(all("api.github.com" in u for u in urls), urls)

    def test_leftover_robinhood_age_quiet_when_sot_fresh(self):
        import tempfile
        from datetime import datetime, timedelta, timezone
        from pathlib import Path

        from treasury import fund_manager as fm

        leftover = {
            "data_quality": {
                "stale": ["robinhood data 17.8h old (>6.0h)"],
                "warnings": [],
            }
        }
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td)
            as_of = datetime.now(timezone.utc) - timedelta(hours=1)
            (snap / "robinhood_latest.json").write_text(
                json.dumps({"as_of": as_of.isoformat(), "source": "live"}),
                encoding="utf-8",
            )
            with mock.patch.object(fm, "SNAPSHOTS_DIR", snap), mock.patch.object(
                fm, "NTFY_STALE_RH_STATE", snap / "ntfy_stale_rh_state.json"
            ), mock.patch.object(
                fm, "load_config", return_value={"notifications": {"enabled": True}}
            ), mock.patch("urllib.request.urlopen") as urlopen:
                out = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=leftover,
                )
        self.assertFalse(out.get("notified"), out)
        self.assertIn("fresh", (out.get("reason") or "").lower())
        self.assertEqual(urlopen.call_count, 0)

    def test_skipped_producer_status_does_not_page(self):
        import tempfile
        from pathlib import Path

        from treasury import fund_manager as fm

        stale_eval = {
            "data_quality": {
                "stale": ["robinhood snapshot is old"],
                "warnings": [],
            }
        }
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td)
            (snap / "rh_producer_status.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "error": "no_refresh_path",
                        "error_class": "skipped",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(fm, "SNAPSHOTS_DIR", snap), mock.patch.object(
                fm, "NTFY_STALE_RH_STATE", snap / "ntfy_stale_rh_state.json"
            ), mock.patch.object(
                fm, "load_config", return_value={"notifications": {"enabled": True}}
            ), mock.patch("urllib.request.urlopen") as urlopen:
                out = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                )
        self.assertFalse(out.get("notified"), out)
        self.assertIn("skipped", (out.get("reason") or "").lower())
        self.assertEqual(urlopen.call_count, 0)


    def test_error_comments_github_not_ntfy(self):
        import os

        urls: list[str] = []

        def fake_urlopen(req, timeout=15):
            urls.append(req.full_url)
            resp = mock.MagicMock()
            resp.status = 200
            resp.getcode.return_value = 200
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        env = {
            "GITHUB_TOKEN": "ghs_test",
            "NTFY_TOKEN": "tk_test",
            "PI_OPS_ALERT_ISSUE": "701",
            "FCC_ALERT_KILL_SWITCH": "",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ), mock.patch(
            "treasury.fund_manager.load_config",
            return_value={"notifications": {"enabled": True, "ntfy_topic": "cvolk-grok-7f3k9x"}},
        ):
            out = notify_if_needed(
                decision_or_review={
                    "kind": "error",
                    "outcome": "error",
                    "summary": "no agentic block",
                },
                treasury_eval={},
            )
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("page"), out)
        self.assertTrue(out.get("notified"), out)
        self.assertTrue((out.get("github") or {}).get("posted"), out)
        self.assertTrue(any("issues/701/comments" in u for u in urls), urls)
        self.assertFalse(any("ntfy.sh" in u for u in urls), urls)

    def test_need_llm_github_not_ntfy(self):
        import os

        urls: list[str] = []

        def fake_urlopen(req, timeout=15):
            urls.append(req.full_url)
            resp = mock.MagicMock()
            resp.status = 201
            resp.getcode.return_value = 201
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        env = {
            "GITHUB_TOKEN": "ghs_test",
            "PI_OPS_ALERT_ISSUE": "701",
            "FCC_ALERT_KILL_SWITCH": "",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ), mock.patch(
            "treasury.fund_manager.load_config",
            return_value={"notifications": {"enabled": True, "ntfy_topic": "cvolk-grok-7f3k9x"}},
        ):
            out = notify_if_needed(
                decision_or_review={
                    "kind": "deploy",
                    "outcome": "need_llm",
                    "summary": "idle cash",
                    "rules_review": {"outcome": "need_llm", "need_llm": True, "summary": "idle cash"},
                },
                treasury_eval={},
            )
        self.assertTrue(out.get("notified"), out)
        self.assertFalse(out.get("page"), out)
        self.assertTrue((out.get("github") or {}).get("posted"), out)
        self.assertTrue(any("issues/701/comments" in u for u in urls), urls)
        self.assertFalse(any("ntfy.sh" in u for u in urls), urls)

    def test_need_llm_github_body_is_structured_what_why(self):
        import os

        bodies: list[str] = []

        def fake_urlopen(req, timeout=15):
            bodies.append(req.data.decode("utf-8") if req.data else "")
            resp = mock.MagicMock()
            resp.status = 201
            resp.getcode.return_value = 201
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        env = {
            "GITHUB_TOKEN": "ghs_test",
            "PI_OPS_ALERT_ISSUE": "701",
            "FCC_ALERT_KILL_SWITCH": "",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ), mock.patch(
            "treasury.fund_manager.load_config",
            return_value={"notifications": {"enabled": True}},
        ):
            out = notify_if_needed(
                decision_or_review={
                    "kind": "deploy",
                    "outcome": "need_llm",
                    "summary": "idle cash $25.04",
                    "need_llm": True,
                },
                treasury_eval={},
            )
        self.assertTrue(out.get("notified"), out)
        self.assertEqual(len(bodies), 1)
        payload = json.loads(bodies[0])
        comment = payload["body"]
        self.assertIn(DECISION_GITHUB_MARKER, comment)
        self.assertIn("idle cash $25.04", comment)
        self.assertNotIn("```\n<!-- fund-manager-decision", comment)


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
