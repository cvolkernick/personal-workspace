"""Solana whitelist adapter — parse, offline fallback (JR-strcUSX retired)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury import solana_sync as ss  # noqa: E402
from treasury.policy import evaluate_treasury  # noqa: E402
from treasury.solana_sync import (  # noqa: E402
    USDC_MINT,
    WSOL_MINT,
    fetch_solana,
    normalize_solana_book,
    overlay_solana_snapshot,
    parse_token_accounts,
    write_solana_snapshot,
)

# Retired mint — still appears on-chain as dust; must be ignored when not whitelisted.
_RETIRED_STRCUSX_MINT = "BQ6LPc68knpko292UsMLbQYfaHhWD7S84sA98632hrzX"


def _rpc_accounts(*rows):
    value = []
    for mint, ui, dec in rows:
        value.append(
            {
                "pubkey": "ata-" + mint[:6],
                "account": {
                    "data": {
                        "parsed": {
                            "info": {
                                "mint": mint,
                                "tokenAmount": {
                                    "uiAmount": ui,
                                    "decimals": dec,
                                },
                            }
                        }
                    }
                },
            }
        )
    return {"result": {"context": {"slot": 1}, "value": value}}


class TestParseAndNormalize(unittest.TestCase):
    def test_whitelist_only_and_usd(self):
        rows = parse_token_accounts(
            _rpc_accounts(
                (USDC_MINT, 12.5, 6),
                (_RETIRED_STRCUSX_MINT, 3.317128, 6),
                ("FLJYGHpCCcfYUdzhcfHSeSd2peb5SMajNWaCsRnhpump", 69.0, 6),
            )
        )
        self.assertEqual(len(rows), 3)
        book = normalize_solana_book(
            wallet="CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm",
            sol_lamports=58_890_792,
            token_rows=rows,
            prices={
                WSOL_MINT: 76.0,
                USDC_MINT: 1.0,
                _RETIRED_STRCUSX_MINT: 1.0255,
            },
            whitelist=[
                {"symbol": "SOL", "mint": WSOL_MINT, "role": "gas"},
                {"symbol": "USDC", "mint": USDC_MINT, "role": "onchain_stable"},
            ],
        )
        self.assertAlmostEqual(book["sol"], 0.058890792)
        self.assertAlmostEqual(book["usdc"], 12.5)
        self.assertNotIn("jr_strcusx", book)
        self.assertFalse(book["counts_toward_hy"])
        self.assertFalse(book["counts_toward_working_usdc"])
        self.assertEqual(book["ignored_token_accounts"], 2)
        symbols = {h["symbol"] for h in book["holdings"]}
        self.assertEqual(symbols, {"SOL", "USDC"})
        self.assertNotIn("JR-strcUSX", symbols)


class TestFetchFallback(unittest.TestCase):
    def test_offline_reads_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {
                    "source": "live",
                    "sol": 0.05,
                    "usdc": 3.3,
                    "book_usd": 7.2,
                    "counts_toward_hy": True,  # poisoned — fetch must force false
                },
                path=p,
            )
            r = fetch_solana(prefer_live=False, snapshot_path=p)
            self.assertAlmostEqual(r["usdc"], 3.3)
            self.assertFalse(r["counts_toward_hy"])
            self.assertFalse(r["counts_toward_ltv_defense"])

    def test_live_error_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {"source": "live", "usdc": 1.0, "book_usd": 1.0, "sol": 0},
                path=p,
            )
            with mock.patch(
                "treasury.solana_sync.fetch_solana_live",
                return_value=(None, "rpc down"),
            ):
                r = fetch_solana(prefer_live=True, snapshot_path=p)
            self.assertEqual(r["live_error"], "rpc down")
            self.assertAlmostEqual(r["usdc"], 1.0)
            self.assertFalse(r["counts_toward_hy"])

    def test_overlay_fills_empty_block_does_not_clobber(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {
                    "source": "live",
                    "as_of": "2026-08-29T18:12:13+00:00",
                    "book_usd": 9.58,
                    "usdc": 3.317128,
                },
                path=p,
            )
            empty = {"snapshot": {"solana": {}, "coinbase": {"as_of": "cb"}}}
            overlay_solana_snapshot(empty, snapshot_path=p)
            self.assertEqual(
                empty["snapshot"]["solana"]["as_of"], "2026-08-29T18:12:13+00:00"
            )
            self.assertTrue(empty["snapshot"]["meta"]["solana_sidecar"])
            keep = {
                "snapshot": {
                    "solana": {"as_of": "keep-me", "book_usd": 1.0},
                }
            }
            overlay_solana_snapshot(keep, snapshot_path=p)
            self.assertEqual(keep["snapshot"]["solana"]["as_of"], "keep-me")


class TestPolicyIsolation(unittest.TestCase):
    def test_solana_does_not_fill_hy_or_working_usdc(self):
        snap = {
            "coinbase": {"liquid_usdc": 10.0, "source": "live"},
            "coinbase_manual": {"vault_usdc": 50.0, "ltv": 0.2},
            "robinhood": {"buying_power": 100, "cash": 0, "equity_value": 100, "total_value": 100},
            "one_card": {"source": "ynab", "card_balance": 0, "balance_owed": 0},
            "solana": {
                "source": "live",
                "wallet": "CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm",
                "sol": 0.05,
                "usdc": 12.0,
                "book_usd": 118.0,
                "counts_toward_hy": False,
            },
        }
        ev = evaluate_treasury(snap)
        self.assertAlmostEqual(ev["inputs"]["working_usdc"], 60.0)
        self.assertAlmostEqual(ev["inputs"]["liquid_usdc"], 10.0)
        self.assertFalse(ev["inputs"]["solana_counts_toward_hy"])
        sleeve = ev["sleeves"]["solana"]
        self.assertFalse(sleeve["counts_toward_hy"])
        self.assertFalse(sleeve["counts_toward_ltv_defense"])
        self.assertAlmostEqual(sleeve["usdc"], 12.0)
        self.assertIn("NOT HY", ev["agent_brief"])



class TestSolanaSleeveZeroWeight(unittest.TestCase):
    """Renamed solana sleeve (formerly strc_jr): observational only, 0% pull."""

    def _snap(self):
        return {
            "coinbase": {"liquid_usdc": 10.0, "source": "live"},
            "coinbase_manual": {"vault_usdc": 50.0, "ltv": 0.2},
            "robinhood": {
                "buying_power": 100,
                "cash": 0,
                "equity_value": 100,
                "total_value": 100,
            },
            "one_card": {"source": "ynab", "card_balance": 0, "balance_owed": 0},
            "solana": {
                "source": "live",
                "wallet": "CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm",
                "sol": 0.05,
                "usdc": 12.0,
                "book_usd": 118.0,
                "counts_toward_hy": False,
            },
        }

    def test_sleeve_key_is_solana_not_strc_jr(self):
        ev = evaluate_treasury(self._snap())
        self.assertIn("solana", ev["sleeves"])
        self.assertNotIn("strc_jr", ev["sleeves"])

    def test_zero_target_and_new_money_weight(self):
        """No allocation pull: target/new-money weights are 0 or absent."""
        ev = evaluate_treasury(self._snap())
        sleeve = ev["sleeves"]["solana"]
        for key in (
            "target_weight",
            "target_pct",
            "new_money_weight",
            "new_money_pct",
            "allocation_weight",
            "weight",
        ):
            val = sleeve.get(key)
            if val is not None:
                self.assertAlmostEqual(float(val), 0.0, msg=f"{key}={val}")
        # Observational flags — cannot pull into HY / LTV / working USDC
        self.assertFalse(sleeve["counts_toward_hy"])
        self.assertFalse(sleeve["counts_toward_ltv_defense"])
        self.assertFalse(sleeve.get("counts_toward_working_usdc", False))
        # Cashflow ladder never allocates *to* the solana sleeve
        steps = (ev.get("cashflow_allocation") or {}).get("steps") or []
        for step in steps:
            blob = json.dumps(step).lower()
            self.assertNotIn("solana", blob)
            self.assertNotIn("strc_jr", blob)

    def test_config_and_consider_share_have_no_strc_jr_weight(self):
        import json as _json
        from treasury.solana_sync import solana_config

        cfg = solana_config()
        self.assertFalse(cfg["counts_toward_hy"])
        self.assertFalse(cfg["counts_toward_ltv_defense"])
        self.assertFalse(cfg["counts_toward_working_usdc"])

        pins_path = ROOT / "investment" / "consider_share.json"
        pins = _json.loads(pins_path.read_text(encoding="utf-8"))
        pin_keys = {k.lower() for k in (pins.get("pins") or {})}
        self.assertNotIn("strc_jr", pin_keys)
        self.assertNotIn("solana", pin_keys)
        self.assertNotIn("jr_strcusx", pin_keys)

        fm_path = ROOT / "investment" / "fund_manager.json"
        fm = _json.loads(fm_path.read_text(encoding="utf-8"))
        self.assertNotIn("strc_jr", fm.get("sleeves") or {})
        self.assertNotIn("solana", fm.get("sleeves") or {})
        # bias staged queue empty / no strc_jr
        bias_path = ROOT / "investment" / "bias_weight_staged.json"
        if bias_path.exists():
            bias = _json.loads(bias_path.read_text(encoding="utf-8"))
            pending = bias.get("pending") or []
            blob = _json.dumps(pending).lower()
            self.assertNotIn("strc_jr", blob)
            self.assertNotIn("jr_strcusx", blob)


class TestWhitelistUsdcAndSol(unittest.TestCase):
    """Only JR mint retired; USDC (EPjF…Dt1v) and native SOL remain."""

    def test_default_whitelist_is_sol_and_usdc_only(self):
        from treasury.solana_sync import DEFAULT_WHITELIST

        mints = {e["mint"] for e in DEFAULT_WHITELIST}
        symbols = {e["symbol"] for e in DEFAULT_WHITELIST}
        self.assertEqual(symbols, {"SOL", "USDC"})
        self.assertEqual(mints, {WSOL_MINT, USDC_MINT})
        self.assertEqual(
            USDC_MINT, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        )
        self.assertNotIn(_RETIRED_STRCUSX_MINT, mints)

    def test_config_whitelist_matches_default(self):
        from treasury.solana_sync import solana_config

        cfg = solana_config()
        mints = {e["mint"] for e in cfg["whitelist"]}
        self.assertEqual(mints, {WSOL_MINT, USDC_MINT})
        self.assertNotIn(_RETIRED_STRCUSX_MINT, mints)

    def test_fixture_snapshot_reader_picks_up_usdc_and_sol(self):
        """Solana panel / snapshot reader surfaces USDC + SOL from a fixture."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sol.json"
            write_solana_snapshot(
                {
                    "source": "live",
                    "as_of": "2026-10-10T12:00:00+00:00",
                    "wallet": "fixture-wallet",
                    "sol": 1.25,
                    "usdc": 42.0,
                    "sol_usd": 250.0,
                    "book_usd": 292.0,
                    "holdings": [
                        {"symbol": "SOL", "mint": WSOL_MINT, "amount": 1.25},
                        {"symbol": "USDC", "mint": USDC_MINT, "amount": 42.0},
                    ],
                    "counts_toward_hy": False,
                },
                path=p,
            )
            r = fetch_solana(prefer_live=False, snapshot_path=p)
            self.assertAlmostEqual(r["sol"], 1.25)
            self.assertAlmostEqual(r["usdc"], 42.0)
            self.assertGreater(r["book_usd"], 0)
            # Overlay into empty policy snapshot (panel reader path)
            empty = {"snapshot": {"solana": {}, "coinbase": {"liquid_usdc": 0}}}
            overlay_solana_snapshot(empty, snapshot_path=p)
            sol = empty["snapshot"]["solana"]
            self.assertAlmostEqual(sol["sol"], 1.25)
            self.assertAlmostEqual(sol["usdc"], 42.0)


