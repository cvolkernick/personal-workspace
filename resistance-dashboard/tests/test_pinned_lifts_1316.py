"""#1316: pinned lifts, main-lift set floor, budget trims accessories first,
SuperGrok must keep the library's required lifts. No live SuperGrok."""

from __future__ import annotations

import unittest
from unittest import mock

from rt_dashboard.agent_plan import ensure_today_grok_plan, saved_plan_problem
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.muscle_sets import is_plan_accessory
from rt_dashboard.workout_plan_store import (
    clear_memory_workout_plans,
    load_last_good_workout_plan,
    save_last_good_workout_plan,
)
from rt_dashboard.workout_planner import (
    MAIN_LIFT_MIN_SETS,
    generate_workout_plan,
    missing_required_lifts,
    normalize_goals,
    pinned_lift_ids,
    required_pinned_lifts,
)
from rt_dashboard.workout_store import load_workspace_catalog, load_workspace_goals

CATALOG, _ = load_workspace_catalog()
GOALS, _ = load_workspace_goals()
FOUR_WEEKS = ("2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05")
DAY = "2026-10-21"


def _s(day, kind, *lifts):
    return Session(
        date=day,
        session_type=kind,
        exercises=[ExerciseEntry(name=n, sets=[SetEntry(w, s, r)]) for (n, w, s, r) in lifts],
    )


def _replay_1021_history():
    """10/21 on prism: Assisted Pullups last logged 10/12 as a single 70x6 set,
    10/18 Pull logged Pulldowns, and Legs 10/19 + Push 10/20 spent the
    week's proper-set budget."""
    hist = [_s(m, "push", ("DB Flat Press", 50, 2, 8), ("Lateral Raises", 15, 2, 12)) for m in FOUR_WEEKS]
    hist += [
        _s("2026-10-12", "pull", ("Assisted Pullups", 70, 1, 6), ("Machine Row", 60, 1, 8),
           ("Face Pulls", 20, 2, 14)),
        _s("2026-10-18", "pull", ("Pulldowns", 110, 3, 5), ("Seated Cable Row", 115, 3, 8)),
        _s("2026-10-19", "legs", ("Leg Press", 200, 4, 10), ("Seated Leg Curls", 80, 3, 10),
           ("Calf Raises", 100, 3, 12)),
        _s("2026-10-20", "push", ("DB Flat Press", 55, 3, 8), ("Lateral Raises", 15, 3, 12),
           ("DB Shoulder Press", 35, 3, 8), ("DB Incline Press", 45, 3, 8)),
    ]
    return hist


def _rows(plan):
    return [
        (r["name"], r["prescription"]["sets"], r["prescription"].get("weight_lbs"),
         r["prescription"].get("target_reps"))
        for r in plan["exercises"]
    ]


class PinConfigTests(unittest.TestCase):
    def test_default_pull_pin(self):
        self.assertEqual(pinned_lift_ids(normalize_goals({}), "pull"), ["assisted-pullups"])
        self.assertEqual(pinned_lift_ids(normalize_goals({}), "push"), [])

    def test_goals_override_pins(self):
        g = normalize_goals({"pinned_lifts": {"pull": ["machine-row"], "bogus": ["x"]}})
        self.assertEqual(g["pinned_lifts"], {"pull": ["machine-row"]})

    def test_pin_required_only_when_programmable(self):
        self.assertEqual(required_pinned_lifts(GOALS, "pull", CATALOG), ["assisted-pullups"])
        no_gear = {"exercises": [e for e in CATALOG["exercises"] if e["id"] != "assisted-pullups"]}
        self.assertEqual(required_pinned_lifts(GOALS, "pull", no_gear), [])


