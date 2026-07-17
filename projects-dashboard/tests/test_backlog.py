"""Tests for backlog + goal initiate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import backlog as bl  # noqa: E402


class TestBacklog(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="bl-")
        self.root = Path(self._td.name)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        # Point module paths at temp workspace
        self._patches = [
            mock.patch.object(bl, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(bl, "BACKLOG_DIR", self.ws / "ops" / "backlog"),
            mock.patch.object(bl, "ITEMS_PATH", self.ws / "ops" / "backlog" / "items.json"),
            mock.patch.object(bl, "SEEDS_DIR", self.ws / "ops" / "backlog" / "seeds"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._td.cleanup()

    def test_add_list_update(self) -> None:
        r = bl.add_item(
            "Voice notes to initiatives",
            description="Capture voice → structured MD",
            priority="high",
            area="initiatives",
            mvp_scope="CLI that writes one initiative file",
        )
        self.assertTrue(r["ok"])
        items = bl.list_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["priority"], "high")
        iid = items[0]["id"]
        u = bl.update_item(iid, {"status": "ready"})
        self.assertEqual(u["item"]["status"], "ready")

    def test_initiate_writes_seed(self) -> None:
        r = bl.add_item("Tiny bot", description="Do a thing", mvp_scope="hello world script")
        iid = r["item"]["id"]
        out = bl.initiate_item(iid, try_spawn_grok=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["item"]["status"], "planning")
        seed = self.ws / out["seed_path"]
        self.assertTrue(seed.is_file())
        text = seed.read_text(encoding="utf-8")
        self.assertIn("MVP", text)
        self.assertIn("/goal", text)
        self.assertIn("Tiny bot", out["goal_objective"])
        launch = self.ws / out["launch_script"]
        self.assertTrue(launch.is_file())
        obj = self.ws / out["objective_path"]
        self.assertTrue(obj.is_file())

    def test_delete(self) -> None:
        r = bl.add_item("Temp")
        bl.delete_item(r["item"]["id"])
        self.assertEqual(bl.list_items(), [])

    def test_payload(self) -> None:
        bl.add_item("A")
        p = bl.backlog_payload()
        self.assertTrue(p["ok"])
        self.assertEqual(p["count"], 1)


if __name__ == "__main__":
    unittest.main()
