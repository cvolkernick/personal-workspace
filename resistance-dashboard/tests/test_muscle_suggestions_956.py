"""Muscle-group suggestions, equipment prompts, and one-tap rotation (#956)."""

from __future__ import annotations

import unittest
from pathlib import Path

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.muscle_suggestions import (
    additions_blocked,
    apply_action,
    build_report,
    load_bands,
    load_gaps,
    tally_group_sets,
)

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-10-01"


def _bands():
    return {
        "window_days": 28,
        "secondary_fraction": 0.5,
        "dismiss_weeks": 4,
        "pin_weeks": 4,
        "equipment_hide_weeks": {"dont": 8, "maybe": 1},
        "groups": [
            {"id": "chest", "label": "Chest", "size": "major", "min": 10, "max": 20, "tags": ["chest"]},
            {"id": "triceps", "label": "Triceps", "size": "small", "min": 6, "max": 14, "tags": ["triceps"]},
            {"id": "abs", "label": "Abs / core", "size": "small", "min": 4, "max": 12, "tags": ["abs"]},
            {"id": "rear_delts", "label": "Rear delts", "size": "small", "min": 6, "max": 16, "tags": ["rear_delts"]},
            {"id": "side_delts", "label": "Side delts", "size": "small", "min": 8, "max": 18, "tags": ["side_delts"]},
            {"id": "biceps", "label": "Biceps", "size": "small", "min": 6, "max": 16, "tags": ["biceps"]},
            {"id": "upper_back_lats", "label": "Upper back / lats", "size": "major", "min": 10, "max": 20, "tags": ["lats", "back", "upper_back"]},
        ],
    }


def _gaps():
    return {
        "items": [
            {
                "id": "cable-pulley",
                "name": "Cable / pulley",
                "tag": "cable",
                "muscles": ["rear_delts"],
                "unlocks": [
                    {"id": "face-pulls", "name": "Face pulls"},
                    {"id": "cable-reverse-fly", "name": "Cable reverse flyes"},
                ],
            },
            {
                "id": "resistance-bands",
                "name": "Resistance bands",
                "tag": "resistance_band",
                "muscles": ["rear_delts"],
                "unlocks": [{"id": "band-pull-apart", "name": "Band pull-aparts"}],
            },
            {
                "id": "ab-wheel",
                "name": "Ab wheel",
                "tag": "ab_wheel",
                "muscles": ["abs"],
                "unlocks": [{"id": "ab-wheel-rollout", "name": "Ab wheel rollout"}],
            },
            {
                "id": "roman-chair",
                "name": "Roman chair",
                "tag": "roman_chair",
                "muscles": ["abs"],
                "unlocks": [{"id": "roman-chair-raise", "name": "Roman chair knee raises"}],
            },
        ]
    }


def _catalog():
    return {
        "exercises": [
            {
                "id": "db-flat-press",
                "name": "DB Flat Press",
                "session_types": ["push"],
                "primary_muscles": ["chest", "triceps"],
                "secondary_muscles": ["abs"],
                "movement": "compound",
                "equipment": ["dumbbells"],
                "priority": 10,
                "available": True,
            },
            {
                "id": "face-pulls",
                "name": "Face Pulls",
                "session_types": ["pull"],
                "primary_muscles": ["rear_delts"],
                "secondary_muscles": ["upper_back"],
                "movement": "isolation",
                "equipment": ["cable"],
                "priority": 7,
                "available": True,
            },
            {
                "id": "band-pull-apart",
                "name": "Band Pull-Apart",
                "session_types": ["pull"],
                "primary_muscles": ["rear_delts"],
                "secondary_muscles": [],
                "movement": "isolation",
                "equipment": ["resistance_band"],
                "priority": 5,
                "available": False,
            },
            {
                "id": "lateral-raises",
                "name": "Lateral Raises",
                "session_types": ["push"],
                "primary_muscles": ["shoulders"],
                "secondary_muscles": [],
                "movement": "isolation",
                "equipment": ["dumbbells"],
                "priority": 6,
                "available": True,
            },
            {
                "id": "db-curls",
                "name": "DB Curls",
                "session_types": ["pull"],
                "primary_muscles": ["biceps"],
                "secondary_muscles": [],
                "movement": "isolation",
                "equipment": ["dumbbells"],
                "priority": 6,
                "available": True,
            },
            {
                "id": "hammer-curls",
                "name": "Hammer Curls",
                "session_types": ["pull"],
                "primary_muscles": ["biceps", "forearms"],
                "secondary_muscles": [],
                "movement": "isolation",
                "equipment": ["dumbbells"],
                "priority": 4,
                "available": True,
            },
            {
                "id": "ab-wheel-rollout",
                "name": "Ab Wheel Rollout",
                "session_types": ["legs"],
                "primary_muscles": ["abs"],
                "secondary_muscles": [],
                "movement": "isolation",
                "equipment": ["ab_wheel"],
                "priority": 5,
                "available": False,
            },
        ]
    }


