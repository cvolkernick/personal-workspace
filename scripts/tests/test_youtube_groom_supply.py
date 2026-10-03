#!/usr/bin/env python3
"""#957 supply-lane tests (no network, no OAuth, no live writer)."""

from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPLY = ROOT / "youtube_groom_supply.py"
POLICY = ROOT / "youtube_groom.py"

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


S = _load(SUPPLY, "youtube_groom_supply")
P = _load(POLICY, "youtube_groom_policy_for_supply")


class _Quota:
    def __init__(self, remaining: int) -> None:
        self.remaining = remaining
        self.kind_cost = {"search": 100, "subscriptions": 1, "list": 1, "insert": 50}

    def can(self, kind: str, n: int = 1) -> bool:
        return self.remaining >= self.kind_cost[kind] * n

    def charge(self, kind: str, n: int = 1) -> None:
        self.remaining -= self.kind_cost[kind] * n


class _FakeYT:
    def __init__(self, quota: _Quota) -> None:
        self.quota = quota
        self.searches: list[str] = []
        self.video_calls: list[list[str]] = []

    def search_videos(self, query, *, order="date", published_after=None, max_results=10):
        del order, published_after, max_results
        if not self.quota.can("search"):
            raise RuntimeError("quota")
        self.quota.charge("search")
        self.searches.append(query)
        return [
            {
                "id": {"videoId": f"d-{len(self.searches)}"},
                "snippet": {
                    "title": "Bitcoin mining and the power grid",
                    "description": "hashrate and energy",
                    "channelId": "UCdiscovered",
                    "channelTitle": "Discovered Desk",
                    "publishedAt": "2026-09-28T12:00:00Z",
                },
            }
        ]

    def subscription_channel_ids(self, max_pages: int = 4):
        del max_pages
        self.quota.charge("subscriptions")
        return {"UCsub"}

    def videos(self, video_ids):
        self.video_calls.append(list(video_ids))
        if hasattr(self, "quota"):
            self.quota.charge("list")
        out = {}
        for vid in video_ids:
            out[vid] = {
                "id": vid,
                "contentDetails": {"duration": "PT10M"},
                "snippet": {"title": "Long form", "description": "interview", "tags": []},
            }
        return out

    def uploads(self, channel_id: str, max_results: int = 15):
        del max_results
        self.quota.charge("list")
        return [
            {
                "contentDetails": {"videoId": f"up-{channel_id}"},
                "snippet": {
                    "title": "Humanoid robots and embodied AI",
                    "description": "actuator",
                    "publishedAt": "2026-09-29T12:00:00Z",
                    "channelTitle": channel_id,
                },
            }
        ]


def _row(score: int, vid: str, channel: str, lane: str):
    return (
        score,
        {"video_id": vid, "title": vid, "channel_id": channel, "lane": lane, "published_at": "2026-09-29T00:00:00Z"},
        {"fit": 2},
    )


