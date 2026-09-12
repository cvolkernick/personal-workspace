"""Unit tests for Pi-first RH snapshot sync (no network)."""

from __future__ import annotations

import json
import os
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
            rss, "_this_host", return_value="prism-gateway"
        ), mock.patch.object(rss, "_sot_host", return_value="prism"):
            self.assertEqual(rss._rh_role(), "consumer")

    def test_notify_skips_no_refresh_path(self) -> None:
        status = {
            "ok": False,
            "role": "producer",
            "this_host": "prism-gateway",
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
            "this_host": "prism-gateway",
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
        files = ["robinhood_latest.json", "solana_latest.json"]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TREASURY_RH_PUSH", None)
            out = rss._strip_rh_push(files)
        self.assertNotIn("robinhood_latest.json", out)
        self.assertIn("solana_latest.json", out)

    def test_strip_rh_push_explicit_override(self) -> None:
        files = ["robinhood_latest.json", "solana_latest.json"]
        with mock.patch.dict(os.environ, {"TREASURY_RH_PUSH": "1"}):
            out = rss._strip_rh_push(files)
        self.assertIn("robinhood_latest.json", out)

    def test_strip_braiins_push_default_no_dual_write(self) -> None:
        files = ["braiins_latest.json", "solana_latest.json"]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TREASURY_BRAIINS_PUSH", None)
            out = rss._strip_rh_push(files)
        self.assertNotIn("braiins_latest.json", out)
        self.assertIn("solana_latest.json", out)

    def test_strip_braiins_push_explicit_override(self) -> None:
        files = ["braiins_latest.json", "solana_latest.json"]
        with mock.patch.dict(os.environ, {"TREASURY_BRAIINS_PUSH": "1"}):
            out = rss._strip_rh_push(files)
        self.assertIn("braiins_latest.json", out)

    def test_strip_coinbase_push_default_no_dual_write(self) -> None:
        files = ["coinbase_latest.json", "solana_latest.json"]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TREASURY_COINBASE_PUSH", None)
            out = rss._strip_rh_push(files)
        self.assertNotIn("coinbase_latest.json", out)
        self.assertIn("solana_latest.json", out)

    def test_strip_coinbase_push_explicit_override(self) -> None:
        files = ["coinbase_latest.json", "solana_latest.json"]
        with mock.patch.dict(os.environ, {"TREASURY_COINBASE_PUSH": "1"}):
            out = rss._strip_rh_push(files)
        self.assertIn("coinbase_latest.json", out)

    def test_default_push_files_omit_pi_sot(self) -> None:
        self.assertNotIn("robinhood_latest.json", rss.DEFAULT_PUSH_FILES)
        self.assertNotIn("braiins_latest.json", rss.DEFAULT_PUSH_FILES)
        self.assertNotIn("coinbase_latest.json", rss.DEFAULT_PUSH_FILES)

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
        self.assertIn("auth_fail", out.get("title") or "")
        self.assertIn("prism", out.get("title") or "")


def _fake_pi_settings() -> dict:
    return {
        "ssh": "prism-agent@192.168.100.98",
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
