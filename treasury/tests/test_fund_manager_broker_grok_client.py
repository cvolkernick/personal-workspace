"""GrokMcpClient output parsing + get_portfolio buying power (fix for prism 2026-10-25 smoke test).

Fixtures in fixtures/grok_1_0_13/ are real ``grok -p ... --json-schema`` stdout
captured on prism (grok 1.0.13, read-only tools, place/review/cancel denied),
redacted: sessionId/requestId zeroed, model reasoning removed. grok 1.0.13 prints
one multi-line JSON envelope with the result under ``structuredOutput``; the old
parser read only the last line (``}``) and looked for ``structured_output``.

No network, no grok, no Robinhood: every runner is a stub.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from treasury import fund_manager_broker as broker  # noqa: E402
from test_fund_manager_broker import ACCT, NOW, TEN, Base  # noqa: E402

FIX = HERE / "fixtures" / "grok_1_0_13"


def fixture(name: str) -> str:
    return (FIX / f"{name}.stdout.json").read_text(encoding="utf-8")


def envelope(structured, turns=3, key="structuredOutput") -> str:
    """Synthetic grok 1.0.13-style envelope (same layout as the captured fixtures)."""
    env = {"text": json.dumps(structured), "stopReason": "end_turn", "sessionId": "0", "requestId": "0",
           "usage": {"input_tokens": 1, "output_tokens": 1}, "num_turns": turns, key: structured}
    return json.dumps(env, indent=2) + "\n"


def allowed_tools(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--allow"]


def denied_tools(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--deny"]


def tool(name):
    return f"MCPTool(robinhood-trading__{name})"


class TestParseGrokOutput(Base):
    def test_fixtures_are_multiline_envelopes(self):
        for n in ("portfolio_ok", "orders_empty", "quotes", "portfolio_no_tool_call"):
            raw = fixture(n)
            self.assertGreater(len(raw.strip().splitlines()), 10, n)
            self.assertEqual(raw.strip().splitlines()[-1], "}", n)  # what the old parser choked on
            self.assertIn("structuredOutput", json.loads(raw), n)

    def test_whole_stdout_structured_output(self):
        self.assertEqual(broker.parse_grok_output(fixture("orders_empty")), {"orders": []})
        q = broker.parse_grok_output(fixture("quotes"))
        self.assertEqual([x["symbol"] for x in q["quotes"]], ["STRC", "SATA", "STRK", "IBIT"])

    def test_structuredoutput_wins_over_text(self):
        # the captured quotes envelope has two concatenated objects in "text"; structuredOutput is authoritative
        env = json.loads(fixture("quotes"))
        self.assertTrue(env["text"].startswith('{"quotes":[]}'))
        self.assertEqual(len(broker.parse_grok_output(fixture("quotes"))["quotes"]), 4)

    def test_legacy_structured_output_key(self):
        self.assertEqual(broker.parse_grok_output(envelope({"ok": True}, key="structured_output")), {"ok": True})

    def test_leading_noise_falls_back_to_last_complete_object(self):
        raw = "warning: update available\nloading MCP servers {partial\n" + fixture("orders_empty")
        self.assertEqual(broker.parse_grok_output(raw), {"orders": []})
        raw2 = "noise " + json.dumps({"a": 1}) + "\n" + fixture("orders_empty") + "\ntrailing log line"
        self.assertEqual(broker.parse_grok_output(raw2), {"orders": []})

    def test_bare_object_is_the_result(self):
        self.assertEqual(broker.parse_grok_output('{"tradable": true, "fractional_tradable": true}'),
                         {"tradable": True, "fractional_tradable": True})

    def test_clear_errors(self):
        cases = {
            "": "empty output",
            "   \n": "empty output",
            "not json at all": "no complete JSON object",
            '{"text": "x", "stopReason": "max_turns", "sessionId": "0"}': "no structuredOutput",
            '[1, 2]': "expected an object",
            '{"structuredOutput": "nope", "num_turns": 3}': "structuredOutput is str",
        }
        for raw, frag in cases.items():
            with self.assertRaises(broker.BrokerError, msg=raw) as cm:
                broker.parse_grok_output(raw)
            self.assertIn(frag, str(cm.exception), raw)

    def test_no_tool_round_is_rejected_when_required(self):
        raw = fixture("portfolio_no_tool_call")  # real capture: all-null answer in 1 turn, no tool call
        self.assertEqual(json.loads(raw)["num_turns"], 1)
        self.assertEqual(broker.parse_grok_output(raw)["account_number"], "")  # parseable...
        with self.assertRaises(broker.BrokerError) as cm:  # ...but never trusted by the client
            broker.parse_grok_output(raw, require_tool_round=True)
        self.assertIn("without a tool call", str(cm.exception))
        with self.assertRaises(broker.BrokerError):
            broker.parse_grok_output(envelope({"orders": []}, turns=1), require_tool_round=True)
        self.assertEqual(broker.parse_grok_output(envelope({"orders": []}, turns=2), require_tool_round=True),
                         {"orders": []})


class TestGrokClientReads(Base):
    def client(self, outputs, seen=None):
        def runner(cmd, **kw):
            if seen is not None:
                seen.append(cmd)
            allows = allowed_tools(cmd)
            for name, out in outputs.items():
                if tool(name) in allows:
                    return mock.Mock(returncode=0, stdout=out)
            raise AssertionError(f"unexpected tools {allows}")
        return broker.GrokMcpClient(grok_bin="grok", runner=runner)

    def test_get_account_reads_get_portfolio_fixture(self):
        seen = []
        acc = self.client({"get_portfolio": fixture("portfolio_ok")}, seen).get_account(ACCT)
        self.assertEqual(acc["account_number"], ACCT)
        self.assertEqual(acc["buying_power"], 7.62)
        self.assertEqual(acc["cash"], 7.62)
        self.assertEqual(acc["unleveraged_buying_power"], 7.62)
        # no explicit settled field -> min(cash, unleveraged_buying_power)
        self.assertEqual(acc["settled_cash"], 7.62)
        self.assertEqual(acc["settled_cash_source"], "min(cash, unleveraged_buying_power)")
        self.assertEqual(broker.settled_cash(acc), broker._d(7.62))
        cmd = seen[0]
        self.assertEqual(allowed_tools(cmd), [tool("get_portfolio"), tool("get_accounts")])
        for d in ("place_*", "review_equity_order", "cancel_*", "*sell*"):
            self.assertIn(tool(d), denied_tools(cmd))
        self.assertIn("search_tool", cmd[cmd.index("-p") + 1])

    def test_get_account_no_tool_call_fixture_fails_closed(self):
        with self.assertRaises(broker.BrokerError):
            self.client({"get_portfolio": fixture("portfolio_no_tool_call")}).get_account(ACCT)

    def test_reads_orders_and_quotes_fixtures(self):
        c = self.client({"get_equity_orders": fixture("orders_empty"), "get_equity_quotes": fixture("quotes")})
        self.assertEqual(c.get_recent_orders(ACCT, NOW), [])
        self.assertEqual(c.get_quotes(["STRC", "SATA", "STRK", "IBIT"]),
                         {"STRC": 99.36, "SATA": 97.98, "STRK": 69.38, "IBIT": 45.945})

    def test_orders_without_tool_round_fail_closed(self):
        # an invented empty order list must never reach the same-day order guard
        with self.assertRaises(broker.BrokerError):
            self.client({"get_equity_orders": envelope({"orders": []}, turns=1)}).get_recent_orders(ACCT, NOW)

    def test_portfolio_account_settled_cash_is_conservative(self):
        pa = broker.portfolio_account
        self.assertEqual(pa({"account_number": ACCT, "buying_power": 50, "cash": 10, "unleveraged_buying_power": 8,
                             "settled_cash": None})["settled_cash"], 8)  # never leveraged BP
        self.assertEqual(pa({"account_number": ACCT, "buying_power": 50, "cash": 10, "unleveraged_buying_power": 12,
                             "settled_cash": 6})["settled_cash"], 6)  # explicit lower wins
        self.assertEqual(pa({"account_number": ACCT, "buying_power": 50, "cash": 10, "unleveraged_buying_power": 12,
                             "settled_cash": 30})["settled_cash"], 10)  # explicit never above cash
        none = pa({"account_number": ACCT, "buying_power": 50, "cash": None, "unleveraged_buying_power": None,
                   "settled_cash": None})
        self.assertIsNone(none["settled_cash"])
        self.assertEqual(none["settled_cash_source"], "unknown")
        self.assertEqual(pa({"account_number": ACCT, "buying_power": 5, "cash": -3, "unleveraged_buying_power": 4,
                             "settled_cash": None})["settled_cash"], 0.0)

    def test_read_calls_deny_review_and_place(self):
        seen = []
        c = self.client({"get_equity_orders": fixture("orders_empty"), "get_equity_quotes": fixture("quotes"),
                         "get_portfolio": fixture("portfolio_ok")}, seen)
        c.get_account(ACCT); c.get_recent_orders(ACCT, NOW); c.get_quotes(["IBIT"])
        for cmd in seen:
            self.assertFalse([a for a in allowed_tools(cmd) if "place_" in a or "review_" in a])
            self.assertIn(tool("review_equity_order"), denied_tools(cmd))
            self.assertIn(tool("place_*"), denied_tools(cmd))


class TestSubmitWithGrokClient(Base):
    """submit() end to end with a stubbed grok runner fed the captured fixtures."""

    def runner_for(self, outputs, seen):
        def runner(cmd, **kw):
            allows = allowed_tools(cmd)
            seen.append(allows)
            for name, out in outputs.items():
                if tool(name) in allows:
                    return mock.Mock(returncode=0, stdout=out)
            raise AssertionError(f"unexpected tools {allows}")
        return runner

    def test_review_reject_never_places(self):
        seen = []
        outs = {"get_portfolio": fixture("portfolio_ok"), "get_equity_orders": fixture("orders_empty"),
                "get_equity_tradability": envelope({"tradable": True, "fractional_tradable": True}),
                "review_equity_order": envelope({"ok": False, "reason": "stub"}),
                "place_equity_order": "MUST NOT BE CALLED"}
        cl = broker.GrokMcpClient(grok_bin="grok", runner=self.runner_for(outs, seen))
        allowed, _ = self.gated(TEN)
        out = self.submit(allowed[0], cl)
        self.assertEqual(out["status"], broker.S_REVIEW_FAILED)
        self.assertIn("broker review rejected", out["broker_reason"])
        self.assertNotIn([tool("place_equity_order")], seen)
        self.assertEqual(seen[0], [tool("get_portfolio"), tool("get_accounts")])

    def test_no_tool_call_portfolio_fails_closed_before_orders(self):
        seen = []
        cl = broker.GrokMcpClient(grok_bin="grok", runner=self.runner_for(
            {"get_portfolio": fixture("portfolio_no_tool_call")}, seen))
        allowed, _ = self.gated(TEN)
        out = self.submit(allowed[0], cl)
        self.assertEqual(out["status"], broker.S_REVIEW_FAILED)
        self.assertIn("without a tool call", out["broker_reason"])
        self.assertEqual(len(seen), 1)

    def test_leg_over_live_buying_power_rejected(self):
        seen = []
        poor = envelope({"account_number": ACCT, "buying_power": 1.0, "cash": 1.0,
                         "unleveraged_buying_power": 1.0, "settled_cash": None}, turns=4)
        cl = broker.GrokMcpClient(grok_bin="grok", runner=self.runner_for({"get_portfolio": poor}, seen))
        allowed, _ = self.gated(TEN)
        out = self.submit(allowed[0], cl)
        self.assertEqual(out["status"], broker.S_REVIEW_FAILED)
        self.assertIn("exceeds live buying power", out["broker_reason"])


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