def _gear(*tags):
    return {
        "items": [
            {"id": tag, "name": tag, "tag": tag, "source": "owned"}
            for tag in tags
        ]
    }


def _session(day, name, sets, reps, weight=50.0, kind="push"):
    return Session(
        date=day,
        session_type=kind,
        exercises=[
            ExerciseEntry(
                name=name,
                sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
            )
        ],
    )


def _row(gid):
    tally = tally_group_sets(
        [_session("2026-09-20", "DB Flat Press", 4, 8)],
        _catalog(),
        _bands(),
        as_of=AS_OF,
    )
    return next(row for row in tally["groups"] if row["id"] == gid)


class SetCounting(unittest.TestCase):
    def test_primary_is_full_credit_not_split(self):
        chest = _row("chest")
        triceps = _row("triceps")
        # 4 hard sets across 4 weeks. Both primaries get the full 4, not 2.
        self.assertEqual(chest["sets_per_week"], 1.0)
        self.assertEqual(triceps["sets_per_week"], 1.0)
        self.assertEqual(chest["direct_sets_per_week"], 1.0)

    def test_secondary_is_half_and_zero_direct_is_neglected(self):
        abs_row = _row("abs")
        self.assertEqual(abs_row["direct_sets_per_week"], 0.0)
        self.assertEqual(abs_row["sets_per_week"], 0.5)
        self.assertEqual(abs_row["status"], "neglected")

    def test_under_when_direct_work_misses_the_floor(self):
        chest = _row("chest")
        self.assertEqual(chest["status"], "under")
        self.assertTrue(chest["flagged"])

    def test_side_delt_isolation_counts_direct(self):
        tally = tally_group_sets(
            [_session("2026-09-20", "Lateral Raises", 8, 12, kind="push")],
            _catalog(),
            _bands(),
            as_of=AS_OF,
        )
        side = next(row for row in tally["groups"] if row["id"] == "side_delts")
        self.assertEqual(side["direct_sets_per_week"], 2.0)
        self.assertEqual(side["status"], "under")

    def test_on_target_when_the_window_clears_the_floor(self):
        bands = _bands()
        for group in bands["groups"]:
            if group["id"] == "chest":
                group["min"] = 1
                group["max"] = 20
        tally = tally_group_sets(
            [_session("2026-09-20", "DB Flat Press", 4, 8)],
            _catalog(),
            bands,
            as_of=AS_OF,
        )
        chest = next(row for row in tally["groups"] if row["id"] == "chest")
        self.assertEqual(chest["status"], "ok")
        self.assertFalse(chest["flagged"])


