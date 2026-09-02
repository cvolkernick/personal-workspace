#!/usr/bin/env python3
"""House-cap tests for youtube_groom policy (no network, no OAuth)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "youtube_groom.py"
CAPS_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_CAPS.md"
QUEUE_MD = ROOT.parent / "ops" / "YOUTUBE_QUEUE.md"


def _load():
    spec = importlib.util.spec_from_file_location("youtube_groom", MOD)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["youtube_groom"] = m
    spec.loader.exec_module(m)
    return m


M = _load()
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _item(vid: str, *, days_ago: float | None = 1) -> "M.PlaylistItem":
    added = None if days_ago is None else NOW - timedelta(days=days_ago)
    return M.PlaylistItem(video_id=vid, added_at=added, title=vid)


def _cand(vid: str, score: float = 1.0) -> "M.Candidate":
    return M.Candidate(video_id=vid, score=score, title=vid)


class TestNoPerTickInsertCeiling(unittest.TestCase):
    def test_constant_removed(self):
        self.assertFalse(hasattr(M, "MAX_INSERTS_PER_TICK"))
        self.assertIsNone(M.HOUSE_CAPS["MAX_INSERTS_PER_TICK"])
        self.assertEqual(M.OLD_MAX_INSERTS_PER_TICK, 8)
        self.assertEqual(M.OLD_MAX_INSERTS_PER_TICK_PRE_831, 4)

    def test_no_invented_daily_add_cap(self):
        self.assertIsNone(M.MAX_ADD_PER_DAY)
        self.assertIsNone(M.HOUSE_CAPS["MAX_ADD_PER_DAY"])

    def test_empty_after_prune_fills_to_house_target_in_one_tick(self):
        # Was clamped to 8. Now one tick may insert all the way to ~50.
        self.assertEqual(M.insert_budget(0), 50)
        self.assertEqual(M.slots_to_house_target(0), 50)

    def test_near_old_live_size_can_catch_up(self):
        # Live list sat ~21–25 because of the 8/tick clamp + 72h cull.
        self.assertEqual(M.insert_budget(21), 29)
        self.assertEqual(M.insert_budget(25), 25)
        self.assertGreater(M.insert_budget(21), M.OLD_MAX_INSERTS_PER_TICK)

    def test_old_eight_cap_would_have_blocked_catch_up(self):
        self.assertEqual(min(M.insert_budget(21), M.OLD_MAX_INSERTS_PER_TICK), 8)

    def test_at_house_target_adds_zero(self):
        self.assertEqual(M.insert_budget(50), 0)
        self.assertEqual(M.slots_to_house_target(50), 0)

    def test_above_house_target_below_cap_adds_zero(self):
        # CAP 200 is a breaker, not a fill target.
        self.assertEqual(M.insert_budget(60), 0)
        self.assertEqual(M.insert_budget(180), 0)

    def test_cap_breaker_stops_inserts(self):
        self.assertEqual(M.insert_budget(200, house_target=500), 0)
        self.assertEqual(M.insert_budget(199, house_target=500), 1)

    def test_playlist_remaining_slots_stop_inserts(self):
        self.assertEqual(
            M.insert_budget(0, playlist_len=4990, playlist_ceiling=5000),
            10,
        )


class TestHearted831Values(unittest.TestCase):
    def test_caps_match_hearted_values_except_inserts(self):
        self.assertEqual(M.FRESH_HOURS, 168)
        self.assertEqual(M.CAP, 200)
        self.assertEqual(M.STALE_HARD_DAYS, 7)
        self.assertEqual(M.MAX_DELETES_PER_TICK, 80)
        self.assertEqual(M.KEEP_N, 10)
        self.assertEqual(M.HOUSE_TARGET, 50)

    def test_playlist_id(self):
        self.assertEqual(M.PLAYLIST_ID, "PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At")

    def test_scorecard_before_after(self):
        card = M.scorecard()
        self.assertEqual(card["old"]["MAX_INSERTS_PER_TICK"], 8)
        self.assertIsNone(card["new"]["MAX_INSERTS_PER_TICK"])
        self.assertEqual(card["new"]["FRESH_HOURS"], 168)
        self.assertEqual(card["new"]["CAP"], 200)
        self.assertEqual(card["new"]["STALE_HARD_DAYS"], 7)
        self.assertTrue(card["cap_is_breaker"])
        self.assertFalse(card["house_target_is_youtube_5000"])
        self.assertFalse(card["copy_over_pi"])
        self.assertIsNone(card["quota_guard_in_nest"])
        self.assertEqual(card["youtube_ceiling"], 5000)
        self.assertEqual(card["pi_writer_path"], "~/.local/lib/youtube-groom/youtube_groom.py")

    def test_module_is_not_a_writer(self):
        src = MOD.read_text(encoding="utf-8")
        self.assertIn("DO NOT copy this module over the Pi binary", src)
        self.assertNotIn("build(", src)
        self.assertNotIn("googleapiclient", src)
        self.assertNotIn("InstalledAppFlow", src)


class TestPruneFirstThenFill(unittest.TestCase):
    def test_stale_is_seven_days(self):
        self.assertFalse(M.is_stale(NOW - timedelta(days=6, hours=23), now=NOW))
        self.assertTrue(M.is_stale(NOW - timedelta(days=7), now=NOW))

    def test_plan_prunes_then_adds_past_old_eight(self):
        items = [
            _item("keep-a", days_ago=1),
            _item("dup", days_ago=1),
            _item("dup", days_ago=1),
            _item("old", days_ago=8),
        ]
        cands = [_cand(f"n{i}", 100 - i) for i in range(20)]
        plan = M.plan_groom(items, cands, now=NOW)
        self.assertEqual(plan.remove_stale, ("old",))
        self.assertEqual(plan.remove_dup, ("dup",))
        self.assertEqual(plan.after_prune, 2)
        self.assertEqual(plan.add_budget, 48)
        self.assertEqual(len(plan.add), 20)
        self.assertGreater(len(plan.add), M.OLD_MAX_INSERTS_PER_TICK)
        self.assertNotIn("keep-a", plan.add)

    def test_never_readd_blocked(self):
        plan = M.plan_groom(
            [_item("keep-a", days_ago=1)],
            [_cand("blocked", 10), _cand("ok", 1)],
            now=NOW,
            never_readd=["blocked"],
        )
        self.assertEqual(plan.add, ("ok",))

    def test_fresh_25_fills_to_50_not_plus_8(self):
        fresh = [_item(f"v{i}", days_ago=1) for i in range(25)]
        cands = [_cand(f"n{i}", 50 - i) for i in range(40)]
        plan = M.plan_groom(fresh, cands, now=NOW)
        self.assertEqual(plan.remove_stale, ())
        self.assertEqual(plan.add_budget, 25)
        self.assertEqual(len(plan.add), 25)


class TestDocsMatchPolicy(unittest.TestCase):
    def test_caps_doc_exists_and_drops_insert_ceiling(self):
        text = CAPS_MD.read_text(encoding="utf-8")
        self.assertIn("FRESH_HOURS          = 168", text)
        self.assertIn("CAP                  = 200", text)
        self.assertIn("STALE_HARD_DAYS      = 7", text)
        self.assertIn("MAX_INSERTS_PER_TICK", text)
        self.assertIn("removed", text.lower())
        self.assertNotRegex(text, r"MAX_INSERTS_PER_TICK\s*=\s*\d+")

    def test_queue_doc_before_after(self):
        text = QUEUE_MD.read_text(encoding="utf-8")
        self.assertIn("MAX_INSERTS_PER_TICK", text)
        self.assertIn("| **8** (4 before 8/31) | **removed**", text)
        self.assertIn("FRESH_HOURS", text)
        self.assertIn("168", text)
        self.assertIn("CAP", text)
        self.assertIn("200", text)
        self.assertIn("Do not invent `MAX_ADD_PER_DAY`", text)


class TestMainNoNetwork(unittest.TestCase):
    def test_main_prints_before_after(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = M.main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("8 → removed", out)
        self.assertIn(M.PLAYLIST_ID, out)
        self.assertIn("do not copy over Pi writer", out)


if __name__ == "__main__":
    unittest.main()