class TestPolicyMatch(unittest.TestCase):
    def test_scorecard_and_supply_agree(self):
        self.assertEqual(S.HOUSE_TARGET, P.HOUSE_TARGET)
        self.assertEqual(S.CAP, P.CAP)
        self.assertEqual(S.BAND_LOW, P.BAND_LOW)
        self.assertEqual(S.BAND_HIGH, P.BAND_HIGH)
        self.assertEqual(S.BAND_HIGH, 250)
        self.assertEqual(S.STALE_HARD_DAYS, P.STALE_HARD_DAYS)
        self.assertEqual(S.FRESH_HOURS, P.FRESH_HOURS)
        self.assertEqual(S.SEED_THROTTLE_WEIGHT_FLOOR, P.SEED_THROTTLE_WEIGHT_FLOOR)
        self.assertEqual(S.WEIGHT_FLOOR, P.WEIGHT_FLOOR)
        self.assertEqual(S.CLIMB_INSERTS_PER_DAY, P.CLIMB_INSERTS_PER_DAY)
        self.assertEqual(S.SEED_UPLOADS_PER_CHANNEL, P.SEED_UPLOADS_PER_CHANNEL)
        self.assertEqual(S.MIN_LONGFORM_SEC, P.MIN_LONGFORM_SEC)
        self.assertEqual(S.MIN_LONGFORM_SEC, 60)
        self.assertEqual(S.SHORTS_MAX_SEC, P.SHORTS_MAX_SEC)
        self.assertEqual(P.HOUSE_CAPS["MIN_LONGFORM_SEC"], 60)
        self.assertFalse(S.COPY_OVER_PI)

    def test_share_cap_is_the_larger_of_twelve_or_five_percent(self):
        self.assertEqual(S.channel_share_cap(0), 12)
        self.assertEqual(S.channel_share_cap(100), 12)
        self.assertEqual(S.channel_share_cap(240), 12)
        self.assertEqual(S.channel_share_cap(250), 13)
        self.assertEqual(P.channel_share_cap(250), S.channel_share_cap(250))

    def test_search_budget_never_starves_inserts(self):
        self.assertEqual(S.search_budget(8000, 40), 4)
        self.assertEqual(S.search_budget(2100, 40), 0)
        self.assertEqual(S.search_budget(500, 40), 0)
        self.assertEqual(S.search_budget(8000, 0), 4)

    def test_climb_spreads_the_fill(self):
        self.assertEqual(S.climb_inserts_allowed(0, 150), 40)
        self.assertEqual(S.climb_inserts_allowed(39, 150), 1)
        self.assertEqual(S.climb_inserts_allowed(40, 150), 0)


class TestMixAndShare(unittest.TestCase):
    def test_prepare_prefers_discovered_until_mix_is_met(self):
        state: dict = {"add_ledger": [], "adds_by_day": {}}
        ranked = [
            _row(90, "sub", "UCsub", "subscribed"),
            _row(10, "disc", "UCnew", "discovered"),
        ]
        chosen, skipped, report = S.prepare_adds(
            ranked,
            playlist_items=[{"channel_id": "UCalready", "video_id": "p"}],
            state=state,
            now=NOW,
            after_prune=100,
            quota_remaining=8000,
        )
        self.assertEqual(chosen[0][1]["video_id"], "disc")
        self.assertEqual(skipped, [])
        self.assertEqual(report["inserts_budget"], 40)
        self.assertLessEqual(report["share_cap"], 250)

    def test_share_cap_skips_the_channel_already_at_the_ceiling(self):
        items = [{"channel_id": "UCbig", "video_id": f"v{i}"} for i in range(12)]
        ranked = [_row(50, "more", "UCbig", "subscribed"), _row(40, "other", "UCother", "discovered")]
        chosen, skipped, report = S.prepare_adds(
            ranked,
            playlist_items=items,
            state={"add_ledger": [], "adds_by_day": {}},
            now=NOW,
            after_prune=12,
            quota_remaining=8000,
        )
        self.assertEqual(report["share_cap"], 12)
        self.assertEqual([row[1]["video_id"] for row in chosen], ["other"])
        self.assertEqual(skipped[0]["why"], "channel-share")

    def test_rolling_mix_ignores_adds_older_than_seven_days(self):
        old = (NOW - timedelta(days=8)).isoformat().replace("+00:00", "Z")
        fresh = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        ledger = [
            {"at": old, "channel_id": "a", "lane": "discovered"},
            {"at": fresh, "channel_id": "b", "lane": "subscribed"},
            {"at": fresh, "channel_id": "c", "lane": "subscribed"},
            {"at": fresh, "channel_id": "d", "lane": "subscribed"},
            {"at": fresh, "channel_id": "e", "lane": "discovered"},
        ]
        mix = S.rolling_mix(ledger, NOW)
        self.assertEqual(mix["adds_7d"], 4)
        self.assertEqual(mix["discovered_adds_7d"], 1)
        self.assertEqual(mix["subscribed_adds_7d"], 3)
        self.assertFalse(mix["mix_met"])

    def test_quota_exhausted_is_the_blocker_when_inserts_cannot_run(self):
        _chosen, _skipped, report = S.prepare_adds(
            [_row(10, "a", "UCa", "subscribed")],
            playlist_items=[],
            state={"add_ledger": [], "adds_by_day": {}},
            now=NOW,
            after_prune=100,
            quota_remaining=10,
        )
        self.assertEqual(report["quota_blocker"], "quota_exhausted")


