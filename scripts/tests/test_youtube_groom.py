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
IMPACT_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_IMPACT_CHECK.md"
IMPACT_JSON = ROOT.parent / "ops" / "youtube_groom_impact_check_baseline.json"


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
        # Was clamped to 8. Now one tick may insert all the way to HOUSE_TARGET.
        self.assertEqual(M.insert_budget(0), 250)
        self.assertEqual(M.slots_to_house_target(0), 250)

    def test_near_old_live_size_can_catch_up(self):
        # Live list sat ~21–25 because of the 8/tick clamp + 72h cull.
        self.assertEqual(M.insert_budget(21), 229)
        self.assertEqual(M.insert_budget(25), 225)
        self.assertGreater(M.insert_budget(21), M.OLD_MAX_INSERTS_PER_TICK)

    def test_old_eight_cap_would_have_blocked_catch_up(self):
        self.assertEqual(min(M.insert_budget(21), M.OLD_MAX_INSERTS_PER_TICK), 8)

    def test_at_house_target_adds_zero(self):
        self.assertEqual(M.insert_budget(250), 0)
        self.assertEqual(M.slots_to_house_target(250), 0)
        self.assertEqual(M.insert_budget(100), 150)

    def test_old_house_50_now_catches_up(self):
        self.assertEqual(M.insert_budget(50), 200)
        self.assertEqual(M.slots_to_house_target(50), 200)

    def test_cap_is_the_target_so_nothing_inserts_past_250(self):
        self.assertEqual(M.insert_budget(249), 1)
        self.assertEqual(M.insert_budget(250), 0)
        self.assertEqual(M.insert_budget(251), 0)

    def test_cap_breaker_stops_inserts(self):
        self.assertEqual(M.insert_budget(250, house_target=500), 0)
        self.assertEqual(M.insert_budget(249, house_target=500), 1)

    def test_playlist_remaining_slots_stop_inserts(self):
        self.assertEqual(
            M.insert_budget(0, playlist_len=4990, playlist_ceiling=5000),
            10,
        )


