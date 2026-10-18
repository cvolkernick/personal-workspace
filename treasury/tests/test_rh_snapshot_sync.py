"""Unit tests for Pi-first RH snapshot sync (no network)."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from treasury import rh_snapshot_sync as rss


class RhSnapshotSyncTests(unittest.TestCase):
    def test_valid_rh_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "robinhood_latest.json"
            as_of = datetime.now(timezone.utc).isoformat()
            p.write_text(
                json.dumps(
                    {
                        "as_of": as_of,
                        "source": "live",
                        "primary": {"buying_power": 1.0},
                        "agentic": {"nav_usd": 100},
                    }
                ),
                encoding="utf-8",
            )
            ok, ts, why = rss._valid_rh_snapshot(p)
            self.assertTrue(ok)
            self.assertEqual(why, "ok")
            self.assertIsNotNone(ts)

    def test_mtime_fallback_for_missing_as_of(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "robinhood_latest.json"
            p.write_text(
                json.dumps({"source": "live", "buying_power": 1.0}),
                encoding="utf-8",
            )
            ok, ts, why = rss._valid_rh_snapshot(p)
            self.assertTrue(ok)
            self.assertIn(why, ("ok", "ok_mtime"))
            self.assertIsNotNone(ts)

    def test_invalid_empty_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "robinhood_latest.json"
            p.write_text(json.dumps({"foo": 1}), encoding="utf-8")
            ok, ts, why = rss._valid_rh_snapshot(p, allow_mtime_as_of=True)
            self.assertFalse(ok)
            self.assertEqual(why, "unexpected_shape")

    def test_remote_paths_include_defaults(self) -> None:
        paths = rss._remote_paths("/home/prism-agent/personal-workspace")
        self.assertTrue(any("robinhood_latest.json" in p for p in paths))
        self.assertTrue(
            any("treasury/snapshots/robinhood_latest.json" in p for p in paths)
        )

    def test_sync_prefers_pi_when_ok(self) -> None:
        pi_ok = {
            "ok": True,
            "source": "pi",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "age_hours": 1.0,
            "path": "/tmp/x",
        }
        with mock.patch.object(rss, "pull_from_pi", return_value=pi_ok), mock.patch.object(
            rss, "refresh_via_local_mcp"
        ) as local, mock.patch.object(rss, "reevaluate_offline"):
            out = rss.sync_rh_snapshot(prefer_pi=True, allow_local_mcp=True, reevaluate=True)
            self.assertTrue(out["ok"])
            self.assertEqual(out["source"], "pi")
            local.assert_not_called()

    def test_sync_falls_back_to_local_mcp(self) -> None:
        pi_bad = {"ok": False, "error": "pi_unreachable:x"}
        local_ok = {
            "ok": True,
            "source": "local_mcp",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "path": "/tmp/y",
        }
        with mock.patch.object(rss, "pull_from_pi", return_value=pi_bad), mock.patch.object(
            rss, "refresh_via_local_mcp", return_value=local_ok
        ), mock.patch.object(rss, "reevaluate_offline"):
            out = rss.sync_rh_snapshot(prefer_pi=True, allow_local_mcp=True, reevaluate=False)
            self.assertTrue(out["ok"])
            self.assertEqual(out["source"], "local_mcp")

    def test_age_hours(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        self.assertAlmostEqual(rss._age_hours(past) or 0, 3.0, delta=0.05)

    def test_snapshot_has_agentic(self) -> None:
        self.assertTrue(rss._snapshot_has_agentic({"agentic": {"nav_usd": 100}}))
        self.assertTrue(rss._snapshot_has_agentic({"agentic_allowed": True, "buying_power": 1}))
        self.assertFalse(rss._snapshot_has_agentic({"primary": {"buying_power": 1}}))
        self.assertFalse(rss._snapshot_has_agentic({"agentic": None, "primary": {}}))
        self.assertFalse(rss._snapshot_has_agentic(None))

    def test_primary_only_downgrade_when_local_has_agentic(self) -> None:
        local = {"as_of": "2026-08-17T16:00:00Z", "primary": {}, "agentic": {"nav_usd": 194}}
        remote = {"as_of": "2026-08-17T16:23:00Z", "primary": {"buying_power": 0.09}}
        self.assertTrue(rss._is_primary_only_downgrade(remote, local))

    def test_bootstrap_accepts_primary_only_when_no_local_dual(self) -> None:
        remote = {"as_of": "2026-08-17T16:23:00Z", "primary": {"buying_power": 0.09}}
        self.assertFalse(rss._is_primary_only_downgrade(remote, None))
        self.assertFalse(rss._is_primary_only_downgrade(remote, {}))
        self.assertFalse(
            rss._is_primary_only_downgrade(remote, {"primary": {"buying_power": 1}})
        )

    def test_dual_remote_is_not_a_downgrade(self) -> None:
        local = {"agentic": {"nav_usd": 194}}
        remote = {"primary": {}, "agentic": {"nav_usd": 195}}
        self.assertFalse(rss._is_primary_only_downgrade(remote, local))

    def test_sync_keeps_local_dual_on_pi_primary_only(self) -> None:
        pi_down = {
            "ok": False,
            "error": "primary_only_downgrade",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        as_of = datetime.now(timezone.utc)
        with mock.patch.object(rss, "pull_from_pi", return_value=pi_down), mock.patch.object(
            rss, "refresh_via_local_mcp"
        ) as local_mcp, mock.patch.object(
            rss,
            "_local_dual_fresh",
            return_value=(True, as_of, 0.4, Path("/tmp/robinhood_latest.json")),
        ), mock.patch.object(rss, "reevaluate_offline") as reeval:
            out = rss.sync_rh_snapshot(
                prefer_pi=True, allow_local_mcp=True, reevaluate=True
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "local_existing")
        self.assertEqual(out["note"], "rejected_pi_primary_only_downgrade")
        local_mcp.assert_not_called()
        reeval.assert_not_called()

    def test_sync_falls_through_to_mcp_when_local_dual_stale(self) -> None:
        pi_down = {"ok": False, "error": "primary_only_downgrade"}
        local_ok = {
            "ok": True,
            "source": "local_mcp",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "path": "/tmp/y",
        }
        with mock.patch.object(rss, "pull_from_pi", return_value=pi_down), mock.patch.object(
            rss,
            "_local_dual_fresh",
            return_value=(False, None, None, Path("/tmp/robinhood_latest.json")),
        ), mock.patch.object(
            rss, "refresh_via_local_mcp", return_value=local_ok
        ), mock.patch.object(rss, "reevaluate_offline"), mock.patch.object(
            rss, "push_snapshots_to_pi", return_value={"ok": True}
        ):
            out = rss.sync_rh_snapshot(
                prefer_pi=True, allow_local_mcp=True, reevaluate=False, push_to_pi=True
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "local_mcp")

    def test_pull_rejects_primary_only_when_local_dual_exists(self) -> None:
        as_of = datetime.now(timezone.utc).isoformat()

        def write_slim(_ssh, _remote, dest, _timeout):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                json.dumps(
                    {
                        "as_of": as_of,
                        "source": "pi",
                        "primary": {"buying_power": 0.09, "account_number_last4": "9737"},
                    }
                ),
                encoding="utf-8",
            )
            return True

        with tempfile.TemporaryDirectory() as td:
            snap_dir = Path(td)
            local = snap_dir / rss.RH_SNAP
            local.write_text(
                json.dumps(
                    {
                        "as_of": as_of,
                        "primary": {"buying_power": 0.0},
                        "agentic": {"nav_usd": 194.95, "buying_power": 0.09},
                    }
                ),
                encoding="utf-8",
            )
            before = local.read_text(encoding="utf-8")
            with mock.patch.object(rss, "SNAPSHOTS_DIR", snap_dir), mock.patch.object(
                rss, "pi_reachable", return_value=True
            ), mock.patch.object(rss, "_scp_file", side_effect=write_slim), mock.patch.object(
                rss, "_pi_settings", return_value=_fake_pi_settings()
            ):
                out = rss.pull_from_pi()
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "primary_only_downgrade")
            self.assertEqual(local.read_text(encoding="utf-8"), before)

    def test_pull_accepts_primary_only_when_local_empty(self) -> None:
        as_of = datetime.now(timezone.utc).isoformat()

        def write_slim(_ssh, _remote, dest, _timeout):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                json.dumps(
                    {
                        "as_of": as_of,
                        "source": "pi",
                        "primary": {"buying_power": 0.09},
                    }
                ),
                encoding="utf-8",
            )
            return True

        with tempfile.TemporaryDirectory() as td:
            snap_dir = Path(td)
            with mock.patch.object(rss, "SNAPSHOTS_DIR", snap_dir), mock.patch.object(
                rss, "pi_reachable", return_value=True
            ), mock.patch.object(rss, "_scp_file", side_effect=write_slim), mock.patch.object(
                rss, "_pi_settings", return_value=_fake_pi_settings()
            ):
                out = rss.pull_from_pi(also_fund_manager=False)
            self.assertTrue(out["ok"])
            written = json.loads((snap_dir / rss.RH_SNAP).read_text(encoding="utf-8"))
            self.assertIn("primary", written)
            self.assertNotIn("agentic", written)


class RhProducerRoleTests(unittest.TestCase):
    def test_classify_rh_error(self) -> None:
        self.assertEqual(rss.classify_rh_error("oauth revoked"), "auth_fail")
        self.assertEqual(rss.classify_rh_error("auth_fail"), "auth_fail")
        self.assertEqual(rss.classify_rh_error("local_mcp_timeout:240s"), "timeout")
        self.assertEqual(rss.classify_rh_error("grok_not_found"), "grok")
        self.assertEqual(rss.classify_rh_error("pi_unreachable:x"), "unreachable")
        self.assertEqual(rss.classify_rh_error("remote_stale:50.1h>6h"), "stale")
        self.assertEqual(rss.classify_rh_error("no_refresh_path"), "skipped")
        self.assertEqual(rss.classify_rh_error("local_mcp_skipped"), "skipped")
        self.assertEqual(rss.classify_rh_error(""), "unknown")

    def test_gateway_host_is_not_auto_producer(self) -> None:
        cleared = {
            k: v
            for k, v in os.environ.items()
            if k not in ("TREASURY_RH_ROLE", "TREASURY_RH_PRODUCER", "TREASURY_RH_BACKUP")
        }
        with mock.patch.dict(os.environ, cleared, clear=True), mock.patch.object(
            rss, "_this_host", return_value="gateway.example"
        ), mock.patch.object(rss, "_sot_host", return_value="prism"):
            self.assertEqual(rss._rh_role(), "consumer")

    def test_notify_skips_no_refresh_path(self) -> None:
        status = {
            "ok": False,
            "role": "producer",
            "this_host": "gateway.example",
            "producer_host": "prism",
            "error": "no_refresh_path",
            "error_class": "skipped",
        }
        with mock.patch.object(rss, "existing_rh_snapshot_fresh", return_value=(True, 1.2, "x")):
            gate = rss.rh_failure_should_page(status)
            out = rss.notify_rh_producer_failure(status)
        self.assertFalse(gate["page"])
        self.assertEqual(gate["reason"], "skipped_or_no_refresh_path")
        self.assertFalse(out.get("notified"))
        self.assertEqual(out.get("reason"), "skipped_or_no_refresh_path")

    def test_notify_skips_local_mcp_timeout_when_as_of_fresh(self) -> None:
        as_of = datetime.now(timezone.utc) - timedelta(hours=1)
        status = {
            "ok": False,
            "role": "producer",
            "this_host": "prism",
            "producer_host": "prism",
            "error": "local_mcp_timeout:240s",
            "error_class": "timeout",
            "as_of": as_of.isoformat(),
            "age_hours": 1.0,
            "local_mcp": {"error": "local_mcp_timeout:240s", "error_class": "timeout"},
        }
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td)
            dest = snap / rss.RH_SNAP
            dest.write_text(
                json.dumps(
                    {
                        "as_of": as_of.isoformat(),
                        "source": "live",
                        "primary": {"buying_power": 1.0},
                        "agentic": {"nav_usd": 100},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(rss, "SNAPSHOTS_DIR", snap):
                gate = rss.rh_failure_should_page(status)
                out = rss.notify_rh_producer_failure(status)
        self.assertFalse(gate["page"])
        self.assertEqual(gate["reason"], "timeout_as_of_fresh")
        self.assertFalse(out.get("notified"))

    def test_notify_skips_timeout_when_rh_mcp_disabled(self) -> None:
        status = {
            "ok": False,
            "role": "producer",
            "this_host": "gateway.example",
            "producer_host": "prism",
            "error": "local_mcp_timeout:240s",
            "error_class": "timeout",
            "rh_mcp_enabled": False,
            "local_mcp": None,
        }
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(rss, "SNAPSHOTS_DIR", Path(td)):
                gate = rss.rh_failure_should_page(status)
        self.assertFalse(gate["page"])
        self.assertEqual(gate["reason"], "timeout_non_producer_mcp")

    def test_notify_pages_producer_timeout_when_as_of_stale(self) -> None:
        as_of = datetime.now(timezone.utc) - timedelta(hours=17.8)
        status = {
            "ok": False,
            "role": "producer",
            "this_host": "prism",
            "producer_host": "prism",
            "error": "local_mcp_timeout:240s",
            "error_class": "timeout",
            "as_of": as_of.isoformat(),
            "local_mcp": {"error": "local_mcp_timeout:240s"},
        }
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td)
            dest = snap / rss.RH_SNAP
            dest.write_text(
                json.dumps(
                    {
                        "as_of": as_of.isoformat(),
                        "source": "live",
                        "primary": {"buying_power": 1.0},
                        "agentic": {"nav_usd": 100},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(rss, "SNAPSHOTS_DIR", snap), mock.patch.object(
                rss, "notify_rh_producer_failure", wraps=rss.notify_rh_producer_failure
            ):
                gate = rss.rh_failure_should_page(status)
        self.assertTrue(gate["page"])
        self.assertEqual(gate["reason"], "producer_failure")

    def test_strip_rh_push_default_no_dual_write(self) -> None:
        files = ["robinhood_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TREASURY_RH_PUSH", None)
            out = rss._strip_rh_push(files)
        self.assertNotIn("robinhood_latest.json", out)
        self.assertIn("x_money_latest.json", out)

    def test_strip_rh_push_explicit_override(self) -> None:
        files = ["robinhood_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {"TREASURY_RH_PUSH": "1"}):
            out = rss._strip_rh_push(files)
        self.assertIn("robinhood_latest.json", out)

    def test_strip_braiins_push_default_no_dual_write(self) -> None:
        files = ["braiins_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TREASURY_BRAIINS_PUSH", None)
            out = rss._strip_rh_push(files)
        self.assertNotIn("braiins_latest.json", out)
        self.assertIn("x_money_latest.json", out)

    def test_strip_braiins_push_explicit_override(self) -> None:
        files = ["braiins_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {"TREASURY_BRAIINS_PUSH": "1"}):
            out = rss._strip_rh_push(files)
        self.assertIn("braiins_latest.json", out)

    def test_strip_coinbase_push_default_no_dual_write(self) -> None:
        files = ["coinbase_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TREASURY_COINBASE_PUSH", None)
            out = rss._strip_rh_push(files)
        self.assertNotIn("coinbase_latest.json", out)
        self.assertIn("x_money_latest.json", out)

    def test_strip_coinbase_push_explicit_override(self) -> None:
        files = ["coinbase_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {"TREASURY_COINBASE_PUSH": "1"}):
            out = rss._strip_rh_push(files)
        self.assertIn("coinbase_latest.json", out)

    def test_strip_solana_push_default_no_dual_write(self) -> None:
        files = ["solana_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TREASURY_SOLANA_PUSH", None)
            out = rss._strip_rh_push(files)
        self.assertNotIn("solana_latest.json", out)
        self.assertIn("x_money_latest.json", out)

    def test_strip_solana_push_explicit_override(self) -> None:
        files = ["solana_latest.json", "x_money_latest.json"]
        with mock.patch.dict(os.environ, {"TREASURY_SOLANA_PUSH": "1"}):
            out = rss._strip_rh_push(files)
        self.assertIn("solana_latest.json", out)

    def test_default_push_files_omit_pi_sot(self) -> None:
        self.assertNotIn("robinhood_latest.json", rss.DEFAULT_PUSH_FILES)
        self.assertNotIn("braiins_latest.json", rss.DEFAULT_PUSH_FILES)
        self.assertNotIn("coinbase_latest.json", rss.DEFAULT_PUSH_FILES)
        self.assertNotIn("solana_latest.json", rss.DEFAULT_PUSH_FILES)

    def test_config_push_files_omit_solana(self) -> None:
        cfg = json.loads((rss.ROOT / "treasury" / "config.json").read_text(encoding="utf-8"))
        ps = cfg["pi_sync"]
        self.assertNotIn("solana_latest.json", ps["push_files"])
        self.assertIn("Solana", ps["notes"])
        self.assertIn("solana-refresh.timer", ps["notes"])

    def test_pi_settings_strip_solana_from_stale_config(self) -> None:
        """A local config that still lists solana_latest.json must not push it."""
        cfg = {
            "pi_sync": {
                "push_files": ["solana_latest.json", "x_money_latest.json"],
            }
        }
        with mock.patch.object(rss, "load_config", return_value=cfg), mock.patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop("TREASURY_SOLANA_PUSH", None)
            settings = rss._pi_settings()
        self.assertNotIn("solana_latest.json", settings["push_files"])
        self.assertIn("x_money_latest.json", settings["push_files"])

    def test_solana_refresh_user_units(self) -> None:
        deploy = rss.ROOT / "treasury" / "deploy"
        svc = (deploy / "solana-refresh.service").read_text(encoding="utf-8")
        tmr = (deploy / "solana-refresh.timer").read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", svc)
        self.assertIn("WorkingDirectory=%h/personal-workspace", svc)
        self.assertIn("ExecStart=/usr/bin/python3 -m treasury.solana_sync", svc)
        self.assertIn("TREASURY_SKIP_PUSH_PI=1", svc)
        self.assertIn("WantedBy=default.target", svc)
        # user unit: no User=/Group= (those are system-unit only)
        self.assertNotRegex(svc, r"(?m)^(User|Group)=")
        self.assertIn("TimeoutStartSec=5min", svc)
        self.assertIn(
            "ExecStartPost=/usr/bin/python3 -m treasury.run_treasury --offline --skip-coinbase",
            svc,
        )
        self.assertIn("--prism-copy", svc)
        for sib in ("braiins-refresh.service", "coinbase-price-refresh.service"):
            txt = (deploy / sib).read_text(encoding="utf-8")
            self.assertIn("Type=oneshot", txt, sib)
            self.assertIn("TimeoutStartSec=5min", txt, sib)
        self.assertIn("Unit=solana-refresh.service", tmr)
        self.assertIn("OnUnitActiveSec=15min", tmr)
        self.assertIn("Persistent=true", tmr)
        doc = (deploy / "SOLANA_PRODUCER.md").read_text(encoding="utf-8")
        self.assertIn("systemctl --user enable --now solana-refresh.timer", doc)

    def test_role_env_producer(self) -> None:
        with mock.patch.dict(os.environ, {"TREASURY_RH_ROLE": "producer"}):
            self.assertEqual(rss._rh_role(), "producer")
        with mock.patch.dict(os.environ, {"TREASURY_RH_ROLE": "consumer"}):
            self.assertEqual(rss._rh_role(), "consumer")
        cleared = {
            k: v
            for k, v in os.environ.items()
            if k not in ("TREASURY_RH_ROLE", "TREASURY_RH_PRODUCER", "TREASURY_RH_BACKUP")
        }
        cleared["TREASURY_RH_BACKUP"] = "1"
        with mock.patch.dict(os.environ, cleared, clear=True):
            self.assertEqual(rss._rh_role(), "backup")

    def test_producer_skips_pi_pull_and_rh_push(self) -> None:
        local_ok = {
            "ok": True,
            "source": "local_mcp",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "path": "/tmp/y",
        }
        with mock.patch.dict(os.environ, {"TREASURY_RH_ROLE": "producer"}), mock.patch.object(
            rss, "pull_from_pi"
        ) as pull, mock.patch.object(
            rss, "refresh_via_local_mcp", return_value=local_ok
        ), mock.patch.object(
            rss, "push_snapshots_to_pi"
        ) as push, mock.patch.object(
            rss, "write_producer_status"
        ), mock.patch.object(
            rss, "reevaluate_offline"
        ):
            out = rss.sync_rh_snapshot(
                prefer_pi=True,
                allow_local_mcp=True,
                reevaluate=False,
                push_to_pi=True,
                notify=False,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["role"], "producer")
        self.assertEqual(out["source"], "local_mcp")
        pull.assert_not_called()
        push.assert_not_called()

    def test_sync_no_refresh_path_does_not_page(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TREASURY_RH_ROLE": "producer", "TREASURY_SKIP_LOCAL_MCP": "1"},
        ), mock.patch.object(rss, "write_producer_status"), mock.patch.object(
            rss, "notify_rh_producer_failure", wraps=rss.notify_rh_producer_failure
        ) as notify:
            out = rss.sync_rh_snapshot(
                prefer_pi=True,
                allow_local_mcp=True,
                reevaluate=False,
                push_to_pi=False,
                notify=True,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_refresh_path")
        self.assertEqual(out["error_class"], "skipped")
        self.assertFalse((out.get("notify") or {}).get("notified"))
        self.assertEqual((out.get("notify") or {}).get("reason"), "skipped_or_no_refresh_path")
        notify.assert_called_once()

    def test_consumer_does_not_run_mcp_when_pi_ok(self) -> None:
        pi_ok = {
            "ok": True,
            "source": "pi",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "age_hours": 0.4,
            "path": "/tmp/x",
        }
        with mock.patch.dict(os.environ, {"TREASURY_RH_ROLE": "consumer"}), mock.patch.object(
            rss, "pull_from_pi", return_value=pi_ok
        ), mock.patch.object(
            rss, "refresh_via_local_mcp"
        ) as local, mock.patch.object(
            rss, "write_producer_status"
        ), mock.patch.object(
            rss, "reevaluate_offline"
        ):
            out = rss.sync_rh_snapshot(
                prefer_pi=True, allow_local_mcp=True, reevaluate=False, notify=False
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "pi")
        local.assert_not_called()

    def test_auth_fail_refresh_does_not_invent(self) -> None:
        as_of = datetime.now(timezone.utc) - timedelta(hours=2)
        with tempfile.TemporaryDirectory() as td:
            snap_dir = Path(td)
            dest = snap_dir / rss.RH_SNAP
            dest.write_text(
                json.dumps(
                    {
                        "as_of": as_of.isoformat(),
                        "source": "live",
                        "primary": {"buying_power": 1.0},
                        "agentic": {"nav_usd": 100},
                    }
                ),
                encoding="utf-8",
            )
            before = json.loads(dest.read_text(encoding="utf-8"))
            fake = mock.Mock(returncode=1, stdout="MCP OAuth revoked / unauthorized", stderr="")
            with mock.patch.object(rss, "SNAPSHOTS_DIR", snap_dir), mock.patch.object(
                rss, "shutil"
            ) as sh, mock.patch("subprocess.run", return_value=fake):
                sh.which.return_value = "/usr/bin/grok"
                prompt = Path(td) / "rh_refresh_prompt.txt"
                # module reads ROOT/treasury/rh_refresh_prompt.txt — keep real prompt
                out = rss.refresh_via_local_mcp(timeout_s=5)
            after = json.loads(dest.read_text(encoding="utf-8"))
            self.assertFalse(out["ok"])
            self.assertEqual(out["error_class"], "auth_fail")
            self.assertEqual(out.get("note"), "left_existing_snapshot")
            self.assertEqual(after["as_of"], before["as_of"])
            self.assertNotEqual(out.get("as_of"), datetime.now(timezone.utc).isoformat())


class RhSnapshotGrokDenyListTests(unittest.TestCase):
    """#1093/#1196: the snapshot refresh runs `grok --yolo` with RH MCP, so it must
    carry the Robinhood order-tool deny list from the shared order-gate helper."""

    def test_grok_yolo_argv_contains_the_full_shared_deny_list(self) -> None:
        from treasury import fund_manager_order_gate as gate

        fake = mock.Mock(returncode=1, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            rss, "SNAPSHOTS_DIR", Path(td)
        ), mock.patch.object(rss.shutil, "which", return_value="/usr/bin/grok"), mock.patch.object(
            rss.subprocess, "run", return_value=fake
        ) as run:
            rss.refresh_via_local_mcp(timeout_s=5)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args[0][0]
        self.assertIn("--yolo", argv)
        deny = gate.grok_deny_args()
        self.assertGreaterEqual(len(deny), 2)
        i = argv.index("--yolo")
        self.assertEqual(argv[i + 1 : i + 1 + len(deny)], deny, "deny list must follow --yolo verbatim")
        denied_rules = argv[i + 2 : i + 1 + len(deny) : 2]
        self.assertIn("MCPTool(robinhood-trading__place_*)", denied_rules)
        self.assertIn("MCPTool(robinhood-trading__exercise_option)", denied_rules)
        # read-only snapshot tools and cancels are not denied
        for tool in ("get_accounts", "get_portfolio", "get_equity_positions", "cancel_equity_order"):
            self.assertFalse(any(tool in r for r in denied_rules), tool)

    def test_globs_changed_in_the_shared_helper_flow_through(self) -> None:
        from treasury import fund_manager_order_gate as gate

        fake = mock.Mock(returncode=1, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            rss, "SNAPSHOTS_DIR", Path(td)
        ), mock.patch.object(rss.shutil, "which", return_value="/usr/bin/grok"), mock.patch.object(
            rss.subprocess, "run", return_value=fake
        ) as run, mock.patch.object(gate, "RH_ORDER_TOOL_GLOBS", ("only_this_*",)):
            rss.refresh_via_local_mcp(timeout_s=5)
        self.assertEqual(
            run.call_args[0][0].count("MCPTool(robinhood-trading__only_this_*)"), 1
        )

    def test_missing_deny_list_means_grok_is_not_run(self) -> None:
        for bad in ([], ["--deny"], ["--allow", "x"]):
            fake = mock.Mock(returncode=0, stdout="", stderr="")
            with tempfile.TemporaryDirectory() as td, mock.patch.object(
                rss, "SNAPSHOTS_DIR", Path(td)
            ), mock.patch.object(rss.shutil, "which", return_value="/usr/bin/grok"), mock.patch.object(
                rss.subprocess, "run", return_value=fake
            ) as run, mock.patch.object(rss, "grok_deny_args", return_value=bad):
                out = rss.refresh_via_local_mcp(timeout_s=5)
            run.assert_not_called()
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "order_gate_deny_list_unavailable")

        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            rss, "SNAPSHOTS_DIR", Path(td)
        ), mock.patch.object(rss.shutil, "which", return_value="/usr/bin/grok"), mock.patch.object(
            rss.subprocess, "run"
        ) as run, mock.patch.object(rss, "grok_deny_args", side_effect=RuntimeError("boom")):
            out = rss.refresh_via_local_mcp(timeout_s=5)
        run.assert_not_called()
        self.assertEqual(out["error"], "order_gate_deny_list_unavailable")


class RhNotifyAc4Tests(unittest.TestCase):
    def test_stale_notify_names_producer_and_error_class(self) -> None:
        from treasury.fund_manager import notify_if_needed
        from treasury import fund_manager as fm

        stale_eval = {
            "data_quality": {
                "stale": ["robinhood snapshot is old"],
                "warnings": [],
            }
        }
        status = {
            "ok": False,
            "producer_host": "prism",
            "this_host": "prism",
            "error_class": "auth_fail",
            "error": "auth_fail",
        }
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td)
            state = snap / "ntfy_stale_rh_state.json"
            prod = snap / "rh_producer_status.json"
            prod.write_text(json.dumps(status), encoding="utf-8")
            with mock.patch.object(fm, "NTFY_STALE_RH_STATE", state), mock.patch.object(
                fm, "SNAPSHOTS_DIR", snap
            ), mock.patch.object(
                fm,
                "load_config",
                return_value={"notifications": {"enabled": True, "stale_rh_cooldown_hours": 6}},
            ), mock.patch("urllib.request.urlopen") as urlopen:
                resp = mock.MagicMock()
                resp.status = 200
                resp.__enter__.return_value = resp
                resp.__exit__.return_value = None
                urlopen.return_value = resp
                out = notify_if_needed(
                    decision_or_review={"kind": "hold", "outcome": "hold"},
                    treasury_eval=stale_eval,
                )
        self.assertTrue(out.get("notified"), out)
        self.assertTrue((out.get("github") or {}).get("posted"), out)
        self.assertIn("auth_fail", out.get("title") or "")
        self.assertIn("prism", out.get("title") or "")
        self.assertTrue(urlopen.called)
        posted = urlopen.call_args[0][0].full_url
        self.assertIn("issues/701/comments", posted)
        self.assertNotIn("ntfy.sh", posted)


class ExpensesPushGuardTests(unittest.TestCase):
    def test_decision_never_replaces_newer_with_older(self) -> None:
        older = datetime(2026, 9, 14, tzinfo=timezone.utc)
        newer = datetime(2026, 10, 3, 20, 53, tzinfo=timezone.utc)
        self.assertEqual(
            rss.expenses_push_decision(older, "ok", newer),
            "skip_remote_newer_or_equal",
        )
        self.assertEqual(rss.expenses_push_decision(newer, "ok", older), "push")
        self.assertEqual(
            rss.expenses_push_decision(newer, "ok", newer),
            "skip_remote_newer_or_equal",
        )
        self.assertEqual(rss.expenses_push_decision(newer, "missing", None), "push")
        self.assertEqual(
            rss.expenses_push_decision(newer, "unreadable", None),
            "skip_remote_unreadable",
        )
        self.assertEqual(
            rss.expenses_push_decision(None, "ok", newer),
            "skip_local_as_of_missing",
        )

    def test_push_older_snapshot_leaves_pi_copy_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            local_dir = root / "local"
            local_dir.mkdir()
            pi = root / "pi" / "expenses_latest.json"
            pi.parent.mkdir()
            pi_body = {
                "as_of": "2026-10-03T20:53:00+00:00",
                "source": "google_sheets",
                "summary": {"upcoming_expense_monthly": 1},
            }
            pi.write_text(json.dumps(pi_body), encoding="utf-8")
            before = pi.read_bytes()
            before_mtime = pi.stat().st_mtime_ns
            (local_dir / "expenses_latest.json").write_text(
                json.dumps(
                    {
                        "as_of": "2026-09-14T16:00:00+00:00",
                        "source": "google_sheets",
                        "summary": {"upcoming_expense_monthly": 0},
                    }
                ),
                encoding="utf-8",
            )

            real_run = rss.subprocess.run

            def fake_run(cmd, **kwargs):
                cmd = list(cmd)
                result = mock.Mock(returncode=0, stdout="", stderr="")
                if cmd and cmd[0] == "scp":
                    raise AssertionError(
                        "older expenses snapshot must not be copied onto the Pi"
                    )
                if cmd and cmd[0] == "ssh" and "python3" in cmd:
                    # patch.object replaces subprocess.run on the module, so
                    # the Pi-side reader has to call the original.
                    ran = real_run(
                        [rss.sys.executable, "-", str(pi)],
                        input=kwargs.get("input") or "",
                        capture_output=True,
                        text=True,
                    )
                    result.returncode = ran.returncode
                    result.stdout = ran.stdout
                    result.stderr = ran.stderr
                    return result
                return result

            with mock.patch.object(rss, "SNAPSHOTS_DIR", local_dir), mock.patch.object(
                rss, "_pi_settings", _fake_pi_settings
            ), mock.patch.object(
                rss.shutil, "which", return_value="/usr/bin/scp"
            ), mock.patch.object(rss.subprocess, "run", side_effect=fake_run):
                out = rss.push_snapshots_to_pi(files=["expenses_latest.json"])

            self.assertNotIn("expenses_latest.json", out["pushed"])
            self.assertEqual(out["skipped"][0]["reason"], "skip_remote_newer_or_equal")
            self.assertEqual(out["skipped"][0]["remote_as_of"], "2026-10-03T20:53:00+00:00")
            self.assertEqual(pi.read_bytes(), before)
            self.assertEqual(pi.stat().st_mtime_ns, before_mtime)



class PiSshTargetTests(unittest.TestCase):
    """prism SSH target defaults to the MagicDNS short name; env overrides it."""

    def test_default_ssh_is_magicdns_short_name(self) -> None:
        self.assertEqual(rss.DEFAULT_SSH, "prism-agent@prism-gateway")

    def test_env_overrides_config_and_default(self) -> None:
        cfg = {"pi_sync": {"ssh": "prism-agent@prism-gateway"}}
        with mock.patch.object(rss, "load_config", return_value=cfg), mock.patch.dict(
            os.environ, {"TREASURY_PI_SSH": "prism-agent@192.0.2.10"}, clear=False
        ):
            self.assertEqual(rss._pi_settings()["ssh"], "prism-agent@192.0.2.10")

    def test_unset_falls_back_to_magicdns_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "TREASURY_PI_SSH"}
        with mock.patch.object(rss, "load_config", return_value={}), mock.patch.dict(
            os.environ, env, clear=True
        ):
            self.assertEqual(rss._pi_settings()["ssh"], "prism-agent@prism-gateway")

    def test_no_placeholder_warning_on_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "TREASURY_PI_SSH"}
        err = io.StringIO()
        with mock.patch.object(rss, "load_config", return_value={}), mock.patch.dict(
            os.environ, env, clear=True
        ), mock.patch.object(sys, "stderr", err):
            rss._pi_settings()
        self.assertNotIn("placeholder", err.getvalue())

    def test_committed_config_uses_magicdns_not_an_ip(self) -> None:
        cfg_path = Path(rss.__file__).resolve().parent / "config.json"
        ssh = json.loads(cfg_path.read_text(encoding="utf-8"))["pi_sync"]["ssh"]
        self.assertEqual(ssh, "prism-agent@prism-gateway")
        host = ssh.rsplit("@", 1)[-1]
        self.assertIsNone(re.fullmatch(r"[0-9.]+", host), ssh)


def _snap_payload(as_of: datetime, *, agentic: bool = True) -> dict:
    """Synthetic snapshot (made-up values only; never real portfolio data)."""
    d: dict = {
        "as_of": as_of.isoformat(),
        "source": "live",
        "primary": {"buying_power": 1.0},
    }
    if agentic:
        d["agentic"] = {"nav_usd": 100}
    return d


class RhRefreshTimeoutAndAuthTests(unittest.TestCase):
    """#1266: timeout-after-write is ok; the 401 classifier needs status context.

    Every test mocks ``subprocess.run`` and uses a temp snapshot dir: no grok,
    no Robinhood MCP and no real snapshot file is ever touched.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.snap_dir = Path(self._td.name)
        self.dest = self.snap_dir / rss.RH_SNAP
        # guard: nothing may spawn a real process
        p = mock.patch.object(
            rss.subprocess, "Popen", side_effect=AssertionError("real subprocess")
        )
        p.start()
        self.addCleanup(p.stop)

    def _seed_old(self, *, hours_old: float = 2.0, agentic: bool = True) -> dict:
        """Pre-run snapshot: as_of and mtime both ``hours_old`` in the past."""
        old = datetime.now(timezone.utc) - timedelta(hours=hours_old)
        payload = _snap_payload(old, agentic=agentic)
        self.dest.write_text(json.dumps(payload), encoding="utf-8")
        t = old.timestamp()
        os.utime(self.dest, (t, t))
        return payload

    def _run(self, side_effect, *, timeout_s: float = 5):
        """Run refresh_via_local_mcp with subprocess.run mocked by side_effect."""
        env = {k: v for k, v in os.environ.items() if k != "TREASURY_SKIP_LOCAL_MCP"}
        with mock.patch.object(rss, "SNAPSHOTS_DIR", self.snap_dir), mock.patch.object(
            rss.shutil, "which", return_value="/usr/bin/grok"
        ), mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            rss.subprocess, "run", side_effect=side_effect
        ) as run:
            out = rss.refresh_via_local_mcp(timeout_s=timeout_s)
        self.assertEqual(run.call_count, 1)
        return out

    @staticmethod
    def _timeout(argv, **kw):
        return subprocess.TimeoutExpired(cmd=argv, timeout=kw["timeout"])

    def _writes_then_times_out(self, text: str):
        def fx(argv, **kw):
            self.dest.write_text(text, encoding="utf-8")
            raise self._timeout(argv, **kw)

        return fx

    def _writes_then_exits(self, text: str, *, rc: int, stdout: str, stderr: str = ""):
        def fx(argv, **kw):
            if text:
                self.dest.write_text(text, encoding="utf-8")
            return mock.Mock(returncode=rc, stdout=stdout, stderr=stderr)

        return fx

    # ---- (1) timeout handling -------------------------------------------

    def test_timeout_with_fresh_snapshot_is_ok(self) -> None:
        self._seed_old()
        new = _snap_payload(datetime.now(timezone.utc))
        out = self._run(self._writes_then_times_out(json.dumps(new)))
        self.assertTrue(out["ok"])
        self.assertIsNone(out["error"])
        self.assertIsNone(out["error_class"])
        self.assertEqual(out["note"], "snapshot_updated_before_timeout")
        self.assertEqual(out["as_of"], new["as_of"])
        self.assertEqual(out["timeout_s"], 5)
        self.assertLess(out["age_hours"], 0.1)

    def test_timeout_with_fresh_snapshot_no_prior_file_is_ok(self) -> None:
        new = _snap_payload(datetime.now(timezone.utc))
        out = self._run(self._writes_then_times_out(json.dumps(new)))
        self.assertTrue(out["ok"])
        self.assertEqual(out["note"], "snapshot_updated_before_timeout")

    def test_timeout_with_stale_snapshot_keeps_failure(self) -> None:
        before = self._seed_old()
        raw_before = self.dest.read_bytes()
        mt_before = self.dest.stat().st_mtime
        out = self._run(lambda argv, **kw: (_ for _ in ()).throw(self._timeout(argv, **kw)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "timeout")
        self.assertEqual(out["error"], "local_mcp_timeout:5s")
        self.assertNotIn("note", out)
        self.assertEqual(out["as_of"], before["as_of"])  # honest stale, not moved
        self.assertEqual(self.dest.read_bytes(), raw_before)
        self.assertEqual(self.dest.stat().st_mtime, mt_before)

    def test_timeout_with_missing_snapshot_keeps_failure(self) -> None:
        out = self._run(lambda argv, **kw: (_ for _ in ()).throw(self._timeout(argv, **kw)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "timeout")
        self.assertNotIn("as_of", out)

    def test_timeout_rewritten_but_as_of_not_newer_keeps_failure(self) -> None:
        before = self._seed_old()
        # mtime moves (file rewritten) but as_of is the same: nothing was refreshed
        out = self._run(self._writes_then_times_out(json.dumps(before)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "timeout")
        self.assertNotIn("note", out)

    def test_timeout_with_invalid_or_partial_snapshot_keeps_failure(self) -> None:
        now = datetime.now(timezone.utc)
        good = json.dumps(_snap_payload(now))
        bad_cases = {
            "truncated_json": good[: len(good) // 2],
            "empty_file": "",
            "not_a_dict_json": "[]",
            "missing_as_of_key": json.dumps({"source": "live", "buying_power": 1.0}),
            "unparseable_as_of": json.dumps({"as_of": "not-a-date", "source": "live"}),
            "unexpected_shape": json.dumps({"as_of": now.isoformat(), "foo": 1}),
        }
        for name, text in bad_cases.items():
            with self.subTest(name):
                before = self._seed_old()
                out = self._run(self._writes_then_times_out(text))
                self.assertFalse(out["ok"], name)
                self.assertEqual(out["error_class"], "timeout", name)
                self.assertEqual(out["error"], "local_mcp_timeout:5s", name)
                self.assertNotIn("note", out)
                self.assertNotEqual(out.get("as_of"), now.isoformat(), name)

    def test_timeout_primary_only_rewrite_over_agentic_keeps_failure(self) -> None:
        self._seed_old(agentic=True)
        new = _snap_payload(datetime.now(timezone.utc), agentic=False)
        out = self._run(self._writes_then_times_out(json.dumps(new)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "timeout")

    def test_timeout_ok_is_recorded_in_status_and_does_not_page(self) -> None:
        self._seed_old()
        new = _snap_payload(datetime.now(timezone.utc))
        env = {k: v for k, v in os.environ.items() if k != "TREASURY_SKIP_LOCAL_MCP"}
        env["TREASURY_RH_ROLE"] = "producer"
        with mock.patch.object(rss, "SNAPSHOTS_DIR", self.snap_dir), mock.patch.object(
            rss.shutil, "which", return_value="/usr/bin/grok"
        ), mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            rss.subprocess, "run", side_effect=self._writes_then_times_out(json.dumps(new))
        ), mock.patch.object(rss, "reevaluate_offline"), mock.patch.object(
            rss, "notify_rh_producer_failure"
        ) as notify:
            out = rss.sync_rh_snapshot(reevaluate=True, push_to_pi=False, notify=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "local_mcp")
        self.assertEqual(out["note"], "snapshot_updated_before_timeout")
        self.assertIsNone(out["error"])
        notify.assert_not_called()
        self.assertNotIn("notify", out)
        status = json.loads((self.snap_dir / rss.RH_PRODUCER_STATUS).read_text("utf-8"))
        self.assertTrue(status["ok"])
        self.assertEqual(status["note"], "snapshot_updated_before_timeout")
        self.assertIsNone(status["error"])
        # (the sidecar writes "unknown" for an empty error_class on any ok run)
        self.assertIn(status["error_class"], (None, "unknown"))
        # main() exits 0 for an ok status
        with mock.patch.object(rss, "sync_rh_snapshot", return_value=out), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ):
            self.assertEqual(rss.main(["--local-only"]), 0)

    def test_timeout_stale_still_fails_the_run_through_sync(self) -> None:
        self._seed_old(hours_old=7.0)
        env = {k: v for k, v in os.environ.items() if k != "TREASURY_SKIP_LOCAL_MCP"}
        env["TREASURY_RH_ROLE"] = "producer"
        with mock.patch.object(rss, "SNAPSHOTS_DIR", self.snap_dir), mock.patch.object(
            rss.shutil, "which", return_value="/usr/bin/grok"
        ), mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            rss.subprocess,
            "run",
            side_effect=lambda argv, **kw: (_ for _ in ()).throw(self._timeout(argv, **kw)),
        ), mock.patch.object(rss, "reevaluate_offline"), mock.patch.object(
            rss, "notify_rh_producer_failure", return_value={"ok": True}
        ):
            out = rss.sync_rh_snapshot(reevaluate=False, push_to_pi=False, notify=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "timeout")
        with mock.patch.object(rss, "sync_rh_snapshot", return_value=out), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ):
            self.assertEqual(rss.main(["--local-only"]), 1)

    # ---- (2) timeout default ---------------------------------------------

    def test_default_timeout_is_420_and_consistent_with_timer_and_max_age(self) -> None:
        self.assertEqual(rss.DEFAULT_MCP_TIMEOUT_S, 420.0)
        cfg_path = Path(rss.__file__).resolve().parent / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["pi_sync"]["mcp_timeout_s"], rss.DEFAULT_MCP_TIMEOUT_S)
        self.assertEqual(cfg["pi_sync"]["max_age_hours"], rss.DEFAULT_MAX_AGE_H)
        self.assertEqual(rss.DEFAULT_MAX_AGE_H, 6.0)
        timer = (cfg_path.parent / "deploy" / "rh-refresh.timer").read_text("utf-8")
        m = re.search(r"^OnUnitActiveSec=(\d+)h\s*$", timer, re.M)
        self.assertIsNotNone(m)
        interval_h = float(m.group(1))
        self.assertEqual(interval_h, 3.0)
        # a run (timeout included) fits well inside one timer interval
        self.assertLess(rss.DEFAULT_MCP_TIMEOUT_S / 3600.0, interval_h / 10)
        # one missed run (2 intervals) is still inside the freshness window
        self.assertGreaterEqual(rss.DEFAULT_MAX_AGE_H, 2 * interval_h)

    def test_timeout_env_override_still_wins(self) -> None:
        with mock.patch.dict(os.environ, {"TREASURY_RH_MCP_TIMEOUT_S": "123"}):
            self.assertEqual(rss._pi_settings()["mcp_timeout_s"], 123.0)
        env = {k: v for k, v in os.environ.items() if k != "TREASURY_RH_MCP_TIMEOUT_S"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(rss._pi_settings()["mcp_timeout_s"], 420.0)

    def test_timeout_passed_to_subprocess_is_the_default(self) -> None:
        seen = {}

        def fx(argv, **kw):
            seen["timeout"] = kw["timeout"]
            return mock.Mock(returncode=1, stdout="", stderr="")

        env = {k: v for k, v in os.environ.items() if k != "TREASURY_RH_MCP_TIMEOUT_S"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(rss, "SNAPSHOTS_DIR", self.snap_dir), mock.patch.object(
                rss.shutil, "which", return_value="/usr/bin/grok"
            ), mock.patch.object(rss.subprocess, "run", side_effect=fx):
                rss.refresh_via_local_mcp()
        self.assertEqual(seen["timeout"], 420.0)

    # ---- (3) auth classifier ---------------------------------------------

    def test_microsecond_401_never_classified_auth_fail(self) -> None:
        import random

        rng = random.Random(1266)
        for i in range(3000):
            digits = [str(rng.randrange(10)) for _ in range(6)]
            pos = rng.randrange(0, 4)
            digits[pos : pos + 3] = list("401")  # microseconds always contain 401
            micro = "".join(digits)
            self.assertIn("401", micro)
            tz = rng.choice(["+00:00", "Z", "-04:00"])
            stamp = (
                f"2026-{rng.randrange(1, 13):02d}-{rng.randrange(1, 29):02d}"
                f"T{rng.randrange(24):02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}"
                f".{micro}{tz}"
            )
            msg = rng.choice(
                [
                    f"RH refreshed as_of={stamp} accounts=2",
                    f"RH refreshed as_of={stamp}",
                    f"wrote snapshot at {stamp} (took {rng.randrange(100, 300)}.{micro}s)",
                    f"as_of={stamp} age=0.{micro}h",
                ]
            )
            self.assertNotEqual(rss.classify_rh_error(msg), "auth_fail", (i, msg))
            self.assertNotEqual(rss.classify_rh_error(msg.upper()), "auth_fail", (i, msg))

    def test_bare_or_embedded_401_is_not_auth(self) -> None:
        for msg in (
            "401",
            "wrote 401 rows",
            "port 8401 ready",
            "https://example.test/401",
            "http 4010",
            "id=a401b",
            "RH refreshed as_of=2026-10-14T02:47:12.163401+00:00",
        ):
            with self.subTest(msg):
                self.assertNotEqual(rss.classify_rh_error(msg), "auth_fail")

    def test_real_401_status_still_auth_fail(self) -> None:
        for msg in (
            "HTTP 401: Bad credentials",
            "http 401",
            "HTTP/1.1 401 Unauthorized",
            "HTTP/2 401",
            "http_401",
            "Request failed: HTTP:401",
            "401 Unauthorized",
            "got a 401 UNAUTHORIZED from the MCP",
            "status 401",
            "Status Code: 401",
            "status=401",
            "auth_fail",
            "MCP OAuth revoked",
        ):
            with self.subTest(msg):
                self.assertEqual(rss.classify_rh_error(msg), "auth_fail")
        # a real status next to a noisy timestamp still counts
        stamp = "2026-10-14T02:47:12.163401+00:00"
        self.assertEqual(
            rss.classify_rh_error(f"as_of={stamp} then HTTP 401 Unauthorized"), "auth_fail"
        )

    def test_other_classes_unchanged(self) -> None:
        self.assertEqual(rss.classify_rh_error("local_mcp_timeout:420.0s"), "timeout")
        self.assertEqual(rss.classify_rh_error("grok_exit_1"), "grok")
        self.assertEqual(rss.classify_rh_error("snapshot_missing_after_mcp"), "mcp")

    # ---- (3b) exit 0 + updated snapshot never auth_fail --------------------

    def test_exit0_updated_snapshot_noisy_stdout_is_ok(self) -> None:
        self._seed_old()
        new = _snap_payload(datetime.now(timezone.utc))
        noisy = (
            "RH refreshed as_of=2026-10-14T02:47:12.163401+00:00 accounts=2\n"
            "tool get_portfolio ok 401 ms; retry count 401\n"
        )
        out = self._run(self._writes_then_exits(json.dumps(new), rc=0, stdout=noisy))
        self.assertTrue(out["ok"])
        self.assertIsNone(out["error"])
        self.assertIsNone(out["error_class"])
        self.assertNotEqual(out.get("note"), "left_existing_snapshot")
        self.assertEqual(out["as_of"], new["as_of"])

    def test_exit0_updated_snapshot_auth_looking_stdout_is_ignored(self) -> None:
        self._seed_old()
        new = _snap_payload(datetime.now(timezone.utc))
        noisy = "log: oauth consent ok; token expired earlier but refreshed; HTTP 401 retry succeeded"
        self.assertEqual(rss.classify_rh_error(noisy), "auth_fail")  # sanity
        out = self._run(
            self._writes_then_exits(json.dumps(new), rc=0, stdout=noisy, stderr=noisy)
        )
        self.assertTrue(out["ok"])
        self.assertIsNone(out["error_class"])
        self.assertEqual(out["note"], "auth_like_output_ignored_snapshot_updated")

    def test_exit0_not_updated_with_real_401_is_still_auth_fail(self) -> None:
        before = self._seed_old()
        out = self._run(
            self._writes_then_exits("", rc=0, stdout="MCP error: HTTP 401 Unauthorized")
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "auth_fail")
        self.assertEqual(out["note"], "left_existing_snapshot")
        self.assertEqual(out["as_of"], before["as_of"])

    def test_exit0_not_updated_with_timestamp_noise_is_not_auth_fail(self) -> None:
        self._seed_old(hours_old=7.0)
        out = self._run(
            self._writes_then_exits(
                "", rc=0, stdout="RH refreshed as_of=2026-10-14T02:47:12.163401+00:00"
            )
        )
        self.assertFalse(out["ok"])  # snapshot really was not updated
        self.assertNotEqual(out["error_class"], "auth_fail")
        self.assertTrue(str(out["error"]).startswith("snapshot_not_updated"))

    def test_nonzero_exit_with_real_401_stays_failure_even_if_file_updated(self) -> None:
        self._seed_old()
        new = _snap_payload(datetime.now(timezone.utc))
        out = self._run(
            self._writes_then_exits(json.dumps(new), rc=1, stdout="HTTP 401 Unauthorized")
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_class"], "auth_fail")


def _fake_pi_settings() -> dict:
    return {
        "ssh": "prism-agent@192.0.2.10",
        "remote_root": "/home/prism-agent/personal-workspace",
        "connect_timeout_s": 5.0,
        "max_age_hours": 6.0,
        "mcp_timeout_s": 240.0,
        "enabled": True,
        "push_enabled": True,
        "push_files": list(rss.DEFAULT_PUSH_FILES),
    }


if __name__ == "__main__":
    unittest.main()