class TestDiscoveryTick(unittest.TestCase):
    def test_search_and_pool_land_in_the_log_shape(self):
        quota = _Quota(8000)
        yt = _FakeYT(quota)
        state: dict = {}
        candidates = [
            {
                "video_id": "seed",
                "channel_id": "UCsub",
                "title": "seed",
                "age_hours": 1,
            }
        ]
        playlist = [
            {
                "video_id": "on",
                "channel_id": "UCfromlist",
                "channel": "From List",
                "title": "Bitcoin lightning and the grid",
                "description": "solar baseload",
            }
        ]

        def fit(title: str, description: str = "") -> int:
            blob = f"{title} {description}".lower()
            return 2 if "bitcoin" in blob or "robot" in blob else 0

        out, partial = S.supply_tick(
            yt=yt,
            state=state,
            candidates=candidates,
            playlist_items=playlist,
            subscribed_seed={"UCseed"},
            quota_remaining=quota.remaining,
            quota_can=quota.can,
            now=NOW,
            thesis_fit=fit,
            fresh_hours=720,
            after_prune=100,
        )
        self.assertGreaterEqual(len(yt.searches), 1)
        self.assertLessEqual(len(yt.searches), 4)
        self.assertGreater(quota.remaining, 8000 - 4 * 100 - 50)
        lanes = {c["video_id"]: c["lane"] for c in out}
        self.assertEqual(lanes["seed"], "subscribed")
        self.assertEqual(lanes["d-1"], "discovered")
        self.assertGreaterEqual(partial["pool"], 1)
        self.assertIn("UCfromlist", state["discovered_channels"])
        S.record_add(state, "UCdiscovered", "discovered", NOW)
        finished = S.finish_supply_report(
            partial,
            added=[{"channel_id": "UCdiscovered", "lane": "discovered"}],
            playlist_items=playlist,
            state=state,
            now=NOW,
        )
        log = finished["log"]
        self.assertEqual(log["discovered_adds"], 1)
        self.assertEqual(log["subscribed_adds"], 0)
        self.assertGreaterEqual(log["unique_channels"], 2)
        self.assertGreaterEqual(log["pool"], 1)
        self.assertGreaterEqual(log["search_units"], 100)
        self.assertIn("discovered_adds_7d", log)
        ast.literal_eval(str(log))


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg: str, *args: object) -> None:
        self.lines.append(msg % args if args else msg)


class _DurationYT:
    def __init__(self, durations: dict[str, str | None]) -> None:
        self.durations = durations
        self.calls: list[list[str]] = []

    def videos(self, video_ids):
        self.calls.append(list(video_ids))
        out = {}
        for vid in video_ids:
            raw = self.durations.get(vid)
            item: dict = {
                "id": vid,
                "snippet": {"title": "Talk", "description": "", "tags": []},
            }
            if raw is not None:
                item["contentDetails"] = {"duration": raw}
            out[vid] = item
        return out