class Suggestions(unittest.TestCase):
    def test_inventory_filters_and_gap_prompts_when_nothing_fits(self):
        report = build_report(
            [],
            _catalog(),
            _gear("dumbbells"),
            bands=_bands(),
            gaps=_gaps(),
            as_of=AS_OF,
            recovery_score=70,
        )
        rear = next(row for row in report["groups"] if row["id"] == "rear_delts")
        self.assertEqual(rear["suggestions"], [])
        names = [item["name"] for item in rear["equipment_prompts"]]
        self.assertIn("Cable / pulley", names)
        self.assertIn("Resistance bands", names)
        self.assertGreaterEqual(len(names), 2)
        self.assertLessEqual(len(names), 4)
        self.assertNotIn("cart", report)
        self.assertNotIn("price", report)

    def test_owned_gear_hides_the_prompt_and_offers_the_lift(self):
        report = build_report(
            [],
            _catalog(),
            _gear("cable"),
            bands=_bands(),
            gaps=_gaps(),
            as_of=AS_OF,
            recovery_score=70,
        )
        rear = next(row for row in report["groups"] if row["id"] == "rear_delts")
        self.assertEqual([s["id"] for s in rear["suggestions"]], ["face-pulls"])
        self.assertEqual(rear["equipment_prompts"], [])

    def test_have_it_updates_inventory_and_refreshes(self):
        result = apply_action(
            "equipment",
            {"item_id": "ab-wheel", "choice": "have"},
            catalog=_catalog(),
            equipment=_gear("dumbbells"),
            goals={},
            sessions=[],
            plan={"session_type": "legs", "exercises": []},
            state={},
            as_of=AS_OF,
            recovery_score=70,
            bands=_bands(),
            gaps=_gaps(),
        )
        tags = {item["tag"] for item in result["equipment"]["items"]}
        self.assertIn("ab_wheel", tags)
        self.assertIn("Nothing was purchased", result["equipment"]["items"][-1]["notes"])
        abs_row = next(row for row in result["muscle_suggestions"]["groups"] if row["id"] == "abs")
        self.assertEqual([s["id"] for s in abs_row["suggestions"]], ["ab-wheel-rollout"])
        blob = str(result)
        self.assertNotIn("cart", blob.lower())

    def test_rotation_skips_the_last_suggestion_when_another_exists(self):
        report = build_report(
            [],
            _catalog(),
            _gear("cable", "resistance_band"),
            state={"last_suggested": {"rear_delts": "face-pulls"}},
            bands=_bands(),
            gaps=_gaps(),
            as_of=AS_OF,
            recovery_score=70,
        )
        rear = next(row for row in report["groups"] if row["id"] == "rear_delts")
        ids = [s["id"] for s in rear["suggestions"]]
        self.assertNotIn("face-pulls", ids)
        self.assertIn("band-pull-apart", ids)

    def test_dismiss_hides_for_four_weeks_then_returns(self):
        dismissed = apply_action(
            "dismiss",
            {"exercise_id": "face-pulls", "group_id": "rear_delts"},
            catalog=_catalog(),
            equipment=_gear("cable", "resistance_band"),
            goals={},
            sessions=[],
            plan=None,
            state={},
            as_of=AS_OF,
            recovery_score=70,
            bands=_bands(),
            gaps=_gaps(),
        )
        self.assertEqual(dismissed["state"]["dismissed"]["face-pulls"], "2026-10-29")
        rear = next(
            row for row in dismissed["muscle_suggestions"]["groups"] if row["id"] == "rear_delts"
        )
        self.assertNotIn("face-pulls", [s["id"] for s in rear["suggestions"]])
        later = build_report(
            [],
            _catalog(),
            _gear("cable"),
            state=dismissed["state"],
            bands=_bands(),
            gaps=_gaps(),
            as_of="2026-10-29",
            recovery_score=70,
        )
        rear_later = next(row for row in later["groups"] if row["id"] == "rear_delts")
        self.assertIn("face-pulls", [s["id"] for s in rear_later["suggestions"]])

    def test_dont_and_maybe_use_eight_weeks_and_one_week(self):
        dont = apply_action(
            "equipment",
            {"item_id": "cable-pulley", "choice": "dont"},
            catalog=_catalog(),
            equipment=_gear("dumbbells"),
            goals={},
            sessions=[],
            plan=None,
            state={},
            as_of=AS_OF,
            bands=_bands(),
            gaps=_gaps(),
        )
        self.assertEqual(dont["state"]["equipment_hides"]["cable-pulley"], "2026-11-26")
        maybe = apply_action(
            "equipment",
            {"item_id": "resistance-bands", "choice": "maybe"},
            catalog=_catalog(),
            equipment=_gear("dumbbells"),
            goals={},
            sessions=[],
            plan=None,
            state={},
            as_of=AS_OF,
            bands=_bands(),
            gaps=_gaps(),
        )
        self.assertEqual(maybe["state"]["equipment_hides"]["resistance-bands"], "2026-10-08")

    def test_empty_state_when_nothing_is_flagged(self):
        bands = {
            "window_days": 28,
            "groups": [
                {"id": "chest", "label": "Chest", "min": 1, "max": 20, "tags": ["chest"]},
            ],
        }
        report = build_report(
            [_session("2026-09-20", "DB Flat Press", 4, 8)],
            _catalog(),
            _gear("dumbbells"),
            bands=bands,
            gaps=_gaps(),
            as_of=AS_OF,
            recovery_score=70,
        )
        self.assertEqual(report["flagged_count"], 0)
        self.assertEqual(report["summary"], "All muscle groups on target.")
        self.assertEqual(report["groups"], [])


