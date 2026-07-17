"""Tests for recommendation generate / approve / reject."""

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
import recommendations as rec  # noqa: E402


class TestRecommendations(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="rec-")
        self.ws = Path(self._td.name) / "ws"
        self.ws.mkdir()
        (self.ws / "strategy").mkdir()
        (self.ws / "strategy" / "today.md").write_text(
            "# Today\n- [ ] **Ship a small capture automation** for notes\n- [x] done already\n",
            encoding="utf-8",
        )
        (self.ws / "ops" / "session-index").mkdir(parents=True)
        (self.ws / "ops" / "session-index" / "latest.json").write_text(
            json.dumps(
                {
                    "sessions": [
                        {"title": "Coinbase Treasury Bridge Experiment"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        # create product dirs so known_project_dirs works if used
        for name in ("treasury", "fitness"):
            (self.ws / name).mkdir()

        self._patches = [
            mock.patch.object(bl, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(bl, "BACKLOG_DIR", self.ws / "ops" / "backlog"),
            mock.patch.object(bl, "ITEMS_PATH", self.ws / "ops" / "backlog" / "items.json"),
            mock.patch.object(bl, "SEEDS_DIR", self.ws / "ops" / "backlog" / "seeds"),
            mock.patch.object(rec, "WORKSPACE_ROOT", self.ws),
            mock.patch.object(rec, "BACKLOG_DIR", self.ws / "ops" / "backlog"),
            mock.patch.object(
                rec, "SUGGESTIONS_PATH", self.ws / "ops" / "backlog" / "suggestions.json"
            ),
        ]
        for p in self._patches:
            p.start()

        bl.add_item(
            "Time allocator",
            description="Allocate time",
            priority="critical",
            status="idea",
            area="holistic",
        )
        bl.add_item(
            "Ready project",
            description="Go",
            priority="high",
            status="ready",
            mvp_scope="MVP one",
        )

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._td.cleanup()

    def test_generate_has_actions_and_new(self) -> None:
        out = rec.generate_recommendations()
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["count_pending"], 1)
        kinds = {s["kind"] for s in out["suggestions"]}
        self.assertIn("action", kinds)
        # today.md unchecked should yield new_item or action
        titles = " ".join(s["title"] for s in out["suggestions"])
        self.assertTrue(
            "initiate" in titles.lower()
            or "ready" in titles.lower()
            or "capture" in titles.lower()
            or "ship" in titles.lower()
        )

    def test_approve_new_item(self) -> None:
        rec.generate_recommendations()
        data = rec.load_suggestions()
        new = next(s for s in data["suggestions"] if s["kind"] == "new_item" and s["status"] == "pending")
        r = rec.approve_suggestion(new["id"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["suggestion"]["status"], "approved")
        self.assertTrue(r.get("backlog_item"))
        items = bl.list_items()
        self.assertTrue(any(i["id"] == r["backlog_item"]["id"] for i in items))

    def test_reject(self) -> None:
        rec.generate_recommendations()
        data = rec.load_suggestions()
        pend = next(s for s in data["suggestions"] if s["status"] == "pending")
        r = rec.reject_suggestion(pend["id"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["suggestion"]["status"], "rejected")

    def test_approve_action_updates_notes(self) -> None:
        rec.generate_recommendations()
        data = rec.load_suggestions()
        act = next(
            s
            for s in data["suggestions"]
            if s["kind"] == "action" and s.get("backlog_item_id") and s["status"] == "pending"
        )
        r = rec.approve_suggestion(act["id"])
        self.assertTrue(r["ok"], r)
        item = bl.get_item(act["backlog_item_id"])
        self.assertIsNotNone(item)
        self.assertTrue(item.get("notes"))


if __name__ == "__main__":
    unittest.main()