class TestHearted831Values(unittest.TestCase):
    def test_caps_match_hearted_values_except_inserts(self):
        self.assertEqual(M.FRESH_HOURS, 720)
        self.assertEqual(M.PREV_FRESH_HOURS, 168)
        self.assertEqual(M.CAP, 250)
        self.assertEqual(M.PREV_CAP, 200)
        self.assertEqual(M.STALE_HARD_DAYS, 30)
        self.assertEqual(M.PREV_STALE_HARD_DAYS, 7)
        self.assertEqual(M.MAX_DELETES_PER_TICK, 80)
        self.assertEqual(M.KEEP_N, 10)
        self.assertEqual(M.HOUSE_TARGET, 250)
        self.assertEqual(M.PREV_HOUSE_TARGET, 100)
        self.assertEqual(M.OLD_HOUSE_TARGET, 50)
        self.assertEqual(M.MIN_FIT, 0)
        self.assertEqual(M.OLD_MIN_FIT, 1)
        self.assertEqual(M.ORIG_MIN_FIT, 2)
        self.assertEqual(M.SEED_THROTTLE_WEIGHT_FLOOR, 0.0)
        self.assertEqual(M.PREV_SEED_THROTTLE_WEIGHT_FLOOR, 0.10)
        self.assertEqual(M.OLD_SEED_THROTTLE_WEIGHT_FLOOR, 0.25)
        self.assertEqual(M.ORIG_SEED_THROTTLE_WEIGHT_FLOOR, 0.4)
        self.assertEqual(M.WEIGHT_FLOOR, 0.05)
        self.assertEqual(M.PREV_WEIGHT_FLOOR, 0.15)
        self.assertEqual(M.SEED_UPLOADS_PER_CHANNEL, 50)
        self.assertEqual(M.CLIMB_INSERTS_PER_DAY, 40)
        self.assertEqual(M.BAND_LOW, 235)
        self.assertEqual(M.BAND_HIGH, 250)
        self.assertIsNone(M.MAX_ADD_PER_DAY)

    def test_playlist_id(self):
        self.assertEqual(M.PLAYLIST_ID, "PLHS8knJRXDexbFZmFI6iBjoW8iSdpc9At")

    def test_scorecard_before_after(self):
        card = M.scorecard()
        self.assertEqual(card["old"]["MAX_INSERTS_PER_TICK"], 8)
        self.assertIsNone(card["new"]["MAX_INSERTS_PER_TICK"])
        self.assertEqual(card["new"]["FRESH_HOURS"], 720)
        self.assertEqual(card["new"]["CAP"], 250)
        self.assertEqual(card["new"]["STALE_HARD_DAYS"], 30)
        self.assertEqual(card["old"]["MIN_FIT"], 1)
        self.assertEqual(card["new"]["MIN_FIT"], 0)
        self.assertEqual(card["old"]["SEED_THROTTLE_WEIGHT_FLOOR"], 0.25)
        self.assertEqual(card["previous"]["SEED_THROTTLE_WEIGHT_FLOOR"], 0.10)
        self.assertEqual(card["new"]["SEED_THROTTLE_WEIGHT_FLOOR"], 0.0)
        self.assertEqual(card["old"]["HOUSE_TARGET"], 50)
        self.assertEqual(card["previous"]["HOUSE_TARGET"], 100)
        self.assertEqual(card["previous"]["CAP"], 200)
        self.assertEqual(card["previous"]["BAND_LOW"], 90)
        self.assertEqual(card["previous"]["BAND_HIGH"], 110)
        self.assertEqual(card["new"]["HOUSE_TARGET"], 250)
        self.assertEqual(card["new"]["HOUSE_TARGET_TOLERANCE"], 15)
        self.assertEqual(card["new"]["BAND_LOW"], 235)
        self.assertEqual(card["new"]["BAND_HIGH"], 250)
        self.assertEqual(card["new"]["CLIMB_INSERTS_PER_DAY"], 40)
        self.assertEqual(card["new"]["SEED_UPLOADS_PER_CHANNEL"], 50)
        self.assertEqual(card["control_loop"]["issue"], 852)
        self.assertEqual(card["control_loop"]["rebased_by"], 957)
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
    def test_stale_is_thirty_days(self):
        self.assertFalse(M.is_stale(NOW - timedelta(days=29, hours=23), now=NOW))
        self.assertTrue(M.is_stale(NOW - timedelta(days=30), now=NOW))

    def test_plan_prunes_then_adds_past_old_eight(self):
        items = [
            _item("keep-a", days_ago=1),
            _item("dup", days_ago=1),
            _item("dup", days_ago=1),
            _item("old", days_ago=31),
        ]
        cands = [_cand(f"n{i}", 100 - i) for i in range(20)]
        plan = M.plan_groom(items, cands, now=NOW)
        self.assertEqual(plan.remove_stale, ("old",))
        self.assertEqual(plan.remove_dup, ("dup",))
        self.assertEqual(plan.after_prune, 2)
        self.assertEqual(plan.add_budget, 248)
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

    def test_fresh_25_fills_to_100_not_plus_8(self):
        fresh = [_item(f"v{i}", days_ago=1) for i in range(25)]
        cands = [_cand(f"n{i}", 50 - i) for i in range(40)]
        plan = M.plan_groom(fresh, cands, now=NOW)
        self.assertEqual(plan.remove_stale, ())
        self.assertEqual(plan.add_budget, 225)
        self.assertEqual(len(plan.add), 40)


class TestAddPathFloors815(unittest.TestCase):
    def test_fit_zero_is_kept(self):
        self.assertIsNone(
            M.skip_add_reason(
                fit=0, channel_weight=1.0, is_seed_throttle=False, duration_sec=600
            )
        )
        self.assertIsNone(
            M.skip_add_reason(
                fit=1, channel_weight=1.0, is_seed_throttle=False, duration_sec=600
            )
        )
        self.assertIsNone(
            M.skip_add_reason(
                fit=2, channel_weight=1.0, is_seed_throttle=False, duration_sec=600
            )
        )

    def test_throttle_floor_off_at_baseline(self):
        # #815 skipped only below 0.10. #957 turns the baseline floor off.
        self.assertIsNone(
            M.skip_add_reason(
                fit=3, channel_weight=0.09, is_seed_throttle=True, duration_sec=600
            )
        )
        self.assertEqual(
            M.skip_add_reason(
                fit=3,
                channel_weight=0.09,
                is_seed_throttle=True,
                throttle_floor=0.10,
                duration_sec=600,
            ),
            "throttled-decay",
        )
        self.assertIsNone(
            M.skip_add_reason(
                fit=3, channel_weight=0.343, is_seed_throttle=True, duration_sec=600
            )
        )

    def test_longform_skip_reasons(self):
        self.assertEqual(M.MIN_LONGFORM_SEC, 60)
        self.assertEqual(M.SHORTS_MAX_SEC, 180)
        self.assertEqual(M.HOUSE_CAPS["MIN_LONGFORM_SEC"], 60)
        self.assertEqual(M.HOUSE_CAPS["SHORTS_MAX_SEC"], 180)
        self.assertEqual(
            M.skip_add_reason(
                fit=3, channel_weight=1.0, is_seed_throttle=False, duration_sec=45
            ),
            "short<60s",
        )
        self.assertEqual(
            M.skip_add_reason(
                fit=3, channel_weight=1.0, is_seed_throttle=False, duration_sec=59
            ),
            "short<60s",
        )
        self.assertEqual(
            M.skip_add_reason(
                fit=3,
                channel_weight=1.0,
                is_seed_throttle=False,
                duration_sec=90,
                is_short=True,
            ),
            "short-flagged",
        )
        self.assertEqual(
            M.skip_add_reason(
                fit=3, channel_weight=1.0, is_seed_throttle=False, duration_sec=None
            ),
            "duration-unknown",
        )
        self.assertIsNone(
            M.skip_add_reason(
                fit=3, channel_weight=1.0, is_seed_throttle=False, duration_sec=60
            )
        )