class LibraryReplay1021Tests(unittest.TestCase):
    def test_replay_1021_assisted_first_mains_floored_accessories_trimmed(self):
        plan = generate_workout_plan(
            CATALOG, GOALS, _replay_1021_history(), recovery_score=71,
            session_type="pull", as_of=DAY,
        )
        rows = _rows(plan)
        # Assisted Pullups opens, SOP load/reps off the last log (70 x6 -> hold 70, aim 7).
        self.assertEqual(rows[0], ("Assisted Pullups", 2, 70.0, 7))
        for row, ex in zip(rows, plan["exercises"]):
            if not is_plan_accessory(ex, GOALS):
                self.assertGreaterEqual(row[1], MAIN_LIFT_MIN_SETS, row)
        self.assertTrue(any(not is_plan_accessory(e, GOALS) for e in plan["exercises"][1:]))
        # Spent weekly budget trims accessories; never 1 set per lift.
        self.assertFalse([e["name"] for e in plan["exercises"] if is_plan_accessory(e, GOALS)])
        self.assertFalse(all(r[1] == 1 for r in rows))
        self.assertIsNone(saved_plan_problem(
            {**plan, "session_type": "pull"}, "pull", ["assisted-pullups"]))

    def test_pin_beats_last_used_family_member(self):
        # Pulldowns logged last; the pin still takes the vertical-pull slot.
        plan = generate_workout_plan(
            CATALOG, GOALS, _replay_1021_history(), recovery_score=71,
            session_type="pull", as_of=DAY,
        )
        names = [r["name"] for r in plan["exercises"]]
        self.assertNotIn("Pulldowns", names)

    def test_custom_pin_opens_session(self):
        g = normalize_goals({**GOALS, "pinned_lifts": {"pull": ["seated-cable-row"]}})
        plan = generate_workout_plan(CATALOG, g, _replay_1021_history(), recovery_score=71,
                                     session_type="pull", as_of=DAY)
        self.assertEqual(plan["exercises"][0]["name"], "Seated Cable Row")

    def test_single_set_log_main_gets_floor(self):
        hist = [_s("2026-10-12", "pull", ("Assisted Pullups", 70, 1, 6), ("Machine Row", 60, 1, 8))]
        plan = generate_workout_plan(CATALOG, GOALS, hist, recovery_score=80,
                                     session_type="pull", as_of="2026-10-14")
        for ex in plan["exercises"]:
            if not is_plan_accessory(ex, GOALS):
                self.assertGreaterEqual(ex["prescription"]["sets"], 2, ex["name"])

    def test_deload_may_go_below_floor(self):
        hist = [_s("2026-10-12", "pull", ("Assisted Pullups", 70, 1, 6), ("Machine Row", 60, 1, 8))]
        normal = generate_workout_plan(CATALOG, GOALS, hist, recovery_score=80,
                                       session_type="pull", as_of="2026-10-14")
        deload = generate_workout_plan(CATALOG, GOALS, hist, recovery_score=80,
                                       session_type="pull", as_of="2026-10-14", deload=True)
        self.assertLessEqual(deload["exercises"][0]["prescription"]["sets"],
                             normal["exercises"][0]["prescription"]["sets"])
        self.assertEqual(deload["exercises"][0]["name"], "Assisted Pullups")


class ValidityTests(unittest.TestCase):
    PLAN = {"session_type": "pull", "exercises": [
        {"name": "Pulldowns", "sets": 3}, {"name": "Seated Cable Row", "sets": 3},
        {"name": "Face Pulls", "sets": 3}]}

    def test_plan_missing_pin_rejected(self):
        self.assertIn("assisted-pullups",
                      saved_plan_problem(self.PLAN, "pull", ["assisted-pullups"]))
        self.assertIsNone(saved_plan_problem(self.PLAN, "pull", []))

    def test_order_check(self):
        plan = {"exercises": [{"name": "Machine Row"}, {"name": "Assisted Pullups"}]}
        self.assertEqual(missing_required_lifts(plan, ["assisted-pullups", "machine-row"], ordered=True), ["order"])
        self.assertEqual(missing_required_lifts(plan, ["assisted-pullups", "machine-row"]), [])


