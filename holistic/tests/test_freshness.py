"""Fixed-clock tests for house-cadence upkeep batteries."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.freshness import (  # noqa: E402
    SEED_BY_ID,
    SEED_ITEMS,
    charge_cliff,
    compute_freshness,
    compute_item,
    freshness_payload,
    load_freshness,
    mark_done,
    mark_done_and_compute,
    merge_seed,
    save_freshness,
)

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


class FreshnessMathTests(unittest.TestCase):
    def test_linear_mid_interval(self) -> None:
        item = {
            "id": "mid",
            "title": "Mid linear",
            "interval_days": 10,
            "curve": "linear",
            "last_done": "2026-08-12T12:00:00Z",
        }
        got = compute_item(item, now=NOW)
        self.assertEqual(got["charge"], 0.5)
        self.assertEqual(got["level"], "ok")
        self.assertEqual(got["overdue_hours"], 0.0)
        self.assertEqual(got["empty_at"], "2026-08-22T12:00:00Z")
        self.assertEqual(got["curve"], "linear")

    def test_cliff_full_at_79_percent_then_drops(self) -> None:
        last = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
        interval = timedelta(days=10)
        item = {
            "id": "cliffy",
            "title": "Cliff plant",
            "interval_days": 10,
            "curve": "cliff",
            "last_done": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        at_79 = compute_item(item, now=last + timedelta(days=7.9))
        self.assertEqual(at_79["charge"], 1.0)
        self.assertEqual(at_79["level"], "ok")
        self.assertAlmostEqual(charge_cliff(0.79), 1.0)

        at_90 = compute_item(item, now=last + timedelta(days=9.0))
        self.assertAlmostEqual(at_90["charge"], 0.5, places=5)
        self.assertEqual(at_90["level"], "ok")

        at_due = compute_item(item, now=last + interval)
        self.assertEqual(at_due["charge"], 0.0)
        self.assertEqual(at_due["level"], "red")
        self.assertEqual(at_due["overdue_hours"], 0.0)

    def test_unknown_last_done_is_empty_red(self) -> None:
        item = {
            "id": "dishes",
            "title": "Dishes",
            "interval_days": 1,
            "curve": "linear",
            "last_done": None,
        }
        got = compute_item(item, now=NOW)
        self.assertEqual(got["charge"], 0.0)
        self.assertEqual(got["level"], "red")
        self.assertEqual(got["empty_at"], "2026-08-17T12:00:00Z")
        self.assertEqual(got["overdue_hours"], 0.0)
        self.assertIsNone(got["last_done"])

    def test_water_bowl_seed_overdue_on_2026_08_17(self) -> None:
        seed = dict(SEED_BY_ID["water-bowl"])
        got = compute_item(seed, now=NOW)
        self.assertEqual(got["id"], "water-bowl")
        self.assertEqual(got["charge"], 0.0)
        self.assertEqual(got["level"], "red")
        self.assertGreater(got["overdue_hours"], 0.0)
        self.assertEqual(got["empty_at"], "2026-08-13T03:34:18Z")
        # last_done 2026-07-14T03:34:18Z + 30d = 2026-08-13T03:34:18Z
        expected = (
            NOW - datetime(2026, 8, 13, 3, 34, 18, tzinfo=timezone.utc)
        ).total_seconds() / 3600.0
        self.assertAlmostEqual(got["overdue_hours"], expected, places=3)

    def test_sort_emptiest_first(self) -> None:
        store = {
            "items": [
                {
                    "id": "full",
                    "title": "Just done",
                    "interval_days": 7,
                    "curve": "linear",
                    "last_done": "2026-08-17T11:00:00Z",
                },
                {
                    "id": "mid",
                    "title": "Halfway",
                    "interval_days": 10,
                    "curve": "linear",
                    "last_done": "2026-08-12T12:00:00Z",
                },
                dict(SEED_BY_ID["dishes"]),
                dict(SEED_BY_ID["water-bowl"]),
            ]
        }
        payload = compute_freshness(store, now=NOW)
        ids = [it["id"] for it in payload["items"]]
        self.assertEqual(ids[0], "water-bowl")
        self.assertEqual(ids[1], "dishes")
        self.assertEqual(ids[2], "mid")
        self.assertEqual(ids[3], "full")
        self.assertLess(payload["items"][0]["charge"], payload["items"][2]["charge"])
        self.assertGreater(payload["items"][0]["overdue_hours"], 0.0)

    def test_levels_ok_mid_red_boundaries(self) -> None:
        last = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
        item = {
            "id": "bounds",
            "title": "Bounds",
            "interval_days": 10,
            "curve": "linear",
            "last_done": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # u = 0.60 → charge 0.40 → ok
        at_ok = compute_item(item, now=last + timedelta(days=6.0))
        self.assertAlmostEqual(at_ok["charge"], 0.40, places=5)
        self.assertEqual(at_ok["level"], "ok")
        # u = 0.70 → charge 0.30 → mid
        at_mid = compute_item(item, now=last + timedelta(days=7.0))
        self.assertAlmostEqual(at_mid["charge"], 0.30, places=5)
        self.assertEqual(at_mid["level"], "mid")
        # u = 0.86 → charge 0.14 → red
        at_red = compute_item(item, now=last + timedelta(days=8.6))
        self.assertAlmostEqual(at_red["charge"], 0.14, places=5)
        self.assertEqual(at_red["level"], "red")


class FreshnessStoreTests(unittest.TestCase):
    def test_merge_seed_fills_missing_ids(self) -> None:
        store, changed = merge_seed({"version": 1, "items": []})
        self.assertTrue(changed)
        ids = [it["id"] for it in store["items"]]
        self.assertEqual(ids, [item["id"] for item in SEED_ITEMS])

    def test_mark_done_sets_last_done(self) -> None:
        store, _ = merge_seed({"version": 1, "items": [dict(SEED_BY_ID["dishes"])]})
        updated = mark_done(store, "dishes", now=NOW)
        dishes = next(it for it in updated["items"] if it["id"] == "dishes")
        self.assertEqual(dishes["last_done"], "2026-08-17T12:00:00Z")
        got = compute_item(dishes, now=NOW)
        self.assertEqual(got["charge"], 1.0)
        self.assertEqual(got["level"], "ok")

    def test_roundtrip_file_and_done(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "freshness.json"
            save_freshness(empty_via_merge(), path=path)
            loaded = load_freshness(path)
            self.assertEqual(len(loaded["items"]), len(SEED_ITEMS))
            payload = mark_done_and_compute("trash", path=path, now=NOW)
            trash = next(it for it in payload["items"] if it["id"] == "trash")
            self.assertEqual(trash["charge"], 1.0)
            self.assertEqual(trash["level"], "ok")
            persisted = json.loads(path.read_text(encoding="utf-8"))
            row = next(it for it in persisted["items"] if it["id"] == "trash")
            self.assertEqual(row["last_done"], "2026-08-17T12:00:00Z")

    def test_payload_includes_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "freshness.json"
            payload = freshness_payload(path, now=NOW)
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["items"]), len(SEED_ITEMS))
            for it in payload["items"]:
                for key in ("id", "title", "charge", "level", "empty_at", "overdue_hours", "curve"):
                    self.assertIn(key, it)
            water = next(it for it in payload["items"] if it["id"] == "water-bowl")
            self.assertEqual(water["level"], "red")
            self.assertGreater(water["overdue_hours"], 0.0)


def empty_via_merge() -> dict:
    store, _ = merge_seed({"version": 1, "items": []})
    return store


if __name__ == "__main__":
    unittest.main()
