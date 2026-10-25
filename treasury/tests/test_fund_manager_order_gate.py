"""#1093 no-sell-to-rebalance guard: gate, decision log, rules engine, runners, CI lint.

No test reaches Robinhood or GitHub: broker submit is a mock, ``post_ops_github``
is mocked, decision/journal paths are temp files.
"""

from __future__ import annotations

import json
import os
import re
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
from treasury import fund_manager_order_gate as gate  # noqa: E402

NOW = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
POLICY_FILE = ROOT / "investment" / "fund_manager.json"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def good_override(**kw):
    o = {
        "id": "ovr-1",
        "approver": "Chris",
        "symbols": ["MARA"],
        "max_qty": 1.0,
        "max_notional_usd": 50.0,
        "reason": "risk exit approved by Chris",
        "approved_at": _iso(NOW - timedelta(hours=1)),
        "expires_at": _iso(NOW + timedelta(hours=6)),
    }
    o.update(kw)
    return o


def authed(ov=None, **kw):
    """An AuthedOverride WITHOUT git (unit tests of caps/expiry). The git
    authentication itself is covered by TestOverrideAuthentication."""
    return gate._mint_authed_override(good_override() if ov is None else ov, **kw)


def led(**used):
    """In-memory spend ledger (never touches disk)."""
    return gate.SpendLedger(Path("/nonexistent/spend.json"), persist=False)


def pol(live=False, allowed=("buy",), blocked=("BE",), guard=True):
    return gate.GatePolicy(True, "", tuple(allowed), tuple(blocked), live=live, min_trade_usd=1.0)


def buy(sym="STRC", usd=5.0, **kw):
    d = {"symbol": sym, "side": "buy", "notional_usd": usd, "purpose": "deploy"}
    d.update(kw)
    return d


def sell(sym="MARA", **kw):
    d = {"symbol": sym, "side": "sell", "quantity": 0.5, "notional_usd": 10.0, "purpose": "risk_exit"}
    d.update(kw)
    return d