LIB_DEGENERATE = {
    "session_type": "pull", "is_rest_day": False, "empty": False,
    "exercises": [
        {"id": "assisted-pullups", "name": "Assisted Pullups", "movement": "compound",
         "primary_muscles": ["lats"], "prescription": {"sets": 1, "reps": 7, "weight_lbs": 70.0}},
        {"id": "machine-row", "name": "Machine Row", "movement": "compound",
         "primary_muscles": ["back"], "prescription": {"sets": 1, "reps": 5, "weight_lbs": 60.0}},
    ],
}


def _grok(*names):
    return {
        "ok": True,
        "model": "grok-test",
        "meal": {"items": [], "empty": True},
        "workout": {
            "session_type": "pull", "is_rest_day": False, "empty": False, "source": "grok",
            "message": "stub SuperGrok",
            "exercises": [{"name": n, "prescription": {"sets": 3, "reps": 8, "weight_lbs": 50}}
                          for n in names],
        },
    }


class SuperGrokRequiredTests(unittest.TestCase):
    """Closed Push -> library Pull rejected -> SuperGrok (mocked) must keep mains."""

    def setUp(self):
        clear_memory_workout_plans()
        last = _s("2026-10-12", "pull", ("Assisted Pullups", 70, 1, 6), ("Machine Row", 60, 1, 8))
        self.ctx = {
            "day": DAY,
            "catalog": CATALOG,
            "goals": GOALS,
            "sessions": [last, _s("2026-10-20", "push", ("DB Flat Press", 55, 3, 8))],
            "recovery": {},
            "stamped": {"session_type": "push", "is_rest_day": False, "exercises": [],
                        "already_trained_today": True, "session_closed_today": True,
                        "next_session_type": "pull"},
        }
        self.patches = [
            mock.patch("rt_dashboard.agent_plan._library_for_plan", return_value=(CATALOG, None)),
            mock.patch("rt_dashboard.agent_plan._deterministic_workout",
                       return_value=dict(LIB_DEGENERATE)),
            mock.patch("rt_dashboard.agent_plan.fallback_workout", return_value={}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        clear_memory_workout_plans()

    def _run(self, grok):
        with mock.patch("rt_dashboard.agent_plan.generate_grok_plans", return_value=grok) as gen:
            out = ensure_today_grok_plan("u-1316", day=DAY, context=dict(self.ctx))
        return out, gen

    def test_supergrok_gets_required_mains_in_order_and_history(self):
        out, gen = self._run(_grok("Assisted Pullups", "Machine Row", "Face Pulls"))
        kw = gen.call_args.kwargs
        req = kw["required_lifts"]
        self.assertEqual([r["id"] for r in req], ["assisted-pullups", "machine-row"])
        self.assertEqual(req[0]["weight_lbs"], 70.0)
        self.assertGreaterEqual(min(r["min_sets"] for r in req), 2)
        hist = kw["same_letter_history"]
        self.assertEqual(hist["last_log"]["date"], "2026-10-12")
        self.assertIn("last_plan", hist)
        self.assertTrue(out["ok"])
        saved = load_last_good_workout_plan("u-1316", DAY)
        self.assertEqual(saved["exercises"][0]["name"], "Assisted Pullups")

    def test_supergrok_dropping_required_lift_is_rejected(self):
        out, gen = self._run(_grok("Pulldowns", "Seated Cable Row", "Face Pulls"))
        gen.assert_called_once()
        self.assertFalse(out["ok"])
        self.assertIn("dropped required lift", out["error"])
        self.assertIsNone(load_last_good_workout_plan("u-1316", DAY))

    def test_supergrok_reordering_required_lifts_is_rejected(self):
        out, _gen = self._run(_grok("Machine Row", "Assisted Pullups", "Face Pulls"))
        self.assertFalse(out["ok"])
        self.assertIn("order", out["error"])


class OpenDayPinTests(unittest.TestCase):
    def setUp(self):
        clear_memory_workout_plans()
        self.ctx = {
            "day": DAY, "catalog": CATALOG, "goals": GOALS, "sessions": [], "recovery": {},
            "stamped": {"session_type": "pull", "is_rest_day": False, "exercises": [],
                        "next_session_type": "pull"},
        }
        self.p = mock.patch("rt_dashboard.agent_plan._library_for_plan", return_value=(CATALOG, None))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        clear_memory_workout_plans()

    def test_saved_plan_missing_pin_is_regenerated(self):
        save_last_good_workout_plan("u-open", DAY, _grok("Pulldowns", "Seated Cable Row", "Face Pulls")["workout"])
        with mock.patch("rt_dashboard.agent_plan.generate_grok_plans",
                        return_value=_grok("Assisted Pullups", "Seated Cable Row", "Face Pulls")) as gen:
            out = ensure_today_grok_plan("u-open", day=DAY, context=dict(self.ctx))
        gen.assert_called_once()
        self.assertEqual([r["id"] for r in gen.call_args.kwargs["required_lifts"]], ["assisted-pullups"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["workout"]["exercises"][0]["name"], "Assisted Pullups")

    def test_open_day_supergrok_dropping_pin_rejected(self):
        with mock.patch("rt_dashboard.agent_plan.generate_grok_plans",
                        return_value=_grok("Pulldowns", "Seated Cable Row", "Face Pulls")), \
             mock.patch("rt_dashboard.agent_plan.fallback_workout", return_value={}):
            out = ensure_today_grok_plan("u-open", day=DAY, context=dict(self.ctx))
        self.assertFalse(out["ok"])
        self.assertIsNone(load_last_good_workout_plan("u-open", DAY))


class ClosedDayReplay1021Tests(unittest.TestCase):
    """End-to-end 10/21: closed Push rolls to the real library Pull plan,
    which now passes validity, so SuperGrok is never called."""

    def setUp(self):
        clear_memory_workout_plans()

    def tearDown(self):
        clear_memory_workout_plans()

    def test_replay(self):
        ctx = {
            "day": DAY, "catalog": CATALOG, "goals": GOALS, "recovery": {"score": 71},
            "sessions": _replay_1021_history(),
            "stamped": {"session_type": "push", "is_rest_day": False, "exercises": [],
                        "already_trained_today": True, "session_closed_today": True,
                        "next_session_type": "pull"},
        }
        with mock.patch("rt_dashboard.agent_plan._library_for_plan", return_value=(CATALOG, None)), \
             mock.patch("rt_dashboard.agent_plan.generate_grok_plans") as gen:
            out = ensure_today_grok_plan("u-replay", day=DAY, context=ctx)
        gen.assert_not_called()
        self.assertTrue(out["ok"], out.get("error"))
        w = out["workout"]
        self.assertEqual(w["session_type"], "pull")
        first = w["exercises"][0]
        self.assertEqual(first["name"], "Assisted Pullups")
        self.assertGreaterEqual(first["prescription"]["sets"], 2)
        self.assertEqual(first["prescription"]["weight_lbs"], 70.0)
        self.assertEqual(first["prescription"]["target_reps"], 7)
        for ex in w["exercises"]:
            if not is_plan_accessory(ex, GOALS):
                self.assertGreaterEqual(ex["prescription"]["sets"], 2, ex["name"])
        self.assertEqual(load_last_good_workout_plan("u-replay", DAY)["exercises"][0]["name"],
                         "Assisted Pullups")


if __name__ == "__main__":
    unittest.main()


class PrescriptionSetsTests(unittest.TestCase):
    def test_library_rows_count_prescription_sets(self):
        # #1313 read only top-level "sets", so every 2-lift library plan
        # looked like 0 sets and was rejected as degenerate (#1316).
        plan = {"session_type": "pull", "exercises": [
            {"name": "Assisted Pullups", "prescription": {"sets": 2}},
            {"name": "Machine Row", "prescription": {"sets": 2}}]}
        self.assertIsNone(saved_plan_problem(plan, "pull"))
        thin = {"session_type": "pull", "exercises": [
            {"name": "Assisted Pullups", "prescription": {"sets": 1}},
            {"name": "Machine Row", "prescription": {"sets": 1}}]}
        self.assertIn("degenerate", saved_plan_problem(thin, "pull"))
