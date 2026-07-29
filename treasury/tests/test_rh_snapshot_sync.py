"""Unit tests for Pi-first RH snapshot sync (no network)."""

from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