class TestDocsMatchPolicy(unittest.TestCase):
    def test_caps_doc_exists_and_drops_insert_ceiling(self):
        text = CAPS_MD.read_text(encoding="utf-8")
        self.assertIn("FRESH_HOURS          = 168", text)
        self.assertIn("CAP                  = 200", text)
        self.assertIn("STALE_HARD_DAYS      = 7", text)
        self.assertIn("HOUSE_TARGET         = 100", text)
        self.assertIn("HOUSE_TARGET_TOLERANCE", text)
        self.assertIn("MIN_FIT              = 0", text)
        self.assertIn("SEED_THROTTLE_WEIGHT_FLOOR = 0.10", text)
        self.assertIn("HOUSE_TARGET         = 250", text)
        self.assertIn("CAP                  = 250", text)
        self.assertIn("STALE_HARD_DAYS      = 30", text)
        self.assertIn("FRESH_HOURS          = 720", text)
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
        self.assertIn("MIN_FIT", text)
        self.assertIn("SEED_THROTTLE_WEIGHT_FLOOR", text)
        self.assertIn("0.25", text)
        self.assertIn("0.10", text)
        self.assertIn("thesis-fit skip disabled", text)
        self.assertIn("HOUSE_TARGET", text)
        self.assertIn("**100**", text)
        self.assertIn("#852", text)
        self.assertIn("youtube-groom-impact-check", text)


class TestImpactCheckBaseline815(unittest.TestCase):
    def test_baseline_json_matches_live_knobs(self):
        import json

        data = json.loads(IMPACT_JSON.read_text(encoding="utf-8"))
        self.assertEqual(data["check_id"], "youtube-groom-impact-check")
        self.assertEqual(data["superseded"]["add"], 8)
        self.assertEqual(data["superseded"]["skip"], {})
        self.assertEqual(data["superseded"]["MIN_FIT"], 1)
        self.assertEqual(data["superseded"]["SEED_THROTTLE_WEIGHT_FLOOR"], 0.25)
        base = data["baseline"]
        self.assertEqual(base["MIN_FIT"], M.MIN_FIT)
        # #815 measurement. #957 moved the live knobs; this file stays the t0.
        self.assertEqual(base["SEED_THROTTLE_WEIGHT_FLOOR"], 0.10)
        self.assertEqual(base["HOUSE_TARGET"], 100)
        self.assertEqual(base["CAP"], 200)
        self.assertEqual(M.HOUSE_TARGET, 250)
        self.assertEqual(M.CAP, 250)
        self.assertEqual(base["add"], 1)
        self.assertEqual(base["skip"], {})
        self.assertFalse(data["copy_over_pi"])

    def test_impact_doc_names_old_and_new(self):
        text = IMPACT_MD.read_text(encoding="utf-8")
        self.assertIn("add=8", text)
        self.assertIn("MIN_FIT` | `0`", text)
        self.assertIn("0.10", text)
        self.assertIn("2026-09-19", text)
        self.assertIn("do not use", text.lower())


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
        self.assertIn("MIN_FIT", out)
        self.assertIn("SEED_THROTTLE_WEIGHT_FLOOR", out)
        self.assertIn(M.PLAYLIST_ID, out)
        self.assertIn("do not copy over Pi writer", out)


if __name__ == "__main__":
    unittest.main()
