"""Priority-volume ramp (lats, then chest, then quads) and the lowering cue."""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.muscle_sets import build_muscle_set_report
from rt_dashboard.grok_planner import generate_grok_plans
from rt_dashboard.priority_ramp import (
    LOWERING_CUE,
    bump_priority_sets,
    display_targets,
    prompt_volume_fields,
)
from rt_dashboard.workout_planner import (
    default_goals,
    generate_workout_plan,
    muscle_targets,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

RAMP = {
    "rotation": ["push", "pull", "legs"],
    "exercises_per_session": 5,
    "prefer_compounds_first": True,
    "auto_focus_muscles": False,
    "focus_muscles": [],
    "default_hard_sets": 2,
    "sets_per_muscle_week_min": 4,
    "sets_per_muscle_week_max": 8,
    "sets_per_muscle_week_priority_max": 10,
    "maintenance_sets_per_muscle_week": 3,
    "session_working_set_cap": 14,
    "secondary_set_fraction": 0.5,
    "priority_muscles_ordered": ["lats", "chest", "quads"],
    "priority_sets_target": 10,
    "priority_ramp_increment": 2,
    "priority_ramp_started_on": "2026-11-09",
    "priority_keep_others_in_band": True,
}

OTHERS = (
    "delts",
    "triceps",
    "biceps",
    "hamstrings",
    "glutes",
    "calves",
    "abs",
    "traps",
    "mid_upper_back",
)


def _session(day, kind, name, weight, sets, reps):
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


def _pull_catalog():
    return {
        "exercises": [
            {
                "id": "pulldowns",
                "name": "Pulldowns",
                "session_types": ["pull"],
                "primary_muscles": ["lats"],
                "secondary_muscles": ["biceps"],
                "movement": "compound",
                "default_sets": 3,
                "default_reps": 8,
                "rep_range": [8, 12],
                "priority": 10,
                "available": True,
            },
            {
                "id": "seated-cable-row",
                "name": "Seated Cable Row",
                "session_types": ["pull"],
                "primary_muscles": ["back"],
                "secondary_muscles": ["biceps"],
                "movement": "compound",
                "default_sets": 3,
                "default_reps": 8,
                "rep_range": [8, 12],
                "priority": 9,
                "available": True,
            },
            {
                "id": "db-curls",
                "name": "DB Curls",
                "session_types": ["pull"],
                "primary_muscles": ["biceps"],
                "movement": "isolation",
                "default_sets": 3,
                "default_reps": 10,
                "rep_range": [8, 12],
                "priority": 5,
                "available": True,
            },
            {
                "id": "db-shrugs",
                "name": "DB Shrugs",
                "session_types": ["pull"],
                "primary_muscles": ["traps"],
                "movement": "isolation",
                "default_sets": 3,
                "default_reps": 10,
                "rep_range": [8, 12],
                "priority": 4,
                "available": True,
            },
            {
                "id": "db-flat-press",
                "name": "DB Flat Press",
                "session_types": ["push"],
                "primary_muscles": ["chest"],
                "secondary_muscles": ["triceps"],
                "movement": "compound",
                "default_sets": 3,
                "default_reps": 8,
                "rep_range": [8, 12],
                "priority": 10,
                "available": True,
            },
        ]
    }


def _lat_credits(plan) -> float:
    total = 0.0
    for row in plan.get("exercises") or []:
        total += float((row.get("set_credits") or {}).get("lats") or 0)
    return total


def _row(plan, name):
    for row in plan.get("exercises") or []:
        if row.get("name") == name:
            return row
    return None


class GoalsFileTests(unittest.TestCase):
    def test_goals_files_expose_the_ramp_and_priority_max_10(self):
        for path in (
            REPO / "fitness" / "exercises" / "goals.json",
            ROOT / "fitness" / "exercises" / "goals.json",
        ):
            raw = json.loads(path.read_text())
            self.assertEqual(raw["priority_muscles_ordered"], ["lats", "chest", "quads"])
            self.assertEqual(raw["priority_sets_target"], 10)
            self.assertEqual(raw["priority_ramp_increment"], 2)
            self.assertEqual(raw["priority_ramp_started_on"], "2026-11-09")
            self.assertIs(raw["priority_keep_others_in_band"], True)
            self.assertEqual(raw["sets_per_muscle_week_priority_max"], 10)
            self.assertEqual(raw["sets_per_muscle_week_min"], 4)
            self.assertEqual(raw["sets_per_muscle_week_max"], 8)
            self.assertEqual(raw["session_working_set_cap"], 14)
        loaded = default_goals()
        self.assertEqual(loaded["sets_per_muscle_week_priority_max"], 10)
        self.assertEqual(loaded["priority_sets_target"], 10)
        self.assertTrue(loaded["priority_keep_others_in_band"])


class MuscleTargetTests(unittest.TestCase):
    def _bands(self, prior, **kwargs):
        return muscle_targets(RAMP, as_of="2026-11-09", prior_credits=prior, **kwargs)

    def test_plus_two_rate_limit_does_not_jump_to_10(self):
        bands = self._bands({"lats": 6}, lifts_status="green")
        self.assertEqual(bands["lats"]["max"], 8)
        self.assertEqual(bands["lats"]["min"], 6)
        self.assertTrue(bands["lats"]["priority"])
        self.assertNotEqual(bands["lats"]["max"], 10)

    def test_chest_and_quads_wait_until_lats_are_near_10(self):
        mid = self._bands({"lats": 6, "chest": 5, "quads": 5}, lifts_status="green")
        self.assertEqual(mid["chest"]["min"], 4)
        self.assertEqual(mid["chest"]["max"], 8)
        self.assertFalse(mid["chest"]["priority"])
        self.assertEqual(mid["quads"]["max"], 8)
        self.assertFalse(mid["quads"]["priority"])
        still = self._bands({"lats": 8, "chest": 6, "quads": 6}, lifts_status="green")
        self.assertEqual(still["lats"]["max"], 10)
        self.assertEqual(still["chest"]["max"], 8)
        self.assertEqual(still["quads"]["max"], 8)
        nxt = muscle_targets(
            RAMP,
            as_of="2026-11-16",
            prior_credits={"lats": 10, "chest": 5, "quads": 4},
            lifts_status="green",
            prior_lifts_status="green",
        )
        self.assertEqual(nxt["lats"]["max"], 10)
        self.assertEqual(nxt["lats"]["min"], 8)
        self.assertEqual(nxt["chest"]["max"], 7)
        self.assertTrue(nxt["chest"]["priority"])
        self.assertEqual(nxt["quads"]["max"], 8)
        self.assertFalse(nxt["quads"]["priority"])

    def test_others_stay_in_4_8_while_lats_are_priority(self):
        bands = self._bands({"lats": 6}, lifts_status="green")
        for muscle in OTHERS:
            self.assertEqual(bands[muscle]["min"], 4, muscle)
            self.assertEqual(bands[muscle]["max"], 8, muscle)
            self.assertFalse(bands[muscle]["priority"], muscle)
        # A focus pin must not maintenance-drop the rest while the ramp is on.
        focused = muscle_targets(
            {**RAMP, "focus_muscles": ["lats"], "auto_focus_muscles": False},
            as_of="2026-11-09",
            prior_credits={"lats": 6},
            lifts_status="green",
        )
        self.assertEqual(focused["delts"]["max"], 8)
        self.assertGreaterEqual(focused["hamstrings"]["min"], 4)
        self.assertNotEqual(focused["triceps"]["max"], 3)

    def test_legacy_focus_still_drops_to_maintenance_when_the_ramp_is_off(self):
        bands = muscle_targets(
            {
                "priority_keep_others_in_band": False,
                "focus_muscles": ["lats"],
                "priority_ramp_started_on": "2026-11-09",
                "sets_per_muscle_week_priority_max": 10,
                "maintenance_sets_per_muscle_week": 3,
            },
            as_of="2026-11-09",
            prior_credits={"lats": 6},
        )
        self.assertEqual(bands["lats"]["max"], 10)
        self.assertEqual(bands["delts"]["max"], 3)
        self.assertLess(bands["biceps"]["min"], 4)

    def test_lifts_yellow_rolls_back_and_green_week_resumes(self):
        rolled = self._bands({"lats": 6}, lifts_status="yellow")
        self.assertEqual(rolled["lats"]["max"], 6)
        self.assertNotEqual(rolled["lats"]["max"], 8)
        held = muscle_targets(
            RAMP,
            as_of="2026-11-16",
            prior_credits={"lats": 6},
            lifts_status="green",
            prior_lifts_status="yellow",
        )
        self.assertEqual(held["lats"]["max"], 6)
        resumed = muscle_targets(
            RAMP,
            as_of="2026-11-23",
            prior_credits={"lats": 6},
            lifts_status="green",
            prior_lifts_status="green",
        )
        self.assertEqual(resumed["lats"]["max"], 8)
        restore = self._bands({"lats": 6}, lifts_status="green", restore_flag=True)
        self.assertEqual(restore["lats"]["max"], 6)

    def test_several_muscles_at_10_drop_toward_8_on_rollback(self):
        bands = self._bands(
            {"lats": 10, "chest": 10, "quads": 4},
            lifts_status="red",
        )
        self.assertEqual(bands["lats"]["max"], 8)
        self.assertEqual(bands["chest"]["max"], 8)
        self.assertEqual(bands["quads"]["max"], 4)
        self.assertEqual(bands["hamstrings"]["max"], 8)
        self.assertGreaterEqual(bands["delts"]["min"], 4)


class PullPlanTests(unittest.TestCase):
    def _plan(self, **kwargs):
        sessions = [
            _session("2026-11-02", "pull", "Pulldowns", 100, 6, 8),
            _session("2026-11-08", "push", "DB Flat Press", 50, 2, 8),
        ]
        return generate_workout_plan(
            _pull_catalog(),
            RAMP,
            sessions,
            recovery_score=80,
            recovery_label="Ready",
            session_type="pull",
            as_of="2026-11-09",
            **kwargs,
        )

    def test_logged_6_targets_8_and_the_session_stays_within_the_cap(self):
        plan = self._plan(lifts_status="green", prior_lifts_status="green")
        bands = plan["volume"]["bands"]
        self.assertEqual(bands["lats"]["max"], 8)
        self.assertEqual(bands["chest"]["max"], 8)
        self.assertEqual(bands["quads"]["max"], 8)
        for muscle in ("delts", "triceps", "biceps", "hamstrings"):
            self.assertEqual(bands[muscle]["min"], 4, muscle)
            self.assertEqual(bands[muscle]["max"], 8, muscle)
        credits = _lat_credits(plan)
        self.assertLessEqual(credits, 8)
        self.assertGreater(credits, 4)
        self.assertLessEqual(credits, 6)
        pulldown = _row(plan, "Pulldowns")
        self.assertIsNotNone(pulldown)
        rx = pulldown["prescription"]
        self.assertLessEqual(int(rx["sets"]), 6)
        self.assertGreaterEqual(int(rx["sets"]), 2)
        self.assertNotIn("3-0-1", str(rx.get("reps")))
        self.assertNotIn("3-0-1", str(rx.get("sets")))
        self.assertNotIn("3-0-1", str(rx.get("weight_lbs")))
        total = sum(int((e.get("prescription") or {}).get("sets") or 0) for e in plan["exercises"])
        self.assertLessEqual(total, 14)
        self.assertGreaterEqual(total, 1)
        names = [e["name"] for e in plan["exercises"]]
        compounds = [e["name"] for e in plan["exercises"] if e.get("movement") == "compound"]
        self.assertEqual(names[: len(compounds)], compounds)
        message = plan["message"]
        self.assertIn("Priority volume: lats toward ~10 hard sets/week (week 1 of ramp)", message)
        self.assertIn("other muscles 4–8", message)
        self.assertIn("Session cap ~14", message)
        self.assertIn("2–3 seconds", message)
        self.assertIn(LOWERING_CUE, message)
        self.assertNotIn("Framework: ≈4–8 sets/muscle/week", message)

    def test_a_deload_log_this_week_pauses_the_ramp(self):
        sessions = [
            _session("2026-11-02", "pull", "Pulldowns", 100, 6, 8),
            Session(
                date="2026-11-09",
                session_type="push",
                notes="Restore deload",
                exercises=[
                    ExerciseEntry(
                        name="DB Flat Press",
                        sets=[SetEntry(weight_lbs=50, sets=2, reps=8)],
                    )
                ],
            ),
        ]
        plan = generate_workout_plan(
            _pull_catalog(),
            RAMP,
            sessions,
            recovery_score=80,
            session_type="pull",
            as_of="2026-11-09",
            lifts_status="green",
        )
        self.assertEqual(plan["volume"]["bands"]["lats"]["max"], 6)
        self.assertIn("ramp paused", plan["message"])

    def test_lifts_yellow_uses_the_rolled_back_target(self):
        plan = self._plan(lifts_status="yellow")
        self.assertEqual(plan["volume"]["bands"]["lats"]["max"], 6)
        self.assertLessEqual(_lat_credits(plan), 6)
        self.assertIn("ramp paused", plan["message"])
        self.assertIn("2–3 seconds", plan["message"])
        total = sum(int((e.get("prescription") or {}).get("sets") or 0) for e in plan["exercises"])
        self.assertLessEqual(total, 14)


class BumpTests(unittest.TestCase):
    def test_trim_isolation_before_adding_and_never_cut_a_compound_below_2(self):
        pulldown = {
            "id": "pulldowns",
            "name": "Pulldowns",
            "movement": "compound",
            "primary_muscles": ["lats"],
            "secondary_muscles": [],
            "prescription": {"sets": 2, "reps": 8, "weight_lbs": 100},
            "set_credits": {"lats": 2},
        }
        curls = {
            "id": "db-curls",
            "name": "DB Curls",
            "movement": "isolation",
            "primary_muscles": ["biceps"],
            "secondary_muscles": [],
            "prescription": {"sets": 12, "reps": 10, "weight_lbs": 25},
            "set_credits": {"biceps": 12},
        }
        bumped = bump_priority_sets(
            [pulldown, curls],
            muscle="lats",
            increment=2,
            weekly_max=10,
            logged=0,
            session_cap=14,
        )
        rows = {row["name"]: row for row in bumped["exercises"]}
        self.assertEqual(rows["Pulldowns"]["prescription"]["sets"], 4)
        self.assertGreaterEqual(rows["Pulldowns"]["prescription"]["sets"], 2)
        self.assertEqual(rows["Pulldowns"]["prescription"]["reps"], 8)
        self.assertEqual(rows["Pulldowns"]["prescription"]["weight_lbs"], 100)
        self.assertEqual(rows["DB Curls"]["prescription"]["sets"], 10)
        self.assertEqual(bumped["session_sets"], 14)
        self.assertEqual(bumped["added_credits"], 2)

    def test_new_exercise_only_when_no_priority_compound_can_take_a_set(self):
        curls = {
            "id": "db-curls",
            "name": "DB Curls",
            "movement": "isolation",
            "primary_muscles": ["biceps"],
            "prescription": {"sets": 2, "reps": 10, "weight_lbs": 20},
            "set_credits": {"biceps": 2},
        }

        def _make():
            return {
                "id": "pulldowns",
                "name": "Pulldowns",
                "movement": "compound",
                "primary_muscles": ["lats"],
                "secondary_muscles": [],
                "prescription": {"sets": 1, "reps": 8, "weight_lbs": 90},
                "set_credits": {"lats": 1},
            }

        bumped = bump_priority_sets(
            [curls],
            muscle="lats",
            increment=2,
            weekly_max=8,
            logged=0,
            session_cap=14,
            make_exercise=_make,
        )
        names = [row["name"] for row in bumped["exercises"]]
        self.assertEqual(names[0], "Pulldowns")
        pulldown = bumped["exercises"][0]
        self.assertEqual(pulldown["prescription"]["sets"], 2)
        self.assertLessEqual(bumped["session_sets"], 14)

    def test_a_full_compound_session_is_not_cut_below_2(self):
        rows = [
            {
                "id": "pulldowns",
                "name": "Pulldowns",
                "movement": "compound",
                "primary_muscles": ["lats"],
                "prescription": {"sets": 2, "reps": 8, "weight_lbs": 100},
                "set_credits": {"lats": 2},
            },
            {
                "id": "seated-cable-row",
                "name": "Seated Cable Row",
                "movement": "compound",
                "primary_muscles": ["back"],
                "prescription": {"sets": 12, "reps": 8, "weight_lbs": 80},
                "set_credits": {"mid_upper_back": 12},
            },
        ]
        bumped = bump_priority_sets(
            rows,
            muscle="lats",
            increment=2,
            weekly_max=10,
            logged=0,
            session_cap=14,
        )
        by_name = {row["name"]: row for row in bumped["exercises"]}
        self.assertEqual(by_name["Pulldowns"]["prescription"]["sets"], 2)
        self.assertGreaterEqual(by_name["Seated Cable Row"]["prescription"]["sets"], 2)
        self.assertLessEqual(bumped["session_sets"], 14)


class PromptAndTrendsTests(unittest.TestCase):
    def test_model_prompt_gets_per_muscle_targets_and_the_cue(self):
        fields = prompt_volume_fields(
            RAMP,
            [_session("2026-11-02", "pull", "Pulldowns", 100, 6, 8)],
            _pull_catalog(),
            as_of="2026-11-09",
            lifts_status="green",
        )
        self.assertIn("muscle_targets", fields)
        self.assertEqual(fields["muscle_targets"]["lats"]["max"], 8)
        self.assertEqual(fields["muscle_targets"]["chest"]["max"], 8)
        self.assertEqual(fields["muscle_targets"]["delts"]["max"], 8)
        self.assertIn("lats toward ~10", fields["sets_per_muscle_week"])
        self.assertNotEqual(fields["sets_per_muscle_week"], "4-8")
        self.assertNotIn("DeanT 4-8", fields["volume_clause"])
        self.assertIn("2–3 seconds", fields["lowering_cue"])
        self.assertIn("2–3 seconds", fields["message_rule"])
        self.assertIn("Do not encode a tempo", fields["volume_clause"])

    def test_trends_labels_follow_the_ramp_for_lats_chest_and_quads(self):
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-11-02",
                        "session_type": "pull",
                        "exercises": [
                            {
                                "name": "Pulldowns",
                                "sets": [{"weight_lbs": 100, "sets": 6, "reps": 8}],
                            }
                        ],
                    }
                ],
                "workout_store": {
                    "goals": RAMP,
                    "catalog": _pull_catalog(),
                },
            },
            as_of="2026-11-09",
        )
        sets = report["muscle_sets"]
        self.assertEqual(sets["lats_upper_back"]["target"], "6-8")
        self.assertEqual(sets["chest"]["target"], "4-8")
        self.assertEqual(sets["quads"]["target"], "4-8")
        self.assertEqual(sets["side_delts"]["target"], "8-10")
        self.assertEqual(sets["hamstrings"]["target"], "4-7")


