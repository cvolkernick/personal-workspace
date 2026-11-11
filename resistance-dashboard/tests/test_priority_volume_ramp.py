"""Equal volume ramp toward ~10 hard sets/week.

The 2–3 second lowering cue is standing coaching only. It must not appear
on the workout message, in the planner prompt, or on the Today payload.
"""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from rt_dashboard.coach import build_today_board
from rt_dashboard.grok_planner import generate_grok_plans
from rt_dashboard.models import ExerciseEntry, RecoveryStatus, Session, SetEntry
from rt_dashboard.muscle_sets import build_muscle_set_report
from rt_dashboard.priority_ramp import (
    bump_equal_sets,
    display_targets,
    ensure_workout_message,
    prompt_volume_fields,
)
from rt_dashboard.workout_planner import (
    MAJOR_MUSCLES,
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
    "auto_focus_muscles": True,
    "focus_muscles": [],
    "default_hard_sets": 2,
    "sets_per_muscle_week_min": 4,
    "sets_per_muscle_week_max": 10,
    "sets_per_muscle_week_priority_max": 10,
    "maintenance_sets_per_muscle_week": 3,
    "session_working_set_cap": 14,
    "secondary_set_fraction": 0.5,
    "volume_sets_target": 10,
    "volume_ramp_increment": 2,
    "volume_ramp_started_on": "2026-11-09",
    "volume_ramp_equal": True,
}

CLIMB = (
    "Volume: ramping major muscles toward ~10 hard sets/week "
    "(at most +1–2 credits each from last week’s logged sets). Session cap ~14."
)

# Phrases that mean the controlled-lowering cue. Bare "lower" and "controlled"
# are not cues (muscle names and "uncontrolled" show up elsewhere).
_CUE_MARKERS = (
    "2–3 s",
    "2-3 s",
    "2–3s",
    "2-3s",
    "2–3 second",
    "2-3 second",
    "lower the weight",
    "lowering",
    "under control",
    "controlled lower",
    "eccentric",
    "tempo",
    "extra pause at the bottom",
    "lowering_cue",
)

_MODEL_CUE = (
    "On main lifts (bench, rows, overhead press, squat, hack squat, leg press, "
    "deadlift, RDL, and pull-ups), lower the weight under control for 2–3 seconds, "
    "then lift as usual on the way up. No extra pause at the bottom unless the lift "
    "already has one. Use a 2-3 s eccentric tempo on the way down."
)


def _assert_no_lowering_cue(case, text, where):
    blob = str(text).lower()
    for marker in _CUE_MARKERS:
        case.assertNotIn(
            marker.lower(),
            blob,
            f"{where} still contains a lowering/tempo cue ({marker!r})",
        )


def _session(day, kind, name, weight, sets, reps, notes=""):
    return Session(
        date=day,
        session_type=kind,
        exercises=[
            ExerciseEntry(
                name=name,
                sets=[SetEntry(weight_lbs=weight, sets=sets, reps=reps)],
            )
        ],
        notes=notes,
    )


def _catalog():
    def row(eid, name, kind, primary, movement, priority=5):
        return {
            "id": eid,
            "name": name,
            "session_types": [kind],
            "primary_muscles": primary,
            "secondary_muscles": [],
            "movement": movement,
            "default_sets": 3,
            "default_reps": 8,
            "rep_range": [8, 12],
            "priority": priority,
            "available": True,
        }

    return {
        "exercises": [
            row("pulldowns", "Pulldowns", "pull", ["lats"], "compound", 10),
            row("seated-cable-row", "Seated Cable Row", "pull", ["back"], "compound", 9),
            row("db-curls", "DB Curls", "pull", ["biceps"], "isolation", 4),
            row("db-shrugs", "DB Shrugs", "pull", ["traps"], "isolation", 3),
            row("db-flat-press", "DB Flat Press", "push", ["chest"], "compound", 10),
            row("leg-press", "Leg Press", "legs", ["quads"], "compound", 10),
        ]
    }


