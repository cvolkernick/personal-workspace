"""Tests for backlog scoring, schedule, and groom."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))

import backlog as bl  # noqa: E402
import backlog_groom as groom  # noqa: E402


class TestBacklogGroom(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="blg-")
        self.ws = Path(self._td.name)
        self._patches = [
            mock.patch.object(bl, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(bl, "BACKLOG_DIR", self.ws / "ops" / "backlog"),
            mock.patch.object(bl, "ITEMS_PATH", self.ws / "ops" / "backlog" / "items.json"),
            mock.patch.object(bl, "SEEDS_DIR", self.ws / "ops" / "backlog" / "seeds"),
        ]
        for p in self._patches:
            p.start()
        bl.add_item(
            "Critical ready",
            priority="critical",
            status="ready",
            mvp_scope="MVP A",
            notes="Start soon",
            area="core",
        )
        bl.add_item(
            "Vague idea",
            priority="low",
            status="idea",
            description="no mvp yet",
        )
        bl.add_item(
            "Well specified idea",
            priority="medium",
            status="idea",
            mvp_scope="ship X",
            notes="clarify done",
            area="tools",
        )

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._td.cleanup()

    def test_rank_press_order(self) -> None:
        items = bl.list_items(ranked=True)
        self.assertGreaterEqual(len(items), 3)
        ranks = [i.get("press_rank") for i in items if i.get("press_rank")]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        scores = [i.get("score") or 0 for i in items if i.get("status") != "done"]
        self.assertEqual(scores, sorted(scores, reverse=True))
        top = items[0]
        self.assertEqual(top.get("rank_label"), "Do first")
        self.assertIn(top.get("priority_color"), groom._PRIORITY_COLORS.values())
        self.assertIn(top.get("schedule_slot"), ("now", "this_week", "next_week", "later", "parked"))

    def test_groom_applies_ready_hygiene(self) -> None:
        out = groom.groom_backlog(apply=True)
        self.assertTrue(out["ok"])
        items = {i["title"]: i for i in bl.list_items(include_done=True, ranked=False)}
        # well-specified idea should become ready
        self.assertEqual(items["Well specified idea"]["status"], "ready")
        self.assertTrue(out.get("by_schedule"))

    def test_payload_has_schedule(self) -> None:
        p = bl.backlog_payload()
        self.assertTrue(p["ranked"])
        self.assertIn("by_schedule", p)
        self.assertIn("how_to_groom", p)


if __name__ == "__main__":
    unittest.main()