class TestPrismCopyWins(unittest.TestCase):
    """Zero-flicker: prism's own copy beats a Mac push (even pre-#1147 Mac code)."""

    NOW = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)

    def _book(self, minutes_ago, **extra):
        d = {
            "source": "live",
            "as_of": (self.NOW - timedelta(minutes=minutes_ago)).isoformat(),
            "sol": 0.004,
            "usdc": 0.0,
        }
        d.update(extra)
        return d

    def test_rules(self):
        prism = self._book(10, producer_host="prism")
        self.assertTrue(ss.prism_copy_wins(prism, None, now=self.NOW))
        self.assertTrue(ss.prism_copy_wins(prism, {"as_of": None}, now=self.NOW))
        # older Mac copy -> dropped
        self.assertTrue(ss.prism_copy_wins(prism, self._book(30), now=self.NOW))
        # newer Mac copy, prism still fresh -> prism keeps winning (no flicker)
        self.assertTrue(ss.prism_copy_wins(prism, self._book(1), now=self.NOW))
        # newer prism-produced block (live merge on prism) -> keep that block
        self.assertFalse(
            ss.prism_copy_wins(prism, self._book(1, producer_host="prism"), now=self.NOW)
        )
        # prism producer dead past the stale window -> newer Mac copy may return
        stale = self._book(7 * 60, producer_host="prism")
        self.assertFalse(ss.prism_copy_wins(stale, self._book(5), now=self.NOW))
        self.assertTrue(ss.prism_copy_wins(stale, self._book(8 * 60), now=self.NOW))
        self.assertFalse(ss.prism_copy_wins({"as_of": "junk"}, None, now=self.NOW))
        self.assertFalse(ss.prism_copy_wins(None, None, now=self.NOW))

    def _dirs(self, td, prism=None, pushed=None):
        d = Path(td)
        if prism is not None:
            (d / ss.PRISM_SNAPSHOT_NAME).write_text(json.dumps(prism), encoding="utf-8")
        if pushed is not None:
            (d / ss.SNAPSHOT_NAME).write_text(json.dumps(pushed), encoding="utf-8")
        return d

    def test_load_preferred_drops_mac_push(self):
        mac = self._book(1, holdings=[{"symbol": "JR-strcUSX"}])
        prism = self._book(10, producer_host="prism")
        with tempfile.TemporaryDirectory() as td:
            d = self._dirs(td, prism=prism, pushed=mac)
            with mock.patch.object(ss, "SNAPSHOTS_DIR", d):
                got = ss.load_preferred_solana(now=self.NOW)
                self.assertEqual(got["as_of"], prism["as_of"])
                r = ss.fetch_solana(prefer_live=False, config={})
                self.assertEqual(r["as_of"], prism["as_of"])
                self.assertNotIn("strcusx", json.dumps(r).lower())

    def test_load_preferred_falls_back_without_prism_copy(self):
        mac = self._book(1)
        with tempfile.TemporaryDirectory() as td:
            d = self._dirs(td, pushed=mac)
            with mock.patch.object(ss, "SNAPSHOTS_DIR", d):
                self.assertEqual(ss.load_preferred_solana(now=self.NOW)["as_of"], mac["as_of"])

    def test_overlay_replaces_mac_pushed_block(self):
        mac = self._book(1, holdings=[{"symbol": "JR-strcUSX"}])
        prism = self._book(10, producer_host="prism")
        with tempfile.TemporaryDirectory() as td:
            d = self._dirs(td, prism=prism, pushed=mac)
            with mock.patch.object(ss, "SNAPSHOTS_DIR", d):
                tre = {"snapshot": {"solana": dict(mac)}}
                ss.overlay_solana_snapshot(tre, now=self.NOW)
                self.assertEqual(tre["snapshot"]["solana"]["as_of"], prism["as_of"])
                self.assertEqual(tre["snapshot"]["meta"]["solana_producer"], "prism")
                # newer prism-produced block is kept
                newer = self._book(1, producer_host="prism")
                keep = {"snapshot": {"solana": dict(newer)}}
                ss.overlay_solana_snapshot(keep, now=self.NOW)
                self.assertEqual(keep["snapshot"]["solana"]["as_of"], newer["as_of"])
                # empty block fills from the prism copy, not the newer Mac push
                empty = {"snapshot": {"solana": {}}}
                ss.overlay_solana_snapshot(empty, now=self.NOW)
                self.assertEqual(empty["snapshot"]["solana"]["as_of"], prism["as_of"])

    def test_write_prism_copy_live_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ss.PRISM_SNAPSHOT_NAME
            self.assertIsNone(ss.write_prism_copy({"source": "snapshot", "as_of": "x"}, p))
            self.assertFalse(p.exists())
            self.assertEqual(ss.write_prism_copy(self._book(0), p), p)
            self.assertEqual(json.loads(p.read_text())["producer_host"], "prism")

    def test_main_prism_copy_flag_and_host_tag(self):
        live = self._book(0)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            with mock.patch.object(ss, "SNAPSHOTS_DIR", d), mock.patch.object(
                ss, "fetch_solana_live", return_value=(dict(live), None)
            ), mock.patch.object(ss, "solana_config", return_value={"counts_toward_working_usdc": False}), mock.patch.dict(
                "os.environ", {"FCC_HOST_TAG": "mac"}
            ), mock.patch("sys.stdout"):
                self.assertEqual(ss.main([]), 0)
                self.assertFalse((d / ss.PRISM_SNAPSHOT_NAME).exists())
                self.assertEqual(ss.main(["--prism-copy"]), 0)
                self.assertTrue((d / ss.PRISM_SNAPSHOT_NAME).exists())
            (d / ss.PRISM_SNAPSHOT_NAME).unlink()
            with mock.patch.object(ss, "SNAPSHOTS_DIR", d), mock.patch.object(
                ss, "fetch_solana_live", return_value=(dict(live), None)
            ), mock.patch.object(ss, "solana_config", return_value={"counts_toward_working_usdc": False}), mock.patch.dict(
                "os.environ", {"FCC_HOST_TAG": "prism"}
            ), mock.patch("sys.stdout"):
                self.assertEqual(ss.main([]), 0)
                pc = json.loads((d / ss.PRISM_SNAPSHOT_NAME).read_text())
                self.assertEqual(pc["producer_host"], "prism")
            # live failure must not re-stamp the prism copy from a fallback
            (d / ss.PRISM_SNAPSHOT_NAME).unlink()
            with mock.patch.object(ss, "SNAPSHOTS_DIR", d), mock.patch.object(
                ss, "fetch_solana_live", return_value=(None, "rpc down")
            ), mock.patch.object(ss, "solana_config", return_value={"counts_toward_working_usdc": False}), mock.patch.dict(
                "os.environ", {"FCC_HOST_TAG": "prism"}
            ), mock.patch("sys.stdout"):
                ss.main(["--prism-copy"])
                self.assertFalse((d / ss.PRISM_SNAPSHOT_NAME).exists())


class TestRunTreasuryOfflineMerge(unittest.TestCase):
    """solana-refresh ExecStartPost: offline re-merge makes no live calls."""

    def test_offline_skip_coinbase_flags(self):
        from treasury import run_treasury

        seen = {}

        class Stop(Exception):
            pass

        def fake_build(**kw):
            seen.update(kw)
            raise Stop()

        with mock.patch.object(run_treasury, "build_snapshot", side_effect=fake_build):
            with self.assertRaises(Stop):
                run_treasury.main(["--offline", "--skip-coinbase"])
        self.assertFalse(seen["prefer_live_coinbase"])
        self.assertFalse(seen["prefer_live_ynab"])
        self.assertFalse(seen["prefer_live_expenses"])
        self.assertFalse(seen["prefer_live_solana"])

    def test_run_treasury_has_no_push_or_order_path(self):
        src = (ROOT / "treasury" / "run_treasury.py").read_text(encoding="utf-8")
        for needle in ("rh_snapshot_sync", "push_snapshots_to_pi", "subprocess", "place_", "scp"):
            self.assertNotIn(needle, src)


if __name__ == "__main__":
    unittest.main()
