"""Proper-set muscle volume, ET weeks, tiers, and the cap warning (#1193)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rt_dashboard.agent_today import export_agent_today
from rt_dashboard.models import ExerciseEntry, SetEntry
from rt_dashboard.muscle_sets import (
    accessory_sets_allowed,
    accessory_tiebreak,
    build_muscle_set_report,
    credit_proper_sets,
    demand_min,
    et_week_bounds,
    exercise_proper_sets,
    proper_set_count,
    resolve_weekly_cap,
    tier_for,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

PRESS = {
    "id": "db-flat-press",
    "name": "DB Flat Press",
    "movement": "compound",
    "primary_muscles": ["chest"],
    "secondary_muscles": ["triceps", "shoulders"],
    "volume_primary": ["chest"],
    "volume_secondary": ["triceps", "front_delts"],
    "available": True,
}
LATERAL = {
    "id": "lateral-raises",
    "name": "Lateral Raises",
    "movement": "isolation",
    "primary_muscles": ["shoulders"],
    "volume_primary": ["side_delts"],
    "volume_secondary": [],
    "available": True,
}
ASSISTED = {
    "id": "assisted-pullups",
    "name": "Assisted Pullups",
    "movement": "compound",
    "equipment": ["assisted_pullup"],
    "assisted": True,
    "primary_muscles": ["lats", "back"],
    "volume_primary": ["lats_upper_back"],
    "volume_secondary": ["biceps"],
    "available": True,
}
CATALOG = {"exercises": [PRESS, LATERAL, ASSISTED]}


def _row(weight, sets, reps, **extra):
    row = {"weight_lbs": weight, "sets": sets, "reps": reps}
    row.update(extra)
    return row


class ProperSetFilter(unittest.TestCase):
    def test_warmup_and_below_band_are_excluded(self):
        rows = [
            _row(95, 2, 15),  # high-rep warm-up, outside 4–10
            _row(135, 1, 3),  # below the compound window
            _row(155, 1, 5),  # in window, under 85% of 200
            _row(170, 1, 4),  # one rep under the 5–9 band, at 85%
            _row(200, 3, 6),
            _row(200, 1, 11),  # one rep over the window
        ]
        self.assertEqual(
            proper_set_count(rows, movement="compound", planned_load=200, assisted=False),
            4,
        )

    def test_isolation_window_is_seven_through_sixteen(self):
        rows = [_row(20, 1, 6), _row(20, 2, 7), _row(20, 1, 16), _row(20, 1, 17)]
        self.assertEqual(
            proper_set_count(rows, movement="isolation", planned_load=20, assisted=False),
            3,
        )

    def test_status_row_and_quest_seed_never_count(self):
        status = {
            "name": "Already trained today",
            "sets": [_row(0, 1, 8)],
        }
        seed = {
            "name": "DB Flat Press",
            "quest_seeded": True,
            "sets": [_row(200, 3, 6)],
        }
        self.assertEqual(exercise_proper_sets(status, PRESS, planned_load=200), 0)
        self.assertEqual(exercise_proper_sets(seed, PRESS, planned_load=200), 0)

    def test_assisted_uses_effective_load_and_counts_reps_when_missing(self):
        rows = [
            _row(
                80,
                1,
                6,
                effective_load_lbs=70,
                effective_load_source="nearest_weighin",
            ),
            _row(
                40,
                2,
                6,
                effective_load_lbs=110,
                effective_load_source="nearest_weighin",
            ),
        ]
        # 85% of 110 is 93.5. The pin-heavy ramp (effective 70) drops.
        self.assertEqual(
            proper_set_count(rows, movement="compound", planned_load=110, assisted=True),
            2,
        )
        reps_only = [
            _row(90, 2, 8, effective_load_source="none"),
        ]
        self.assertEqual(
            proper_set_count(reps_only, movement="compound", planned_load=200, assisted=True),
            2,
        )


class CreditMath(unittest.TestCase):
    def test_each_primary_gets_one_and_each_secondary_half(self):
        credits = credit_proper_sets(["chest", "front_delts"], ["triceps", "chest"], 2)
        self.assertEqual(credits["chest"], 2.0)
        self.assertEqual(credits["front_delts"], 2.0)
        self.assertEqual(credits["triceps"], 1.0)
        self.assertNotIn("shoulders", credits)

    def test_logged_press_credits_the_working_sets_only(self):
        payload = {
            "sessions": [
                {
                    "date": "2026-10-13",
                    "session_type": "push",
                    "exercises": [
                        {
                            "name": "DB Flat Press",
                            "sets": [
                                _row(95, 1, 15),
                                _row(200, 2, 8),
                            ],
                        }
                    ],
                }
            ],
            "workout_store": {
                "catalog": CATALOG,
                "plan": {"exercises": [{"name": "DB Flat Press", "weight_lbs": 200}]},
            },
        }
        report = build_muscle_set_report(payload, as_of="2026-10-15")
        sets = report["muscle_sets"]
        self.assertEqual(sets["chest"]["proper_sets"], 2.0)
        self.assertEqual(sets["triceps"]["proper_sets"], 1.0)
        self.assertEqual(sets["front_delts"]["proper_sets"], 1.0)
        self.assertEqual(report["budget"]["week_proper_sets"], 2)


class EtWeekBounds(unittest.TestCase):
    def test_sunday_night_et_stays_in_the_same_week(self):
        # 2026-10-18 is Sunday. 03:30Z is still 23:30 EDT. 04:30Z is Monday.
        self.assertEqual(et_week_bounds("2026-10-18"), ("2026-10-12", "2026-10-18"))
        late = datetime(2026, 10, 19, 3, 30, tzinfo=timezone.utc)
        early = datetime(2026, 10, 19, 4, 30, tzinfo=timezone.utc)
        self.assertEqual(et_week_bounds(late), ("2026-10-12", "2026-10-18"))
        self.assertEqual(et_week_bounds(early), ("2026-10-19", "2026-10-25"))

    def test_sunday_log_counts_in_the_et_week_not_the_next(self):
        payload = {
            "sessions": [
                {
                    "date": "2026-10-18",
                    "session_type": "push",
                    "exercises": [
                        {"name": "DB Flat Press", "sets": [_row(200, 1, 8)]}
                    ],
                },
                {
                    "date": "2026-10-19",
                    "session_type": "push",
                    "exercises": [
                        {"name": "DB Flat Press", "sets": [_row(200, 4, 8)]}
                    ],
                },
            ],
            "workout_store": {"catalog": CATALOG},
        }
        report = build_muscle_set_report(payload, as_of="2026-10-18")
        self.assertEqual(report["week_start"], "2026-10-12")
        self.assertEqual(report["week_end"], "2026-10-18")
        self.assertEqual(report["muscle_sets"]["chest"]["proper_sets"], 1.0)


class Tiering(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(tier_for(0), "red")
        self.assertEqual(tier_for(3.5), "red")
        self.assertEqual(tier_for(4), "green")
        self.assertEqual(tier_for(7.5), "green")
        self.assertEqual(tier_for(8), "blue")
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-10-13",
                        "session_type": "push",
                        "exercises": [
                            {"name": "DB Flat Press", "sets": [_row(200, 4, 8)]}
                        ],
                    }
                ],
                "workout_store": {"catalog": CATALOG},
            },
            as_of="2026-10-15",
        )
        self.assertEqual(report["muscle_sets"]["chest"]["tier"], "green")
        self.assertEqual(report["muscle_sets"]["chest"]["target"], "8-10")
        self.assertEqual(report["muscle_sets"]["side_delts"]["tier"], "red")
        self.assertEqual(report["muscle_sets"]["side_delts"]["target"], "8-10")
        self.assertEqual(report["muscle_sets"]["hamstrings"]["target"], "4-7")
        self.assertEqual(report["muscle_sets"]["quads"]["target"], "4")


class CapWarning(unittest.TestCase):
    def test_cap_steps_at_most_ten_percent_and_holds_when_blocked(self):
        counts = {
            "2026-09-07": 40,
            "2026-09-14": 40,
            "2026-09-21": 40,
            "2026-09-28": 40,
            "2026-10-05": 40,
            "2026-10-12": 80,
        }
        held = resolve_weekly_cap(counts, as_of="2026-10-12")
        self.assertEqual(held["cap"], 40)
        self.assertIsNone(held["cap_override"])
        jumped = resolve_weekly_cap(counts, as_of="2026-10-19")
        self.assertEqual(jumped["cap_raw"], 50)
        self.assertEqual(jumped["cap"], 44)
        blocked = resolve_weekly_cap(
            counts, as_of="2026-10-19", blocked_mondays=["2026-10-19"]
        )
        self.assertEqual(blocked["cap"], 40)
        override = resolve_weekly_cap(counts, as_of="2026-10-19", override=55)
        self.assertEqual(override["cap"], 55)
        self.assertEqual(override["cap_override"], 55)

    def test_priorities_plus_floors_warn_instead_of_overrunning(self):
        self.assertEqual(demand_min(), 68)
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-10-06",
                        "session_type": "push",
                        "exercises": [
                            {"name": "DB Flat Press", "sets": [_row(200, 10, 8)]}
                        ],
                    }
                ],
                "workout_store": {"catalog": CATALOG},
            },
            as_of="2026-10-13",
        )
        budget = report["budget"]
        self.assertEqual(budget["cap_override"], None)
        self.assertIsNotNone(budget["cap"])
        self.assertLess(budget["cap"], demand_min())
        self.assertTrue(budget["overrun"])
        self.assertIn("chest", budget["warning"])
        self.assertEqual(budget["drop_suggestion"], "chest")

    def test_accessory_sets_do_not_pass_the_cap(self):
        self.assertEqual(accessory_sets_allowed(3, None), 3)
        self.assertEqual(accessory_sets_allowed(3, 1), 1)
        self.assertEqual(accessory_sets_allowed(2, 0), 0)


class ThursdayFlags(unittest.TestCase):
    def test_thursday_flags_priority_pace_and_under_floor(self):
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-10-06",
                        "session_type": "push",
                        "exercises": [
                            {"name": "DB Flat Press", "sets": [_row(200, 2, 8)]}
                        ],
                    }
                ],
                "workout_store": {"catalog": CATALOG},
            },
            as_of="2026-10-15",
        )
        coach = report["coach"]
        self.assertTrue(coach["active"])
        self.assertTrue(coach["suggestions_only"])
        kinds = {(flag["group"], flag["kind"]) for flag in coach["flags"]}
        self.assertIn(("side_delts", "priority_pace"), kinds)
        self.assertIn(("chest", "under_floor"), kinds)
        wednesday = build_muscle_set_report(
            {"sessions": [], "workout_store": {"catalog": CATALOG}},
            as_of="2026-10-14",
        )
        self.assertFalse(wednesday["coach"]["active"])


class AgentFeed(unittest.TestCase):
    def test_muscle_sets_are_additive_on_the_current_week(self):
        payload = {
            "meta": {"local_today": "2026-10-15"},
            "coach": {"today": {"date": "2026-10-15"}},
            "sessions": [
                {
                    "date": "2026-10-13",
                    "session_type": "push",
                    "volume": 3200,
                    "exercises": [
                        {
                            "name": "DB Flat Press",
                            "sets": [_row(200, 2, 8)],
                            "volume": 3200,
                        }
                    ],
                }
            ],
            "workout_store": {"catalog": CATALOG},
        }
        body = export_agent_today(payload)
        week = body["week"]
        self.assertEqual(week["start"], "2026-10-12")
        self.assertEqual(week["end"], "2026-10-15")
        self.assertEqual(week["logged_sessions"][0]["session_type"], "push")
        self.assertEqual(week["logged_sessions"][0]["volume"], 3200)
        self.assertIn("nutrition", week)
        self.assertIn("sleep", week)
        self.assertEqual(week["muscle_sets"]["chest"]["proper_sets"], 2.0)
        self.assertEqual(week["muscle_sets"]["chest"]["tier"], "red")
        self.assertIsNone(week["muscle_set_budget"]["cap_override"])
        self.assertIn("ok", body)
        self.assertIn("today", body)
        self.assertIn("guardrails", body)


class CatalogTags(unittest.TestCase):
    def test_both_copies_carry_the_reviewed_groups(self):
        root = REPO / "fitness" / "exercises" / "catalog.json"
        bundled = ROOT / "fitness" / "exercises" / "catalog.json"
        self.assertEqual(root.read_text(encoding="utf-8"), bundled.read_text(encoding="utf-8"))
        catalog = json.loads(root.read_text(encoding="utf-8"))
        self.assertEqual(catalog["updated_at"], "2026-10-16")
        by_id = {ex["id"]: ex for ex in catalog["exercises"]}
        self.assertEqual(len(by_id), 46)
        self.assertEqual(by_id["db-flat-press"]["primary_muscles"], ["chest"])
        self.assertEqual(by_id["db-flat-press"]["volume_primary"], ["chest"])
        self.assertEqual(
            by_id["db-flat-press"]["volume_secondary"], ["triceps", "front_delts"]
        )
        self.assertEqual(by_id["lateral-raises"]["volume_primary"], ["side_delts"])
        self.assertEqual(by_id["rdl"]["volume_secondary"], ["lower_back"])
        self.assertEqual(by_id["smith-btn-press"]["volume_primary"], ["side_delts"])
        self.assertEqual(by_id["wrist-roller"]["volume_primary"], [])
        self.assertEqual(by_id["band-adduction"]["volume_primary"], [])


class AccessoryTiebreakOnly(unittest.TestCase):
    def test_main_lifts_do_not_take_the_tiebreak(self):
        report = {
            "muscle_sets": {
                "side_delts": {"proper_sets": 0},
                "chest": {"proper_sets": 0},
            }
        }
        goals = {"main_lifts": ["DB Flat Press"]}
        self.assertGreater(accessory_tiebreak(LATERAL, report, goals), 0)
        self.assertEqual(accessory_tiebreak(PRESS, report, goals), 0)
        entry = ExerciseEntry(
            name="DB Flat Press",
            sets=[SetEntry(weight_lbs=200, sets=2, reps=8)],
        )
        self.assertEqual(entry.sets[0].reps, 8)


if __name__ == "__main__":
    unittest.main()
