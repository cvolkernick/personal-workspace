"""Buy-only broker adapter + run planner (treasury.fund_manager_broker) behind the #1093 gate.

Every broker call is a mock; nothing touches the network, Robinhood, grok or
GitHub (#701 posting is mocked). Policy, snapshot and ledger live in temp dirs.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury import fund_manager as fm  # noqa: E402
from treasury import fund_manager_broker as broker  # noqa: E402
from treasury import fund_manager_order_gate as gate  # noqa: E402

NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)  # Mon 11:00 ET
ACCT = "674601752"
REPO_POLICY = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))


def pos(sym, qty, px):
    return {"symbol": sym, "quantity": str(qty), "average_buy_price": str(px)}


# BTC sleeve 50 of 170 invested (29%) -> under 40%
UNDER = [pos("TSLA", 1, 60), pos("SPCX", 1, 60), pos("STRC", 0.5, 100)]
# BTC sleeve 100 of 150 (67%) -> over 40%; TSLA 30 vs SPCX 20
OVER = [pos("TSLA", 1, 30), pos("SPCX", 1, 20), pos("STRC", 1, 100)]


def snap(cash=10.0, bp=None, positions=UNDER, now=NOW, **extra):
    ag = {"account_number": ACCT, "cash": cash, "buying_power": cash if bp is None else bp,
          "unleveraged_buying_power": cash if bp is None else bp, "positions": positions,
          "as_of": (now - timedelta(hours=1)).isoformat()}
    ag.update(extra)
    return {"agentic": ag}


class FakeClient:
    def __init__(self, bp=200.0, orders=None, tradable=True, fractional=True, review_ok=True,
                 place_result=None, place_exc=None, account=ACCT):
        self.bp, self.orders, self.tradable, self.fractional = bp, list(orders or []), tradable, fractional
        self.review_ok, self.place_result, self.place_exc, self.account = review_ok, place_result, place_exc, account
        self.calls = []
        self.n = 0

    def get_account(self, account_number):
        self.calls.append(("get_account", account_number))
        return {"account_number": self.account, "buying_power": self.bp, "cash": self.bp}

    def get_recent_orders(self, account_number, since):
        self.calls.append(("get_recent_orders", account_number))
        return list(self.orders)

    def get_tradability(self, symbol):
        self.calls.append(("get_tradability", symbol))
        return {"tradable": self.tradable, "fractional_tradable": self.fractional}

    def review_order(self, order):
        self.calls.append(("review_order", order))
        return {"ok": self.review_ok, "reason": "" if self.review_ok else "review says no"}

    def place_order(self, order):
        self.calls.append(("place_order", order))
        if self.place_exc:
            raise self.place_exc
        if self.place_result:
            return self.place_result
        self.n += 1
        res = {"order_id": f"ord-{self.n}", "state": "filled", "symbol": order["symbol"], "side": "buy",
               "dollar_amount": float(order["dollar_amount"]), "quantity": 0.01, "average_price": 100.0,
               "created_at": NOW.isoformat()}
        self.orders.append(res)  # the broker now shows our own order
        return res

    @property
    def placed(self):
        return [c for c in self.calls if c[0] == "place_order"]


def gpol(live=True):
    return gate.GatePolicy(True, "", ("buy",), ("BE",), live=live, min_trade_usd=1.0)


def buy(sym="STRC", usd=2.5, **kw):
    d = {"symbol": sym, "side": "buy", "notional_usd": usd, "purpose": "deploy"}
    d.update(kw)
    return d


TEN = [buy("STRC", 2.5), buy("SATA", 2.5), buy("STRK", 1.5), buy("IBIT", 3.5)]  # exact $10 plan


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.ledger = self.dir / "ledger.json"
        self.policy = self.dir / "fund_manager.json"
        self.write_policy()
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop(broker.DISABLE_ENV, None)
        os.environ.pop(broker.CLIENT_ENV, None)
        err = mock.patch("sys.stderr")
        err.start()
        self.addCleanup(err.stop)
        self.addCleanup(env.stop)
        self.addCleanup(self.td.cleanup)

    def write_policy(self, live=True, **adapter):
        p = copy.deepcopy(REPO_POLICY)
        p["live"] = live
        p["execution"]["broker_adapter"].update(adapter)
        self.policy.write_text(json.dumps(p), encoding="utf-8")

    def plan(self, intents, snapshot=None, now=NOW, persist=True):
        return broker.plan_intents(intents, snapshot=snapshot or snap(), policy_path=self.policy,
                                   ledger_path=self.ledger, now=now, persist=persist)

    def legs(self, info):
        return {l["symbol"]: l["notional_usd"] for l in info.get("legs") or []}

    def gated(self, intents, snapshot=None, now=NOW):
        orders, info = self.plan(intents, snapshot, now)
        res = gate.gate_orders(orders, deployable_cash=1000.0, policy=gpol(), now=now)
        return res.allowed, info

    def submit(self, g, client, now=NOW):
        return broker.submit(g, client=client, policy_path=self.policy, ledger_path=self.ledger, now=now)

    def run_all(self, intents, client, snapshot=None, now=NOW):
        allowed, info = self.gated(intents, snapshot, now)
        return [self.submit(g, client, now) for g in allowed], info

    def ledger_data(self):
        return json.loads(self.ledger.read_text())


# ---------------------------------------------------------------- 1 allowlist
class TestAllowlist(Base):
    def test_repo_config_allowlist_and_ibit_floating(self):
        cfg, err = broker.load_adapter_config()
        self.assertEqual(err, "")
        self.assertTrue(cfg["live"])
        self.assertEqual(set(cfg["allowlist"]), {"STRC", "SATA", "STRK", "MSTR", "ASST", "BITA", "IBIT", "MARA",
                                                 "RIOT", "CLSK", "IREN", "WULF", "TSLA", "SPCX"})
        self.assertEqual(cfg["floating"], ("IBIT", "MSTR", "ASST", "BITA", "MARA", "RIOT", "CLSK", "WULF", "IREN"))
        self.assertEqual(cfg["fold_into"], "IBIT")
        self.assertEqual(cfg["stocks"], ("TSLA", "SPCX"))
        btc = REPO_POLICY["sleeves"]["btc_digital_credit"]
        self.assertIn("IBIT", btc["symbols"])
        self.assertEqual(btc["new_money_weights"]["floating_default"], "IBIT")
        self.assertNotIn("BTC", btc["new_money_weights"]["floating_universe"])
        self.assertEqual(REPO_POLICY["execution"]["broker_adapter"]["low_preference"]["MSTR"]["until"], "2026-11-30")

    def test_prompt_names_ibit_standin_and_mstr_low_preference(self):
        text = (ROOT / "treasury" / "fund_manager_daily_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("native-BTC stand-in", text)
        self.assertIn("LOW PREFERENCE through 2026-11-30", text)
        self.assertIn("ONLY choose how the floating 35% splits", text)

    def test_non_allowlisted_buy_dropped_by_planner(self):
        orders, info = self.plan(TEN + [buy("NVDA", 3.0)])
        self.assertNotIn("NVDA", [o["symbol"] for o in orders])
        self.assertEqual(info["dropped"][0]["symbol"], "NVDA")

    def test_non_allowlisted_symbol_rejected_by_adapter(self):
        c = FakeClient()
        g = gate.gate_orders([buy("NVDA", 5.0)], deployable_cash=10, policy=gpol(), now=NOW).allowed[0]
        out = self.submit(g, c)
        self.assertEqual(out["status"], "not_submitted_review_failed")
        self.assertIn("allowlist", out["broker_reason"])
        self.assertEqual(c.calls, [])

    def test_mstr_low_preference_logged_until_date(self):
        _, info = self.plan([buy("STRC", 2.5), buy("SATA", 2.5), buy("STRK", 1.5), buy("MSTR", 3.5)])
        self.assertTrue(any("MSTR has low preference until 2026-11-30" in n for n in info["notes"]))
        _, info2 = broker.plan_intents([buy("STRC", 2.5), buy("SATA", 2.5), buy("STRK", 1.5), buy("MSTR", 3.5)],
                                       snapshot=snap(now=NOW + timedelta(days=36)), policy_path=self.policy,
                                       ledger_path=self.dir / "l2.json", now=NOW + timedelta(days=36))
        self.assertFalse(any("low preference" in n for n in info2["notes"]))


# ------------------------------------------------------- 2 sleeve gate/weights
class TestSleeveGateAndWeights(Base):
    def test_exact_plan_is_planned(self):
        orders, info = self.plan(TEN)
        self.assertEqual(info["status"], "planned")
        self.assertEqual(self.legs(info), {"STRC": 2.5, "SATA": 2.5, "STRK": 1.5, "IBIT": 3.5})
        self.assertTrue(all(o["run_id"] == info["run_id"] for o in orders))

    def test_stock_buy_under_40pct_dropped_and_redirected(self):
        orders, info = self.plan(TEN + [buy("TSLA", 5.0)], snapshot=snap(cash=20))
        self.assertEqual(info["status"], "weights_repaired")
        self.assertNotIn("TSLA", self.legs(info))
        self.assertEqual(info["dropped"][0]["status"], "not_submitted_sleeve_gate")
        legs = self.legs(info)
        self.assertAlmostEqual(sum(legs.values()), 15.0, places=2)
        self.assertAlmostEqual(legs["STRC"], 3.75, places=2)
        self.assertAlmostEqual(legs["STRK"], 2.25, places=2)

    def test_over_40pct_stocks_get_rest_toward_50_50(self):
        # BTC 100 / invested 150; run 30 -> BTC target 0.4*180-100 < 0 -> all to stocks
        _, info = self.plan([buy("TSLA", 15), buy("SPCX", 15)], snapshot=snap(cash=30, positions=OVER))
        legs = self.legs(info)
        # TSLA 30 / SPCX 20: equalize at (50+30)/2 = 40 -> TSLA 10, SPCX 20
        self.assertEqual(legs, {"TSLA": 10.0, "SPCX": 20.0})
        self.assertEqual(info["status"], "weights_repaired")

    def test_weights_within_tolerance_kept(self):
        _, info = self.plan([buy("STRC", 3.2), buy("SATA", 2.0), buy("STRK", 1.5), buy("IBIT", 3.3)])
        self.assertEqual(info["status"], "planned")
        self.assertEqual(self.legs(info)["STRC"], 3.2)

    def test_weights_outside_tolerance_repaired(self):
        _, info = self.plan([buy("STRC", 8.0), buy("IBIT", 2.0)])
        self.assertEqual(info["status"], "weights_repaired")
        self.assertEqual(self.legs(info), {"STRC": 2.5, "SATA": 2.5, "STRK": 1.5, "IBIT": 3.5})
        self.assertTrue(info["repairs"])

    def test_ai_floating_split_is_honoured(self):
        _, info = self.plan([buy("STRC", 5), buy("SATA", 5), buy("STRK", 3), buy("MARA", 3.5), buy("IBIT", 3.5)],
                            snapshot=snap(cash=20))
        legs = self.legs(info)
        self.assertEqual(legs["MARA"], 3.5)
        self.assertEqual(legs["IBIT"], 3.5)

    def test_leg_under_one_dollar_folds_into_ibit(self):
        _, info = self.plan([buy("STRC", 1.5), buy("SATA", 1.5), buy("STRK", 0.9), buy("IBIT", 2.1)], snapshot=snap(cash=6))
        legs = self.legs(info)
        self.assertNotIn("STRK", legs)
        self.assertAlmostEqual(legs["IBIT"], 3.0, places=2)
        self.assertAlmostEqual(sum(legs.values()), 6.0, places=2)

    def test_unplanned_order_rejected_by_adapter(self):
        c = FakeClient()
        g = gate.gate_orders([buy("STRC", 5.0)], deployable_cash=10, policy=gpol(), now=NOW).allowed[0]
        self.assertEqual(self.submit(g, c)["status"], "not_submitted_unplanned")
        self.assertEqual(c.calls, [])

    def test_tampered_leg_amount_is_unplanned(self):
        allowed, _ = self.gated(TEN)
        allowed[0].intent["notional_usd"] = 9.99
        self.assertEqual(self.submit(allowed[0], FakeClient())["status"], "not_submitted_unplanned")


# ----------------------------------------------------------------- 3 caps
class TestCaps(Base):
    def test_run_capped_at_settled_cash(self):
        _, info = self.plan([buy("STRC", 25), buy("SATA", 25), buy("STRK", 15), buy("IBIT", 35)], snapshot=snap(cash=40))
        self.assertAlmostEqual(sum(self.legs(info).values()), 40.0, places=2)

    def test_run_capped_at_160_and_daily_cap(self):
        big = [buy("STRC", 75), buy("SATA", 75), buy("STRK", 45), buy("IBIT", 105)]
        _, info = self.plan(big, snapshot=snap(cash=500))
        self.assertAlmostEqual(info["run_total_usd"], 150.0)  # daily cap 150 < run cap 160
        self.write_policy(max_daily_notional_usd=500)
        _, info2 = broker.plan_intents(big, snapshot=snap(cash=500), policy_path=self.policy,
                                       ledger_path=self.dir / "l3.json", now=NOW)
        self.assertAlmostEqual(info2["run_total_usd"], 160.0)
        self.assertTrue(all(v <= 75 for v in self.legs(info2).values()))

    def test_below_min_investable_skips(self):
        orders, info = self.plan(TEN, snapshot=snap(cash=4.99))
        self.assertEqual(info["status"], "skipped_below_min_investable")
        self.assertEqual(orders, [])

    def test_over_buying_power(self):
        c = FakeClient(bp=1.0)
        outs, _ = self.run_all(TEN, c)
        self.assertTrue(all(o["status"] == "not_submitted_review_failed" for o in outs))
        self.assertIn("buying power", outs[0]["broker_reason"])
        self.assertEqual(c.placed, [])

    def test_zero_amount_rejected(self):
        allowed, _ = self.gated(TEN)
        allowed[0].intent["notional_usd"] = 0
        out = self.submit(allowed[0], FakeClient())
        self.assertEqual(out["status"], "not_submitted_review_failed")
        self.assertIn("> 0", out["broker_reason"])

    def test_daily_cap_counts_placed(self):
        self.write_policy(max_daily_notional_usd=6.0)
        _, info = self.plan(TEN)
        self.assertAlmostEqual(info["run_total_usd"], 6.0)


# ------------------------------------------------------------- 4 orders/window
class TestOrdersAndWindow(Base):
    def test_buy_places_after_review_market_day_regular_hours(self):
        c = FakeClient()
        outs, info = self.run_all(TEN, c)
        self.assertEqual([o["status"] for o in outs], ["placed"] * 4)
        order = c.placed[0][1]
        self.assertEqual((order["type"], order["time_in_force"], order["market_hours"], order["extended_hours"]),
                         ("market", "gfd", "regular_hours", False))
        self.assertEqual(order["account_number"], ACCT)
        names = [n for n, *_ in c.calls[:5]]
        self.assertEqual(names, ["get_account", "get_recent_orders", "get_tradability", "review_order", "place_order"])

    def test_before_10am_et_not_submitted_and_not_queued(self):
        early = datetime(2026, 10, 26, 13, 59, tzinfo=timezone.utc)  # 09:59 ET
        orders, info = self.plan(TEN, snapshot=snap(now=early), now=early)
        self.assertEqual(info["status"], "not_submitted_before_window")
        self.assertEqual(orders, [])
        allowed, _ = self.gated(TEN)  # planned at 11:00
        self.assertEqual(self.submit(allowed[0], FakeClient(), now=early)["status"], "not_submitted_before_window")

    def test_after_hours_weekend_holiday_not_queued(self):
        for when in (datetime(2026, 10, 26, 19, 55, tzinfo=timezone.utc),  # 15:55 ET
                     datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc),   # Saturday
                     datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)):  # Thanksgiving
            _, info = broker.plan_intents(TEN, snapshot=snap(now=when), policy_path=self.policy,
                                          ledger_path=self.ledger, now=when)
            self.assertEqual(info["status"], "not_submitted_outside_market_hours", when)

    def test_review_reject_fails_closed_without_retry(self):
        c = FakeClient(review_ok=False)
        outs, _ = self.run_all(TEN, c)
        self.assertEqual(outs[0]["status"], "not_submitted_review_failed")
        self.assertIn("not retried", outs[0]["broker_reason"])
        self.assertEqual(c.placed, [])
        self.assertEqual(sum(1 for n, *_ in c.calls if n == "review_order"), 4)  # once per leg

    def test_not_fractional_rejected(self):
        c = FakeClient(fractional=False)
        outs, _ = self.run_all(TEN, c)
        self.assertIn("fractional", outs[0]["broker_reason"])
        self.assertEqual(c.placed, [])

    def test_account_mismatch_and_broker_rejected(self):
        outs, _ = self.run_all(TEN, FakeClient(account="5QW39737"))
        self.assertIn("account mismatch", outs[0]["broker_reason"])
        c = FakeClient(place_result={"order_id": "r1", "state": "rejected"})
        outs2, _ = self.run_all(TEN, c, snapshot=snap(now=NOW + timedelta(days=1)), now=NOW + timedelta(days=1))
        self.assertEqual(outs2[0]["status"], "not_submitted_broker_rejected")

    def test_place_error_blocks_retry_and_mismatch_flags(self):
        c = FakeClient(place_exc=TimeoutError())
        allowed, _ = self.gated(TEN)
        self.assertEqual(self.submit(allowed[0], c)["status"], "place_error_outcome_unknown")
        self.assertEqual(self.submit(allowed[0], FakeClient())["status"], "skipped_duplicate_idempotency")
        c2 = FakeClient(place_result={"order_id": "m1", "state": "queued", "symbol": "STRC", "side": "sell"})
        allowed2, _ = self.gated(TEN, snapshot=snap(now=NOW + timedelta(days=1)), now=NOW + timedelta(days=1))
        out = self.submit(allowed2[0], c2, now=NOW + timedelta(days=1))
        self.assertEqual(out["status"], "place_error_outcome_unknown")
        self.assertIn("CHECK BROKER", out["broker_reason"])


# --------------------------------------------------------------- sell / gating
class TestSellRejected(Base):
    def test_sell_rejected_even_if_gate_approved(self):
        c = FakeClient()
        ov = gate._mint_authed_override({
            "id": "o1", "approver": "Chris", "symbols": ["STRC"], "max_notional_usd": 50.0, "reason": "risk exit",
            "expires_at": "2026-10-26T20:00:00Z",
        })
        led = gate.SpendLedger(Path("/nonexistent/x.json"), persist=False)
        res = gate.gate_orders([{"symbol": "STRC", "side": "sell", "notional_usd": 2.0, "quantity": 0.1, "purpose": "risk_exit"}],
                               deployable_cash=0.0, policy=gpol(), override=ov, now=NOW, ledger=led)
        self.assertEqual(len(res.allowed), 1)
        self.assertEqual(self.submit(res.allowed[0], c)["status"], "rejected_non_buy_side")
        self.assertEqual(c.calls, [])

    def test_planner_passes_sells_through_to_be_blocked(self):
        orders, _ = self.plan(TEN + [{"symbol": "TSLA", "side": "sell", "notional_usd": 3, "purpose": "rebalance"}])
        self.assertIn("sell", [o["side"] for o in orders])
        res = gate.gate_orders(orders, deployable_cash=100, policy=gpol(), now=NOW)
        self.assertEqual(res.blocked[0][1].status, "blocked_no_sell_guard")

    def test_non_gated_dict_rejected(self):
        c = FakeClient()
        self.assertEqual(self.submit({"symbol": "STRC", "side": "buy", "notional_usd": 2}, c)["status"], "rejected_not_gated")
        self.assertEqual(c.calls, [])

    def test_grok_client_refuses_non_buy(self):
        cl = broker.GrokMcpClient(runner=mock.Mock())
        with self.assertRaises(broker.BrokerError):
            cl.place_order({"symbol": "STRC", "side": "sell", "dollar_amount": "1.00"})
        cl.runner.assert_not_called()

    def test_runbook_says_sells_are_manual_chris(self):
        text = (ROOT / "investment" / "FUND_MANAGER_RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("any sell is a manual action chris takes himself", text.lower())


# ------------------------------------------------------------- 5 duplicates
class TestDuplicates(Base):
    def test_duplicate_skipped_by_idempotency_key(self):
        c = FakeClient()
        allowed, _ = self.gated(TEN)
        self.assertEqual(self.submit(allowed[0], c)["status"], "placed")
        self.assertEqual(self.submit(allowed[0], c)["status"], "skipped_duplicate_idempotency")
        self.assertEqual(len(c.placed), 1)

    def test_open_buy_from_earlier_day_blocks_symbol(self):
        c = FakeClient(orders=[{"symbol": "IBIT", "side": "buy", "state": "queued", "dollar_amount": 9.0, "order_id": "x",
                                "created_at": (NOW - timedelta(days=1)).isoformat()}])
        outs, _ = self.run_all(TEN, c)
        by = {c.placed[i][1]["symbol"] for i in range(len(c.placed))}
        self.assertNotIn("IBIT", by)
        self.assertIn("skipped_duplicate_broker_order", [o["status"] for o in outs])

    def test_matching_recent_fill_from_yesterday_skipped(self):
        c = FakeClient(orders=[{"symbol": "STRC", "side": "buy", "state": "filled", "dollar_amount": 2.5, "order_id": "y",
                                "created_at": (NOW - timedelta(hours=20)).isoformat()}])
        outs, _ = self.run_all(TEN, c)
        self.assertEqual(outs[0]["status"], "skipped_duplicate_broker_order")

    def test_broker_orders_unavailable_fails_closed(self):
        c = FakeClient()
        c.get_recent_orders = lambda a, s: None
        outs, _ = self.run_all(TEN, c)
        self.assertEqual(outs[0]["status"], "not_submitted_review_failed")
        self.assertEqual(c.placed, [])

    def test_same_day_manual_fill_outside_adapter_skips_and_claims_day(self):
        # Nakatoshi's Mon 10/26 ~10:15 ET manual $7.62 buy, placed outside the adapter
        manual = [{"symbol": s, "side": "buy", "state": "filled", "dollar_amount": a, "order_id": f"man-{s}",
                   "created_at": "2026-10-26T14:15:00Z"} for s, a in (("STRC", 1.9), ("SATA", 1.9), ("STRK", 1.15), ("IBIT", 2.67))]
        c = FakeClient(orders=manual)
        outs, _ = self.run_all(TEN, c)
        self.assertEqual(outs[0]["status"], "skipped_already_bought_today")
        self.assertIn("outside this run", outs[0]["broker_reason"])
        self.assertEqual(c.placed, [])
        self.assertEqual(self.ledger_data()["days"]["2026-10-26"]["claimed_by"], "external")
        _, info = self.plan(TEN)
        self.assertEqual(info["status"], "skipped_already_bought_today")
        ok, why = broker.should_buy(snapshot=snap(), policy_path=self.policy, ledger_path=self.ledger, now=NOW)
        self.assertFalse(ok)
        self.assertIn("skipped_already_bought_today", why)

    def test_own_orders_in_same_run_do_not_trip_day_guard(self):
        c = FakeClient()
        outs, _ = self.run_all(TEN, c)
        self.assertEqual([o["status"] for o in outs], ["placed"] * 4)


# ------------------------------------------------------- 6 bp-poll / day guard
class TestShouldBuyAndDayGuard(Base):
    def sb(self, snapshot, now=NOW):
        return broker.should_buy(snapshot=snapshot, policy_path=self.policy, ledger_path=self.ledger, now=now)

    def test_cash_above_zero_but_settled_under_5_does_not_buy(self):
        ok, why = self.sb(snap(cash=7.62, bp=3.0))
        self.assertFalse(ok)
        self.assertIn("skipped_below_min_investable", why)

    def test_settled_5_or_more_triggers(self):
        self.assertTrue(self.sb(snap(cash=7.62))[0])
        self.assertTrue(self.sb(snap(cash=5.0))[0])
        self.assertFalse(self.sb(snap(cash=20, settled_cash=2.0))[0])  # explicit settled field wins

    def test_before_window_and_stale_snapshot(self):
        early = datetime(2026, 10, 26, 13, 30, tzinfo=timezone.utc)
        self.assertIn("before_window", self.sb(snap(now=early), now=early)[1])
        self.assertFalse(self.sb(snap(now=NOW - timedelta(hours=7)))[0])

    def test_once_per_et_day_shared_between_runs(self):
        c = FakeClient()
        outs, _ = self.run_all(TEN, c)
        self.assertEqual(outs[0]["status"], "placed")
        self.assertFalse(self.sb(snap(cash=50))[0])
        _, info = self.plan(TEN, snapshot=snap(cash=50), now=NOW + timedelta(hours=2))
        self.assertEqual(info["status"], "skipped_already_bought_today")
        nxt = NOW + timedelta(days=1)
        self.assertTrue(self.sb(snap(cash=50, now=nxt), now=nxt)[0])

    def test_bp_poll_runner_uses_should_buy(self):
        text = (ROOT / "treasury" / "fund_manager_bp_poll.sh").read_text(encoding="utf-8")
        self.assertIn("python3 -m treasury.fund_manager_broker --should-buy", text)
        self.assertNotIn('if [[ "${RR}" -eq 2 ]]; then', text)
        self.assertEqual(REPO_POLICY["automation"]["bp_poll_trigger"], "settled_cash_gte_min_investable_once_per_et_day")


# ---------------------------------------------------------- 8 #701 posting
class TestDecisionPosting(Base):
    def _execute(self, client, record_fn):
        orders, info = self.plan(TEN + [buy("TSLA", 2.0)], snapshot=snap(cash=12))

        def sub(g):
            return broker.submit(g, client=client, policy_path=self.policy, ledger_path=self.ledger, now=NOW)

        return gate.execute_intents(orders, deployable_cash=12, submit=sub, policy=gpol(), now=NOW,
                                    record_fn=record_fn, plan=info)

    def test_placed_orders_post_decision_entry(self):
        seen = []
        out = self._execute(FakeClient(), seen.append)
        self.assertEqual([r["status"] for r in out["placed"]], ["placed"] * 4)
        entry = [e for e in seen if e.get("path") == "order_gate+broker_adapter"][0]
        self.assertEqual(entry["kind"], "deploy")
        self.assertIn("placed 4 buy(s) $12.00", entry["summary"])
        a = [x for x in entry["actions"] if x["status"] == "placed"][0]
        for k in ("symbol", "notional_usd", "qty", "fill_price", "order_id"):
            self.assertIsNotNone(a[k], k)
        self.assertIn("not_submitted_sleeve_gate", [x["status"] for x in entry["actions"]])

    def test_posts_through_append_decision_to_701_mocked(self):
        posts = []

        def fake_post(title, text, *, dry_run=False, as_markdown=False):
            posts.append({"title": title, "text": text})
            return {"ok": True, "posted": True, "issue": "701", "status": 201}

        p = self.dir / "dec.jsonl"
        with mock.patch("treasury.fund_manager.post_ops_github", side_effect=fake_post), mock.patch.dict(
            os.environ, {"FM_DECISION_GITHUB_IN_TEST": "1"}, clear=False
        ), mock.patch.object(fm, "_load_sell_override", return_value=(None, "")), mock.patch.object(
            fm, "_append_journal_markdown"
        ):
            self._execute(FakeClient(), lambda e: fm.append_decision(e, path=p, also_journal=False))
        self.assertTrue(any("fund-manager deploy" in x["title"] for x in posts))
        rows = fm.load_decision_log(path=p, limit=5)
        self.assertTrue(any(r.get("kind") == "deploy" and not r.get("blocked_sell") for r in rows))

    def test_nothing_to_report_posts_nothing(self):
        seen = []
        gate.execute_intents([], deployable_cash=10, submit=mock.Mock(), policy=gpol(), now=NOW, record_fn=seen.append,
                             plan={"status": "skipped_below_min_investable"})
        self.assertEqual(seen, [])


# ------------------------------------------------- adapter selection / live
class TestAdapterSelectionAndLive(Base):
    def test_missing_adapter_still_not_submitted(self):
        with mock.patch.dict(os.environ, {gate.BROKER_ADAPTER_ENV: ""}):
            self.assertIsNone(gate.load_broker_adapter())
        out = gate.execute_intents([buy()], deployable_cash=10.0, submit=None, policy=gpol(), now=NOW, record=False)
        self.assertEqual(out["placed"][0]["status"], "not_submitted_no_broker_adapter")
        self.assertEqual(out["broker_calls"], 0)

    def test_env_resolves_to_this_adapter_with_planner(self):
        with mock.patch.dict(os.environ, {gate.BROKER_ADAPTER_ENV: broker.ADAPTER_SPEC}):
            sub = gate.load_broker_adapter()
        self.assertIs(sub, broker.submit)
        self.assertIs(sub.plan, broker.plan_intents)

    def test_gate_cli_runs_planner(self):
        f = self.dir / "fund_manager_intents.json"
        f.write_text(json.dumps({"orders": TEN + [buy("TSLA", 2.0)]}))
        planner = mock.Mock(return_value=([], {"status": "skipped_below_min_investable"}))
        sub = mock.Mock()
        sub.plan = planner
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.object(gate, "load_broker_adapter", return_value=sub), mock.patch.object(
            gate, "_deployable_cash_from_snapshot", return_value=12.0
        ), mock.patch.object(gate, "load_override", return_value=(None, "")), redirect_stdout(buf):
            rc = gate.main(["--execute-intents", str(f), "--no-record"])
        self.assertEqual(rc, 0)
        planner.assert_called_once()
        sub.assert_not_called()
        self.assertEqual(json.loads(buf.getvalue())["plan"]["status"], "skipped_below_min_investable")

    def test_no_client_configured(self):
        outs, _ = self.run_all(TEN, None)
        self.assertEqual(outs[0]["status"], "not_submitted_no_broker_client")

    def test_live_false_adapter_places_nothing(self):
        allowed, _ = self.gated(TEN)
        self.write_policy(live=False)
        c = FakeClient()
        self.assertEqual(self.submit(allowed[0], c)["status"], "not_submitted_live_false")
        self.assertEqual(c.calls, [])
        self.assertFalse(broker.should_buy(snapshot=snap(), policy_path=self.policy, ledger_path=self.ledger, now=NOW)[0])

    def test_live_false_gate_never_calls_adapter(self):
        sub = mock.Mock()
        out = gate.execute_intents([buy()], deployable_cash=10.0, submit=sub, policy=gpol(live=False), now=NOW, record=False)
        sub.assert_not_called()
        self.assertEqual(out["would_place"][0]["status"], "would_place")

    def test_kill_switch_and_disabled_config(self):
        allowed, _ = self.gated(TEN)
        c = FakeClient()
        with mock.patch.dict(os.environ, {broker.DISABLE_ENV: "1"}):
            self.assertEqual(self.submit(allowed[0], c)["status"], "not_submitted_adapter_disabled")
        self.write_policy(enabled=False)
        self.assertEqual(self.submit(allowed[0], c)["status"], "not_submitted_adapter_config")
        self.assertEqual(c.calls, [])

    def test_grok_client_place_allows_only_place_equity_order(self):
        seen = {}

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0, stdout=json.dumps({"order_id": "g1", "state": "queued"}))

        cl = broker.GrokMcpClient(grok_bin="grok", runner=runner)
        out = cl.place_order({"symbol": "STRC", "side": "buy", "dollar_amount": "1.00"})
        self.assertEqual(out["order_id"], "g1")
        cmd = seen["cmd"]
        allows = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--allow"]
        denies = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--deny"]
        self.assertEqual(allows, ["MCPTool(robinhood-trading__place_equity_order)"])
        self.assertNotIn("MCPTool(robinhood-trading__place_*)", denies)
        for t in ("place_option_order", "place_crypto_order", "*sell*", "*short*", "replace_*", "exercise_option"):
            self.assertIn(f"MCPTool(robinhood-trading__{t})", denies)
        cl.runner = lambda cmd, **kw: (seen.update(cmd=cmd), mock.Mock(returncode=0, stdout='{"tradable":true,"fractional_tradable":true}'))[1]
        cl.get_tradability("STRC")
        self.assertIn("MCPTool(robinhood-trading__place_*)", [seen["cmd"][i + 1] for i, a in enumerate(seen["cmd"]) if a == "--deny"])


if __name__ == "__main__":
    unittest.main()
