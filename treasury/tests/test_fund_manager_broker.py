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
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury import fund_manager as fm  # noqa: E402
from treasury import fund_manager_broker as broker  # noqa: E402
from treasury import fund_manager_order_gate as gate  # noqa: E402

NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)  # Mon 11:00 ET
REPO_POLICY = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
# Account id is read from the repo policy so no full account number lives in this test (last 4: 1752).
ACCT = REPO_POLICY["account"]["account_number"]


def pos(sym, qty, px, *, priced=True):
    """A position. ``px`` is the mark (last price) unless ``priced`` is false.

    ``priced=False`` leaves only average buy price, which sizing must not treat
    as market value.
    """
    row = {"symbol": sym, "quantity": str(qty), "average_buy_price": str(px)}
    if priced:
        row["last_price"] = str(px)
    return row


# BTC sleeve 50 of 170 invested (29%) -> under 40%
UNDER = [pos("TSLA", 1, 60), pos("SPCX", 1, 60), pos("STRC", 0.5, 100)]
# BTC sleeve 100 of 150 (67%) -> over 40%; TSLA 30 vs SPCX 20
OVER = [pos("TSLA", 1, 30), pos("SPCX", 1, 20), pos("STRC", 1, 100)]


def _marked_total(positions):
    """Sum of market values, or None if any held row has no market price."""
    total = 0.0
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        try:
            qty = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            return None
        if qty <= 0:
            continue
        if p.get("market_value") not in (None, ""):
            total += float(p["market_value"])
            continue
        px = None
        for k in ("last_price", "price", "mark_price"):
            if p.get(k) not in (None, ""):
                px = float(p[k])
                break
        if px is None:
            return None
        total += qty * px
    return total


def snap(cash=10.0, bp=None, positions=UNDER, now=NOW, **extra):
    ag = {"account_number": ACCT, "cash": cash, "buying_power": cash if bp is None else bp,
          "unleveraged_buying_power": cash if bp is None else bp, "positions": positions,
          "as_of": (now - timedelta(hours=1)).isoformat()}
    marked = _marked_total(positions)
    if marked is not None and "total_value" not in extra:
        ag["total_value"] = round(marked + float(cash), 4)
    ag.update(extra)
    return {"agentic": ag}