class GoalsFileTests(unittest.TestCase):
    def test_goals_files_expose_the_equal_ramp(self):
        for path in (
            REPO / "fitness" / "exercises" / "goals.json",
            ROOT / "fitness" / "exercises" / "goals.json",
        ):
            raw = json.loads(path.read_text())
            self.assertNotIn("priority_muscles_ordered", raw)
            self.assertTrue(raw["volume_ramp_equal"])
            self.assertEqual(raw["volume_sets_target"], 10)
            self.assertEqual(raw["volume_ramp_increment"], 2)
            self.assertEqual(raw["volume_ramp_started_on"], "2026-11-09")
            self.assertEqual(raw["sets_per_muscle_week_max"], 10)
            self.assertEqual(raw["sets_per_muscle_week_priority_max"], 10)
            self.assertEqual(raw["sets_per_muscle_week_min"], 4)
            self.assertEqual(raw["session_working_set_cap"], 14)
            self.assertNotIn("pre-bulk", raw["notes"].lower())
            self.assertNotIn("bulk started", raw["notes"].lower())
            self.assertNotIn("tempo", raw)
            self.assertNotIn("lowering_cue", raw)
            _assert_no_lowering_cue(self, path.read_text(), path.name)
        loaded = default_goals()
        self.assertTrue(loaded["volume_ramp_equal"])
        self.assertNotIn("priority_muscles_ordered", loaded)


class MuscleTargetTests(unittest.TestCase):
    def _bands(self, prior, **kwargs):
        opts = {"lifts_status": "green", "prior_lifts_status": "green"}
        opts.update(kwargs)
        return muscle_targets(
            RAMP, as_of="2026-11-09", prior_credits=prior, **opts
        )

    def test_parallel_plus_two_does_not_sequence_or_jump_to_10(self):
        bands = self._bands({"chest": 5, "lats": 6, "quads": 7})
        self.assertEqual(bands["chest"]["max"], 7)
        self.assertEqual(bands["lats"]["max"], 8)
        self.assertEqual(bands["quads"]["max"], 9)
        self.assertEqual(bands["chest"]["min"], 5)
        self.assertEqual(bands["lats"]["min"], 6)
        self.assertEqual(bands["quads"]["min"], 7)
        for muscle in ("chest", "lats", "quads"):
            self.assertNotEqual(bands[muscle]["max"], 10)
            self.assertFalse(bands[muscle]["priority"])

    def test_every_major_ramps_and_nobody_drops_to_maintenance(self):
        bands = self._bands({"lats": 6, "delts": 4, "hamstrings": 3, "calves": 1})
        self.assertEqual(set(bands), set(MAJOR_MUSCLES))
        for muscle in MAJOR_MUSCLES:
            self.assertGreaterEqual(bands[muscle]["max"], 2, muscle)
            self.assertFalse(bands[muscle]["priority"], muscle)
        self.assertEqual(bands["delts"]["max"], 6)
        self.assertGreaterEqual(bands["delts"]["min"], 4)
        self.assertEqual(bands["hamstrings"]["max"], 5)
        self.assertEqual(bands["calves"]["max"], 3)
        self.assertEqual(bands["biceps"]["max"], 2)
        focused = muscle_targets(
            {**RAMP, "focus_muscles": ["lats"], "auto_focus_muscles": True},
            as_of="2026-11-09",
            prior_credits={"lats": 6, "delts": 4},
            lifts_status="green",
        )
        self.assertEqual(focused["delts"]["max"], 6)
        self.assertNotEqual(focused["triceps"]["max"], 3)
        self.assertGreaterEqual(focused["biceps"]["min"], 2)

    def test_legacy_focus_still_drops_to_maintenance_when_the_ramp_is_off(self):
        bands = muscle_targets(
            {
                "volume_ramp_equal": False,
                "focus_muscles": ["lats"],
                "sets_per_muscle_week_priority_max": 10,
                "maintenance_sets_per_muscle_week": 3,
            },
            as_of="2026-11-09",
        )
        self.assertEqual(bands["lats"]["max"], 10)
        self.assertEqual(bands["delts"]["max"], 3)
        self.assertLess(bands["biceps"]["min"], 4)

    def test_rollback_undoes_every_raise_and_drops_the_ceiling_toward_8(self):
        prior = {"chest": 5, "lats": 6, "quads": 7, "delts": 10, "biceps": 9.5}
        rolled = self._bands(prior, lifts_status="yellow")
        self.assertEqual(rolled["chest"]["max"], 5)
        self.assertEqual(rolled["lats"]["max"], 6)
        self.assertEqual(rolled["quads"]["max"], 7)
        self.assertEqual(rolled["delts"]["max"], 8)
        self.assertEqual(rolled["biceps"]["max"], 8)
        held = muscle_targets(
            RAMP,
            as_of="2026-11-16",
            prior_credits=prior,
            lifts_status="green",
            prior_lifts_status="yellow",
        )
        self.assertEqual(held["lats"]["max"], 6)
        resumed = muscle_targets(
            RAMP,
            as_of="2026-11-23",
            prior_credits=prior,
            lifts_status="green",
            prior_lifts_status="green",
        )
        self.assertEqual(resumed["lats"]["max"], 8)
        self.assertEqual(resumed["chest"]["max"], 7)
        self.assertEqual(resumed["delts"]["max"], 10)
        restore = self._bands(prior, lifts_status="green", restore_flag=True)
        self.assertEqual(restore["quads"]["max"], 7)