class TestLongform(unittest.TestCase):
    def test_parse_and_boundaries(self):
        self.assertEqual(S.parse_duration_sec("PT45S"), 45)
        self.assertEqual(S.parse_duration_sec("PT10M"), 600)
        self.assertEqual(S.parse_duration_sec("PT59S"), 59)
        self.assertEqual(S.parse_duration_sec("PT60S"), 60)
        self.assertEqual(S.parse_duration_sec("PT1H2M3S"), 3723)
        for raw in ("garbage", "", None, "P0D", "PT"):
            self.assertIsNone(S.parse_duration_sec(raw))

    def test_short_medium_malformed_and_flag(self):
        yt = _DurationYT(
            {
                "short": "PT45S",
                "long": "PT10M",
                "edge": "PT59S",
                "keep": "PT60S",
                "flag": "PT1M30S",
                "plain": "PT1M30S",
                "bad": "garbage",
                "empty": "",
                "missing": None,
                "live": "P0D",
            }
        )
        log = _Log()
        candidates = [
            {"video_id": "short", "title": "Clip", "duration_sec": 0},
            {"video_id": "long", "title": "Interview", "duration_sec": 0},
            {"video_id": "edge", "title": "Almost", "duration_sec": 0},
            {"video_id": "keep", "title": "Minute", "duration_sec": 0},
            {"video_id": "flag", "title": "Quick #shorts take", "duration_sec": 0},
            {"video_id": "plain", "title": "Ninety seconds", "duration_sec": 0},
            {"video_id": "bad", "title": "Bad", "duration_sec": 0},
            {"video_id": "empty", "title": "Empty", "duration_sec": 0},
            {"video_id": "missing", "title": "Missing", "duration_sec": 0},
            {"video_id": "live", "title": "Live", "duration_sec": 0},
        ]
        kept, skipped = S.filter_longform(candidates, yt, log)
        why = {row["video_id"]: row["why"] for row in skipped}
        self.assertEqual(why["short"], "short<60s")
        self.assertEqual(why["edge"], "short<60s")
        self.assertEqual(why["flag"], "short-flagged")
        self.assertEqual(why["bad"], "duration-unknown")
        self.assertEqual(why["empty"], "duration-unknown")
        self.assertEqual(why["missing"], "duration-unknown")
        self.assertEqual(why["live"], "duration-unknown")
        kept_ids = {row["video_id"]: row["duration_sec"] for row in kept}
        self.assertEqual(kept_ids["long"], 600)
        self.assertEqual(kept_ids["keep"], 60)
        self.assertEqual(kept_ids["plain"], 90)
        self.assertNotIn("plain", why)
        self.assertTrue(any("short<60s" in line for line in log.lines))
        self.assertTrue(all(len(call) <= 50 for call in yt.calls))

    def test_supply_discovery_fetches_duration_before_a_keep(self):
        quota = _Quota(8000)
        yt = _FakeYT(quota)
        state: dict = {}
        candidates = [
            {
                "video_id": "known",
                "channel_id": "UCsub",
                "title": "Already enriched",
                "age_hours": 1,
                "duration_sec": 600,
            }
        ]
        out, _partial = S.supply_tick(
            yt=yt,
            state=state,
            candidates=candidates,
            playlist_items=[],
            subscribed_seed=set(),
            quota_remaining=quota.remaining,
            quota_can=quota.can,
            now=NOW,
            thesis_fit=lambda title, description="": 1,
            fresh_hours=720,
            after_prune=100,
        )
        discovered = next(c for c in out if c["video_id"] == "d-1")
        self.assertEqual(discovered["duration_sec"], 0)
        kept, skipped = S.filter_longform(out, yt, _Log())
        fetched = [vid for call in yt.video_calls for vid in call]
        self.assertIn("d-1", fetched)
        self.assertNotIn("known", fetched)
        kept_ids = {row["video_id"]: row["duration_sec"] for row in kept}
        self.assertEqual(kept_ids["d-1"], 600)
        self.assertEqual(kept_ids["known"], 600)
        self.assertNotIn("d-1", {row["video_id"] for row in skipped})

    def test_patched_writer_parses_when_the_pi_copy_is_local(self):
        path = Path("/tmp/youtube_groom_pi.py")
        if not path.is_file():
            self.skipTest("no local writer copy")
        patched = S.patch_writer_source(path.read_text(encoding="utf-8"))
        ast.parse(patched)
        self.assertEqual(patched, S.patch_writer_source(patched))
        self.assertNotIn("or dur == 0", patched)
        self.assertIn("filter_longform(", patched)


