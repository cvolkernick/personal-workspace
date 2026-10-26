"""End-to-end dry run: rules review -> order gate -> planner -> submit.

Uses ``python -m treasury.fund_manager_order_gate`` (the daily/bp-poll entry)
and a fake broker client. Settled cash is $7.62. Nothing here contacts a
broker, grok, Robinhood, or Turso, and nothing places or reviews a live order.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury import fund_manager as fm  # noqa: E402
from treasury import fund_manager_broker as broker  # noqa: E402
from treasury import fund_manager_order_gate as gate  # noqa: E402

# Monday 2026-10-26 11:00 ET. Inside the place window, not a holiday.
AS_OF = "2026-10-26T14:00:00+00:00"
NOW = "2026-10-26T15:00:00+00:00"
EXPECTED = {"STRC": 1.90, "SATA": 1.90, "STRK": 1.14, "IBIT": 2.68}
STOCKS = {"TSLA", "SPCX"}


def _pos(sym: str, qty: float, px: float) -> dict:
    return {
        "symbol": sym,
        "quantity": str(qty),
        "average_buy_price": str(px),
        "market_value": f"{qty * px:.2f}",
    }


def _snapshot() -> dict:
    # BTC sleeve 50 of 170 invested (~29%) so the sleeve gate keeps stocks out.
    return {
        "agentic": {
            "account_number": "674601752",
            "cash": 7.62,
            "buying_power": 7.62,
            "unleveraged_buying_power": 7.62,
            "settled_cash": 7.62,
            "as_of": AS_OF,
            "positions": [
                _pos("TSLA", 1, 60),
                _pos("SPCX", 1, 60),
                _pos("STRC", 0.5, 100),
            ],
        }
    }


def _child_env(extra: dict) -> dict:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT", "TERM")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONNOUSERSITE"] = "1"
    env["FM_GATE_TEST"] = "1"
    env["FM_NOW"] = NOW
    env["FM_BROKER_ADAPTER"] = "treasury.tests.fm_e2e_doubles:submit"
    env["FM_BROKER_CLIENT"] = "treasury.tests.fm_e2e_doubles:factory"
    env.update(extra)
    banned_bits = ("TURSO", "ROBINHOOD", "GROK", "XAI", "OPENAI", "ANTHROPIC")
    for key in list(env):
        upper = key.upper()
        if any(bit in upper for bit in banned_bits):
            env.pop(key, None)
    return env


class OrderPathEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.addCleanup(self.td.cleanup)
        policy = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
        self.assertFalse(policy["execution"]["broker_adapter"]["honor_ai_floating"])
        self.policy_path = self.dir / "fund_manager.json"
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self.policy = policy
        self.snap_path = self.dir / "robinhood_latest.json"
        self.snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

    def _run_gate(self, intents_path: Path, ledger: Path, log: Path) -> tuple:
        env = _child_env(
            {
                "FM_POLICY_PATH": str(self.policy_path),
                "FM_RH_SNAPSHOT_PATH": str(self.snap_path),
                "FM_BROKER_LEDGER_PATH": str(ledger),
                "FM_E2E_LOG": str(log),
            }
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "treasury.fund_manager_order_gate",
                "--execute-intents",
                str(intents_path),
                "--no-record",
            ],
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"gate stdout was not JSON (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
        events = json.loads(log.read_text(encoding="utf-8")) if log.is_file() else []
        return proc.returncode, out, events

    def _assert_four_buys(self, out: dict, events: list) -> None:
        legs = {row["symbol"]: row["notional_usd"] for row in out["plan"]["legs"]}
        self.assertEqual(set(legs), set(EXPECTED))
        for sym, amt in EXPECTED.items():
            self.assertAlmostEqual(legs[sym], amt, places=2, msg=sym)
        self.assertAlmostEqual(sum(legs.values()), 7.62, places=2)
        self.assertNotIn("TSLA", legs)
        self.assertNotIn("SPCX", legs)
        self.assertNotIn("MSTR", legs)
        placed = out["placed"]
        self.assertEqual(len(placed), 4, out)
        self.assertEqual(out["broker_calls"], 4)
        self.assertEqual({row["symbol"] for row in placed}, set(EXPECTED))
        for row in placed:
            self.assertEqual(row["status"], "placed", row)
            self.assertEqual(row["side"], "buy")
            self.assertNotIn("not a GatedOrder", row.get("broker_reason") or "")
            self.assertAlmostEqual(row["notional_usd"], EXPECTED[row["symbol"]], places=2)
        submits = [e for e in events if e.get("event") == "submit"]
        self.assertEqual(len(submits), 4, events)
        for event in submits:
            self.assertTrue(event["is_gated"], event)
            self.assertEqual(event["cls"], "treasury.fund_manager_order_gate.GatedOrder")
            self.assertEqual(event["side"], "buy")
            self.assertAlmostEqual(float(event["notional_usd"]), EXPECTED[event["symbol"]], places=2)
        places = [e for e in events if e.get("event") == "place_order"]
        self.assertEqual(len(places), 4, events)
        for event in places:
            order = event["order"]
            self.assertEqual(order["side"], "buy")
            self.assertAlmostEqual(float(order["dollar_amount"]), EXPECTED[order["symbol"]], places=2)
            self.assertEqual(event["result"]["state"], "accepted")
        self.assertFalse(any(e.get("event") == "place_order" and e["order"].get("side") != "buy" for e in events))
        self.assertFalse(STOCKS & {e.get("symbol") for e in submits})
        self.assertNotIn("grok", json.dumps(events).lower())

    def test_rules_review_then_empty_and_named_intents_mint_and_submit(self) -> None:
        snap = _snapshot()
        with mock.patch.object(fm, "write_fund_manager_snapshot", return_value=self.dir / "fm.json"):
            reviewed = fm.rules_based_review(rh_snapshot=snap, policy=self.policy, log=False)
        rr = reviewed["rules_review"]
        self.assertTrue(rr["need_llm"], rr)
        self.assertEqual(rr["outcome"], "need_llm")
        self.assertAlmostEqual(rr["deployable_usd"], 7.62, places=2)

        missing = self.dir / "no-such-intents.json"
        rc, out, events = self._run_gate(missing, self.dir / "ledger-empty.json", self.dir / "log-empty.json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["blocked"], [])
        self._assert_four_buys(out, events)
        self.assertIn("IBIT_fallback", out["plan"]["floating_source"])

        empty = self.dir / "empty-intents.json"
        empty.write_text(json.dumps({"orders": []}), encoding="utf-8")
        rc, out, events = self._run_gate(empty, self.dir / "ledger-blank.json", self.dir / "log-blank.json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["blocked"], [])
        self._assert_four_buys(out, events)

        named = self.dir / "named-intents.json"
        named.write_text(
            json.dumps(
                {
                    "orders": [
                        {"symbol": "MSTR", "side": "buy", "notional_usd": 10, "purpose": "deploy"},
                        {"symbol": "TSLA", "side": "buy", "notional_usd": 5, "purpose": "deploy"},
                        {"symbol": "SPCX", "side": "buy", "notional_usd": 5, "purpose": "deploy"},
                        {"symbol": "STRC", "side": "sell", "notional_usd": 1, "purpose": "rebalance"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc, out, events = self._run_gate(named, self.dir / "ledger-named.json", self.dir / "log-named.json")
        self.assertEqual(rc, 3, out)  # the sell is blocked; the buys still mint
        self._assert_four_buys(out, events)
        self.assertIn("MSTR", out["plan"]["floating_source"])
        self.assertIn("ignored", out["plan"]["floating_source"])
        dropped = {row["symbol"] for row in out["plan"]["dropped"]}
        self.assertTrue(STOCKS <= dropped, out["plan"]["dropped"])
        sells = [row for row in out["blocked"] if row.get("side") == "sell"]
        self.assertEqual(len(sells), 1, out["blocked"])
        self.assertEqual(sells[0]["symbol"], "STRC")
        self.assertNotEqual(sells[0]["status"], "placed")
        self.assertFalse(any(e.get("event") == "place_order" and e["order"].get("side") == "sell" for e in events))

        # Once-per-day claim from the first run blocks a second submit.
        again_log = self.dir / "log-again.json"
        rc, again, again_events = self._run_gate(missing, self.dir / "ledger-empty.json", again_log)
        self.assertEqual(rc, 0, again)
        self.assertEqual(again["plan"]["status"], "skipped_already_bought_today")
        self.assertEqual(again["broker_calls"], 0)
        self.assertEqual(again["placed"], [])
        self.assertFalse(any(e.get("event") == "place_order" for e in again_events))

    def test_submit_still_rejects_a_non_gated_order(self) -> None:
        class Boom:
            def __getattr__(self, name: str):
                raise AssertionError(f"broker client should not be called ({name})")

        plain = {"symbol": "IBIT", "side": "buy", "notional_usd": 2.68, "purpose": "deploy"}
        out = broker.submit(plain, client=Boom(), policy_path=self.policy_path, now=gate.test_now())
        self.assertEqual(out["status"], broker.S_NOT_GATED)
        self.assertIn("not a GatedOrder minted by the order gate", out["broker_reason"])
        with self.assertRaises(TypeError):
            gate.GatedOrder(plain, gate.Verdict(True, "ok", "forged", "buy"))

    def test_clock_override_is_inert_without_the_test_flag(self) -> None:
        # Production must not honor FM_NOW.
        wall = gate._parse_ts(NOW) + timedelta(hours=5)
        with mock.patch.dict(os.environ, {"FM_NOW": NOW}, clear=False):
            os.environ.pop("FM_GATE_TEST", None)
            with mock.patch.object(gate, "_now", return_value=wall):
                stamped = gate.test_now()
        self.assertEqual(stamped, wall)


if __name__ == "__main__":
    unittest.main()