def _grok_answer(message: str) -> dict:
    return {
        "answer": json.dumps(
            {
                "meal": {"message": "ok", "items": [], "meals": []},
                "workout": {
                    "session_type": "pull",
                    "is_rest_day": False,
                    "message": message,
                    "exercises": [
                        {
                            "name": "Pulldowns",
                            "prescription": {"sets": 2, "reps": 8, "weight_lbs": 100},
                            "primary_muscles": ["lats"],
                        }
                    ],
                },
            }
        ),
        "model": "grok-test",
        "auth_source": "test",
    }


class ModelAndTrendsRollbackTests(unittest.TestCase):
    """The model prompt, stamped message, and Trends labels use the same rollback."""

    def _history(self):
        return [_session("2026-11-02", "pull", "Pulldowns", 100, 6, 8)]

    def _run_grok(self, *, recovery=None, lifts=None):
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return _grok_answer(
                "Priority volume: lats toward ~10 hard sets/week "
                "(week 1 of ramp); other muscles 4–8. Session cap ~14."
            )

        stack = ExitStack()
        stack.enter_context(
            mock.patch(
                "rt_dashboard.grok_ask.resolve_xai_credentials",
                return_value={"token": "t", "source": "test", "expired": False},
            )
        )
        stack.enter_context(
            mock.patch("rt_dashboard.grok_ask.chat_completions", side_effect=fake_chat)
        )
        if lifts is not None:
            stack.enter_context(
                mock.patch(
                    "rt_dashboard.priority_ramp.lifts_chip_status",
                    return_value=lifts,
                )
            )
        with stack:
            out = generate_grok_plans(
                "user-1",
                goals=RAMP,
                catalog=_pull_catalog(),
                sessions=self._history(),
                recovery=recovery or {"score": 80, "label": "Ready"},
                next_session_type="pull",
                as_of="2026-11-09",
            )
        raw = captured["messages"][1]["content"]
        start = raw.index("{")
        end = raw.rindex("}")
        return out, json.loads(raw[start : end + 1])

    def test_yellow_and_red_lifts_roll_the_model_path_back(self):
        fallback = muscle_targets(
            RAMP,
            as_of="2026-11-09",
            prior_credits={"lats": 6},
            lifts_status="yellow",
        )
        for status in ("yellow", "red"):
            with self.subTest(status=status):
                out, context = self._run_grok(lifts=status)
                targets = context["volume_caps"]["muscle_targets"]
                self.assertEqual(targets["lats"]["max"], fallback["lats"]["max"])
                self.assertEqual(targets["lats"]["max"], 6)
                self.assertEqual(targets["chest"]["max"], 8)
                self.assertEqual(targets["delts"]["max"], 8)
                self.assertIn("ramp paused", context["volume_caps"]["sets_per_muscle_week"])
                message = out["workout"]["message"]
                self.assertIn("ramp paused", message)
                self.assertNotIn("toward ~10", message)
                self.assertIn("2–3 seconds", message)

    def test_restore_deload_rolls_the_model_path_back(self):
        out, context = self._run_grok(
            recovery={"score": 80, "label": "Restore deload", "deload": True}
        )
        self.assertEqual(context["volume_caps"]["muscle_targets"]["lats"]["max"], 6)
        self.assertIn("ramp paused", out["workout"]["message"])
        self.assertNotIn("toward ~10", out["workout"]["message"])
        fields = prompt_volume_fields(
            RAMP,
            self._history(),
            _pull_catalog(),
            as_of="2026-11-09",
            restore_flag=True,
        )
        self.assertEqual(
            fields["muscle_targets"]["lats"]["max"],
            context["volume_caps"]["muscle_targets"]["lats"]["max"],
        )

    def test_display_targets_match_the_fallback_rollback(self):
        sessions = self._history()
        catalog = _pull_catalog()
        fallback = muscle_targets(
            RAMP,
            as_of="2026-11-09",
            prior_credits={"lats": 6},
            lifts_status="yellow",
        )
        with mock.patch(
            "rt_dashboard.priority_ramp.lifts_chip_status",
            return_value="yellow",
        ):
            labels = display_targets(RAMP, sessions, catalog, as_of="2026-11-09")
        self.assertEqual(labels["lats_upper_back"], "4-6")
        self.assertEqual(float(labels["lats_upper_back"].split("-")[1]), fallback["lats"]["max"])
        self.assertEqual(labels["chest"], "4-8")
        self.assertEqual(labels["quads"], "4-8")
        report = build_muscle_set_report(
            {
                "sessions": [
                    {
                        "date": "2026-11-02",
                        "session_type": "pull",
                        "exercises": [
                            {
                                "name": "Pulldowns",
                                "sets": [{"weight_lbs": 100, "sets": 6, "reps": 8}],
                            }
                        ],
                    }
                ],
                "recovery": {"label": "recovery week", "recovery_week": True},
                "workout_store": {"goals": RAMP, "catalog": catalog},
            },
            as_of="2026-11-09",
        )
        self.assertEqual(report["muscle_sets"]["lats_upper_back"]["target"], "4-6")
        self.assertEqual(report["muscle_sets"]["side_delts"]["target"], "8-10")


if __name__ == "__main__":
    unittest.main()