class TestPolicyFailClosed(unittest.TestCase):
    def test_repo_policy_loads_buy_only_and_live_resumed_with_adapter(self):
        # Chris resumed Agentic buys 2026-10-25 2:58 PM ET (live:true, Nakatoshi agrees);
        # the buy-only broker adapter stays wired behind this gate.
        p = gate.load_gate_policy(POLICY_FILE)
        self.assertTrue(p.ok, p.error)
        self.assertEqual(p.allowed_sides, ("buy",))
        self.assertTrue(p.live)
        raw = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        self.assertIs(raw["live"], True)
        self.assertIs(raw["execution"]["review_before_place"], True)
        self.assertEqual(raw["execution"]["broker_adapter"]["adapter"], "treasury.fund_manager_broker:submit")
        self.assertIs(raw["guardrails"]["no_sell_to_rebalance"], True)
        self.assertEqual(raw["guardrails"]["allowed_sides"], ["buy"])

    def test_missing_malformed_or_disabled_policy_places_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            bad = []
            missing = Path(td) / "nope.json"
            bad.append(gate.load_gate_policy(missing))
            junk = Path(td) / "junk.json"
            junk.write_text("{not json", encoding="utf-8")
            bad.append(gate.load_gate_policy(junk))
            off = Path(td) / "off.json"
            off.write_text(json.dumps({"guardrails": {"no_sell_to_rebalance": False, "allowed_sides": ["buy", "sell"]}}))
            bad.append(gate.load_gate_policy(off))
            noblock = Path(td) / "nob.json"
            noblock.write_text(json.dumps({"live": True}))
            bad.append(gate.load_gate_policy(noblock))
            for p in bad:
                self.assertFalse(p.ok)
                v = gate.evaluate_intent(buy(), policy=p, deployable_cash=100.0, now=NOW)
                self.assertFalse(v.allowed, "even buys are blocked when policy cannot load")
                self.assertEqual(v.code, gate.R_POLICY)
                self.assertEqual(v.status, gate.BLOCK_STATUS)

    def test_config_cannot_add_sell_to_allowed_sides(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "p.json"
            f.write_text(json.dumps({"guardrails": {"no_sell_to_rebalance": True, "allowed_sides": ["buy", "sell"]}}))
            p = gate.load_gate_policy(f)
        self.assertEqual(p.allowed_sides, ("buy",))
        v = gate.evaluate_intent(sell(), policy=p, override=None, now=NOW)
        self.assertFalse(v.allowed)


class TestSellToRebalanceBlocked(unittest.TestCase):
    def test_rebalance_sells_blocked_even_with_valid_override(self):
        cases = [
            sell(purpose="rebalance"),
            sell(purpose="risk_exit", reason="trim TSLA toward the 40/60 target"),
            sell(sym="TSLA", purpose="deploy", note="sell to buy BTC complex"),
            sell(purpose="risk_exit", rationale="rotate out of miners"),
            {"symbol": "TSLA", "side": "SELL", "notional_usd": 3.0, "purpose": "rebalance"},
            {"symbol": "MARA", "side": "sell", "quantity": 0.5, "purpose": "tidy up drift"},
        ]
        for c in cases:
            for ov in (None, authed(good_override(symbols=["MARA", "TSLA"]))):
                v = gate.evaluate_intent(c, policy=pol(), override=ov, now=NOW, ledger=led())
                self.assertFalse(v.allowed, c)
                self.assertEqual(v.code, gate.R_REBALANCE_SELL, c)
                self.assertEqual(v.status, "blocked_no_sell_guard")

    def test_shorts_options_crypto_sells_never_allowed_even_with_override(self):
        cases = [
            sell(side="sell_short"),
            sell(side="short"),
            sell(side="sell_to_open"),
            sell(short=True),
            sell(asset_class="option"),
            sell(strike=10, expiration="2026-12-18"),
            buy(asset_class="option"),
            sell(sym="BTC", asset_class="crypto"),
        ]
        for c in cases:
            v = gate.evaluate_intent(c, policy=pol(), override=authed(good_override(symbols=["MARA", "BTC"])), now=NOW, ledger=led())
            self.assertFalse(v.allowed, c)


class TestAllowedNonRebalanceSell(unittest.TestCase):
    """Issue AC1/AC8: a sell is only possible with an explicit Chris override."""

    def test_non_rebalance_sell_without_override_is_blocked(self):
        v = gate.evaluate_intent(sell(), policy=pol(), override=None, now=NOW)
        self.assertFalse(v.allowed)
        self.assertEqual(v.code, gate.R_SELL_NO_OVERRIDE)

    def test_valid_override_allows_exactly_that_sell(self):
        v = gate.evaluate_intent(sell(), policy=pol(), override=authed(), now=NOW, ledger=led())
        self.assertTrue(v.allowed, v)
        self.assertTrue(v.override_used)

    def test_override_edge_cases_are_rejected(self):
        bad = {
            "expired": good_override(expires_at=_iso(NOW - timedelta(minutes=1))),
            "over_24h": good_override(expires_at=_iso(NOW + timedelta(hours=25))),
            "window_over_24h": good_override(approved_at=_iso(NOW - timedelta(hours=20)), expires_at=_iso(NOW + timedelta(hours=8))),
            "naive_ts": good_override(expires_at="2026-10-16T20:00:00"),
            "no_expiry": {k: v for k, v in good_override().items() if k != "expires_at"},
            "not_chris": good_override(approver="Grok"),
            "no_approver": {k: v for k, v in good_override().items() if k != "approver"},
            "no_reason": good_override(reason=""),
            "no_caps": {k: v for k, v in good_override().items() if k not in ("max_qty", "max_notional_usd")},
            "empty_symbols": good_override(symbols=[]),
            "no_id": {k: v for k, v in good_override().items() if k != "id"},
            "not_a_dict": ["Chris"],
        }
        for name, ov in bad.items():
            wrapped = authed(ov) if isinstance(ov, dict) else ov
            v = gate.evaluate_intent(sell(), policy=pol(), override=wrapped, now=NOW, ledger=led())
            self.assertFalse(v.allowed, name)

    def test_plain_dict_override_never_authorises_a_sell(self):
        # the old "type approver: Chris into a JSON file" path: a raw dict is not authenticated
        v = gate.evaluate_intent(sell(), policy=pol(), override=good_override(), now=NOW, ledger=led())
        self.assertFalse(v.allowed)
        self.assertEqual(v.code, gate.R_OVERRIDE_UNAUTH)
        with self.assertRaises(TypeError):
            gate.AuthedOverride([good_override()], 100.0, "x")

    def test_different_symbol_or_larger_qty_or_notional_rejected(self):
        ov = authed()

        def ev(i):
            return gate.evaluate_intent(i, policy=pol(), override=ov, now=NOW, ledger=led())

        self.assertFalse(ev(sell(sym="TSLA")).allowed)
        self.assertFalse(ev(sell(quantity=1.01)).allowed)
        self.assertFalse(ev(sell(notional_usd=50.01)).allowed)
        # qty cap present but intent carries no qty: cannot verify => blocked
        no_qty = {"symbol": "MARA", "side": "sell", "notional_usd": 10.0, "purpose": "risk_exit"}
        self.assertFalse(ev(no_qty).allowed)

    def test_batch_budget_counts_all_sells_against_override(self):
        res = gate.gate_orders(
            [sell(quantity=0.6, notional_usd=10), sell(quantity=0.6, notional_usd=10)],
            deployable_cash=0.0, policy=pol(), override=authed(), now=NOW, ledger=led(),
        )
        self.assertEqual(len(res.allowed), 1)
        self.assertEqual(len(res.blocked), 1)

    def test_load_override_missing_or_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(gate.load_override(Path(td) / "none.json"), (None, ""))
            bad = Path(td) / "o.json"
            bad.write_text("{oops")
            data, err = gate.load_override(bad)
            self.assertIsNone(data)
            self.assertTrue(err)


CHRIS = ("Chris Volkernick", "cvolkern@gmail.com")
BOT = ("GrokBot-BTC", "GrokBot-BTC@users.noreply.github.com")
GITHUB = ("GitHub", "noreply@github.com")


class GitRepoCase(unittest.TestCase):
    """Real throwaway git repos: override authentication is tested against git itself."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.repo = Path(self._td.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "README.md").write_text("x\n")
        self.commit("README.md", author=CHRIS, msg="init")
        (self.repo / "investment").mkdir()
        self.ov = self.repo / "investment" / "fund_manager_sell_override.json"
        self.ledger = Path(self._td.name) / "spend.json"
        self.env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def git(self, *args, env=None):
        import subprocess

        e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        e.update(env or {})
        r = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, rel, *, author=CHRIS, committer=None, msg="c"):
        committer = committer or author
        self.git("add", "--", rel)
        self.git(
            "commit", "-q", "-m", msg,
            env={
                "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
                "GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1],
            },
        )

    def write_override(self, data=None):
        self.ov.write_text(json.dumps(data if data is not None else live_override()), encoding="utf-8")

    def load(self, policy=None):
        return gate.load_override(self.ov, repo_root=self.repo, policy=policy)


def live_override(**kw):
    """Override valid at the REAL current time (the CLI path uses the wall clock)."""
    now = datetime.now(timezone.utc)
    o = good_override(
        approved_at=_iso(now - timedelta(minutes=5)), expires_at=_iso(now + timedelta(hours=6))
    )
    o.update(kw)
    return o


class TestOverrideAuthentication(GitRepoCase):
    def assertRejected(self, needle):
        ov, err = self.load()
        self.assertIsNone(ov)
        self.assertIn(needle, err)

    def test_missing_file_is_simply_no_override(self):
        self.assertEqual(self.load(), (None, ""))

    def test_uncommitted_file_is_rejected(self):
        self.write_override()  # untracked
        self.assertRejected("not committed")

    def test_staged_but_uncommitted_file_is_rejected(self):
        self.write_override()
        self.git("add", "--", "investment/fund_manager_sell_override.json")
        self.assertRejected("uncommitted changes")

    def test_modified_after_commit_is_rejected(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        self.assertIsNotNone(self.load()[0])
        # LLM/anything raises the cap in the working tree after Chris committed
        self.write_override(live_override(max_notional_usd=5000.0))
        self.assertRejected("uncommitted changes")

    def test_modified_but_hidden_from_status_is_still_rejected(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        self.git("update-index", "--assume-unchanged", "investment/fund_manager_sell_override.json")
        self.write_override(live_override(max_notional_usd=5000.0))
        self.assertRejected("differs from git HEAD")

    def test_wrong_author_is_rejected(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=BOT)
        self.assertRejected("author is not Chris")

    def test_chris_author_with_untrusted_committer_is_rejected(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS, committer=BOT)
        self.assertRejected("committer")

    def test_chris_commit_via_github_web_is_accepted(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS, committer=GITHUB)
        ov, err = self.load()
        self.assertEqual(err, "")
        self.assertIsInstance(ov, gate.AuthedOverride)

    def test_not_a_git_repo_fails_closed(self):
        loose = Path(self._td.name) / "loose"
        loose.mkdir()
        f = loose / "o.json"
        f.write_text(json.dumps(live_override()))
        ov, err = gate.load_override(f, repo_root=loose)
        self.assertIsNone(ov)
        self.assertIn("not a git repository", err)

    def test_valid_committed_override_loads(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        ov, err = self.load()
        self.assertEqual(err, "")
        self.assertEqual(ov.overrides[0]["id"], "ovr-1")
        self.assertEqual(ov.total_cap_usd, gate.DEFAULT_TOTAL_CAP_USD)
        self.assertEqual(len(ov.commit), 40)

    def test_committed_but_malformed_json_is_rejected(self):
        self.ov.write_text("{oops")
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        self.assertRejected("malformed")

    def test_last_commit_touching_file_decides_not_head(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        (self.repo / "other.txt").write_text("bot change\n")
        self.commit("other.txt", author=BOT)  # HEAD is a bot commit, file untouched
        self.assertIsNotNone(self.load()[0])
        self.write_override(live_override(max_notional_usd=60.0))
        self.commit("investment/fund_manager_sell_override.json", author=BOT)  # bot re-commits it
        self.assertRejected("author is not Chris")

    def test_trusted_ref_requires_commit_to_be_pushed(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        head = self.git("rev-parse", "HEAD")
        self.git("branch", "pushed", "HEAD~1")  # a ref that does NOT contain the override commit
        with mock.patch.dict(os.environ, {gate.OVERRIDE_TRUSTED_REF_ENV: "pushed"}):
            self.assertRejected("not contained")
        self.git("branch", "pushed2", head)
        with mock.patch.dict(os.environ, {gate.OVERRIDE_TRUSTED_REF_ENV: "pushed2"}):
            self.assertIsNotNone(self.load()[0])

    def test_total_cap_in_file_and_policy_can_only_lower_ceiling(self):
        self.write_override({"overrides": [live_override()], "total_max_notional_usd": 40.0})
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        self.assertEqual(self.load()[0].total_cap_usd, 40.0)
        self.assertEqual(self.load(policy=pol_cap(25.0))[0].total_cap_usd, 25.0)
        self.assertEqual(self.load(policy=pol_cap(900.0))[0].total_cap_usd, 40.0)
        self.write_override({"overrides": [live_override()], "total_max_notional_usd": 5000.0})
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        self.assertEqual(self.load()[0].total_cap_usd, gate.DEFAULT_TOTAL_CAP_USD)


def pol_cap(cap):
    p = pol()
    p.override_total_cap_usd = cap
    return p


def run_gate(intents, override, ledger_path, *, now=NOW, live=True, policy=None, **kw):
    return gate.execute_intents(
        intents, deployable_cash=0.0, submit=mock.Mock(return_value={"status": "submitted"}),
        policy=policy or pol(live=live), override=override, now=now, record=False,
        ledger_path=ledger_path, **kw,
    )


class TestOverrideSpendAndCaps(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.lp = Path(self._td.name) / "spend.json"

    def spent(self, oid="ovr-1"):
        return gate.SpendLedger(self.lp, gate.SpendLedger._load(self.lp)).used(oid)

    def test_valid_override_passes_exactly_within_caps_then_nothing_more(self):
        ov = authed()  # max_qty 1.0, max_notional 50.0, total ceiling 100
        out = run_gate([sell(quantity=1.0, notional_usd=50.0)], ov, self.lp)
        self.assertEqual(len(out["placed"]), 1, out)
        self.assertEqual(out["broker_calls"], 1)
        self.assertEqual(self.spent(), (1.0, 50.0))
        out2 = run_gate([sell(quantity=0.0001, notional_usd=0.01)], ov, self.lp)
        self.assertEqual(out2["placed"], [])
        self.assertEqual(out2["broker_calls"], 0)
        self.assertEqual(self.spent(), (1.0, 50.0), "blocked sells are never charged")

    def test_cumulative_spend_exceeding_per_override_cap_across_two_runs(self):
        ov = authed()
        r1 = run_gate([sell(quantity=0.6, notional_usd=30.0)], ov, self.lp)
        self.assertEqual(len(r1["placed"]), 1)
        # a fresh run (the next 15-minute poll) gets NO fresh cap
        r2 = run_gate([sell(quantity=0.6, notional_usd=30.0)], ov, self.lp)
        self.assertEqual(r2["placed"], [])
        self.assertEqual(r2["broker_calls"], 0)
        self.assertEqual(r2["blocked"][0]["reason_code"], gate.R_SELL_NO_OVERRIDE)
        self.assertIn("max_qty", r2["blocked"][0]["reason"])
        # what is left still works: 0.4 qty / $20
        r3 = run_gate([sell(quantity=0.4, notional_usd=20.0)], ov, self.lp)
        self.assertEqual(len(r3["placed"]), 1)
        self.assertEqual(self.spent(), (1.0, 50.0))

    def test_notional_only_override_cumulative_cap(self):
        ov = authed(good_override(max_qty=None, max_notional_usd=50.0))
        self.assertEqual(len(run_gate([sell(quantity=5, notional_usd=30.0)], ov, self.lp)["placed"]), 1)
        r2 = run_gate([sell(quantity=5, notional_usd=30.0)], ov, self.lp)
        self.assertEqual(r2["placed"], [])
        self.assertIn("max_notional_usd", r2["blocked"][0]["reason"])

    def test_total_cap_across_all_overrides_exceeded(self):
        data = {
            "overrides": [
                good_override(id="a", max_qty=None, max_notional_usd=80.0),
                good_override(id="b", max_qty=None, max_notional_usd=80.0),
            ],
            "total_max_notional_usd": 100.0,
        }
        ov = authed(data, total_cap_usd=None)
        self.assertEqual(ov.total_cap_usd, 100.0)
        r1 = run_gate([sell(quantity=1, notional_usd=70.0)], ov, self.lp)
        self.assertEqual(len(r1["placed"]), 1)
        self.assertEqual(self.spent("a")[1], 70.0)
        # 'a' has 10 left, 'b' has 80 left, but the TOTAL has only 30 left
        r2 = run_gate([sell(quantity=1, notional_usd=40.0)], ov, self.lp)
        self.assertEqual(r2["placed"], [])
        self.assertIn("total override cap exceeded", r2["blocked"][0]["reason"])
        r3 = run_gate([sell(quantity=1, notional_usd=30.0)], ov, self.lp)  # exactly the remainder, via 'b'
        self.assertEqual(len(r3["placed"]), 1)
        self.assertEqual(self.spent("b")[1], 30.0)
        r4 = run_gate([sell(quantity=0.001, notional_usd=0.5)], ov, self.lp)
        self.assertEqual(r4["placed"], [])

    def test_default_ceiling_and_policy_cap_bound_a_huge_override(self):
        big = good_override(max_qty=None, max_notional_usd=10_000.0)
        out = run_gate([sell(quantity=1, notional_usd=150.0)], authed(big), self.lp)
        self.assertEqual(out["placed"], [])
        self.assertIn("total override cap exceeded", out["blocked"][0]["reason"])
        out2 = run_gate([sell(quantity=1, notional_usd=30.0)], authed(big), self.lp, policy=pol_cap_live(20.0))
        self.assertEqual(out2["placed"], [])

    def test_total_cap_window_rolls_after_24h(self):
        ov = authed(good_override(max_qty=None, max_notional_usd=500.0, id="a"), total_cap_usd=60.0)
        self.assertEqual(len(run_gate([sell(quantity=1, notional_usd=60.0)], ov, self.lp)["placed"]), 1)
        self.assertEqual(run_gate([sell(quantity=1, notional_usd=1.0)], ov, self.lp)["placed"], [])
        later = NOW + timedelta(hours=25)
        ov2 = authed(
            good_override(max_qty=None, max_notional_usd=500.0, id="c", expires_at=_iso(later + timedelta(hours=2)),
                          approved_at=_iso(later - timedelta(hours=1))),
            total_cap_usd=60.0,
        )
        self.assertEqual(len(run_gate([sell(quantity=1, notional_usd=60.0)], ov2, self.lp, now=later)["placed"]), 1)

    def test_expired_override_is_rejected_and_not_charged(self):
        ov = authed(good_override(expires_at=_iso(NOW - timedelta(seconds=1))))
        out = run_gate([sell()], ov, self.lp)
        self.assertEqual(out["placed"], [])
        self.assertEqual(out["broker_calls"], 0)
        self.assertIn("expired", out["blocked"][0]["reason"])
        self.assertFalse(self.lp.exists() and gate.SpendLedger._load(self.lp)["overrides"])

    def test_sell_without_known_notional_cannot_be_counted_so_is_blocked(self):
        ov = authed(good_override(max_qty=1.0, max_notional_usd=None))
        out = run_gate([{"symbol": "MARA", "side": "sell", "quantity": 0.5, "purpose": "risk_exit"}], ov, self.lp)
        self.assertEqual(out["placed"], [])

    def test_rebalance_sells_stay_blocked_even_with_authenticated_override_and_budget(self):
        out = run_gate([sell(purpose="rebalance")], authed(), self.lp)
        self.assertEqual(out["placed"], [])
        self.assertEqual(out["blocked"][0]["reason_code"], gate.R_REBALANCE_SELL)

    def test_dry_run_reads_but_never_writes_the_ledger(self):
        out = run_gate([sell()], authed(), self.lp, live=False)
        self.assertEqual(len(out["would_place"]), 1)
        self.assertFalse(self.lp.exists())

    def test_corrupt_ledger_fails_closed(self):
        self.lp.write_text("{not json")
        out = run_gate([sell()], authed(), self.lp)
        self.assertEqual(out["placed"], [])
        self.assertEqual(out["broker_calls"], 0)
        self.assertIn("ledger", out["ledger_error"])
        self.lp.write_text(json.dumps({"overrides": {"ovr-1": "oops"}}))
        self.assertEqual(run_gate([sell()], authed(), self.lp)["placed"], [])

    def test_ledger_writes_are_atomic_and_leave_no_temp_files(self):
        run_gate([sell()], authed(), self.lp)
        names = sorted(p.name for p in self.lp.parent.iterdir())
        self.assertEqual(names, ["spend.json", "spend.json.lock"])
        json.loads(self.lp.read_text())

    def test_concurrent_runs_cannot_double_spend(self):
        import threading

        ov = authed()
        results = []

        def one():
            results.append(len(run_gate([sell(quantity=0.6, notional_usd=30.0)], ov, self.lp)["placed"]))

        ts = [threading.Thread(target=one) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(results), 1, results)
        self.assertEqual(self.spent(), (0.6, 30.0))

    def test_batch_charges_each_sell_in_the_same_run(self):
        out = run_gate([sell(quantity=0.6, notional_usd=30.0), sell(quantity=0.6, notional_usd=30.0)], authed(), self.lp)
        self.assertEqual(len(out["placed"]), 1)
        self.assertEqual(len(out["blocked"]), 1)


def pol_cap_live(cap):
    p = pol(live=True)
    p.override_total_cap_usd = cap
    return p


class TestCliOverrideEndToEnd(GitRepoCase):
    """`python -m treasury.fund_manager_order_gate --execute-intents` against a real repo."""

    def _run(self, intents):
        f = Path(self._td.name) / "fund_manager_intents.json"
        f.write_text(json.dumps({"orders": intents}))
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.object(gate, "ROOT", self.repo), mock.patch.object(
            gate, "OVERRIDE_PATH", self.ov
        ), mock.patch.object(gate, "SPEND_LEDGER_PATH", self.ledger), mock.patch.object(
            gate, "_deployable_cash_from_snapshot", return_value=0.18
        ), mock.patch.object(gate, "load_broker_adapter", return_value=None), redirect_stdout(buf):
            rc = gate.main(["--execute-intents", str(f), "--no-record"])
        return rc, json.loads(buf.getvalue())

    def test_committed_override_allows_one_sell_within_cap(self):
        self.write_override()
        self.commit("investment/fund_manager_sell_override.json", author=CHRIS)
        rc, out = self._run([sell(quantity=0.5, notional_usd=10.0)])
        self.assertEqual(rc, 0, out)
        # repo policy is live:false (paused 2026-10-25) => would_place; either way nothing is submitted
        rows = out["would_place"] + out["placed"]
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0]["status"], ("would_place", "not_submitted_no_broker_adapter"))
        self.assertEqual(out["blocked"], [])
        self.assertEqual(out["broker_calls"], 0)

    def test_uncommitted_override_is_rejected(self):
        self.write_override()
        rc, out = self._run([sell(quantity=0.5, notional_usd=10.0)])
        self.assertEqual(rc, 3)
        self.assertEqual(out["would_place"], [])
        self.assertIn("not committed", out["override_error"])
        self.assertEqual(out["blocked"][0]["reason_code"], gate.R_SELL_NO_OVERRIDE)


class TestUnclassifiableFailsClosed(unittest.TestCase):
    def test_unclassifiable_side(self):
        cases = [
            {"symbol": "TSLA", "notional_usd": 5},  # no side
            {"symbol": "TSLA", "side": "", "notional_usd": 5},
            {"symbol": "TSLA", "side": "hold", "notional_usd": 5},
            {"symbol": "TSLA", "side": "flip", "notional_usd": 5},
            {"symbol": "TSLA", "side": "buy", "action": "sell", "notional_usd": 5},  # conflicting
            {"symbol": "TSLA", "side": 7, "notional_usd": 5},
            "SELL TSLA $3",
            None,
            [],
        ]
        for c in cases:
            v = gate.evaluate_intent(c, policy=pol(), override=good_override(symbols=["TSLA"]), deployable_cash=100, now=NOW)
            self.assertFalse(v.allowed, c)
            self.assertIn(v.code, (gate.R_UNCLASSIFIABLE, gate.R_BAD_INTENT), c)

    def test_sell_with_unknown_or_missing_purpose_fails_closed(self):
        for p in (None, "", "misc", "because"):
            it = sell()
            if p is None:
                it.pop("purpose")
            else:
                it["purpose"] = p
            v = gate.evaluate_intent(it, policy=pol(), override=good_override(), now=NOW)
            self.assertFalse(v.allowed, p)
            self.assertEqual(v.code, gate.R_UNCLASSIFIED_SELL)

    def test_batch_payload_not_a_list(self):
        res = gate.gate_orders({"orders": "x"}, deployable_cash=10, policy=pol(), now=NOW)
        self.assertEqual(res.allowed, [])
        self.assertEqual(len(res.blocked), 1)

    def test_gate_never_raises(self):
        class Boom(dict):
            def get(self, *a, **k):
                raise RuntimeError("boom")

        v = gate.evaluate_intent(Boom(side="buy"), policy=pol(), deployable_cash=10, now=NOW)
        self.assertFalse(v.allowed)


class TestBuysUnaffected(unittest.TestCase):
    def test_buy_within_cash_allowed(self):
        v = gate.evaluate_intent(buy("STRC", 5.0), policy=pol(), deployable_cash=10.0, now=NOW)
        self.assertTrue(v.allowed, v)
        for s in ("buy", "BUY", " Buy "):
            self.assertTrue(gate.evaluate_intent(buy(side=s), policy=pol(), deployable_cash=10.0, now=NOW).allowed)

    def test_buy_batch_sum_capped_at_deployable_cash(self):
        res = gate.gate_orders([buy("STRC", 6), buy("SATA", 6)], deployable_cash=10.0, policy=pol(), now=NOW)
        self.assertEqual([g.intent["symbol"] for g in res.allowed], ["STRC"])
        self.assertEqual(res.blocked[0][1].code, gate.R_OVER_CASH)

    def test_buy_blocked_on_unknown_cash_zero_cash_bad_amount_or_blocked_symbol(self):
        self.assertEqual(gate.evaluate_intent(buy(), policy=pol(), deployable_cash=None, now=NOW).code, gate.R_CASH_UNKNOWN)
        self.assertEqual(gate.evaluate_intent(buy(), policy=pol(), deployable_cash=0.18, now=NOW).code, gate.R_OVER_CASH)
        self.assertEqual(gate.evaluate_intent(buy(usd=-1), policy=pol(), deployable_cash=9, now=NOW).code, gate.R_BAD_AMOUNT)
        self.assertEqual(gate.evaluate_intent(buy(sym="BE"), policy=pol(), deployable_cash=9, now=NOW).code, gate.R_BLOCKED_SYMBOL)

    def test_allocate_new_cash_is_buy_only_and_raises_not_asserts(self):
        sleeve = json.loads(POLICY_FILE.read_text(encoding="utf-8"))["sleeves"]["btc_digital_credit"]
        intents = fm.allocate_new_cash(sleeve, 100.0)
        self.assertTrue(intents and all(i["side"] == "buy" for i in intents))
        res = gate.gate_orders(
            [dict(i, symbol=("MSTR" if i["symbol"] == "floating" else i["symbol"])) for i in intents],
            deployable_cash=100.0, policy=pol(), now=NOW,
        )
        self.assertEqual(len(res.allowed), len(intents))
        src = (ROOT / "treasury" / "fund_manager.py").read_text(encoding="utf-8")
        self.assertNotIn("assert all(i.get(\"side\")", src)


class TestExecutor(unittest.TestCase):
    def test_sell_intents_never_reach_broker_and_are_recorded(self):
        submit = mock.Mock()
        recorded = []
        out = gate.execute_intents(
            [
                {"symbol": "TSLA", "side": "sell", "notional_usd": 3.0, "purpose": "rebalance"},
                {"symbol": "MARA", "side": "sell", "quantity": 0.5},
                {"symbol": "RIOT", "side": "sell_short", "quantity": 1},
            ],
            deployable_cash=0.18, submit=submit, policy=pol(live=True), override=None, now=NOW,
            record_fn=recorded.append,
        )
        submit.assert_not_called()
        self.assertEqual(out["broker_calls"], 0)
        self.assertEqual(len(out["blocked"]), 3)
        self.assertTrue(all(b["status"] == "blocked_no_sell_guard" for b in out["blocked"]))
        self.assertEqual(len(recorded), 1)
        self.assertTrue(recorded[0]["blocked_sell"])
        self.assertEqual(recorded[0]["kind"], "blocked_sell")

    def test_live_buy_submits_gated_order_only(self):
        seen = []

        def submit(order):
            seen.append(order)
            return {"status": "submitted"}

        out = gate.execute_intents(
            [buy("STRC", 5), sell("TSLA", purpose="rebalance")],
            deployable_cash=10.0, submit=submit, policy=pol(live=True), now=NOW, record_fn=lambda e: None,
        )
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], gate.GatedOrder)
        self.assertEqual(seen[0].as_dict()["symbol"], "STRC")
        self.assertEqual(out["broker_calls"], 1)
        self.assertEqual(len(out["blocked"]), 1)

    def test_live_false_and_missing_adapter_submit_nothing(self):
        submit = mock.Mock()
        out = gate.execute_intents([buy("STRC", 5)], deployable_cash=10.0, submit=submit, policy=pol(live=False), now=NOW, record=False)
        submit.assert_not_called()
        self.assertEqual(len(out["would_place"]), 1)
        out2 = gate.execute_intents([buy("STRC", 5)], deployable_cash=10.0, submit=None, policy=pol(live=True), now=NOW, record=False)
        self.assertEqual(out2["placed"][0]["status"], "not_submitted_no_broker_adapter")
        self.assertEqual(out2["broker_calls"], 0)

    def test_override_sell_is_the_only_sell_submitted(self):
        submit = mock.Mock(return_value={"status": "submitted"})
        gate.execute_intents(
            [sell("MARA"), sell("RIOT")], deployable_cash=0.0, submit=submit,
            policy=pol(live=True), override=authed(), now=NOW, record_fn=lambda e: None,
            ledger_path=Path(tempfile.mkdtemp()) / "spend.json",
        )
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(submit.call_args[0][0].as_dict()["symbol"], "MARA")

    def test_gatedorder_cannot_be_forged(self):
        with self.assertRaises(TypeError):
            gate.GatedOrder({"symbol": "TSLA", "side": "sell"}, gate.Verdict(True, "ok", "x"))

    def test_policy_unreadable_places_nothing(self):
        submit = mock.Mock()
        bad = gate.GatePolicy(False, "cannot load policy")
        out = gate.execute_intents([buy()], deployable_cash=100, submit=submit, policy=bad, now=NOW, record_fn=lambda e: None)
        submit.assert_not_called()
        self.assertEqual(len(out["blocked"]), 1)

    def test_cli_execute_intents_blocks_sell_and_consumes_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "fund_manager_intents.json"
            f.write_text(json.dumps({"orders": [{"symbol": "TSLA", "side": "sell", "notional_usd": 3.0, "purpose": "rebalance"}]}))
            with mock.patch.object(gate, "_deployable_cash_from_snapshot", return_value=0.18), mock.patch.object(
                gate, "load_override", return_value=(None, "")
            ), mock.patch.object(gate, "load_broker_adapter", return_value=None), mock.patch(
                "sys.stdout"
            ):
                rc = gate.main(["--execute-intents", str(f), "--no-record"])
            self.assertEqual(rc, 3)
            self.assertFalse(f.exists(), "intents file is consumed, never replayed")

    def test_cli_missing_intents_is_a_noop(self):
        with mock.patch("sys.stdout"):
            self.assertEqual(gate.main(["--execute-intents", "/nonexistent/intents.json"]), 0)


class TestDecisionLevelGuard(unittest.TestCase):
    def _append(self, decision):
        posts = []

        def fake_post(title, text, *, dry_run=False, as_markdown=False):
            posts.append({"title": title, "text": text})
            return {"ok": True, "posted": True, "issue": "701", "status": 201}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "dec.jsonl"
            with mock.patch("treasury.fund_manager.post_ops_github", side_effect=fake_post), mock.patch.dict(
                os.environ, {"FM_DECISION_GITHUB_IN_TEST": "1"}, clear=False
            ), mock.patch.object(fm, "_load_sell_override", return_value=(None, "")):
                entry = fm.append_decision(decision, path=p, also_journal=False)
            rows = fm.load_decision_log(path=p, limit=5)
        return entry, rows, posts

    def test_llm_sell_proposal_is_blocked_marked_and_alerted(self):
        entry, rows, posts = self._append(
            {
                "kind": "rebalance",
                "summary": "sell TSLA to fund BTC complex",
                "actions": [
                    {"symbol": "TSLA", "side": "sell", "notional_usd": 3.0, "status": "proposed"},
                    {"symbol": "MARA", "side": "sell", "quantity": 0.5, "status": "not_sent"},
                    {"symbol": "STRC", "side": "buy", "notional_usd": 1.0, "status": "proposed"},
                ],
            }
        )
        self.assertTrue(entry["blocked_sell"])
        sells = [a for a in rows[0]["actions"] if a["side"] == "sell"]
        self.assertTrue(sells and all(a["status"] == "blocked_no_sell_guard" for a in sells))
        self.assertEqual([a["status"] for a in rows[0]["actions"] if a["side"] == "buy"], ["proposed"])
        self.assertEqual(rows[0]["guard"]["status"], "blocked_no_sell_guard")
        # decision record AND separate ops alert both carry the marker
        self.assertEqual(len(posts), 2, posts)
        for p in posts:
            self.assertIn(fm.BLOCKED_SELL_MARKER, p["text"])
        self.assertIn("blocked_sell", posts[1]["title"])

    def test_executed_sell_is_flagged_loudly(self):
        entry, rows, posts = self._append(
            {"kind": "rotate", "summary": "x", "actions": [{"symbol": "MSTR", "side": "sell", "notional_usd": 2, "status": "filled", "order_id": "abc"}]}
        )
        a = rows[0]["actions"][0]
        self.assertTrue(a.get("guard_violation"))
        self.assertIn("SELL EXECUTED PAST GUARD", rows[0]["summary"])
        self.assertIn("SELL EXECUTED PAST GUARD", posts[1]["title"])

    def test_unclassifiable_action_row_is_blocked_but_cancel_and_hold_are_not(self):
        entry, rows, posts = self._append(
            {
                "kind": "deploy",
                "summary": "x",
                "actions": [
                    {"symbol": "TSLA", "status": "filled", "order_id": "z"},  # no side but executed
                    {"symbol": "STRC", "action": "cancel", "order_id": "q"},
                    {"status": "none"},
                ],
            }
        )
        self.assertTrue(entry["blocked_sell"])
        self.assertEqual(len(entry["guard"]["violations"]), 1)

    def test_buys_and_holds_are_untouched_and_no_alert(self):
        entry, rows, posts = self._append(
            {"kind": "deploy", "summary": "buy", "actions": [{"symbol": "STRC", "side": "buy", "notional_usd": 5, "status": "filled"}]}
        )
        self.assertFalse(entry.get("blocked_sell"))
        self.assertEqual(len(posts), 1)
        self.assertNotIn(fm.BLOCKED_SELL_MARKER, posts[0]["text"])

    def test_override_approved_sell_in_log_is_not_flagged(self):
        d = {"kind": "deploy", "summary": "x", "actions": [dict(sell("MARA"), status="filled")]}
        out = gate.guard_decision_entry(d, override=authed(), now=NOW)
        self.assertFalse(out.get("blocked_sell"))
        # a raw (unauthenticated) override dict does not launder an executed sell
        raw = gate.guard_decision_entry(d, override=good_override(), now=NOW)
        self.assertTrue(raw.get("blocked_sell"))

    def test_guard_never_raises_and_keeps_record(self):
        out = gate.guard_decision_entry({"summary": "keep me", "actions": 5})
        self.assertEqual(out["summary"], "keep me")
        self.assertIn("guard_error", out)

    def test_nothing_posted_under_pytest_by_default(self):
        with tempfile.TemporaryDirectory() as td, mock.patch("treasury.fund_manager.post_ops_github") as post:
            fm.append_decision(
                {"kind": "x", "actions": [{"symbol": "TSLA", "side": "sell", "status": "proposed"}]},
                path=Path(td) / "d.jsonl", also_journal=False,
            )
        post.assert_not_called()


class TestRulesEngineNoSellToRebalance(unittest.TestCase):
    """Issue AC3/AC6: 19.52 / 80.48 / $0.18 => zero orders, no LLM, exit 0."""

    def setUp(self):
        # never touch the tracked snapshot files
        p = mock.patch.object(fm, "write_fund_manager_snapshot")
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def snapshot(cash=0.18, bp=0.18):
        # ~NAV $424: BTC complex 19.52% / stocks 80.48% of deployed. No account ids.
        return {
            "agentic": {
                "account_number_last4": "XXXX",
                "agentic_allowed": True,
                "cash": cash,
                "buying_power": bp,
                "total_value": 424.0 + cash,
                "positions": [
                    {"symbol": "MSTR", "quantity": 1, "average_buy_price": 82.76},
                    {"symbol": "TSLA", "quantity": 1, "average_buy_price": 170.62},
                    {"symbol": "SPCX", "quantity": 1, "average_buy_price": 170.62},
                ],
            }
        }

    @staticmethod
    def live_policy():
        p = fm.load_fund_policy()
        p = json.loads(json.dumps(p))
        p["live"] = True  # in-memory only; the committed flag stays false
        return p

    def test_todays_snapshot_is_hold_drift_no_cash_zero_orders(self):
        out = fm.rules_based_review(rh_snapshot=self.snapshot(), policy=self.live_policy(), log=False)
        rr = out["rules_review"]
        self.assertFalse(rr["need_llm"])
        self.assertEqual(rr["outcome"], "hold")
        self.assertEqual(rr["kind"], "hold")
        self.assertEqual(rr["reason"], "drift_no_cash")
        self.assertFalse(rr["in_band"])
        self.assertAlmostEqual(out["analysis"]["weights_of_deployed"]["btc_digital_credit"], 0.1952, places=3)

    def test_main_exits_zero_and_logs_hold_once_a_day(self):
        snap, polcy = self.snapshot(), self.live_policy()
        real = fm.rules_based_review
        with tempfile.TemporaryDirectory() as td:
            dec = Path(td) / "dec.jsonl"
            logged = []
            with mock.patch.object(fm, "DECISIONS_PATH", dec), mock.patch.object(
                fm, "write_fund_manager_snapshot"
            ), mock.patch.object(fm, "publish_decision_record", return_value={"ok": True, "posted": True}), mock.patch.object(
                fm, "_append_journal_markdown", side_effect=lambda e: logged.append(e)
            ), mock.patch.object(
                fm, "rules_based_review", side_effect=lambda log=True: real(rh_snapshot=snap, policy=polcy, log=log)
            ), mock.patch("sys.stdout"):
                rc1 = fm.main(["--rules-review"])
                rc2 = fm.main(["--rules-review"])
            rows = fm.load_decision_log(path=dec, limit=10)
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertEqual(len(rows), 1, "second run the same day must not log/post again")
        self.assertEqual(rows[0]["kind"], "hold")
        self.assertEqual(rows[0]["actions"], [])
        self.assertEqual(rows[0]["reason"], "drift_no_cash")

    def test_dust_and_zero_bp_drift_never_need_llm(self):
        for cash, bp in ((0.0, 0.0), (0.18, 0.18), (50.0, 0.0), (0.9, 5.0)):
            out = fm.rules_based_review(rh_snapshot=self.snapshot(cash, bp), policy=self.live_policy(), log=False)
            self.assertFalse(out["rules_review"]["need_llm"], (cash, bp))

    def test_cash_deploys_buys_only_and_expected_weights_reflect_cash(self):
        out = fm.rules_based_review(rh_snapshot=self.snapshot(20.0, 20.0), policy=self.live_policy(), log=False)
        rr = out["rules_review"]
        self.assertTrue(rr["need_llm"])
        self.assertEqual(rr["kind"], "deploy")
        w = fm.expected_weights_buy_only(0.1952, 0.8048, deployed_usd=424.0, cash_usd=20.0, btc_target=0.4)
        self.assertLess(w["btc_digital_credit"], 0.40, "$20 cannot reach 40/60 without selling")
        self.assertGreater(w["btc_digital_credit"], 0.1952)
        self.assertAlmostEqual(w["btc_digital_credit"] + w["stocks_growth"], 1.0, places=3)
        w0 = fm.expected_weights_buy_only(0.1952, 0.8048, deployed_usd=424.0, cash_usd=0.0, btc_target=0.4)
        self.assertEqual(w0, {"btc_digital_credit": 0.1952, "stocks_growth": 0.8048})
        # a huge deposit stops at the target (rest goes to stocks)
        w1 = fm.expected_weights_buy_only(0.1952, 0.8048, deployed_usd=424.0, cash_usd=10_000.0, btc_target=0.4)
        self.assertAlmostEqual(w1["btc_digital_credit"], 0.4, places=2)

    def test_rules_source_never_emits_rebalance_kind(self):
        src = (ROOT / "treasury" / "fund_manager.py").read_text(encoding="utf-8")
        self.assertNotIn('kind = "rebalance"', src)


class TestConfigAndPrompt(unittest.TestCase):
    def test_prompt_has_top_level_no_sell_rule_and_no_rotate_wording(self):
        text = (ROOT / "treasury" / "fund_manager_daily_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("NO SELLS. No shorts.", text)
        self.assertIn("Rebalance with new cash only", text)
        self.assertLess(text.index("NO SELLS"), text.index("## Policy"))
        self.assertNotIn("rotates toward 40/60", text)
        self.assertNotIn("you may place orders without asking", text)

    def test_config_no_trims_and_consider_share_not_forced_rebalance(self):
        cfg = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        self.assertNotIn("trims", cfg["team"]["roles"]["thesis"]["job"].replace("NO sells, trims", ""))
        cs = json.loads((ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8"))
        self.assertIs(cs["not_a_forced_rebalance"], True)
        self.assertIn("HISTORICAL", cs["reallocate_status"])


class TestRunnersAndLint(unittest.TestCase):
    """CI check: every path that can turn analysis into an order goes through the gate."""

    RUNNERS = ("fund_manager_daily.sh", "fund_manager_bp_poll.sh")
    # No exceptions: rh_snapshot_sync.py now passes the deny list too (#1196 review).
    # Adding an entry here requires eng-gate sign-off.
    YOLO_ALLOWLIST: set = set()
    ORDER_MARKERS = re.compile(
        r"place_[a-z_]*order|submit_order|create_order|order_build|\"side\"\s*:\s*\"sell\"|side\s*=\s*[\"']sell[\"']",
        re.IGNORECASE,
    )
    ORDER_MARKER_ALLOW = {
        "treasury/fund_manager_order_gate.py",  # the gate itself (deny globs)
        # the buy-only broker adapter: the sanctioned submit target of the gate (FM_BROKER_ADAPTER)
        "treasury/fund_manager_broker.py",
        "treasury/tests/test_fund_manager_broker.py",
        "treasury/tests/test_fund_manager_broker_grok_client.py",  # asserts the place tool is never reached
        "treasury/tests/test_fund_manager_order_gate.py",
    }

    def _tracked(self, patterns):
        base = ROOT / "treasury"
        for pat in patterns:
            for p in sorted(base.rglob(pat)):
                if "snapshots" in p.parts or "__pycache__" in p.parts:
                    continue
                yield p, p.relative_to(ROOT).as_posix()

    def test_runners_only_run_grok_with_deny_list_and_then_the_gate(self):
        for name in self.RUNNERS:
            text = (ROOT / "treasury" / name).read_text(encoding="utf-8")
            self.assertIn("--print-grok-deny-args", text, name)
            self.assertIn("fund_manager_order_gate --execute-intents", text, name)
            yolo_lines = re.findall(r"^\s+--yolo \\$", text, re.MULTILINE)
            deny_lines = re.findall(r'^\s+"\$\{GROK_DENY\[@\]\}" \\$', text, re.MULTILINE)
            self.assertEqual(len(yolo_lines), 1, name)
            self.assertEqual(len(deny_lines), 1, name)
            self.assertLess(text.index("--yolo"), text.index('"${GROK_DENY[@]}"'), name)
            self.assertIn("fail closed", text, name)
            self.assertNotIn("--dangerously", text)

    @staticmethod
    def yolo_calls_without_deny(source: str, filename: str = "<src>"):
        """Line numbers of argv lists containing "--yolo" that lack ``*grok_deny_args()``.

        Python files are checked structurally (a comment or an unused import of
        grok_deny_args does not count). Shell files must pass ``"${GROK_DENY[@]}"``.
        """
        import ast

        if filename.endswith(".sh"):
            return [] if ("--yolo" not in source or '"${GROK_DENY[@]}"' in source) else [0]
        tree = ast.parse(source)

        def calls_helper(n) -> bool:
            return any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "grok_deny_args"
                for c in ast.walk(n)
            )

        # names bound from the helper, e.g. ``deny_args = list(grok_deny_args())``
        helper_names = {
            t.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and calls_helper(n.value)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)) and any(
                isinstance(e, ast.Constant) and e.value == "--yolo" for e in node.elts
            ):
                ok = any(
                    isinstance(e, ast.Starred)
                    and (calls_helper(e.value) or (isinstance(e.value, ast.Name) and e.value.id in helper_names))
                    for e in node.elts
                )
                if not ok:
                    bad.append(node.lineno)
        return bad

    def test_every_yolo_grok_call_in_treasury_is_gated_or_allowlisted(self):
        offenders = []
        for p, rel in self._tracked(["*.sh", "*.py"]):
            if rel in self.YOLO_ALLOWLIST or rel.startswith("treasury/tests/"):
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if "--yolo" in text and self.yolo_calls_without_deny(text, rel):
                offenders.append(rel)
        self.assertEqual(offenders, [], "grok --yolo without the order-gate deny list")

    def test_yolo_lint_catches_a_missing_or_decorative_deny_list(self):
        lint = self.yolo_calls_without_deny
        bare = 'import subprocess\nsubprocess.run(["grok", "-p", p, "--yolo", "--output-format", "plain"])\n'
        self.assertTrue(lint(bare))
        decorative = "# grok_deny_args()\nfrom x import grok_deny_args\n" + bare
        self.assertTrue(lint(decorative), "a comment/import is not a deny list")
        unused = 'from x import grok_deny_args\nd = []\nsubprocess.run(["grok", "--yolo", *d])\n'
        self.assertTrue(lint(unused))
        direct = 'subprocess.run(["grok", "--yolo", *grok_deny_args()])\n'
        self.assertEqual(lint(direct), [])
        via_name = 'd = list(grok_deny_args())\nsubprocess.run(["grok", "--yolo", *d])\n'
        self.assertEqual(lint(via_name), [])
        self.assertTrue(lint("grok --yolo\n", "x.sh"))
        self.assertEqual(lint('grok --yolo "${GROK_DENY[@]}"\n', "x.sh"), [])

    def test_rh_snapshot_sync_is_linted_with_no_allowlist(self):
        self.assertEqual(self.YOLO_ALLOWLIST, set())
        rel = "treasury/rh_snapshot_sync.py"
        text = (ROOT / rel).read_text(encoding="utf-8")
        self.assertIn("--yolo", text)
        self.assertEqual(self.yolo_calls_without_deny(text, rel), [])

    def test_snapshot_prompt_read_tools_stay_allowed(self):
        """rh_refresh_prompt.txt only needs read tools; the deny globs must not catch them."""
        prompt = (ROOT / "treasury" / "rh_refresh_prompt.txt").read_text(encoding="utf-8")
        used = set(re.findall(r"\b(get_[a-z_]+)\b", prompt))
        self.assertTrue(used, "prompt names no RH read tools?")
        self.assertLessEqual(used, set(gate.RH_READ_TOOLS))
        for tool in gate.RH_READ_TOOLS:
            self.assertFalse(gate.is_tool_denied(tool), tool)
        self.assertNotRegex(prompt, r"place_|replace_|modify_|submit_|exercise_")

    def test_no_order_building_code_outside_the_gate(self):
        offenders = []
        for p, rel in self._tracked(["*.py", "*.sh"]):
            if rel in self.ORDER_MARKER_ALLOW:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if self.ORDER_MARKERS.search(line) and not line.lstrip().startswith(("#", "//")):
                    offenders.append(f"{rel}:{i}: {line.strip()[:100]}")
        self.assertEqual(offenders, [], "order-building outside treasury.fund_manager_order_gate")

    def test_deny_args_cover_order_tools_but_not_cancel_or_reads(self):
        args = gate.grok_deny_args()
        self.assertTrue(args[0::2] == ["--deny"] * (len(args) // 2))
        rules = args[1::2]
        joined = " ".join(rules)
        for must in ("place_*", "replace_*", "modify_*", "*sell*", "*short*", "exercise_option"):
            self.assertIn(f"robinhood-trading__{must}", joined)
        allowed = (
            "cancel_equity_order", "cancel_option_order", "cancel_crypto_order",
            "cancel_option_exercise", *gate.RH_READ_TOOLS,
        )
        for tool in allowed:
            self.assertFalse(gate.is_tool_denied(tool), f"{tool} must stay allowed")
        for tool in ("place_equity_order", "place_option_order", "place_crypto_order", "sell_equity", "exercise_option"):
            self.assertTrue(gate.is_tool_denied(tool), tool)

    def test_deny_args_carve_out_only_place_equity_order(self):
        rules = gate.grok_deny_args(allow_tools=("place_equity_order", "place_option_order", "sell_equity"))[1::2]
        self.assertNotIn("MCPTool(robinhood-trading__place_*)", rules)
        self.assertNotIn("MCPTool(robinhood-trading__place_equity_order)", rules)
        for must in ("place_option_order", "place_crypto_order", "*sell*", "*short*", "replace_*", "modify_*", "exercise_option"):
            self.assertIn(f"MCPTool(robinhood-trading__{must})", rules)
        self.assertEqual(gate.grok_deny_args(allow_tools=("sell_equity",)), gate.grok_deny_args())

    def test_cli_prints_deny_args(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(gate.main(["--print-grok-deny-args"]), 0)
        self.assertEqual(buf.getvalue().strip().splitlines(), gate.grok_deny_args())


if __name__ == "__main__":
    unittest.main()
