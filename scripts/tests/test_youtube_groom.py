#!/usr/bin/env python3
"""House-cap tests for youtube_groom (no network, no OAuth)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "youtube_groom.py"


def _load():
    spec = importlib.util.spec_from_file_location("youtube_groom", MOD)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["youtube_groom"] = m
    spec.loader.exec_module(m)
    return m


M = _load()
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _item(vid: str, *, days_ago: float | None = 1) -> "M.PlaylistItem":
    added = None if days_ago is None else NOW - timedelta(days=days_ago)
    return M.PlaylistItem(video_id=vid, added_at=added, title=vid)


def _cand(vid: str, score: float = 1.0) -> "M.Candidate":
    return M.Candidate(video_id=vid, score=score, title=vid)


class TestHouseCapsScorecard(unittest.TestCase):
    def test_new_caps(self):
        self.assertEqual(M.TARGET_SIZE, 50)
        self.assertEqual(M.MAX_PLAYLIST_SIZE, 50)
        self.assertEqual(M.ADD_PER_RUN, 8)
        self.assertEqual(M.STALE_DAYS, 7)
        self.assertIsNone(M.MIN_SCORE)

    def test_old_caps_documented(self):
        self.assertEqual(M.OLD_TARGET_SIZE, 25)
        self.assertEqual(M.OLD_MAX_PLAYLIST_SIZE, 25)
        self.assertEqual(M.OLD_ADD_PER_RUN, 4)
        self.assertEqual(M.OLD_STALE_DAYS, 7)

    def test_playlist_id(self):
        self.assertEqual(M.PLAYLIST_ID, "PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At")

    def test_scorecard_old_vs_new(self):
        card = M.scorecard()
        self.assertEqual(card["old"]["TARGET_SIZE"], 25)
        self.assertEqual(card["old"]["ADD_PER_RUN"], 4)
        self.assertEqual(card["new"]["TARGET_SIZE"], 50)
        self.assertEqual(card["new"]["MAX_PLAYLIST_SIZE"], 50)
        self.assertEqual(card["new"]["ADD_PER_RUN"], 8)
        self.assertEqual(card["new"]["STALE_DAYS"], 7)
        self.assertFalse(card["score_cutoff_caps_size"])
        self.assertFalse(card["stale_prune_is_size_cap"])
        self.assertEqual(card["youtube_ceiling"], 5000)
        self.assertEqual(card["nest_path"], "scripts/youtube_groom.py")
        self.assertEqual(card["pi_copy_path"], "~/.local/lib/youtube-groom/youtube_groom.py")

    def test_raised_from_under_50_not_100(self):
        """Current target was 25 (<50), so new target is 50, not 100."""
        self.assertLess(M.OLD_TARGET_SIZE, 50)
        self.assertEqual(M.TARGET_SIZE, 50)


class TestAddBudgetAndSlots(unittest.TestCase):
    def test_near_old_target_can_grow(self):
        # Live ticks listed ~21–25; under the new target we still have room.
        self.assertEqual(M.slots_to_fill(21), 29)
        self.assertEqual(M.add_budget(21), 8)
        self.assertEqual(M.add_budget(25), 8)

    def test_old_add_cap_would_have_been_4(self):
        self.assertEqual(M.add_budget(21, per_run=M.OLD_ADD_PER_RUN), 4)

    def test_full_target_adds_zero(self):
        self.assertEqual(M.slots_to_fill(50), 0)
        self.assertEqual(M.add_budget(50), 0)

    def test_over_target_adds_zero(self):
        self.assertEqual(M.add_budget(60), 0)

    def test_two_slots_left_uses_slots_not_full_run(self):
        self.assertEqual(M.add_budget(48), 2)

    def test_empty_playlist_capped_by_per_run(self):
        self.assertEqual(M.add_budget(0), 8)


class TestStaleAndDupPrune(unittest.TestCase):
    def test_stale_is_seven_days(self):
        self.assertFalse(M.is_stale(NOW - timedelta(days=6, hours=23), now=NOW))
        self.assertTrue(M.is_stale(NOW - timedelta(days=7), now=NOW))

    def test_missing_added_at_is_not_stale(self):
        self.assertFalse(M.is_stale(None, now=NOW))

    def test_plan_prunes_stale_and_dups_then_adds(self):
        items = [
            _item("keep-a", days_ago=1),
            _item("dup", days_ago=1),
            _item("dup", days_ago=1),
            _item("old", days_ago=8),
        ]
        cands = [_cand("new-1", 9), _cand("new-2", 8), _cand("keep-a", 99)]
        plan = M.plan_groom(items, cands, now=NOW)
        self.assertEqual(plan.remove_stale, ("old",))
        self.assertEqual(plan.remove_dup, ("dup",))
        self.assertEqual(plan.after_prune, 2)
        self.assertEqual(plan.add, ("new-1", "new-2"))
        self.assertNotIn("keep-a", plan.add)

    def test_never_readd_blocked(self):
        plan = M.plan_groom(
            [_item("keep-a", days_ago=1)],
            [_cand("blocked", 10), _cand("ok", 1)],
            now=NOW,
            never_readd=["blocked"],
        )
        self.assertEqual(plan.add, ("ok",))

    def test_stale_prune_does_not_hold_size_at_25(self):
        """Seven-day prune is not why the playlist stayed ~25."""
        fresh = [_item(f"v{i}", days_ago=1) for i in range(25)]
        cands = [_cand(f"n{i}", 10 - i) for i in range(12)]
        plan = M.plan_groom(fresh, cands, now=NOW)
        self.assertEqual(plan.remove_stale, ())
        self.assertEqual(plan.add_budget, 8)
        self.assertEqual(len(plan.add), 8)


class TestScoreIsNotASizeCap(unittest.TestCase):
    def test_none_cutoff_keeps_low_scores(self):
        items = [_item("keep-a", days_ago=1)]
        cands = [_cand("low", 0.0), _cand("neg", -1.0)]
        plan = M.plan_groom(items, cands, now=NOW, min_score=None)
        self.assertEqual(plan.add, ("low", "neg"))

    def test_explicit_cutoff_is_quality_only(self):
        items = [_item("keep-a", days_ago=1)]
        cands = [_cand("hi", 5), _cand("lo", 0.1)]
        plan = M.plan_groom(items, cands, now=NOW, min_score=1.0)
        self.assertEqual(plan.add, ("hi",))


class TestMainNoNetwork(unittest.TestCase):
    def test_main_prints_old_vs_new(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = M.main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("25 → 50", out)
        self.assertIn("4 → 8", out)
        self.assertIn(M.PLAYLIST_ID, out)


if __name__ == "__main__":
    unittest.main()