class Rotation(unittest.TestCase):
    def _pull_plan(self):
        return {
            "session_type": "pull",
            "is_rest_day": False,
            "exercises": [
                {
                    "id": "seated-cable-row",
                    "name": "Seated Cable Row",
                    "movement": "compound",
                    "primary_muscles": ["back"],
                },
                {
                    "id": "hammer-curls",
                    "name": "Hammer Curls",
                    "movement": "isolation",
                    "primary_muscles": ["biceps", "forearms"],
                },
            ],
        }

    def test_add_swaps_accessory_and_follows_double_progression(self):
        sessions = [
            _session("2026-09-30", "DB Curls", 2, 15, weight=30.0, kind="pull")
        ]
        before = self._pull_plan()
        result = apply_action(
            "add",
            {"exercise_id": "db-curls", "group_id": "biceps", "session_type": "pull"},
            catalog=_catalog(),
            equipment=_gear("dumbbells"),
            goals={"default_hard_sets": 2},
            sessions=sessions,
            plan=before,
            state={},
            as_of=AS_OF,
            recovery_score=70,
            bands=_bands(),
            gaps=_gaps(),
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["pending"])
        exercises = result["plan"]["exercises"]
        self.assertEqual(len(exercises), len(before["exercises"]))
        ids = [ex["id"] for ex in exercises]
        self.assertIn("db-curls", ids)
        self.assertNotIn("hammer-curls", ids)
        self.assertIn("seated-cable-row", ids)
        added = next(ex for ex in exercises if ex["id"] == "db-curls")
        self.assertEqual(added["progression_reason"], "add_load")
        self.assertEqual(added["prescription"]["weight_lbs"], 35.0)
        self.assertEqual(added["prescription"]["target_reps"], 8)
        self.assertEqual(added["target_reps"], 8)

    def test_recovery_under_40_does_not_rotate(self):
        before = self._pull_plan()
        result = apply_action(
            "add",
            {"exercise_id": "db-curls", "session_type": "pull"},
            catalog=_catalog(),
            equipment=_gear("dumbbells"),
            goals={},
            sessions=[],
            plan=before,
            state={},
            as_of=AS_OF,
            recovery_score=39,
            bands=_bands(),
            gaps=_gaps(),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "additions_blocked")
        self.assertEqual(result["plan"]["exercises"], before["exercises"])
        self.assertEqual(result["state"]["pins"], [])
        self.assertFalse(result["muscle_suggestions"]["additions_allowed"])

    def test_deload_blocks_the_add(self):
        blocked, note = additions_blocked(recovery_score=80, deload=True)
        self.assertTrue(blocked)
        self.assertIn("Deload", note)
        result = apply_action(
            "add",
            {"exercise_id": "db-curls", "session_type": "pull"},
            catalog=_catalog(),
            equipment=_gear("dumbbells"),
            goals={},
            sessions=[],
            plan=self._pull_plan(),
            state={},
            as_of=AS_OF,
            recovery_score=80,
            deload=True,
            bands=_bands(),
            gaps=_gaps(),
        )
        self.assertEqual(result["error"], "additions_blocked")
        self.assertEqual(result["state"]["pins"], [])

    def test_other_session_queues_without_changing_today(self):
        today = {
            "session_type": "push",
            "exercises": [
                {"id": "db-flat-press", "name": "DB Flat Press", "movement": "compound", "primary_muscles": ["chest"]},
            ],
        }
        result = apply_action(
            "add",
            {"exercise_id": "db-curls", "session_type": "pull"},
            catalog=_catalog(),
            equipment=_gear("dumbbells"),
            goals={},
            sessions=[],
            plan=today,
            state={},
            as_of=AS_OF,
            recovery_score=70,
            bands=_bands(),
            gaps=_gaps(),
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["pending"])
        self.assertEqual(result["plan"]["exercises"], today["exercises"])
        self.assertEqual(result["state"]["pins"][0]["session_type"], "pull")
        self.assertEqual(result["state"]["pins"][0]["until"], "2026-10-29")


