"""Nag control, one-thing ranking, weekly constraint, missing-anything."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from domain import (  # noqa: E402
    ATTENDED,
    PING_NEW,
    PING_STILL_OPEN,
    UNATTENDED,
    UNDER_ATTENDED,
    WIRING_PLANNED,
    apply_observation,
    missing_anything,
    new_watch_item,
    one_thing,
    weekly_constraint,
)

NOW = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)


class TestNagControl(unittest.TestCase):
    def test_first_unattended_pings_once(self) -> None:
        item = new_watch_item("x", layer="goals", label="Bill due")
        item, ping = apply_observation(item, UNATTENDED, now=NOW, detail="card")
        self.assertEqual(item["state"], UNATTENDED)
        self.assertIsNotNone(ping)
        self.assertEqual(ping["kind"], PING_NEW)
        self.assertIn("Unattended", ping["message"])

        item2, ping2 = apply_observation(item, UNATTENDED, now=NOW + timedelta(hours=1))
        self.assertIsNone(ping2)
        self.assertEqual(item2["last_ping_kind"], PING_NEW)

    def test_cooldown_then_still_open_once(self) -> None:
        item = new_watch_item("x", layer="goals", label="Bill due")
        item, _ = apply_observation(item, UNATTENDED, now=NOW)
        later = NOW + timedelta(hours=25)
        item, ping = apply_observation(item, UNATTENDED, now=later)
        self.assertEqual(ping["kind"], PING_STILL_OPEN)
        self.assertIn("Still open", ping["message"])
        item, ping2 = apply_observation(item, UNATTENDED, now=later + timedelta(hours=25))
        self.assertIsNone(ping2)

    def test_resolved_resets_and_silent(self) -> None:
        item = new_watch_item("x", layer="goals", label="Bill due")
        item, _ = apply_observation(item, UNATTENDED, now=NOW)
        item, ping = apply_observation(item, ATTENDED, now=NOW + timedelta(hours=2))
        self.assertIsNone(ping)
        self.assertEqual(item["state"], ATTENDED)
        self.assertEqual(item["flag_count"], 0)
        item, ping2 = apply_observation(item, UNATTENDED, now=NOW + timedelta(hours=3))
        self.assertEqual(ping2["kind"], PING_NEW)

    def test_wiring_planned_never_pings(self) -> None:
        item = new_watch_item("fit", layer="goals", label="Volume")
        item, ping = apply_observation(
            item,
            WIRING_PLANNED,
            now=NOW,
            wiring_dependency="FitDash read API",
        )
        self.assertIsNone(ping)
        self.assertEqual(item["state"], WIRING_PLANNED)

    def test_under_attended_uses_same_nag_rules(self) -> None:
        item = new_watch_item("vol", layer="goals", label="Volume")
        item, ping = apply_observation(item, UNDER_ATTENDED, now=NOW)
        self.assertEqual(ping["kind"], PING_NEW)
        item, ping2 = apply_observation(item, UNDER_ATTENDED, now=NOW + timedelta(hours=2))
        self.assertIsNone(ping2)


class TestOneThing(unittest.TestCase):
    def _items(self) -> list[dict]:
        bill = new_watch_item("financial.bill_due_unscheduled", layer="goals", label="Bill due")
        bill["state"] = UNATTENDED
        bill["severity"] = "high"
        turo = new_watch_item("ops.turo_today_unprepped", layer="operations", label="Turo pickup")
        turo["state"] = UNATTENDED
        turo["severity"] = "critical"
        return [bill, turo]

    def test_all_quiet(self) -> None:
        item = new_watch_item("x", layer="goals", label="ok")
        out = one_thing([item], now=NOW, hour=9)
        self.assertEqual(out["kind"], "all-quiet")
        self.assertEqual(out["text"], "All quiet")

    def test_morning_prefers_operations(self) -> None:
        out = one_thing(self._items(), now=NOW, hour=8)
        self.assertEqual(out["item"]["id"], "ops.turo_today_unprepped")
        self.assertEqual(out["window"], "morning")

    def test_midday_prefers_goals(self) -> None:
        out = one_thing(self._items(), now=NOW, hour=13)
        self.assertEqual(out["item"]["id"], "financial.bill_due_unscheduled")
        self.assertEqual(out["window"], "midday")


class TestMissingAndWeekly(unittest.TestCase):
    def test_missing_false_when_attended(self) -> None:
        item = new_watch_item("x", layer="goals", label="ok")
        out = missing_anything([item], now=NOW)
        self.assertFalse(out["missing"])
        self.assertIn("Nothing unattended", out["text"])

    def test_missing_lists_open(self) -> None:
        item = new_watch_item("x", layer="goals", label="Bill")
        item["state"] = UNATTENDED
        out = missing_anything([item], now=NOW)
        self.assertTrue(out["missing"])
        self.assertEqual(len(out["items"]), 1)

    def test_weekly_unknown_when_quiet(self) -> None:
        rec, ping = weekly_constraint([], now=NOW)
        self.assertEqual(rec["kind"], "unknown")
        self.assertIsNone(ping)

    def test_weekly_pings_once_per_iso_week(self) -> None:
        item = new_watch_item("x", layer="goals", label="Bill")
        item["state"] = UNATTENDED
        item["severity"] = "high"
        rec, ping = weekly_constraint([item], now=NOW)
        self.assertEqual(rec["item_id"], "x")
        self.assertIsNotNone(ping)
        rec2, ping2 = weekly_constraint([item], now=NOW + timedelta(hours=3), prior=rec)
        self.assertIsNone(ping2)

    def test_weekly_still_open_next_week_same_item(self) -> None:
        item = new_watch_item("x", layer="goals", label="Bill")
        item["state"] = UNATTENDED
        item["severity"] = "high"
        rec, _ = weekly_constraint([item], now=NOW)
        next_week = NOW + timedelta(days=7)
        rec2, ping = weekly_constraint([item], now=next_week, prior=rec)
        self.assertIsNotNone(ping)
        self.assertEqual(ping["weekly_kind"], PING_STILL_OPEN)


if __name__ == "__main__":
    unittest.main()