class PlanTests(unittest.TestCase):
    def test_session_stays_within_14_and_the_message_names_no_order(self):
        sessions = [
            _session("2026-11-02", "pull", "Pulldowns", 100, 6, 8),
            _session("2026-11-03", "push", "DB Flat Press", 50, 5, 8),
            _session("2026-11-04", "legs", "Leg Press", 200, 7, 8),
            _session("2026-11-08", "pull", "Seated Cable Row", 80, 2, 8),
        ]
        plan = generate_workout_plan(
            _catalog(),
            RAMP,
            sessions,
            recovery_score=80,
            recovery_label="Ready",
            session_type="pull",
            as_of="2026-11-09",
            lifts_status="green",
            prior_lifts_status="green",
        )
        bands = plan["volume"]["bands"]
        self.assertEqual(bands["chest"]["max"], 7)
        self.assertEqual(bands["lats"]["max"], 8)
        self.assertEqual(bands["quads"]["max"], 9)
        total = sum(
            int((row.get("prescription") or {}).get("sets") or 0)
            for row in plan["exercises"]
        )
        self.assertLessEqual(total, 14)
        self.assertGreaterEqual(total, 1)
        names = [row["name"] for row in plan["exercises"]]
        compounds = [
            row["name"] for row in plan["exercises"] if row.get("movement") == "compound"
        ]
        self.assertEqual(names[: len(compounds)], compounds)
        message = plan["message"]
        self.assertIn(CLIMB, message)
        _assert_no_lowering_cue(self, message, "workout message")
        self.assertNotIn("Priority volume", message)
        self.assertNotIn("lats toward", message)
        self.assertNotIn("then chest", message)
        self.assertNotIn("then quads", message)
        self.assertNotIn("DeanT 4-8", message)
        self.assertEqual(plan["goals"].get("focus_muscles"), [])
        for row in plan["exercises"]:
            rx = row["prescription"]
            self.assertNotIn("tempo", row)
            self.assertNotIn("tempo", rx)
            self.assertNotIn("3-0-1", str(rx.get("reps")))
            self.assertNotIn("3-0-1", str(rx.get("sets")))
            self.assertNotIn("3-0-1", str(rx.get("weight_lbs")))

    def test_lifts_yellow_rolls_every_raised_muscle_back(self):
        sessions = [
            _session("2026-11-02", "pull", "Pulldowns", 100, 6, 8),
            _session("2026-11-08", "push", "DB Flat Press", 50, 2, 8),
        ]
        plan = generate_workout_plan(
            _catalog(),
            RAMP,
            sessions,
            recovery_score=80,
            session_type="pull",
            as_of="2026-11-09",
            lifts_status="yellow",
        )
        self.assertEqual(plan["volume"]["bands"]["lats"]["max"], 6)
        self.assertIn("ramp paused", plan["message"])
        self.assertNotIn("ramping major muscles toward ~10", plan["message"])
        _assert_no_lowering_cue(self, plan["message"], "rolled-back workout message")
        total = sum(
            int((row.get("prescription") or {}).get("sets") or 0)
            for row in plan["exercises"]
        )
        self.assertLessEqual(total, 14)