class ShippedData(unittest.TestCase):
    def test_bands_cover_the_ask_and_major_floors(self):
        bands = load_bands()
        ids = [group["id"] for group in bands["groups"]]
        for required in (
            "chest",
            "upper_back_lats",
            "rear_delts",
            "side_delts",
            "biceps",
            "triceps",
            "quads",
            "hamstrings",
            "glutes",
            "calves",
            "abs",
            "forearms",
            "adductors",
            "lower_back",
        ):
            self.assertIn(required, ids)
        by_id = {group["id"]: group for group in bands["groups"]}
        for major in ("chest", "upper_back_lats", "quads", "hamstrings", "glutes"):
            self.assertEqual(by_id[major]["min"], 10)
            self.assertEqual(by_id[major]["max"], 20)
        self.assertLess(by_id["forearms"]["min"], 10)
        self.assertLess(by_id["adductors"]["min"], 10)
        self.assertEqual(bands["window_days"], 28)
        self.assertEqual(bands["dismiss_weeks"], 4)

    def test_gap_map_is_data_and_the_ui_does_not_hardcode_it(self):
        gaps = load_gaps()
        names = [item["name"] for item in gaps["items"]]
        self.assertIn("Cable / pulley", names)
        self.assertIn("Resistance bands", names)
        self.assertIn("Hamstring curl attachment", names)
        self.assertIn("Nordic strap", names)
        self.assertIn("Calf block or step", names)
        self.assertIn("Dip belt", names)
        self.assertIn("Ab wheel", names)
        self.assertIn("Wrist roller", names)
        self.assertIn("Fat grips", names)
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("Ab wheel", js)
        self.assertNotIn("Nordic strap", js)
        self.assertIn("Do you have any of these?", js)
        self.assertIn("/api/muscle-suggestions", js)
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-collapse="more-suggestions"', html)
        self.assertIn("Suggested additions", html)


class Route(unittest.TestCase):
    def test_have_it_route_returns_gear_without_writing_turso(self):
        import os
        from unittest import mock

        from api.workout._util import client_route_name, suggestions_write

        self.assertEqual(client_route_name({}, "", "/api/muscle-suggestions"), "suggestions")
        dash = {
            "sessions": [],
            "recovery": {"score": 70, "sparse": False, "inputs": {}},
            "meta": {"local_today": AS_OF},
            "workout_store": {
                "catalog": _catalog(),
                "equipment": _gear("dumbbells"),
                "goals": {},
                "plan": {
                    "session_type": "legs",
                    "exercises": [{"id": "squat", "name": "Squat", "movement": "compound"}],
                },
            },
        }
        env = {"TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": ""}
        with mock.patch.dict(os.environ, env), mock.patch(
            "api.workout._util.require_user",
            return_value=({"id": "suggest-route-test"}, None),
        ), mock.patch(
            "api.dashboard.dashboard_body",
            return_value=(200, dash),
        ), mock.patch(
            "rt_dashboard.equipment_store.save_preview_equipment",
            side_effect=RuntimeError("turso env missing"),
        ):
            status, body = suggestions_write(
                {},
                {"action": "equipment", "item_id": "ab-wheel", "choice": "have"},
            )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertNotIn("state", body)
        self.assertEqual(body["write"]["source"], "unpersisted")
        tags = {item["tag"] for item in body["equipment"]["items"]}
        self.assertIn("ab_wheel", tags)
        abs_row = next(row for row in body["muscle_suggestions"]["groups"] if row["id"] == "abs")
        self.assertEqual([s["id"] for s in abs_row["suggestions"]], ["ab-wheel-rollout"])
        self.assertNotIn("cart", str(body).lower())


if __name__ == "__main__":
    unittest.main()
