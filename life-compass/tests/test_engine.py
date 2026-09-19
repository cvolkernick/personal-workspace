"""Sweep engine: quiet days, pings, dashboard sections, radar, consume."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from domain import GOAL, PING_NEW  # noqa: E402
from engine import build_dashboard, consume_pings, empty_state, run_sweep  # noqa: E402
from radar import candidate_fingerprint, dismiss_candidate, promote_candidate  # noqa: E402

NOW = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)


def _quiet_snapshot() -> dict:
    return {
        "as_of": "2026-09-19T14:00:00+00:00",
        "fitness": {"wired": True, "days_since_resistance": 1, "as_of": "2026-09-19T14:00:00+00:00"},
        "financial": {
            "wired": True,
            "as_of": "2026-09-19T14:00:00+00:00",
            "bills": [],
            "categories": [{"name": "Groceries", "balance": 40.0, "goal_under_funded": 0}],
            "payoff": [],
        },
        "operations": {
            "as_of": "2026-09-19T14:00:00+00:00",
            "turo_trips": [],
            "training": [],
            "invoice_ready": {"wired": True, "unassigned": []},
        },
        "radar": {"candidates": []},
    }


class TestSweepQuietAndPings(unittest.TestCase):
    def test_attended_day_zero_pings(self) -> None:
        state, pings, dash = run_sweep(empty_state(), _quiet_snapshot(), now=NOW, hour=9)
        self.assertEqual(pings, [])
        self.assertEqual(dash["one_thing"]["kind"], "all-quiet")
        self.assertTrue(dash["briefing"]["quiet"])
        self.assertEqual(dash["goal"]["id"], GOAL["id"])

    def test_new_unattended_one_ping(self) -> None:
        snap = _quiet_snapshot()
        snap["financial"]["bills"] = [
            {"payee": "Insurance", "date": "2026-09-20", "unscheduled": True, "paid": False}
        ]
        state, pings, dash = run_sweep(empty_state(), snap, now=NOW)
        kinds = [p["kind"] for p in pings if p.get("kind") != "weekly-constraint"]
        self.assertEqual(kinds, [PING_NEW])
        state2, pings2, _ = run_sweep(state, snap, now=NOW + timedelta(hours=1))
        attention = [p for p in pings2 if p.get("kind") in ("new", "still-open")]
        self.assertEqual(attention, [])

    def test_dashboard_has_five_sections_and_watchlist(self) -> None:
        state, _pings, dash = run_sweep(empty_state(), _quiet_snapshot(), now=NOW)
        for key in ("one_thing", "goals", "operations", "radar", "watchlist"):
            self.assertIn(key, dash)
        self.assertTrue(dash["watchlist"])
        for row in dash["watchlist"]:
            self.assertIn("state", row)
            self.assertIn("last_checked", row)
            self.assertIn("as_of", row)
        self.assertTrue(dash["goals"]["fitness"]["chips"])
        self.assertTrue(dash["goals"]["financial"]["chips"])
        self.assertEqual(dash["goal"]["slug"], "life-compass")

    def test_consume_pings_empties_pending(self) -> None:
        snap = _quiet_snapshot()
        snap["operations"]["turo_trips"] = [
            {
                "id": "ev1",
                "title": "Turo pickup",
                "kind": "pickup",
                "today": True,
                "prepped": False,
            }
        ]
        state, pings, _ = run_sweep(empty_state(), snap, now=NOW)
        self.assertTrue(pings)
        state, consumed = consume_pings(state, now=NOW)
        self.assertEqual(len(consumed), len(pings))
        self.assertEqual(state["pending_pings"], [])
        state, consumed2 = consume_pings(state, now=NOW)
        self.assertEqual(consumed2, [])


class TestRadar(unittest.TestCase):
    def test_cap_three_and_why_source(self) -> None:
        snap = _quiet_snapshot()
        snap["radar"] = {
            "candidates": [
                {"title": f"C{i}", "why": f"because {i}", "source": "github"}
                for i in range(8)
            ]
        }
        _state, _pings, dash = run_sweep(empty_state(), snap, now=NOW)
        cands = dash["radar"]["candidates"]
        self.assertLessEqual(len(cands), 3)
        for c in cands:
            self.assertTrue(c["why"])
            self.assertTrue(c["source"])

    def test_dismiss_does_not_resurface_same_fingerprint(self) -> None:
        snap = _quiet_snapshot()
        raw = {"title": "New broker", "why": "fee drop", "source": "gmail"}
        snap["radar"] = {"candidates": [raw]}
        state, _, dash = run_sweep(empty_state(), snap, now=NOW)
        cid = dash["radar"]["candidates"][0]["id"]
        state = dismiss_candidate(state, cid, now=NOW)
        state2, _, dash2 = run_sweep(state, snap, now=NOW + timedelta(hours=2))
        self.assertEqual(dash2["radar"]["candidates"], [])

    def test_material_change_allows_resurface(self) -> None:
        a = candidate_fingerprint("New broker", "gmail", "fee drop")
        b = candidate_fingerprint("New broker", "gmail", "fee drop AND new product")
        self.assertNotEqual(a, b)

    def test_promote_creates_tracked_target(self) -> None:
        snap = _quiet_snapshot()
        snap["radar"] = {
            "candidates": [{"title": "Offer", "why": "emerging", "source": "github"}]
        }
        state, _, dash = run_sweep(empty_state(), snap, now=NOW)
        cid = dash["radar"]["candidates"][0]["id"]
        state, target = promote_candidate(state, cid, now=NOW)
        self.assertIsNotNone(target)
        self.assertTrue(state["tracked_targets"])
        self.assertEqual(state["tracked_targets"][0]["from_radar"], cid)
        self.assertEqual(state["tracked_targets"][0]["goal_id"], GOAL["id"])


class TestDashboardAsOf(unittest.TestCase):
    def test_build_dashboard_from_state_without_resweep(self) -> None:
        state, _, _ = run_sweep(empty_state(), _quiet_snapshot(), now=NOW)
        dash = build_dashboard(state, now=NOW + timedelta(minutes=5), hour=9)
        self.assertEqual(dash["one_thing"]["kind"], "all-quiet")
        self.assertTrue(dash["as_of"])


if __name__ == "__main__":
    unittest.main()
