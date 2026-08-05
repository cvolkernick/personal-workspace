"""Tests for workflow backlog → time allocator bridge."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DASH))

import backlog as bl  # noqa: E402
import bridge  # noqa: E402
from holistic.time_allocator.store import load_state, resolve_data_path  # noqa: E402


class TestBridge(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="bridge-")
        self.ws = Path(self._td.name) / "ws"
        self.ws.mkdir()
        self.data = self.ws / "holistic" / "data" / "tasks.json"
        self.data.parent.mkdir(parents=True)
        self.data.write_text(
            json.dumps({"version": 2, "items": [], "targets": [], "logs": [], "plan": None}),
            encoding="utf-8",
        )
        self.backlog_dir = self.ws / "ops" / "backlog"
        self.backlog_dir.mkdir(parents=True)

        self._patches = [
            mock.patch.object(bl, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(bl, "BACKLOG_DIR", self.backlog_dir),
            mock.patch.object(bl, "ITEMS_PATH", self.backlog_dir / "items.json"),
            mock.patch.object(bl, "SEEDS_DIR", self.backlog_dir / "seeds"),
            mock.patch.object(bridge, "WORKSPACE_ROOT", self.ws),
            mock.patch.dict("os.environ", {"TIME_ALLOCATOR_DATA": str(self.data)}),
        ]
        for p in self._patches:
            p.start()

        r = bl.add_item(
            "Ship bridge",
            description="Connect macro to day",
            priority="high",
            status="ready",
            mvp_scope="one day task",
            notes="do it",
            area="projects-dashboard",
        )
        self.bid = r["item"]["id"]
        # stamp rank-like fields for candidate filter
        data = bl.load_backlog()
        for it in data["items"]:
            if it["id"] == self.bid:
                it["press_rank"] = 1
                it["schedule_slot"] = "now"
                it["schedule_label"] = "Do now"
        bl.save_backlog(data)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._td.cleanup()

    def test_send_creates_linked_task(self) -> None:
        out = bridge.send_backlog_to_allocator(self.bid, rebuild_plan=False)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["created"])
        item = out["item"]
        self.assertEqual(item.get("backlog_id"), self.bid)
        self.assertEqual(item.get("source"), "workflow-backlog")
        self.assertGreaterEqual(int(item.get("priority") or 0), 7)
        # idempotent
        out2 = bridge.send_backlog_to_allocator(self.bid)
        self.assertTrue(out2["ok"])
        self.assertTrue(out2.get("already_linked"))
        state = load_state()
        linked = [i for i in state["items"] if i.get("backlog_id") == self.bid]
        self.assertEqual(len(linked), 1)

    def test_status_lists_candidates(self) -> None:
        st = bridge.list_bridge_status()
        self.assertTrue(st["ok"])
        ids = [c["backlog_id"] for c in st["candidates"]]
        self.assertIn(self.bid, ids)


if __name__ == "__main__":
    unittest.main()
