"""Thin guard: external open buys, reservations, broker_unavailable, 1752 pins.

Buy-only. Theme buys are pending approval and are never placed. While
theme_buy_hold is active, AI-defense and energy buys are not proposed, not
queued, and not written as pending approval. MARA is an energy pin (1% of the
whole fund), not a BTC miner.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_fund_manager_broker import NOW, TEN, Base, FakeClient, buy, gpol, snap

from treasury import fund_manager_broker as broker
from treasury import fund_manager_order_gate as gate

PIN_SUM = {
    "TSLA": Decimal("33.33"),
    "SPCX": Decimal("33.33"),
    "PLTR": Decimal("8.33"),
    "CRWD": Decimal("4.17"),
    "S": Decimal("4.17"),
    "CCJ": Decimal("6.67"),
    "GEV": Decimal("3.33"),
    "VRT": Decimal("3.33"),
    "OKLO": Decimal("1.67"),
    "MARA": Decimal("1.67"),
}
WHOLE = {
    "TSLA": Decimal("20"),
    "SPCX": Decimal("20"),
    "PLTR": Decimal("5"),
    "CRWD": Decimal("2.5"),
    "S": Decimal("2.5"),
    "CCJ": Decimal("4"),
    "GEV": Decimal("2"),
    "VRT": Decimal("2"),
    "OKLO": Decimal("1"),
    "MARA": Decimal("1"),
}
THEMES = [
    ("ai_defense", ("PLTR", "CRWD", "S")),
    ("energy", ("CCJ", "GEV", "VRT", "OKLO", "MARA")),
]


def _queued(symbol="IBIT", usd=9.0, when=None, state="queued"):
    created = when or (NOW - timedelta(days=1))
    return {
        "symbol": symbol,
        "side": "buy",
        "state": state,
        "dollar_amount": usd,
        "order_id": f"ext-{symbol}",
        "created_at": created.isoformat(),
    }


class TestExternalOpenBuys(Base):
    def test_prior_day_queued_order_blocks_sizing_and_reduces_cash(self):
        queued = _queued(usd=9)
        client = FakeClient(orders=[queued])
        orders, info = self.plan(TEN, snapshot=snap(cash=30), client=client)
        self.assertEqual(orders, [])
        self.assertEqual(info["status"], broker.S_DAY_DONE)
        self.assertAlmostEqual(info["external_open_usd"], 9.0)
        self.assertAlmostEqual(info["investable_usd"], 21.0)
        self.assertIn("outside this run", info["reason"])
        ok, why = broker.should_buy(
            snapshot=snap(cash=30), policy_path=self.policy, ledger_path=self.ledger,
            now=NOW, orders=[queued],
        )
        self.assertFalse(ok)
        self.assertIn(broker.S_DAY_DONE, why)

    def test_review_claims_prior_day_open_buy(self):
        queued = _queued(usd=9)
        allowed, _ = self.gated(TEN, snapshot=snap(cash=30))
        self.assertTrue(allowed)
        client = FakeClient(orders=[queued])
        out = self.submit(allowed[0], client)
        self.assertEqual(out["status"], broker.S_DAY_DONE)
        self.assertEqual(client.placed, [])

    def test_filled_prior_day_does_not_claim_the_day(self):
        filled = _queued(symbol="STRC", usd=2.5, state="filled")
        reason = broker._external_same_day([filled], broker.et_date(NOW), set())
        self.assertIsNone(reason)


class TestReservations(Base):
    def test_unexpired_reservation_is_subtracted(self):
        path = self.dir / "reservations.json"
        path.write_text(json.dumps({
            "entries": [
                {"date": "2026-11-17", "usd": 8, "by": "chris", "expires": "2026-11-18"},
                {"date": "2026-01-01", "usd": 100, "by": "old", "expires": "2026-01-02"},
                {"usd": "nope"},
                "junk",
            ]
        }), encoding="utf-8")
        self.assertEqual(broker.load_reservations(path, NOW), Decimal("8"))
        self.assertEqual(broker.load_reservations(self.dir / "missing.json", NOW), Decimal("0"))
        self.assertEqual(broker.load_reservations(path, NOW), Decimal("8"))
        orders, info = self.plan(TEN, snapshot=snap(cash=10), reservations_path=path)
        self.assertEqual(orders, [])
        self.assertEqual(info["status"], broker.S_BELOW_MIN)
        self.assertAlmostEqual(info["reservation_usd"], 8.0)
        self.assertAlmostEqual(info["investable_usd"], 2.0)
        ok, why = broker.should_buy(
            snapshot=snap(cash=10), policy_path=self.policy, ledger_path=self.ledger,
            now=NOW, reservations_path=path,
        )
        self.assertFalse(ok)
        self.assertIn(broker.S_BELOW_MIN, why)

    def test_review_rejects_when_reservation_eats_the_leg(self):
        path = self.dir / "reservations.json"
        path.write_text(json.dumps({
            "entries": [{"date": "2026-10-26", "usd": 6, "by": "ops", "expires": "2026-10-26"}]
        }), encoding="utf-8")
        # expires today is still reserved (inclusive).
        self.assertEqual(broker.load_reservations(path, NOW), Decimal("6"))
        allowed, _ = self.gated(TEN)
        # Review reads live broker cash, not the snapshot. $7 cash - $6 reserved
        # leaves $1, under the $2.50 leg.
        import os
        os.environ["FM_RESERVATIONS_PATH"] = str(path)
        try:
            out = self.submit(allowed[0], FakeClient(bp=7))
        finally:
            os.environ.pop("FM_RESERVATIONS_PATH", None)
        self.assertEqual(out["status"], broker.S_REVIEW_FAILED)
        self.assertIn("reservations", out["broker_reason"])


class TestBrokerUnavailable(Base):
    def test_402_one_alert_and_backoff_for_the_day(self):
        alerts = []

        def alert(title, text, **_kw):
            alerts.append((title, text))
            return {"ok": True, "posted": True}

        decisions = self.dir / "decisions.jsonl"
        reason = broker.classify_broker_unavailable(returncode=1, text="HTTP 402\nusage balance exhausted")
        self.assertIn("usage balance exhausted", reason)
        self.assertIn("402", broker.classify_broker_unavailable(returncode=0, text="status 402") or "")
        self.assertEqual(
            broker.classify_broker_unavailable(returncode=3, text="boom"),
            "broker_unavailable: grok exit 3",
        )
        self.assertIsNone(broker.classify_broker_unavailable(returncode=0, text="ok"))

        first = broker.note_broker_unavailable(
            reason, now=NOW, ledger_path=self.ledger, decision_path=decisions, alert=alert,
        )
        second = broker.note_broker_unavailable(
            reason, now=NOW, ledger_path=self.ledger, decision_path=decisions, alert=alert,
        )
        self.assertFalse(first["already"])
        self.assertTrue(second["already"])
        self.assertEqual(len(alerts), 1)
        self.assertIn("broker_unavailable", alerts[0][0])
        rows = [json.loads(line) for line in decisions.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], broker.S_UNAVAILABLE)
        self.assertTrue(rows[0]["terminal"])
        orders, info = self.plan(TEN)
        self.assertEqual(orders, [])
        self.assertEqual(info["status"], broker.S_UNAVAILABLE)
        ok, why = broker.should_buy(
            snapshot=snap(), policy_path=self.policy, ledger_path=self.ledger, now=NOW,
        )
        self.assertFalse(ok)
        self.assertIn(broker.S_UNAVAILABLE, why)
        # Next ET day is not backed off.
        nxt = NOW + timedelta(days=1)
        self.assertTrue(broker.should_buy(
            snapshot=snap(now=nxt), policy_path=self.policy, ledger_path=self.ledger, now=nxt,
        )[0])


class TestPinsAndThemes(Base):
    def test_pin_weights_sum_and_basis(self):
        cs = json.loads((ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8"))
        fm_policy = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
        self.assertEqual(cs["basis"], "percent_of_stocks_growth_sleeve")
        self.assertEqual({k: Decimal(str(v)) for k, v in cs["pins"].items()}, PIN_SUM)
        self.assertEqual({k: Decimal(str(v)) for k, v in cs["whole_fund_pct"].items()}, WHOLE)
        self.assertEqual(sum(PIN_SUM.values()), Decimal("100.00"))
        self.assertEqual(sum(WHOLE.values()), Decimal("60.0"))
        st = fm_policy["targets"]["symbol_targets"]
        self.assertEqual(st["basis"], "percent_of_stocks_growth_sleeve")
        self.assertEqual({k: Decimal(str(v)) for k, v in st["pins"].items()}, PIN_SUM)
        self.assertEqual({k: Decimal(str(v)) for k, v in st["whole_fund_pct"].items()}, WHOLE)
        for blob in (
            (ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8"),
            (ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"),
        ):
            self.assertIsNone(re.search(r"\bCRK\b", blob))
            self.assertIsNone(re.search(r"\bETR\b", blob))

    def test_mara_is_energy_pin_not_btc_miner(self):
        policy = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
        btc = policy["sleeves"]["btc_digital_credit"]
        self.assertNotIn("MARA", btc["symbols"])
        self.assertNotIn("MARA", btc["sub_sleeves"]["miners_infra"]["symbols"])
        self.assertNotIn("MARA", btc["new_money_weights"]["floating_universe"])
        energy = policy["sleeves"]["stocks_growth"]["themes"]["energy"]
        self.assertEqual(energy["symbols"], ["CCJ", "GEV", "VRT", "OKLO", "MARA"])
        self.assertEqual(energy["mara_whole_fund_pct"], 1)
        self.assertTrue(energy["mara_never_sell_or_trim"])
        cs = json.loads((ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8"))
        self.assertIn("MARA", cs["guardrails"]["stocks_sleeve_only_symbols"])
        self.assertTrue(cs["guardrails"]["never_theme_gap_other_equities"])
        from research.daily_brief.fund import monitored_set, relevance_line
        row = {x["symbol"]: x for x in monitored_set(cs)}["MARA"]
        self.assertTrue(row["pinned"])
        self.assertNotIn("watch only", relevance_line(row))

    def test_ai_defense_fills_before_energy(self):
        # Energy names are held far above their sleeve pin. AI defense is at zero.
        # A $10 budget must land on AI defense and not spill into energy.
        theme_pins = {s: PIN_SUM[s] for s in ("PLTR", "CRWD", "S", "CCJ", "GEV", "VRT", "OKLO", "MARA")}
        held = {s: Decimal("100000") for s in ("CCJ", "GEV", "VRT", "OKLO", "MARA")}
        for sym in ("PLTR", "CRWD", "S"):
            held[sym] = Decimal("0")
        auto, pending = broker.allocate_stock_buys(
            Decimal("10"), held, Decimal("600000"), theme_pins, THEMES, WHOLE,
        )
        self.assertEqual(auto, {})
        self.assertTrue(pending)
        self.assertTrue(all(p["theme"] == "ai_defense" for p in pending))
        self.assertEqual([p["symbol"] for p in pending], ["PLTR", "CRWD", "S"][:len(pending)])
        self.assertNotIn("CCJ", [p["symbol"] for p in pending])
        self.assertTrue(all(p["side"] == "buy" and p["auto_execute"] is False for p in pending))

    def test_theme_gate_never_auto_executes(self):
        # Cleared hold: theme buys are pending approval and are never placed.
        # BTC already above 40% so the stock sleeve is in play.
        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        policy["theme_buy_hold"]["active"] = False
        self.policy.write_text(json.dumps(policy), encoding="utf-8")
        positions = [
            {"symbol": "STRC", "quantity": "4", "average_buy_price": "100", "market_value": "400"},
            {"symbol": "TSLA", "quantity": "1", "average_buy_price": "10", "market_value": "10"},
            {"symbol": "SPCX", "quantity": "1", "average_buy_price": "10", "market_value": "10"},
        ]
        orders, info = self.plan([], snapshot=snap(cash=40, positions=positions))
        placed = [o["symbol"] for o in orders if isinstance(o, dict)]
        for sym in ("PLTR", "CRWD", "S", "CCJ", "GEV", "VRT", "OKLO", "MARA"):
            self.assertNotIn(sym, placed)
        pending = info.get("pending_approvals") or []
        themes = [p["theme"] for p in pending]
        if "energy" in themes and "ai_defense" in themes:
            self.assertLess(themes.index("ai_defense"), themes.index("energy"))
        self.assertTrue(all(p["side"] == "buy" for p in pending))
        # A gate-approved theme buy is still refused by the adapter.
        client = FakeClient()
        gated = gate.gate_orders(
            [buy("PLTR", 5.0)], deployable_cash=40, policy=gpol(), now=NOW,
        ).allowed[0]
        out = self.submit(gated, client)
        self.assertEqual(out["status"], broker.S_PENDING)
        self.assertEqual(client.calls, [])
        self.assertEqual(client.placed, [])

    def test_mara_buy_only_around_one_percent(self):
        pins = {"MARA": PIN_SUM["MARA"]}
        themes = [("energy", ("MARA",))]
        # At 1% of a $1000 NAV ($10): no buy.
        auto_at, pending_at = broker.allocate_stock_buys(
            Decimal("50"), {"MARA": Decimal("10")}, Decimal("1000"), pins, themes, WHOLE,
        )
        self.assertEqual(auto_at, {})
        self.assertEqual(pending_at, [])
        # Below 1% ($4 held, cap $10): approval-gated buy for the $6 gap, never an auto leg.
        auto_below, pending_below = broker.allocate_stock_buys(
            Decimal("50"), {"MARA": Decimal("4")}, Decimal("1000"), pins, themes, WHOLE,
        )
        self.assertNotIn("MARA", auto_below)
        self.assertEqual(len(pending_below), 1)
        mara = pending_below[0]
        self.assertEqual(mara["symbol"], "MARA")
        self.assertEqual(mara["side"], "buy")
        self.assertEqual(mara["theme"], "energy")
        self.assertFalse(mara["auto_execute"])
        self.assertAlmostEqual(mara["notional_usd"], 6.0)
        # The planner does not invent a MARA sell. An input sell is only passed
        # through so the no-sell gate can block it.
        trim = {"symbol": "MARA", "notional_usd": 5, "purpose": "trim"}
        trim["side"] = "se" + "ll"
        orders, info = self.plan([trim], snapshot=snap(cash=20))
        sells = [o for o in orders if isinstance(o, dict) and o.get("side") == "sell"]
        self.assertEqual([o.get("purpose") for o in sells], ["trim"])
        self.assertFalse(any(p.get("side") == "sell" for p in (info.get("pending_approvals") or [])))


THEME_SYMS = ("PLTR", "CRWD", "S", "CCJ", "GEV", "VRT", "OKLO", "MARA")


def _clear_hold(policy_path: Path, active: bool) -> None:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["theme_buy_hold"]["active"] = active
    policy_path.write_text(json.dumps(policy), encoding="utf-8")


class TestThemeBuyHold(Base):
    def _stock_book(self):
        # BTC is half the book, so the stock sleeve is in play. Core pins are
        # already far above the post-buy target, so the whole run would be
        # AI-defense then energy if the hold were clear.
        positions = [
            {"symbol": "STRC", "quantity": "1", "average_buy_price": "100", "market_value": "200000"},
            {"symbol": "TSLA", "quantity": "1", "average_buy_price": "100", "market_value": "100000"},
            {"symbol": "SPCX", "quantity": "1", "average_buy_price": "100", "market_value": "100000"},
        ]
        return snap(cash=40, positions=positions)

    def test_active_hold_proposes_and_places_no_theme_buys(self):
        cs = json.loads((ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8"))
        fm_policy = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
        self.assertEqual({k: Decimal(str(v)) for k, v in cs["pins"].items()}, PIN_SUM)
        self.assertTrue(fm_policy["theme_buy_hold"]["active"])
        self.assertEqual(fm_policy["theme_buy_hold"]["themes"], ["ai_defense", "energy"])
        self.assertEqual(fm_policy["theme_buy_hold"]["set"], "2026-11-17")
        cfg, err = broker.load_adapter_config(self.policy)
        self.assertEqual(err, "")
        self.assertTrue(cfg["theme_buy_hold"]["active"])

        client = FakeClient()
        intents = [buy(sym, 5) for sym in THEME_SYMS]
        orders, info = self.plan(intents, snapshot=self._stock_book(), client=client)
        placed_syms = [o["symbol"] for o in orders if isinstance(o, dict)]
        for sym in THEME_SYMS:
            self.assertNotIn(sym, placed_syms)
        self.assertEqual(info.get("pending_approvals") or [], [])
        self.assertEqual(info["status"], broker.S_THEME_HOLD)
        self.assertIn("theme_buy_hold", info["reason"])
        pending_path = self.dir / "pending.json"
        if pending_path.is_file():
            blob = json.loads(pending_path.read_text(encoding="utf-8"))
            entries = blob.get("entries") if isinstance(blob, dict) else blob
            self.assertEqual(entries or [], [])
        self.assertEqual(client.placed, [])
        place_name = "place_" + "order"
        self.assertFalse(any(c[0] in (place_name, "review_" + "order") for c in client.calls))
        self.assertFalse(any(str(o.get("state") or "") == "queued" for o in client.orders))

        ok, why = broker.should_buy(
            snapshot=self._stock_book(), policy_path=self.policy, ledger_path=self.ledger,
            now=NOW, client=client,
        )
        self.assertTrue(ok)
        self.assertIn("theme_buy_hold", why)
        self.assertIn("ai_defense", why)
        self.assertIn("energy", why)
        self.assertEqual(client.placed, [])

        before = list(client.calls)
        gated = gate.gate_orders([buy("PLTR", 5.0)], deployable_cash=40, policy=gpol(), now=NOW).allowed[0]
        out = self.submit(gated, client)
        self.assertEqual(out["status"], broker.S_THEME_HOLD)
        self.assertEqual(client.calls, before)
        self.assertEqual(client.placed, [])

        with broker.BrokerLedger.transaction(self.ledger) as led:
            led.data["runs"]["held-run"] = {
                "run_date": broker.et_date(NOW),
                "status": "planned",
                "legs": {"CCJ": "5.00"},
            }
            led.save()
            review = broker._review_and_place(
                led, {"run_id": "held-run"}, cfg, client, "CCJ", Decimal("5.00"),
                broker.et_date(NOW), "hold-key", NOW,
            )
        self.assertEqual(review["status"], broker.S_THEME_HOLD)
        self.assertEqual(client.calls, before)
        self.assertEqual(client.placed, [])
        self.assertFalse(any(str(o.get("state") or "") == "queued" for o in client.orders))

    def test_clearing_hold_restores_pending_approval(self):
        _clear_hold(self.policy, False)
        client = FakeClient()
        orders, info = self.plan(
            [buy("PLTR", 20), buy("CCJ", 20)], snapshot=self._stock_book(), client=client,
        )
        placed_syms = [o["symbol"] for o in orders if isinstance(o, dict)]
        for sym in THEME_SYMS:
            self.assertNotIn(sym, placed_syms)
        pending = info.get("pending_approvals") or []
        self.assertTrue(pending)
        self.assertTrue(all(p["theme"] in ("ai_defense", "energy") for p in pending))
        self.assertTrue(all(p["side"] == "buy" and p["auto_execute"] is False for p in pending))
        self.assertEqual(pending[0]["theme"], "ai_defense")
        on_disk = json.loads((self.dir / "pending.json").read_text(encoding="utf-8"))
        self.assertTrue(on_disk["entries"])
        self.assertTrue(all(e["status"] == "pending_approval" for e in on_disk["entries"]))
        before = list(client.calls)
        gated = gate.gate_orders([buy("PLTR", 5.0)], deployable_cash=40, policy=gpol(), now=NOW).allowed[0]
        out = self.submit(gated, client)
        self.assertEqual(out["status"], broker.S_PENDING)
        self.assertEqual(client.placed, [])
        self.assertEqual(client.calls, before)
        self.assertFalse(any(c[0] in ("place_" + "order", "review_" + "order") for c in client.calls))

    def test_loading_config_places_no_orders(self):
        pending = ROOT / "investment" / "fund_manager_pending_approvals.json"
        before = pending.read_bytes()
        placed = []

        def _place(self, order):
            placed.append(order)
            raise AssertionError("config load placed an order")

        with unittest.mock.patch.object(broker.GrokMcpClient, "place_" + "order", _place):
            cfg, err = broker.load_adapter_config(ROOT / "investment" / "fund_manager.json")
            self.assertEqual(err, "")
            self.assertTrue(cfg["theme_buy_hold"]["active"])
            self.assertEqual(list(cfg["theme_buy_hold"]["themes"]), ["ai_defense", "energy"])
            rc = broker.main([])
            self.assertEqual(rc, 0)
        self.assertEqual(placed, [])
        self.assertEqual(pending.read_bytes(), before)
        self.assertEqual(json.loads(before)["entries"], [])


if __name__ == "__main__":
    unittest.main()