class FakeClient:
    def __init__(self, bp=200.0, orders=None, tradable=True, fractional=True, review_ok=True,
                 place_result=None, place_exc=None, account=ACCT, quotes=None, quote_exc=None):
        self.quotes, self.quote_exc = quotes, quote_exc
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

    def get_quotes(self, symbols):
        self.calls.append(("get_quotes", tuple(symbols)))
        if self.quote_exc:
            raise self.quote_exc
        return dict(self.quotes or {})

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
        os.environ["FM_PENDING_APPROVALS_PATH"] = str(self.dir / "pending.json")
        os.environ["FM_DECISIONS_PATH"] = str(self.dir / "decisions.jsonl")
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

    def plan(self, intents, snapshot=None, now=NOW, persist=True, **kw):
        return broker.plan_intents(intents, snapshot=snapshot or snap(), policy_path=self.policy,
                                   ledger_path=self.ledger, now=now, persist=persist, **kw)

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
        self.assertTrue(cfg["live"])  # Chris resumed Agentic buys 2026-10-25 2:58 PM ET
        self.assertEqual(set(cfg["allowlist"]), {
            "STRC", "SATA", "STRK", "SATS", "MSTR", "ASST", "BITA", "IBIT",
            "MARA", "RIOT", "CLSK", "IREN", "WULF", "TSLA", "SPCX",
            "PLTR", "CRWD", "S", "CCJ", "GEV", "VRT", "OKLO",
        })
        self.assertEqual({k: float(v) for k, v in cfg["fixed_weights"].items()},
                         {"STRC": 0.25, "SATA": 0.25, "STRK": 0.15, "SATS": 0.10})
        self.assertEqual(float(cfg["floating_weight"]), 0.25)
        self.assertEqual({k: float(v) for k, v in cfg["price_caps"].items()}, {"SATS": 95.0})
        self.assertEqual(cfg["floating"], ("IBIT", "MSTR", "ASST", "BITA", "RIOT", "CLSK", "WULF", "IREN"))
        self.assertNotIn("MARA", cfg["floating"])
        self.assertEqual(cfg["fold_into"], "IBIT")
        self.assertFalse(cfg["honor_ai_floating"])
        self.assertFalse(REPO_POLICY["execution"]["broker_adapter"]["honor_ai_floating"])
        self.assertEqual(cfg["stocks"], ("TSLA", "SPCX", "PLTR", "CRWD", "S", "CCJ", "GEV", "VRT", "OKLO", "MARA"))
        self.assertIn("MARA", cfg["theme_symbols"])
        self.assertNotIn("TSLA", cfg["theme_symbols"])
        btc = REPO_POLICY["sleeves"]["btc_digital_credit"]
        self.assertIn("IBIT", btc["symbols"])
        self.assertEqual(btc["new_money_weights"]["floating_default"], "IBIT")
        self.assertNotIn("BTC", btc["new_money_weights"]["floating_universe"])
        self.assertEqual(REPO_POLICY["execution"]["broker_adapter"]["low_preference"]["MSTR"]["until"], "2026-11-30")

    def test_prompt_names_ibit_standin_and_mstr_low_preference(self):
        text = (ROOT / "treasury" / "fund_manager_daily_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("native-BTC stand-in", text)
        self.assertIn("LOW PREFERENCE through 2026-11-30", text)
        self.assertIn("The floating 25% is always IBIT", text)
        self.assertIn("STRC, SATA, STRK, SATS + floating", text)
        self.assertIn("SATS 10%", text)
        self.assertIn("honor_ai_floating", text)
        self.assertNotIn("ONLY choose how the floating 35% splits", text)

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
        # Default policy ignores AI floating names, so MSTR is never chosen and
        # the low-preference note (which fires only when the name is a leg) stays quiet.
        _, info = self.plan([buy("STRC", 2.5), buy("SATA", 2.5), buy("STRK", 1.5), buy("MSTR", 3.5)])
        self.assertNotIn("MSTR", self.legs(info))
        self.assertEqual(self.legs(info)["IBIT"], 3.08)
        self.assertFalse(any("low preference" in n for n in info["notes"]))
        self.write_policy(honor_ai_floating=True)
        _, chosen = self.plan([buy("STRC", 2.5), buy("SATA", 2.5), buy("STRK", 1.5), buy("MSTR", 3.5)])
        self.assertTrue(any("MSTR has low preference until 2026-11-30" in n for n in chosen["notes"]))
        _, info2 = broker.plan_intents([buy("STRC", 2.5), buy("SATA", 2.5), buy("STRK", 1.5), buy("MSTR", 3.5)],
                                       snapshot=snap(now=NOW + timedelta(days=36)), policy_path=self.policy,
                                       ledger_path=self.dir / "l2.json", now=NOW + timedelta(days=36))
        self.assertFalse(any("low preference" in n for n in info2["notes"]))


# ------------------------------------------------------- 2 sleeve gate/weights
class TestSleeveGateAndWeights(Base):
    def test_exact_plan_is_planned(self):
        orders, info = self.plan(TEN)
        self.assertEqual(info["status"], "planned")
        self.assertEqual(self.legs(info), {"STRC": 2.18, "SATA": 2.18, "STRK": 1.31, "IBIT": 3.08})
        self.assertTrue(all(o["run_id"] == info["run_id"] for o in orders))

    def test_stock_buy_under_40pct_dropped_and_redirected(self):
        orders, info = self.plan(TEN + [buy("TSLA", 5.0)], snapshot=snap(cash=20))
        self.assertEqual(info["status"], "weights_repaired")
        self.assertNotIn("TSLA", self.legs(info))
        self.assertEqual(info["dropped"][0]["status"], "not_submitted_sleeve_gate")
        legs = self.legs(info)
        gldm = sum(p["notional_usd"] for p in (info.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(legs.values()) + gldm, 20.0, places=2)
        self.assertAlmostEqual(legs["STRC"], 4.37, places=2)
        self.assertAlmostEqual(legs["STRK"], 2.62, places=2)

    def test_over_40pct_stocks_get_rest_toward_50_50(self):
        # BTC 100 / invested 150; run 30 -> BTC target 0.4*180-100 < 0 -> all to stocks.
        # Sleeve pins are buy-only. Theme names are pending approval, not executable legs.
        # The shipped theme_buy_hold would leave that theme cash uninvested. This
        # assertion is the cleared-flag path.
        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        policy["theme_buy_hold"]["active"] = False
        self.policy.write_text(json.dumps(policy), encoding="utf-8")
        _, info = self.plan([buy("TSLA", 15), buy("SPCX", 15)], snapshot=snap(cash=30, positions=OVER))
        legs = self.legs(info)
        self.assertGreater(info["btc_share_before"], 0.4)
        self.assertNotIn("STRC", legs)
        theme = set(broker.load_adapter_config(self.policy)[0]["theme_symbols"])
        self.assertTrue(set(legs).isdisjoint(theme))
        pending = info.get("pending_approvals") or []
        self.assertTrue(all(p["side"] == "buy" and p["auto_execute"] is False for p in pending))
        spent = sum(legs.values()) + sum(p["notional_usd"] for p in pending)
        self.assertAlmostEqual(spent, 30.0, places=2)
        self.assertEqual(info["status"], "weights_repaired")

    def test_fixed_legs_exact_ai_amounts_within_tolerance_not_flagged(self):
        _, info = self.plan([buy("STRC", 2.5), buy("SATA", 2.0), buy("STRK", 1.5), buy("IBIT", 3.0)])
        self.assertEqual(info["status"], "planned")
        self.assertEqual(self.legs(info), {"STRC": 2.18, "SATA": 2.18, "STRK": 1.31, "IBIT": 3.08})

    def test_weights_outside_tolerance_repaired(self):
        _, info = self.plan([buy("STRC", 8.0), buy("IBIT", 2.0)])
        self.assertEqual(info["status"], "weights_repaired")
        self.assertEqual(self.legs(info), {"STRC": 2.18, "SATA": 2.18, "STRK": 1.31, "IBIT": 3.08})
        self.assertTrue(info["repairs"])

    def test_ai_floating_names_are_ignored(self):
        # RIOT stands in for a floating miner. MARA is an energy pin, not floating.
        _, info = self.plan([buy("STRC", 5), buy("SATA", 5), buy("STRK", 3), buy("RIOT", 3.5), buy("IBIT", 3.5)],
                            snapshot=snap(cash=20))
        legs = self.legs(info)
        self.assertNotIn("RIOT", legs)
        self.assertNotIn("MARA", legs)
        self.assertEqual(legs["IBIT"], 6.14)
        self.assertIn("ignored", info["floating_source"])
        self.write_policy(honor_ai_floating=True)
        _, honoured = self.plan(
            [buy("STRC", 5), buy("SATA", 5), buy("STRK", 3), buy("RIOT", 3.5), buy("IBIT", 3.5)],
            snapshot=snap(cash=20),
        )
        split = self.legs(honoured)
        # floating 25% of the post-GLDM sleeve cash, split RIOT/IBIT.
        # SATS has no quote and folds into IBIT. GLDM is pending, not a leg.
        self.assertEqual(split["RIOT"], 2.18)
        self.assertEqual(split["IBIT"], 3.96)

    def test_leg_under_one_dollar_folds_into_ibit(self):
        _, info = self.plan([buy("STRC", 1.5), buy("SATA", 1.5), buy("STRK", 0.9), buy("IBIT", 2.1)], snapshot=snap(cash=6))
        legs = self.legs(info)
        self.assertNotIn("STRK", legs)
        self.assertAlmostEqual(legs["IBIT"], 2.63, places=2)
        gldm = sum(p["notional_usd"] for p in (info.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(legs.values()) + gldm, 6.0, places=2)

    def test_unplanned_order_rejected_by_adapter(self):
        c = FakeClient()
        g = gate.gate_orders([buy("STRC", 5.0)], deployable_cash=10, policy=gpol(), now=NOW).allowed[0]
        self.assertEqual(self.submit(g, c)["status"], "not_submitted_unplanned")
        self.assertEqual(c.calls, [])

    def test_tampered_leg_amount_is_unplanned(self):
        allowed, _ = self.gated(TEN)
        allowed[0].intent["notional_usd"] = 9.99
        self.assertEqual(self.submit(allowed[0], FakeClient())["status"], "not_submitted_unplanned")


# ------------------------------------- 2c SATS fixed leg (Chris 2026-10-28)
class TestSatsFixedLeg(Base):
    """SATS 10 / floating 25; SATS bought only on a live quote <= $95 (market
    dollar orders carry no limit, so the planner's read-only quote is the cap)."""

    def write_nmw(self, **changes):
        p = copy.deepcopy(REPO_POLICY)
        nmw = p["sleeves"]["btc_digital_credit"]["new_money_weights"]
        for k, v in changes.items():
            if v is None:
                nmw.pop(k, None)
            else:
                nmw[k] = v
        self.policy.write_text(json.dumps(p), encoding="utf-8")
        return p

    def test_sats_bought_when_quote_under_cap(self):
        c = FakeClient(quotes={"SATS": 90.0})
        _, info = self.plan(TEN, client=c, snapshot=snap(cash=16))
        self.assertEqual(self.legs(info), {"STRC": 3.5, "SATA": 3.5, "STRK": 2.1, "SATS": 1.4, "IBIT": 3.5})
        self.assertIn(("get_quotes", ("SATS",)), c.calls)  # read-only price check
        self.assertFalse(any(n in ("place_order", "review_order") for n, *_ in c.calls))
        self.assertIn("SATS", info["price_checks"])

    def test_sats_quote_exactly_at_cap_is_bought(self):
        _, info = self.plan([], client=FakeClient(quotes={"SATS": 95.0}), snapshot=snap(cash=16))
        self.assertAlmostEqual(self.legs(info)["SATS"], 1.4, places=2)

    def test_sats_quote_over_cap_folds_into_ibit(self):
        _, info = self.plan(TEN, client=FakeClient(quotes={"SATS": 95.01}), snapshot=snap(cash=16))
        legs = self.legs(info)
        self.assertNotIn("SATS", legs)
        self.assertEqual(legs["IBIT"], 4.9)
        self.assertIn("> $95", info["skipped_fixed"]["SATS"])
        self.assertTrue(any("SATS leg" in n and "folded into IBIT" in n for n in info["notes"]))

    def test_sats_no_quote_fails_closed(self):
        for c in (None, FakeClient(quotes={}), FakeClient(quote_exc=broker.BrokerError("grok timeout")),
                  FakeClient(quotes={"SATS": "n/a"})):
            _, info = broker.plan_intents([], snapshot=snap(), policy_path=self.policy,
                                          ledger_path=self.dir / f"l{id(c)}.json", now=NOW, client=c)
            self.assertNotIn("SATS", self.legs(info))
            gldm = sum(p["notional_usd"] for p in (info.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
            self.assertAlmostEqual(sum(self.legs(info).values()) + gldm, 10.0, places=2)
            self.assertIn("no live quote", info["skipped_fixed"]["SATS"])

    def test_blocked_sats_folds_without_quote_call(self):
        p = copy.deepcopy(REPO_POLICY)
        p["guardrails"]["blocked_symbols"] = list(p["guardrails"].get("blocked_symbols") or []) + ["SATS"]
        self.policy.write_text(json.dumps(p), encoding="utf-8")
        c = FakeClient(quotes={"SATS": 50.0})
        _, info = self.plan([], client=c)
        self.assertNotIn("SATS", self.legs(info))
        self.assertNotIn(("get_quotes", ("SATS",)), c.calls)
        self.assertIn("blocked", info["skipped_fixed"]["SATS"])

    def test_sats_leg_submits_end_to_end(self):
        c = FakeClient(quotes={"SATS": 90.0})
        orders, info = self.plan([], client=c, snapshot=snap(cash=16))
        res = gate.gate_orders(orders, deployable_cash=1000.0, policy=gpol(), now=NOW)
        sats = [g for g in res.allowed if g.intent["symbol"] == "SATS"]
        self.assertEqual(len(sats), 1)
        out = self.submit(sats[0], c)
        self.assertEqual(out["status"], "placed", out)
        self.assertEqual([o for n, o in c.placed][0]["symbol"], "SATS")

    def test_fixed_legs_are_data_driven(self):
        cfg, err = broker.load_adapter_config(self.policy)
        self.assertEqual(err, "")
        self.assertEqual(set(cfg["fixed_weights"]), {"STRC", "SATA", "STRK", "SATS"})  # ruled_*/flags skipped
        # the pre-10/28 policy (no SATS, floating 35) still loads
        self.write_nmw(SATS=None, floating=0.35)
        cfg, err = broker.load_adapter_config(self.policy)
        self.assertEqual(err, "")
        self.assertEqual(set(cfg["fixed_weights"]), {"STRC", "SATA", "STRK"})

    def test_weights_must_sum_to_one(self):
        self.write_nmw(floating=0.35)  # 1.10 with SATS
        cfg, err = broker.load_adapter_config(self.policy)
        self.assertIsNone(cfg)
        self.assertIn("sum to 1", err)
        orders, info = self.plan(TEN)
        self.assertEqual(orders, [])
        self.assertEqual(info["status"], broker.S_CONFIG)

    def test_fixed_leg_must_be_on_allowlist(self):
        self.write_nmw(SATS=None, NVDA=0.10)
        cfg, err = broker.load_adapter_config(self.policy)
        self.assertIsNone(cfg)
        self.assertIn("NVDA", err)
        p = copy.deepcopy(REPO_POLICY)
        p["execution"]["broker_adapter"]["symbol_allowlist"].remove("SATS")
        self.policy.write_text(json.dumps(p), encoding="utf-8")
        cfg, err = broker.load_adapter_config(self.policy)
        self.assertIsNone(cfg)
        self.assertIn("SATS", err)

    def test_negative_fixed_weight_rejected(self):
        self.write_nmw(SATS=-0.1, floating=0.45)
        cfg, err = broker.load_adapter_config(self.policy)
        self.assertIsNone(cfg)
        self.assertIn("SATS", err)


# ------------------------------------------------ 4b deterministic run size
class TestDeterministicRunSize(Base):
    def test_ai_error_falls_back_to_ibit_and_still_runs(self):
        orders, info = self.plan("unreadable", ai_note="AI intents file unreadable")
        self.assertEqual(info["status"], "planned")
        self.assertEqual(self.legs(info), {"STRC": 2.18, "SATA": 2.18, "STRK": 1.31, "IBIT": 3.08})
        self.assertIn("IBIT_fallback", info["floating_source"])
        self.assertIn("unreadable", info["floating_source"])
        self.assertEqual(orders[0], "unreadable")  # malformed payload still reaches the gate to be blocked
        res = gate.gate_orders(orders, deployable_cash=100, policy=gpol(), now=NOW)
        self.assertEqual(len(res.allowed), 4)
        self.assertEqual(len(res.blocked), 1)

    def test_empty_proposal_is_fully_invested(self):
        orders, info = self.plan([], snapshot=snap(cash=37.5))
        legs = self.legs(info)
        gldm = sum(p["notional_usd"] for p in (info.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(legs.values()) + gldm, 37.5, places=2)
        self.assertAlmostEqual(legs["IBIT"], 11.50, places=2)
        self.assertIn("AI proposed nothing", info["floating_source"])
        self.assertEqual(len(orders), 4)

    def test_nothing_valid_proposed_still_fully_invested(self):
        _, info = self.plan([buy("NVDA", 10.0), {"symbol": "TSLA", "side": "sell", "notional_usd": 3}])
        gldm = sum(p["notional_usd"] for p in (info.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(self.legs(info).values()) + gldm, 10.0, places=2)
        self.assertIn("no valid floating", info["floating_source"])

    def test_ai_proposed_totals_ignored(self):
        _, small = self.plan(TEN, snapshot=snap(cash=40))  # AI proposes $10 of $40
        self.assertAlmostEqual(small["run_total_usd"], 40.0)
        gldm = sum(p["notional_usd"] for p in (small.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(self.legs(small).values()) + gldm, 40.0, places=2)
        self.assertTrue(any("ignored" in n for n in small["notes"]))
        big = [buy("STRC", 75), buy("SATA", 75), buy("STRK", 45), buy("IBIT", 105)]
        _, capped = broker.plan_intents(big, snapshot=snap(cash=12), policy_path=self.policy,
                                        ledger_path=self.dir / "l4.json", now=NOW)
        gldm = sum(p["notional_usd"] for p in (capped.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(self.legs(capped).values()) + gldm, 12.0, places=2)

    def test_per_order_cap_spills_to_ibit_when_fully_invested(self):
        self.write_policy(max_daily_notional_usd=500)
        _, info = self.plan([buy("MARA", 1.0)], snapshot=snap(cash=500))
        legs = self.legs(info)
        gldm = sum(p["notional_usd"] for p in (info.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(legs.values()) + gldm, 160.0, places=2)
        self.assertTrue(all(v <= 75 for v in legs.values()))

    def test_gate_cli_missing_intents_still_runs_planner(self):
        planner = mock.Mock(return_value=([], {"status": "planned"}))
        sub = mock.Mock()
        sub.plan = planner
        import io
        from contextlib import redirect_stdout

        with mock.patch.object(gate, "load_broker_adapter", return_value=sub), mock.patch.object(
            gate, "_deployable_cash_from_snapshot", return_value=12.0
        ), mock.patch.object(gate, "load_override", return_value=(None, "")), redirect_stdout(io.StringIO()):
            rc = gate.main(["--execute-intents", str(self.dir / "missing.json"), "--no-record"])
        self.assertEqual(rc, 0)
        args, kw = planner.call_args
        self.assertEqual(args[0], [])
        self.assertIn("errored/timed out", kw["ai_note"])

    def test_runners_time_box_ai_and_run_gate_without_grok(self):
        for name in ("fund_manager_daily.sh", "fund_manager_bp_poll.sh"):
            text = (ROOT / "treasury" / name).read_text(encoding="utf-8")
            self.assertIn('timeout "${FM_GROK_TIMEOUT_S:-1800}" grok -p', text, name)
            tail = text[text.index("grok missing"):]
            self.assertIn("fund_manager_order_gate --execute-intents", tail.split("\nelif")[0], name)


# ------------------------------------------- 2b valuing the 40% test (quotes)
# Cost only: no last price and no market value. Quotes must supply the mark.
NOVALUE = [pos("TSLA", 1, 60, priced=False), pos("SPCX", 1, 60, priced=False), pos("STRC", 0.5, 100, priced=False)]


class TestValuationQuotes(Base):
    def test_live_quotes_used_when_snapshot_has_no_market_value(self):
        c = FakeClient(quotes={"TSLA": 10, "SPCX": 10, "STRC": 200})  # BTC 100 / 120 = 83%
        # Quoted book 120 + cash 20. The stated total has to be that market NAV.
        _, info = self.plan(
            [buy("TSLA", 5)],
            snapshot=snap(cash=20, positions=NOVALUE, total_value="140"),
            client=c,
        )
        self.assertEqual(info["valuation"], "live_quotes")
        self.assertAlmostEqual(info["btc_share_before"], 0.8333, places=3)
        self.assertIn("TSLA", self.legs(info))  # over 40% -> stocks allowed
        self.assertEqual(c.calls, [("get_recent_orders", ACCT), ("get_quotes", ("SPCX", "STRC", "TSLA"))])
        self.assertFalse(any(n == "place_order" for n, *_ in c.calls))

    def test_quote_failure_blocks_the_buy_and_logs_one_decision(self):
        c = FakeClient(quote_exc=broker.BrokerError("grok timeout"))
        orders, info = self.plan([buy("TSLA", 5)], snapshot=snap(cash=20, positions=NOVALUE), client=c)
        self.assertEqual(orders, [])
        self.assertEqual(info["status"], broker.S_BOOK_UNKNOWN)
        self.assertIn("missing market price", info["reason"])
        self.assertNotIn("cost_basis", info["reason"])
        self.assertNotIn("TSLA", self.legs(info))
        rows = [
            json.loads(line)
            for line in (self.dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 1)
        self.assertIn("missing market price", rows[0]["reason"])
        self.assertEqual(rows[0]["actions"], [])
        # A second pass the same day does not add another row and does not raise.
        again, info2 = self.plan([buy("TSLA", 5)], snapshot=snap(cash=20, positions=NOVALUE), client=c)
        self.assertEqual(again, [])
        self.assertEqual(info2["status"], broker.S_BOOK_UNKNOWN)
        rows2 = [
            json.loads(line)
            for line in (self.dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows2), 1)

    def test_no_client_or_partial_quotes_block_unpriced_symbols(self):
        _, info = self.plan([], snapshot=snap(cash=20, positions=NOVALUE))
        self.assertEqual(info["status"], broker.S_BOOK_UNKNOWN)
        self.assertIn("missing market price", info["reason"])
        self.assertIn("no broker client", info.get("valuation") or info["reason"])
        c = FakeClient(quotes={"STRC": 200})
        _, info2 = broker.plan_intents([], snapshot=snap(cash=20, positions=NOVALUE), policy_path=self.policy,
                                       ledger_path=self.dir / "l5.json", now=NOW, client=c)
        self.assertEqual(info2["status"], broker.S_BOOK_UNKNOWN)
        self.assertIn("SPCX", info2["reason"])
        self.assertIn("TSLA", info2["reason"])
        self.assertNotIn("cost_basis", info2["reason"])

    def test_snapshot_market_value_needs_no_quotes(self):
        mv = [dict(p, market_value=str(float(p["quantity"]) * float(p["average_buy_price"]))) for p in NOVALUE]
        c = FakeClient(quotes={"TSLA": 1})
        _, info = self.plan([], snapshot=snap(cash=20, positions=mv), client=c)
        self.assertEqual(info["valuation"], "snapshot_market_value")
        # no valuation quotes; the only call is the SATS <= $95 price check
        self.assertEqual(c.calls, [("get_recent_orders", ACCT), ("get_quotes", ("SATS",))])

    def test_mara_cap_uses_market_value_not_cost_basis(self):
        # Pi 2026-11-17: 0.712517 sh, $11.23 average buy = $8.0016 (1.69% of
        # $473.15). Live mark $6.495 = $4.63, which is 0.978% (reported 0.976%
        # on the rounded dollar). The 1% cap must use the mark and let MARA pass.
        qty = Decimal("0.712517")
        nav = Decimal("473.15")
        cost = qty * Decimal("11.23")
        market = qty * Decimal("6.495")
        self.assertAlmostEqual(float(cost), 8.0016, places=4)
        self.assertGreater(float(cost / nav * 100), 1.6)
        pct = float(market / nav * 100)
        self.assertLess(pct, 1.0)
        self.assertAlmostEqual(pct, 0.978, places=2)
        pins = {"MARA": Decimal("1.67")}
        themes = [("energy", ("MARA",))]
        whole = {"MARA": Decimal("1")}
        _, blocked = broker.allocate_stock_buys(Decimal("50"), {"MARA": cost}, nav, pins, themes, whole)
        self.assertEqual(blocked, [])
        _, allowed = broker.allocate_stock_buys(Decimal("50"), {"MARA": market}, nav, pins, themes, whole)
        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]["symbol"], "MARA")
        self.assertGreater(allowed[0]["notional_usd"], 0)
        self.assertFalse(allowed[0]["auto_execute"])

        shipped = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
        self.assertTrue(shipped["theme_buy_hold"]["active"])
        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        policy["theme_buy_hold"]["active"] = False
        self.policy.write_text(json.dumps(policy), encoding="utf-8")
        (self.dir / "consider_share.json").write_text(json.dumps({
            "pins": {"MARA": 1.67},
            "whole_fund_pct": {"MARA": 1},
            "themes": {"energy": {"fill_order": 1, "symbols": ["MARA"]}},
        }), encoding="utf-8")
        # STRC is the rest of the market book so NAV stays $473.15 and the BTC
        # sleeve is already over 40% (the stock budget can reach MARA).
        strc = nav - Decimal("10") - market
        positions = [
            {"symbol": "STRC", "quantity": "1", "market_value": str(strc)},
            {"symbol": "MARA", "quantity": "0.712517", "average_buy_price": "11.23"},
        ]
        client = FakeClient(quotes={"MARA": "6.495"})
        orders, info = self.plan(
            [buy("MARA", 5)],
            snapshot=snap(cash=10, positions=positions, total_value="473.15"),
            client=client,
        )
        self.assertNotEqual(info["status"], broker.S_BOOK_UNKNOWN, info.get("reason"))
        self.assertNotIn("cost_basis", str(info.get("valuation")))
        self.assertAlmostEqual(info["symbol_nav_pct"]["MARA"], pct, places=3)
        self.assertAlmostEqual(info["nav_usd"], 473.15, places=2)
        pending = [p for p in info.get("pending_approvals") or [] if p.get("symbol") == "MARA"]
        self.assertEqual(len(pending), 1)
        self.assertAlmostEqual(pending[0]["notional_usd"], 0.10, places=2)
        self.assertFalse(any(o.get("symbol") == "MARA" and o.get("side") != "buy" for o in orders if isinstance(o, dict)))
        self.assertTrue(shipped["theme_buy_hold"]["active"])

    def test_missing_mara_price_blocks_the_buy(self):
        positions = [
            {"symbol": "STRC", "quantity": "1", "market_value": "400"},
            {"symbol": "MARA", "quantity": "0.712517", "average_buy_price": "11.23"},
        ]
        client = FakeClient(quotes={})
        orders, info = self.plan(
            [buy("MARA", 5)],
            snapshot=snap(cash=10, positions=positions, total_value="410"),
            client=client,
        )
        self.assertEqual(orders, [])
        self.assertEqual(info["status"], broker.S_BOOK_UNKNOWN)
        self.assertIn("MARA", info["reason"])
        self.assertIn("missing market price", info["reason"])
        self.assertNotIn("cost_basis", info["reason"])
        self.assertEqual(info.get("pending_approvals") or [], [])
        rows = [
            json.loads(line)
            for line in (self.dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 1)
        self.assertIn("MARA", rows[0]["reason"])

    def _decision_rows(self):
        path = self.dir / "decisions.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_near_zero_total_with_positions_blocks_and_logs_once(self):
        bad = snap(cash=0.0, bp=0.0, positions=[pos("TSLA", 1, 200)], total_value="0.00537")
        orders, info = self.plan([buy("TSLA", 5)], snapshot=bad)
        self.assertEqual(orders, [])
        self.assertIn("near zero", info["reason"])
        self.assertEqual(len(self._decision_rows()), 1)
        again, _ = self.plan([buy("TSLA", 5)], snapshot=bad)
        self.assertEqual(again, [])
        self.assertEqual(len(self._decision_rows()), 1)
        self.assertEqual(self._decision_rows()[0]["actions"], [])

    def test_nav_disagreement_stale_and_missing_fields_block(self):
        disagree = snap(cash=10, positions=[pos("TSLA", 1, 400)], total_value="10")
        orders, info = self.plan([buy("TSLA", 5)], snapshot=disagree)
        self.assertEqual(orders, [])
        self.assertIn("disagrees", info["reason"])
        self.assertEqual(len(self._decision_rows()), 1)

        stale = snap(now=NOW - timedelta(hours=9))
        orders, info = self.plan([buy("TSLA", 5)], snapshot=stale)
        self.assertEqual(orders, [])
        self.assertIn("stale", info["reason"])

        missing_as_of = snap()
        missing_as_of["agentic"].pop("as_of")
        orders, info = self.plan([], snapshot=missing_as_of)
        self.assertEqual(orders, [])
        self.assertIn("missing as_of", info["reason"])

        missing_cash = snap()
        for key in ("cash", "buying_power", "unleveraged_buying_power", "settled_cash"):
            missing_cash["agentic"].pop(key, None)
        orders, info = self.plan([], snapshot=missing_cash)
        self.assertEqual(orders, [])
        self.assertIn("missing cash", info["reason"])

        missing_positions = snap()
        missing_positions["agentic"].pop("positions")
        orders, info = self.plan([], snapshot=missing_positions)
        self.assertEqual(orders, [])
        self.assertIn("missing positions", info["reason"])
        # one row per distinct reason, and nothing raised
        reasons = [r["reason"] for r in self._decision_rows()]
        self.assertGreaterEqual(len(reasons), 4)
        self.assertEqual(len(reasons), len(set(reasons)))

    def test_deployable_cash_fails_closed_on_implausible_snapshot(self):
        from datetime import datetime as dt
        fresh = (dt.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        bad = {
            "agentic": {
                "as_of": fresh,
                "cash": 20.0,
                "buying_power": 20.0,
                "total_value": 0.00537,
                "positions": [{"symbol": "TSLA", "quantity": "1", "last_price": "200"}],
            }
        }
        path = self.dir / "bad_rh.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        self.assertIsNone(gate._deployable_cash_from_snapshot(path))
        good = {
            "agentic": {
                "as_of": fresh,
                "cash": 12.5,
                "buying_power": 12.5,
                "total_value": 212.5,
                "positions": [{"symbol": "TSLA", "quantity": "1", "last_price": "200", "market_value": "200"}],
            }
        }
        path.write_text(json.dumps(good), encoding="utf-8")
        self.assertAlmostEqual(gate._deployable_cash_from_snapshot(path), 12.5)

    def test_grok_quote_call_is_read_only_place_denied(self):
        seen = {}

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0, stdout=json.dumps({"quotes": [{"symbol": "strc", "price": 99.5}]}))

        cl = broker.GrokMcpClient(grok_bin="grok", runner=runner)
        self.assertEqual(cl.get_quotes(["STRC"]), {"STRC": 99.5})
        cmd = seen["cmd"]
        allows = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--allow"]
        denies = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--deny"]
        self.assertEqual(allows, ["MCPTool(robinhood-trading__get_equity_quotes)"])
        self.assertIn("MCPTool(robinhood-trading__place_*)", denies)
        self.assertNotIn("place_equity_order", " ".join(allows))


# ------------------------------------------------------ 8b market calendar
class TestMarketCalendar(Base):
    NAKATOSHI_CLOSED = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
                        "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
    NAKATOSHI_EARLY = {"2026-11-27", "2026-12-24", "2027-11-26"}

    def test_repo_calendar_matches_nyse_and_nakatoshi(self):
        cfg, err = broker.load_adapter_config()
        cal = cfg["calendar"]
        self.assertEqual(cal["years"], frozenset({2026, 2027}))
        self.assertTrue(self.NAKATOSHI_CLOSED <= cal["holidays"])
        self.assertEqual(set(cal["early_close"]), self.NAKATOSHI_EARLY)
        self.assertNotIn("2027-12-31", cal["holidays"])
        self.assertNotIn("2027-12-31", cal["early_close"])
        self.assertIn("nyse.com", cal["source"])

    def test_early_close_day_window_ends_1250(self):
        cfg, _ = broker.load_adapter_config(self.policy)
        at = datetime(2026, 11, 27, 17, 55, tzinfo=timezone.utc)  # 12:55 ET
        self.assertEqual(broker.market_window(at, cfg)[0], "not_submitted_outside_market_hours")
        self.assertIsNone(broker.market_window(datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc), cfg)[0])

    def test_date_beyond_last_calendar_year_fails_closed(self):
        when = datetime(2028, 1, 4, 16, 0, tzinfo=timezone.utc)  # Tue 11:00 ET, 2028 not configured
        orders, info = self.plan(TEN, snapshot=snap(now=when), now=when)
        self.assertEqual(info["status"], "not_submitted_calendar_unknown")
        self.assertEqual(orders, [])
        ok, why = broker.should_buy(snapshot=snap(now=when), policy_path=self.policy, ledger_path=self.ledger, now=when)
        self.assertFalse(ok)
        self.assertIn("calendar_unknown", why)

    def test_calendar_missing_or_malformed_fails_closed(self):
        allowed, _ = self.gated(TEN)
        for bad in (None, {"years": [2026], "holidays": ["2026-13-01"], "early_close": {}},
                    {"years": [2026], "holidays": ["2027-01-01"], "early_close": {}}, {"holidays": []}):
            self.write_policy(market_calendar=bad)
            _, info = broker.plan_intents(TEN, snapshot=snap(), policy_path=self.policy,
                                          ledger_path=self.dir / "lc.json", now=NOW)
            self.assertEqual(info["status"], "not_submitted_calendar_unknown", bad)
            c = FakeClient()
            self.assertEqual(self.submit(allowed[0], c)["status"], "not_submitted_calendar_unknown", bad)
            self.assertEqual(c.calls, [])


# ----------------------------------------------------------------- 3 caps
class TestCaps(Base):
    def test_run_capped_at_settled_cash(self):
        _, info = self.plan([buy("STRC", 25), buy("SATA", 25), buy("STRK", 15), buy("IBIT", 35)], snapshot=snap(cash=40))
        gldm = sum(p["notional_usd"] for p in (info.get("pending_approvals") or []) if p.get("symbol") == "GLDM")
        self.assertAlmostEqual(sum(self.legs(info).values()) + gldm, 40.0, places=2)

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
        self.assertEqual((order["type"], order["time_in_force"], order["market_hours"]),
                         ("market", "gfd", "regular_hours"))
        self.assertNotIn("extended_hours", order)
        self.assertEqual(order["account_number"], ACCT)
        names = [n for n, *_ in c.calls[:5]]
        self.assertEqual(names, ["get_account", "get_recent_orders", "get_tradability", "review_order", "place_order"])

    def test_review_payload_has_only_accepted_params(self):
        # 2026-10-26 prism run: review_equity_order rejected extended_hours/ref_id (MCP -32602).
        c = FakeClient()
        self.run_all(TEN, c)
        reviews = [o for n, o, *_ in c.calls if n == "review_order"]
        self.assertTrue(reviews)
        for order in reviews:
            self.assertLessEqual(set(order), broker.REVIEW_ORDER_PARAMS)
            self.assertNotIn("extended_hours", order)
            self.assertNotIn("ref_id", order)
            self.assertEqual(order["market_hours"], "regular_hours")
        for _, order in c.placed:
            self.assertLessEqual(set(order), broker.PLACE_ORDER_PARAMS)
            self.assertNotIn("extended_hours", order)
            self.assertTrue(order.get("ref_id"))

    def test_grok_client_prompts_use_filtered_payloads(self):
        order = {"account_number": ACCT, "symbol": "STRC", "side": "buy", "type": "market",
                 "dollar_amount": "1.90", "time_in_force": "gfd", "market_hours": "regular_hours",
                 "extended_hours": False, "ref_id": "r-1"}
        seen = []
        cli = broker.GrokMcpClient.__new__(broker.GrokMcpClient)
        cli._call = lambda instruction, schema, tools, **kw: seen.append(instruction) or {"ok": True, "order_id": "o", "state": "filled"}
        cli.review_order(dict(order))
        cli.place_order(dict(order))
        self.assertNotIn("extended_hours", seen[0])
        self.assertNotIn("ref_id", seen[0])
        self.assertNotIn("extended_hours", seen[1])
        self.assertIn('"ref_id": "r-1"', seen[1])

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
        # A still-open buy from the prior ET day claims the day and blocks sizing.
        c = FakeClient(orders=[{"symbol": "IBIT", "side": "buy", "state": "queued", "dollar_amount": 9.0, "order_id": "x",
                                "created_at": (NOW - timedelta(days=1)).isoformat()}])
        orders, info = self.plan(TEN, client=c)
        self.assertEqual(orders, [])
        self.assertEqual(info["status"], "skipped_already_bought_today")
        self.assertAlmostEqual(info["external_open_usd"], 9.0)
        self.assertEqual(c.placed, [])

    def test_matching_recent_fill_from_yesterday_skipped(self):
        c = FakeClient(orders=[{"symbol": "STRC", "side": "buy", "state": "filled", "dollar_amount": 2.18, "order_id": "y",
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
        self.assertIn("placed 4 buy(s) $10.50", entry["summary"])
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

    def test_committed_repo_policy_live_false_places_nothing(self):
        # Chris resumed buys 2026-10-25 (committed live:true). A pause is a one-line
        # flip back to live:false, so the committed policy with only that flag
        # flipped must still refuse at every layer.
        committed = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
        self.assertIs(committed["live"], True)
        committed["live"] = False
        repo_policy = self.dir / "fund_manager_paused.json"
        repo_policy.write_text(json.dumps(committed), encoding="utf-8")
        self.assertFalse(gate.load_gate_policy(repo_policy).live)
        # adapter (defense in depth): a planned, gate-approved leg is still refused with no broker call
        allowed, _ = self.gated(TEN)  # planned against a live temp copy
        c = FakeClient()
        out = broker.submit(allowed[0], client=c, policy_path=repo_policy, ledger_path=self.ledger, now=NOW)
        self.assertEqual(out["status"], "not_submitted_live_false")
        self.assertEqual(c.calls, [])
        self.assertFalse(broker.should_buy(snapshot=snap(), policy_path=repo_policy, ledger_path=self.ledger, now=NOW)[0])
        # gate CLI with the committed policy: the adapter is never called
        f = self.dir / "fund_manager_intents.json"
        f.write_text(json.dumps({"orders": [buy("STRC", 2.5)]}))
        sub = mock.Mock(return_value={"status": "placed"})
        sub.plan = mock.Mock(side_effect=lambda payload, **kw: (payload, {"status": "planned"}))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.object(gate, "load_broker_adapter", return_value=sub), mock.patch.object(
            gate, "_deployable_cash_from_snapshot", return_value=12.0
        ), mock.patch.object(gate, "load_override", return_value=(None, "")), mock.patch.object(
            gate, "POLICY_PATH", repo_policy
        ), redirect_stdout(buf):
            gate.main(["--execute-intents", str(f), "--no-record"])
        res = json.loads(buf.getvalue())
        sub.assert_not_called()
        self.assertEqual(res["placed"], [])
        self.assertEqual(res["broker_calls"], 0)
        self.assertEqual([r["status"] for r in res["would_place"]], ["would_place"])

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


# ------------------------------------------------- advisory-only AI review (2026-10-26)
class TestAdvisoryOnlyRefusesExecution(Base):
    """The grok review step must never execute. These fail if submit or the
    grok client would place under the advisory marker or a grok parent."""

    def test_submit_refuses_under_advisory_marker(self):
        allowed, _ = self.gated(TEN)
        self.assertTrue(allowed)
        c = FakeClient()
        with mock.patch.dict(os.environ, {gate.ADVISORY_ENV: "1"}):
            outs = [self.submit(g, c) for g in allowed]
        self.assertEqual({o["status"] for o in outs}, {broker.S_ADVISORY})
        self.assertEqual(c.calls, [])  # no account read, no review, no place

    def test_submit_refuses_under_grok_ancestor(self):
        allowed, _ = self.gated(TEN)
        c = FakeClient()
        with mock.patch.object(gate, "grok_ancestor", return_value="4242:grok"):
            outs = [self.submit(g, c) for g in allowed]
        self.assertEqual({o["status"] for o in outs}, {broker.S_ADVISORY})
        self.assertIn("grok", outs[0]["broker_reason"])
        self.assertEqual(c.calls, [])

    def test_submit_still_places_without_marker(self):
        c = FakeClient()
        with mock.patch.dict(os.environ, {gate.ADVISORY_ENV: "0"}):
            outs, _ = self.run_all(TEN, c)
        self.assertEqual([o["status"] for o in outs], ["placed"] * 4)

    def test_grok_client_place_refused_under_marker(self):
        ran = []
        cli = broker.GrokMcpClient(grok_bin="grok", cwd=str(ROOT), runner=lambda *a, **k: ran.append(a))
        order = {"account_number": ACCT, "symbol": "IBIT", "side": "buy", "type": "market",
                 "dollar_amount": "2.68", "time_in_force": "gfd", "market_hours": "regular_hours", "ref_id": "r"}
        with mock.patch.dict(os.environ, {gate.ADVISORY_ENV: "1"}):
            with self.assertRaises(broker.BrokerError):
                cli.place_order(order)
        with mock.patch.object(gate, "grok_ancestor", return_value="7:grok-1.0.13-linux-aarch64"):
            with self.assertRaises(broker.BrokerError):
                cli.place_order(order)
        self.assertEqual(ran, [])  # grok was never launched with the place carve-out