class BumpTests(unittest.TestCase):
    def test_a_full_session_does_not_trim_isolation_to_feed_another_muscle(self):
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
        bumped = bump_equal_sets(
            [pulldown, curls],
            targets={"lats": {"min": 6, "max": 8}, "biceps": {"min": 4, "max": 6}},
            logged={"lats": 0, "biceps": 0},
            session_muscles=("lats", "biceps"),
            session_cap=14,
        )
        rows = {row["name"]: row for row in bumped["exercises"]}
        self.assertEqual(rows["DB Curls"]["prescription"]["sets"], 12)
        self.assertEqual(rows["Pulldowns"]["prescription"]["sets"], 2)
        self.assertEqual(bumped["session_sets"], 14)
        self.assertEqual(rows["Pulldowns"]["prescription"]["reps"], 8)
        self.assertEqual(rows["Pulldowns"]["prescription"]["weight_lbs"], 100)

    def test_existing_compound_gets_the_set_before_a_new_exercise(self):
        pulldown = {
            "id": "pulldowns",
            "name": "Pulldowns",
            "movement": "compound",
            "primary_muscles": ["lats"],
            "secondary_muscles": ["biceps"],
            "prescription": {"sets": 2, "reps": 8, "weight_lbs": 100},
            "set_credits": {"lats": 2, "biceps": 1},
        }
        added = []

        def _new(_rows):
            added.append(True)
            return [
                {
                    "id": "row",
                    "name": "Seated Cable Row",
                    "movement": "compound",
                    "primary_muscles": ["back"],
                    "prescription": {"sets": 1, "reps": 8, "weight_lbs": 80},
                }
            ]

        bumped = bump_equal_sets(
            [pulldown],
            targets={"lats": {"min": 6, "max": 8}, "biceps": {"min": 4, "max": 6}},
            logged={},
            session_muscles=("lats", "biceps"),
            session_cap=14,
            new_rows=_new,
        )
        self.assertEqual(bumped["exercises"][0]["name"], "Pulldowns")
        self.assertEqual(bumped["exercises"][0]["prescription"]["sets"], 8)
        self.assertNotIn("Seated Cable Row", [row["name"] for row in bumped["exercises"]])
        self.assertLessEqual(bumped["session_sets"], 14)

    def test_compounds_stay_ahead_of_isolation(self):
        curls = {
            "id": "db-curls",
            "name": "DB Curls",
            "movement": "isolation",
            "primary_muscles": ["biceps"],
            "prescription": {"sets": 2, "reps": 10, "weight_lbs": 20},
            "set_credits": {"biceps": 2},
        }

        def _new(_rows):
            return [
                {
                    "id": "pulldowns",
                    "name": "Pulldowns",
                    "movement": "compound",
                    "primary_muscles": ["lats"],
                    "secondary_muscles": [],
                    "prescription": {"sets": 1, "reps": 8, "weight_lbs": 90},
                }
            ]

        bumped = bump_equal_sets(
            [curls],
            targets={"lats": {"min": 4, "max": 4}, "biceps": {"min": 4, "max": 4}},
            logged={},
            session_muscles=("lats", "biceps"),
            session_cap=14,
            new_rows=_new,
        )
        self.assertEqual(bumped["exercises"][0]["name"], "Pulldowns")
        self.assertLessEqual(bumped["session_sets"], 14)


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
    def _history(self):
        return [
            _session("2026-11-02", "pull", "Pulldowns", 100, 6, 8),
            _session("2026-11-03", "push", "DB Flat Press", 50, 5, 8),
        ]

    def _run_grok(self, *, recovery=None, lifts=None, model_message=None):
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return _grok_answer(
                model_message
                if model_message is not None
                else (
                    "Priority volume: lats toward ~10 hard sets/week (week 1 of ramp). "
                    + CLIMB
                )
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
                catalog=_catalog(),
                sessions=self._history(),
                recovery=recovery or {"score": 80, "label": "Ready"},
                next_session_type="pull",
                as_of="2026-11-09",
            )
        raw = captured["messages"][1]["content"]
        body = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
        return out, body, captured["messages"]

    def test_yellow_and_red_roll_the_model_message_back(self):
        fallback = muscle_targets(
            RAMP,
            as_of="2026-11-09",
            prior_credits={"lats": 6, "chest": 5},
            lifts_status="yellow",
        )
        for status in ("yellow", "red"):
            with self.subTest(status=status):
                out, context, _prompt = self._run_grok(lifts=status)
                targets = context["volume_caps"]["muscle_targets"]
                self.assertEqual(targets["lats"]["max"], fallback["lats"]["max"])
                self.assertEqual(targets["chest"]["max"], fallback["chest"]["max"])
                self.assertEqual(targets["lats"]["max"], 6)
                self.assertEqual(targets["chest"]["max"], 5)
                self.assertIn("ramp paused", context["volume_caps"]["volume_line"])
                message = out["workout"]["message"]
                self.assertIn("ramp paused", message)
                self.assertNotIn("Priority volume", message)
                self.assertNotIn("lats toward", message)
                self.assertNotIn("ramping major muscles toward ~10", message)
                _assert_no_lowering_cue(self, message, "model workout message")

    def test_restore_deload_rolls_the_model_path_back(self):
        out, context, _prompt = self._run_grok(
            recovery={"score": 80, "label": "Restore deload", "deload": True}
        )
        self.assertEqual(context["volume_caps"]["muscle_targets"]["lats"]["max"], 6)
        self.assertIn("ramp paused", out["workout"]["message"])
        fields = prompt_volume_fields(
            RAMP,
            self._history(),
            _catalog(),
            as_of="2026-11-09",
            restore_flag=True,
        )
        self.assertEqual(fields["muscle_targets"]["lats"]["max"], 6)
        self.assertEqual(fields["muscle_targets"]["chest"]["max"], 5)
        self.assertNotIn("lowering_cue", fields)
        self.assertNotIn("message_rule", fields)
        _assert_no_lowering_cue(self, fields["volume_clause"], "volume clause")

    def test_workout_message_planner_prompt_and_today_omit_lowering_cue(self):
        sessions = [
            _session("2026-11-02", "pull", "Pulldowns", 100, 6, 8),
            _session("2026-11-03", "push", "DB Flat Press", 50, 5, 8),
            _session("2026-11-04", "legs", "Leg Press", 200, 7, 8),
        ]
        plan = generate_workout_plan(
            _catalog(),
            RAMP,
            sessions,
            recovery_score=80,
            recovery_label="Ready",
            session_type="pull",
            as_of="2026-11-09",
            lifts_status="green",
            prior_lifts_status="green",
        )
        self.assertIn(CLIMB, plan["message"])
        self.assertNotIn("Priority volume", plan["message"])
        self.assertNotIn("lats toward", plan["message"])
        _assert_no_lowering_cue(self, plan["message"], "workout message")
        for row in plan["exercises"]:
            self.assertNotIn("tempo", row)
            self.assertNotIn("tempo", row.get("prescription") or {})

        stripped = ensure_workout_message(
            _MODEL_CUE
            + " Priority volume: lats toward ~10, then chest, then quads. "
            + CLIMB,
            {"live": True, "today_line": CLIMB},
        )
        self.assertIn(CLIMB, stripped)
        self.assertNotIn("Priority volume", stripped)
        self.assertNotIn("lats toward", stripped)
        self.assertNotIn("then chest", stripped)
        self.assertNotIn("then quads", stripped)
        _assert_no_lowering_cue(self, stripped, "stripped workout message")

        out, context, messages = self._run_grok(
            lifts="green",
            model_message=(
                _MODEL_CUE
                + " Priority volume: lats toward ~10 hard sets/week, then chest, then quads. "
                + CLIMB
            ),
        )
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        _assert_no_lowering_cue(self, prompt, "planner prompt")
        self.assertIn("ramping major muscles toward", prompt)
        self.assertNotIn("lowering_cue", context["volume_caps"])
        self.assertNotIn("tempo", context["volume_caps"])
        message = out["workout"]["message"]
        self.assertIn(CLIMB, message)
        self.assertNotIn("Priority volume", message)
        self.assertNotIn("lats toward", message)
        self.assertNotIn("then chest", message)
        self.assertNotIn("then quads", message)
        _assert_no_lowering_cue(self, message, "model workout message")
        for row in out["workout"]["exercises"]:
            self.assertNotIn("tempo", row)
            self.assertNotIn("tempo", row.get("prescription") or {})

        def _today(workout):
            return build_today_board(
                as_of="2026-11-09",
                recovery=RecoveryStatus(label="Ready", score=80.0),
                workout_plan=workout,
                meal_plan={"message": "Pantry sketch", "items": [], "meals": []},
                consumed={"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
                targets={"calories": 2100, "protein_g": 180, "carbs_g": 180, "fat_g": 60},
                adherence={},
            )

        for label, workout in (("local plan", plan), ("model plan", out["workout"])):
            board = _today(workout)
            _assert_no_lowering_cue(self, board["workout"]["message"], f"Today {label} message")
            _assert_no_lowering_cue(self, json.dumps(board), f"Today {label} payload")
            self.assertNotIn("lats toward", board["workout"]["message"] or "")

    def test_display_targets_follow_the_parallel_rollback(self):
        sessions = self._history()
        catalog = _catalog()
        with mock.patch(
            "rt_dashboard.priority_ramp.lifts_chip_status",
            return_value="yellow",
        ):
            labels = display_targets(RAMP, sessions, catalog, as_of="2026-11-09")
        self.assertEqual(labels["chest"], "4-5")
        # lats/upper back shows the lower ceiling of lats and mid/upper back.
        self.assertEqual(labels["lats_upper_back"], "0")
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
        self.assertEqual(report["muscle_sets"]["lats_upper_back"]["target"], "0")
        self.assertEqual(report["muscle_sets"]["lower_back"]["target"], "4")


if __name__ == "__main__":
    unittest.main()
