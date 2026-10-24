"""Buy-only broker adapter (treasury.fund_manager_broker) behind the #1093 gate.

Every broker call is a mock; nothing touches the network, Robinhood, grok or
GitHub. Policy and ledger live in temp dirs.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury import fund_manager_broker as broker  # noqa: E402
from treasury import fund_manager_order_gate as gate  # noqa: E402

NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)  # Mon 11:00 ET
ACCT = "674601752"
REPO_POLICY = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, bp=20.0, orders=None, tradable=True, fractional=True, review_ok=True,
                 place_result=None, place_exc=None, account=ACCT):
        self.bp, self.orders, self.tradable, self.fractional = bp, orders or [], tradable, fractional
        self.review_ok, self.place_result, self.place_exc, self.account = review_ok, place_result, place_exc, account
        self.calls = []

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
        return self.place_result or {"order_id": "ord-1", "state": "queued", "symbol": order["symbol"],
                                     "side": "buy", "dollar_amount": float(order["dollar_amount"])}

    @property
    def placed(self):
        return [c for c in self.calls if c[0] == "place_order"]


def gpol(live=True):
    return gate.GatePolicy(True, "", ("buy",), ("BE",), live=live, min_trade_usd=1.0)


def gated(intent, deployable=100.0):
    res = gate.gate_orders([intent], deployable_cash=deployable, policy=gpol(), now=NOW)
    assert res.allowed, res.blocked
    return res.allowed[0]


def buy(sym="STRC", usd=1.9, **kw):
    d = {"symbol": sym, "side": "buy", "notional_usd": usd, "purpose": "deploy"}
    d.update(kw)
    return d


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
        self.addCleanup(env.stop)
        self.addCleanup(self.td.cleanup)

    def write_policy(self, live=True, **adapter):
        p = copy.deepcopy(REPO_POLICY)
        p["live"] = live
        p["execution"]["broker_adapter"].update(adapter)
        self.policy.write_text(json.dumps(p), encoding="utf-8")

    def submit(self, g, client, now=NOW):
        with mock.patch("sys.stderr"):
            return broker.submit(g, client=client, policy_path=self.policy, ledger_path=self.ledger, now=now)

    def ledger_rows(self):
        return json.loads(self.ledger.read_text())["orders"]


class TestBuyPlacesAfterReview(Base):
    def test_buy_places_after_review(self):
        c = FakeClient()
        out = self.submit(gated(buy("STRC", 1.909)), c)
        self.assertEqual(out["status"], "placed")
        self.assertEqual(out["order_id"], "ord-1")
        names = [n for n, *_ in c.calls]
        self.assertEqual(names, ["get_account", "get_recent_orders", "get_tradability", "review_order", "place_order"])
        order = c.placed[0][1]
        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["dollar_amount"], "1.90")  # rounded DOWN to cents
        self.assertEqual(order["account_number"], ACCT)
        self.assertEqual(order["type"], "market")
        row = self.ledger_rows()[out["idempotency_key"]]
        self.assertEqual(row["status"], "placed")
        self.assertIn("review", row)
        self.assertEqual(out["idempotency_key"], "2026-10-26|1752|STRC|buy|190c")

    def test_gate_execute_intents_with_adapter_end_to_end(self):
        c = FakeClient(bp=10.0)

        def sub(g):
            return broker.submit(g, client=c, policy_path=self.policy, ledger_path=self.ledger, now=NOW)

        with mock.patch("sys.stderr"):
            out = gate.execute_intents(
                [buy("STRC", 2.0), buy("SATA", 2.0), {"symbol": "TSLA", "side": "sell", "notional_usd": 3, "purpose": "rebalance"}],
                deployable_cash=7.62, submit=sub, policy=gpol(), now=NOW, record=False,
            )
        self.assertEqual([r["status"] for r in out["placed"]], ["placed", "placed"])
        self.assertEqual(out["placed"][0]["order_id"], "ord-1")
        self.assertIn("idempotency_key", out["placed"][0])
        self.assertEqual(len(out["blocked"]), 1)
        self.assertEqual(len(c.placed), 2)


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
        self.assertEqual(len(res.allowed), 1, "override let the sell through the gate")
        out = self.submit(res.allowed[0], c)
        self.assertEqual(out["status"], "rejected_non_buy_side")
        self.assertEqual(c.calls, [])

    def test_non_gated_dict_rejected(self):
        c = FakeClient()
        out = self.submit({"symbol": "STRC", "side": "buy", "notional_usd": 2}, c)
        self.assertEqual(out["status"], "rejected_not_gated")
        self.assertEqual(c.calls, [])

    def test_grok_client_refuses_non_buy(self):
        cl = broker.GrokMcpClient(runner=mock.Mock())
        with self.assertRaises(broker.BrokerError):
            cl.place_order({"symbol": "STRC", "side": "sell", "dollar_amount": "1.00"})
        cl.runner.assert_not_called()


class TestDuplicates(Base):
    def test_duplicate_skipped_by_idempotency_key(self):
        c = FakeClient()
        g = gated(buy("SATA", 2.0))
        self.assertEqual(self.submit(g, c)["status"], "placed")
        out = self.submit(gated(buy("SATA", 2.0)), c)
        self.assertEqual(out["status"], "skipped_duplicate_idempotency")
        self.assertEqual(len(c.placed), 1)

    def test_place_error_blocks_retry(self):
        c = FakeClient(place_exc=TimeoutError())
        out = self.submit(gated(buy("STRK", 3.0)), c)
        self.assertEqual(out["status"], "place_error_outcome_unknown")
        c2 = FakeClient()
        self.assertEqual(self.submit(gated(buy("STRK", 3.0)), c2)["status"], "skipped_duplicate_idempotency")
        self.assertEqual(c2.calls, [])

    def test_duplicate_skipped_by_broker_open_order(self):
        c = FakeClient(orders=[{"symbol": "IBIT", "side": "buy", "state": "queued", "dollar_amount": 9.0, "order_id": "x"}])
        out = self.submit(gated(buy("IBIT", 1.5)), c)
        self.assertEqual(out["status"], "skipped_duplicate_broker_order")
        self.assertEqual(c.placed, [])

    def test_duplicate_skipped_by_broker_recent_matching_fill(self):
        c = FakeClient(orders=[{"symbol": "STRC", "side": "buy", "state": "filled", "dollar_amount": 1.9, "order_id": "y"}])
        self.assertEqual(self.submit(gated(buy("STRC", 1.9)), c)["status"], "skipped_duplicate_broker_order")
        # cancelled / different symbol / sells do not count
        c2 = FakeClient(orders=[
            {"symbol": "STRC", "side": "buy", "state": "cancelled", "dollar_amount": 1.9},
            {"symbol": "SATA", "side": "buy", "state": "queued", "dollar_amount": 1.9},
        ])
        self.assertEqual(self.submit(gated(buy("STRC", 1.91)), c2)["status"], "placed")

    def test_broker_orders_unavailable_fails_closed(self):
        c = FakeClient()
        c.get_recent_orders = lambda a, s: None
        self.assertEqual(self.submit(gated(buy()), c)["status"], "not_submitted_review_failed")
        self.assertEqual(c.placed, [])


class TestReviewFailures(Base):
    def assertNotSubmitted(self, out, c, needle):
        self.assertEqual(out["status"], "not_submitted_review_failed")
        self.assertIn(needle, out["broker_reason"])
        self.assertEqual(c.placed, [])

    def test_over_buying_power(self):
        c = FakeClient(bp=1.0)
        self.assertNotSubmitted(self.submit(gated(buy("STRC", 5.0)), c), c, "buying power")

    def test_non_allowlisted_symbol(self):
        c = FakeClient()
        self.assertNotSubmitted(self.submit(gated(buy("TSLA", 5.0)), c), c, "allowlist")
        self.assertEqual(c.calls, [])

    def test_zero_or_sub_cent_amount(self):
        c = FakeClient()
        g = gated(buy("STRC", 5.0))
        g.intent["notional_usd"] = 0  # tampered after the gate
        self.assertNotSubmitted(self.submit(g, c), c, "> 0")
        g2 = gated(buy("STRC", 5.0))
        g2.intent["notional_usd"] = 0.004
        self.assertNotSubmitted(self.submit(g2, c), c, "> 0")

    def test_over_per_order_and_daily_caps(self):
        self.write_policy(max_order_notional_usd=5.0, max_daily_notional_usd=6.0)
        c = FakeClient(bp=100)
        self.assertNotSubmitted(self.submit(gated(buy("STRC", 5.5)), c), c, "max_order")
        self.assertEqual(self.submit(gated(buy("STRC", 4.0)), c)["status"], "placed")
        out = self.submit(gated(buy("SATA", 3.0)), c)
        self.assertEqual(out["status"], "not_submitted_review_failed")
        self.assertIn("daily cap", out["broker_reason"])

    def test_not_fractional_and_broker_review_reject(self):
        c = FakeClient(fractional=False)
        self.assertNotSubmitted(self.submit(gated(buy("STRK", 2.0)), c), c, "fractional")
        c2 = FakeClient(review_ok=False)
        self.assertNotSubmitted(self.submit(gated(buy("STRK", 2.0)), c2), c2, "review says no")

    def test_account_mismatch(self):
        c = FakeClient(account="5QW39737")
        self.assertNotSubmitted(self.submit(gated(buy()), c), c, "account mismatch")

    def test_broker_rejected_state(self):
        c = FakeClient(place_result={"order_id": "r1", "state": "rejected"})
        self.assertEqual(self.submit(gated(buy()), c)["status"], "not_submitted_broker_rejected")

    def test_response_mismatch_flags_outcome_unknown(self):
        c = FakeClient(place_result={"order_id": "m1", "state": "queued", "symbol": "STRC", "side": "sell"})
        out = self.submit(gated(buy()), c)
        self.assertEqual(out["status"], "place_error_outcome_unknown")
        self.assertIn("CHECK BROKER", out["broker_reason"])


class TestAdapterSelectionAndLive(Base):
    def test_missing_adapter_still_not_submitted(self):
        with mock.patch.dict(os.environ, {gate.BROKER_ADAPTER_ENV: ""}):
            self.assertIsNone(gate.load_broker_adapter())
        out = gate.execute_intents([buy()], deployable_cash=10.0, submit=gate.load_broker_adapter(), policy=gpol(), now=NOW, record=False)
        self.assertEqual(out["placed"][0]["status"], "not_submitted_no_broker_adapter")
        self.assertEqual(out["broker_calls"], 0)

    def test_env_resolves_to_this_adapter(self):
        with mock.patch.dict(os.environ, {gate.BROKER_ADAPTER_ENV: broker.ADAPTER_SPEC}):
            self.assertIs(gate.load_broker_adapter(), broker.submit)

    def test_no_client_configured(self):
        out = self.submit(gated(buy()), None)
        self.assertEqual(out["status"], "not_submitted_no_broker_client")
        self.assertEqual(self.ledger_rows()[out["idempotency_key"]]["status"], "not_submitted_no_broker_client")

    def test_live_false_adapter_places_nothing(self):
        self.write_policy(live=False)
        c = FakeClient()
        self.assertEqual(self.submit(gated(buy()), c)["status"], "not_submitted_live_false")
        self.assertEqual(c.calls, [])

    def test_live_false_gate_never_calls_adapter(self):
        sub = mock.Mock()
        out = gate.execute_intents([buy()], deployable_cash=10.0, submit=sub, policy=gpol(live=False), now=NOW, record=False)
        sub.assert_not_called()
        self.assertEqual(out["would_place"][0]["status"], "would_place")

    def test_kill_switch_and_disabled_config(self):
        c = FakeClient()
        with mock.patch.dict(os.environ, {broker.DISABLE_ENV: "1"}):
            self.assertEqual(self.submit(gated(buy()), c)["status"], "not_submitted_adapter_disabled")
        self.write_policy(enabled=False)
        self.assertEqual(self.submit(gated(buy()), c)["status"], "not_submitted_adapter_config")
        self.assertEqual(c.calls, [])

    def test_repo_config_is_live_and_buy_only(self):
        cfg, err = broker.load_adapter_config()
        self.assertEqual(err, "")
        self.assertTrue(cfg["live"])
        self.assertEqual(cfg["allowlist"], ("IBIT", "SATA", "STRC", "STRK"))
        self.assertEqual(cfg["account_number"], ACCT)

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
        # read calls keep every order glob denied
        cl.runner = lambda cmd, **kw: (seen.update(cmd=cmd), mock.Mock(returncode=0, stdout='{"tradable":true,"fractional_tradable":true}'))[1]
        cl.get_tradability("STRC")
        self.assertIn("MCPTool(robinhood-trading__place_*)", [seen["cmd"][i + 1] for i, a in enumerate(seen["cmd"]) if a == "--deny"])


if __name__ == "__main__":
    unittest.main()