class TestWriterPatch(unittest.TestCase):
    def test_patch_is_idempotent_on_the_anchors(self):
        text = _anchor_excerpt()
        once = S.patch_writer_source(text, parse=False)
        twice = S.patch_writer_source(once, parse=False)
        self.assertEqual(once, twice)
        self.assertIn("HOUSE_TARGET = 250", once)
        self.assertIn("CAP = 250", once)
        self.assertIn("FRESH_HOURS = 720", once)
        self.assertIn("STALE_HARD_DAYS = 30", once)
        self.assertIn("SEED_THROTTLE_WEIGHT_FLOOR = 0.00", once)
        self.assertIn("WEIGHT_FLOOR = 0.05", once)
        self.assertIn("def search_videos", once)
        self.assertIn("supply_tick(", once)
        self.assertIn("filter_longform(", once)
        self.assertIn('log.warning("longform filter failed")', once)
        self.assertNotIn("or dur == 0", once)
        self.assertIn("unknown is not a bonus (#1045)", once)
        self.assertEqual(once.count("filter_longform("), 1)
        self.assertIn("prepare_adds(", once)
        self.assertIn("record_add(", once)
        self.assertIn("solar|geothermal", once)
        src = Path(S.__file__).read_text(encoding="utf-8")
        self.assertNotIn("googleapiclient", src)
        self.assertNotIn("InstalledAppFlow", src)

    def test_live_writer_copy_parses_when_present(self):
        path = Path("/tmp/youtube_groom_pi.py")
        if not path.is_file():
            self.skipTest("no local writer copy")
        patched = S.patch_writer_source(path.read_text(encoding="utf-8"))
        ast.parse(patched)
        self.assertEqual(patched, S.patch_writer_source(patched))


def _anchor_excerpt() -> str:
    return '''"""doc
  Hourly search is forbidden. Scout stays off unless --scout (manual).
"""
STALE_HARD_DAYS = 7
FRESH_HOURS = 168
HOUSE_TARGET = 100  # fill target after prune; was 50 (#788)
CAP = 200
SEED_THROTTLE_WEIGHT_FLOOR = 0.10  # skip SEED_THROTTLE only below this (was 0.25, #815)
SEED_UPLOADS_PER_CHANNEL = 50  # was 6; YouTube page max so 7d window can fill house 100
WEIGHT_FLOOR = 0.15
    duration = 1 if 15 * 60 <= dur <= 3 * 3600 or dur == 0 else 0
HARD_LENSES = {
    "energy": (
        r"\\b(nuclear|smr|uranium|grid|electricity|watt|gw\\b|terawatt|"
        r"power\\s+bottleneck|energy\\s+(crisis|shortage|super|age)|fusion)\\b"
    ),
    "bitcoin": (
        r"\\b(bitcoin|btc|sats|sound\\s+money|debasement|collateral|"
        r"gold\\s+to|monetary|hyperbitcoin)\\b"
    ),
    "ai": r"\\b(ai\\b|a\\.i\\.|llm|intelligence|deepmind|agentic|foundation\\s+model)\\b",
    "autonomy": r"\\b(autonom|agent\\s+system|multi-?agent|cybercab|robotaxi|fsd)\\b",
    "robotics": r"\\b(robot|humanoid|optimus|bots?\\b)\\b",
    "capital": (
        r"\\b(fed\\b|fomc|liquidity|treasury|warsh|debt\\b|capital|"
        r"rate\\s+cut|balance\\s+sheet|dollar)\\b"
    ),
    "fitness": r"\\b(vo2|zone\\s*2|sleep\\s+scientist|hypertrophy|training\\s+load)\\b",
}
COST = {
    "search": 100,
}
        return resp.get("items") or []

    def maybe_retitle_playlist(self) -> bool:
        pass
        apply_live_knobs(globals())
    except Exception:
        pass
    added: list[dict[str, Any]] = []
    skipped_add: list[dict[str, Any]] = []
        ranked_add = []
        after_prune = len(remaining_ids)
        remaining_playlist_slots = max(0, CAP - after_prune)
        take = min(
            max(0, HOUSE_TARGET - after_prune),
            max(0, CAP - after_prune),
            remaining_playlist_slots,
            len(ranked_add),
        )
        for sc, c, detail in ranked_add[:take]:
                        "score": sc,
                        "detail": detail,
                    }
                )
                existing.add(c["video_id"])
    reasons: dict[str, int] = {}
        "skipped_add": skipped_add[:20],
            "quota_used_today",
        f"house={HOUSE_TARGET}"
'''


if __name__ == "__main__":
    unittest.main()
