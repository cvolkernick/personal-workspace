#!/usr/bin/env python3
"""#852 control-loop tests (no network, no OAuth, no writer)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "youtube_groom_control.py"
POLICY = ROOT / "youtube_groom.py"
TICK = ROOT / "youtube_groom_tick_report.py"
HEALTH = ROOT / "youtube_groom_health.py"
SERVICE = ROOT / "youtube-groom.service"
CAPS_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_CAPS.md"
QUEUE_MD = ROOT.parent / "ops" / "YOUTUBE_QUEUE.md"
CONTROL_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_CONTROL.md"
REPORT_MD = ROOT.parent / "ops" / "YOUTUBE_GROOM_TICK_REPORT.md"
EXPORT = ROOT.parent / "scripts" / "export-day-packets.sh"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


C = _load(MOD, "youtube_groom_control")
P = _load(POLICY, "youtube_groom_policy_for_control")


def _tick(*, remain=59, add=2, deleted=2, at="2026-09-20T12:00:40Z", quota=2938, skip=None):
    return {
        "at": at,
        "listed": remain,
        "remain": remain,
        "add": add,
        "deleted": deleted,
        "skip": skip or {},
        "quota": quota,
        "house": 100,
        "hour0": False,
        "dry_run": False,
    }


def _quota(*, remaining=5062, median=126, used=2938):
    return {
        "quota_used_today": used,
        "daily_soft_cap": 8000,
        "quota_remaining_soft": remaining,
        "median_units_per_tick": median,
        "crank_without_quota": False,
    }


LIVE_SEED_IDS = {
    "UCkrwgzhIBKccuDsi_SvZtnQ",
    "UCICRehoZjq3ZtAWgRJX118A",
    "UCDo6-SUypaXlTmH6AyrYBZA",
    "UCtI0Hodo5o5dUb67FeUjDeA",
    "UCP7jMXSY2xbc3KCAE0MHQ-A",
    "UCESLZhusAkFfsNsApnjF_Cg",
    "UClBMLpP3UHXLmgEypMmXPuA",
    "UC_EzgYSqu4a6-Po65FxAoKg",
    "UCnYMOamNKLGVlJgRUbamveA",
    "UC4DBLlq1x0AKmip1QJUcbXg",
}


class TestBandMath(unittest.TestCase):
    def test_policy_and_control_agree(self):
        self.assertEqual(C.HOUSE_TARGET, P.HOUSE_TARGET)
        self.assertEqual(C.HOUSE_TARGET_TOLERANCE, P.HOUSE_TARGET_TOLERANCE)
        self.assertEqual(C.BAND_LOW, 90)
        self.assertEqual(C.BAND_HIGH, 110)
        self.assertEqual(P.BAND_LOW, 90)
        self.assertEqual(P.BAND_HIGH, 110)
        self.assertEqual(C.EXTRA_SEED_LADDER, P.EXTRA_SEED_LADDER)

    def test_below_inside_above(self):
        below = C.band_metrics(_tick(remain=59, add=2, deleted=2))
        self.assertEqual(below["side"], "below")
        self.assertEqual(below["playlist_count"], 59)
        self.assertEqual(below["net_new"], 0)
        self.assertEqual(below["distance"], 31)
        inside = C.band_metrics(_tick(remain=100, add=3, deleted=1))
        self.assertEqual(inside["side"], "inside")
        self.assertEqual(inside["distance"], 0)
        self.assertEqual(inside["net_new"], 2)
        above = C.band_metrics(_tick(remain=120, add=5, deleted=0))
        self.assertEqual(above["side"], "above")
        self.assertEqual(above["distance"], 10)
        self.assertEqual(P.band_side(59), "below")
        self.assertEqual(P.distance_from_band(59), 31)
        self.assertEqual(P.net_new(2, 2), 0)

    def test_edges_of_band(self):
        self.assertEqual(C.band_metrics(_tick(remain=90))["side"], "inside")
        self.assertEqual(C.band_metrics(_tick(remain=110))["side"], "inside")
        self.assertEqual(C.band_metrics(_tick(remain=89))["side"], "below")
        self.assertEqual(C.band_metrics(_tick(remain=111))["side"], "above")


class TestLoosenLadder(unittest.TestCase):
    def test_live_min_fit_already_floor_so_throttle_then_seeds(self):
        knobs = dict(C.DEFAULT_KNOBS)
        metrics = C.band_metrics(_tick(remain=59, add=2, deleted=2))
        state = {"last_action": None, "cooldown_remaining": 0}
        first = C.decide(metrics, knobs, state, _quota())
        self.assertEqual(first["adjustment"]["action"], "loosen")
        self.assertEqual(first["adjustment"]["knob"], "SEED_THROTTLE_WEIGHT_FLOOR")
        self.assertEqual(first["knobs"]["SEED_THROTTLE_WEIGHT_FLOOR"], 0.05)
        self.assertFalse(first["adjustment"].get("replay"))

        second = C.decide(metrics, first["knobs"], {"last_action": "loosen", "cooldown_remaining": 2}, _quota())
        self.assertEqual(second["adjustment"]["knob"], "SEED_THROTTLE_WEIGHT_FLOOR")
        self.assertEqual(second["knobs"]["SEED_THROTTLE_WEIGHT_FLOOR"], 0.00)

        third = C.decide(metrics, second["knobs"], {"last_action": "loosen", "cooldown_remaining": 2}, _quota())
        self.assertEqual(third["adjustment"]["knob"], "SEED_EXTRA")
        extra = third["knobs"]["SEED_EXTRA"]
        self.assertEqual(len(extra), 1)
        cid, name = C.EXTRA_SEED_LADDER[0]
        self.assertEqual(extra[cid], name)

    def test_under_band_loosens_tick_over_tick_not_identical(self):
        knobs = dict(C.DEFAULT_KNOBS)
        state = {"last_action": None, "cooldown_remaining": 0}
        seen = []
        for i in range(8):
            metrics = C.band_metrics(_tick(remain=59, add=2, deleted=2, at=f"2026-09-20T{i:02d}:00:00Z"))
            d = C.decide(metrics, knobs, state, _quota())
            seen.append((d["adjustment"]["action"], d["adjustment"]["knob"], d["adjustment"].get("to")))
            knobs = d["knobs"]
            state = {
                "last_action": d["adjustment"]["action"],
                "cooldown_remaining": d["cooldown_remaining"],
                "last_tick_at": metrics["tick_at"],
                "last_adjustment": d["adjustment"],
                "knobs": knobs,
            }
        knobs_changed = [row for row in seen if row[0] == "loosen"]
        self.assertGreaterEqual(len(knobs_changed), 4)
        self.assertEqual(len({row[1:] for row in knobs_changed}), len(knobs_changed))

    def test_quota_blocks_seed_and_ticks_not_fit_floor(self):
        knobs = dict(C.DEFAULT_KNOBS)
        knobs["SEED_THROTTLE_WEIGHT_FLOOR"] = 0.00
        metrics = C.band_metrics(_tick(remain=59))
        dead = C.decide(metrics, knobs, {}, _quota(remaining=0, used=8000))
        self.assertEqual(dead["adjustment"]["action"], "rest")
        self.assertEqual(dead["adjustment"]["blocker"], "quota_exhausted")
        self.assertFalse(dead.get("crank_without_quota", False))

        tight = C.decide(metrics, knobs, {}, _quota(remaining=200, median=226))
        self.assertEqual(tight["adjustment"]["action"], "rest")
        self.assertIn("broaden_seed", str(tight["limits_hit"]))

    def test_more_ticks_requires_quota_headroom(self):
        knobs = dict(C.DEFAULT_KNOBS)
        knobs["SEED_THROTTLE_WEIGHT_FLOOR"] = 0.00
        knobs["SEED_EXTRA"] = {cid: name for cid, name in C.EXTRA_SEED_LADDER}
        metrics = C.band_metrics(_tick(remain=59))
        blocked = C.decide(metrics, knobs, {}, _quota(remaining=5000, median=226))
        self.assertEqual(blocked["adjustment"]["action"], "rest")
        self.assertIn("more_ticks_per_day", str(blocked["limits_hit"] + [blocked["adjustment"]["why"]]))

        ok = C.decide(metrics, knobs, {}, _quota(remaining=5000, median=126))
        self.assertEqual(ok["adjustment"]["action"], "loosen")
        self.assertEqual(ok["adjustment"]["knob"], "ticks_per_day")
        self.assertEqual(ok["knobs"]["ticks_per_day"], 48)


class TestTightenAndAntiOscillation(unittest.TestCase):
    def test_above_band_prunes_via_cap(self):
        metrics = C.band_metrics(_tick(remain=120, add=5, deleted=0))
        d = C.decide(metrics, dict(C.DEFAULT_KNOBS), {}, _quota())
        self.assertEqual(d["adjustment"]["action"], "tighten")
        self.assertEqual(d["adjustment"]["knob"], "CAP")
        self.assertEqual(d["knobs"]["CAP"], 110)
        self.assertTrue(d["knobs"]["PRUNE_TO_BAND"])

    def test_no_loosen_tighten_flip_on_consecutive_ticks(self):
        knobs = dict(C.DEFAULT_KNOBS)
        below = C.band_metrics(_tick(remain=59, at="t1"))
        first = C.decide(below, knobs, {}, _quota())
        self.assertEqual(first["adjustment"]["action"], "loosen")
        above = C.band_metrics(_tick(remain=120, at="t2"))
        second = C.decide(
            above,
            first["knobs"],
            {
                "last_action": "loosen",
                "cooldown_remaining": first["cooldown_remaining"],
                "last_tick_at": "t1",
            },
            _quota(),
        )
        self.assertEqual(second["adjustment"]["action"], "rest")
        self.assertEqual(second["adjustment"]["blocker"], "anti_oscillation")
        third = C.decide(
            above,
            first["knobs"],
            {
                "last_action": "loosen",
                "cooldown_remaining": second["cooldown_remaining"],
                "last_tick_at": "t2",
            },
            _quota(),
        )
        # cooldown ticks down; still blocked while cooldown > 0 after first rest
        if third["cooldown_remaining"] == 0 or third["adjustment"]["action"] == "tighten":
            self.assertIn(third["adjustment"]["action"], {"rest", "tighten"})
        fourth = C.decide(
            above,
            first["knobs"],
            {
                "last_action": "loosen",
                "cooldown_remaining": 0,
                "last_tick_at": "t3",
            },
            _quota(),
        )
        self.assertEqual(fourth["adjustment"]["action"], "tighten")

    def test_inside_band_rests(self):
        d = C.decide(C.band_metrics(_tick(remain=100)), dict(C.DEFAULT_KNOBS), {}, _quota())
        self.assertEqual(d["adjustment"]["action"], "rest")
        self.assertIsNone(d["adjustment"]["blocker"])
        self.assertIn("inside", d["adjustment"]["why"])

    def test_idempotent_same_tick_at(self):
        metrics = C.band_metrics(_tick(remain=59, at="same"))
        first = C.decide(metrics, dict(C.DEFAULT_KNOBS), {}, _quota())
        replay = C.decide(
            metrics,
            first["knobs"],
            {
                "knobs": first["knobs"],
                "last_action": first["adjustment"]["action"],
                "last_tick_at": "same",
                "last_adjustment": first["adjustment"],
                "cooldown_remaining": first["cooldown_remaining"],
            },
            _quota(),
        )
        self.assertTrue(replay["idempotent"])
        self.assertTrue(replay["adjustment"].get("replay"))
        self.assertEqual(replay["adjustment"]["to"], first["adjustment"]["to"])


class TestApplyKnobsAndPersist(unittest.TestCase):
    def test_apply_live_knobs_merges_extra_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            knobs = {
                "MIN_FIT": 0,
                "SEED_THROTTLE_WEIGHT_FLOOR": 0.05,
                "SEED_EXTRA": {C.EXTRA_SEED_LADDER[0][0]: C.EXTRA_SEED_LADDER[0][1]},
                "CAP": 200,
                "HOUSE_TARGET": 100,
            }
            path = td / "knobs.json"
            path.write_text(json.dumps(knobs), encoding="utf-8")
            g = {
                "MIN_FIT": 0,
                "SEED_THROTTLE_WEIGHT_FLOOR": 0.10,
                "HOUSE_TARGET": 100,
                "CAP": 200,
                "SEED_KEEPERS": {"UCkrwgzhIBKccuDsi_SvZtnQ": "Forward Guidance"},
            }
            applied = C.apply_live_knobs(g, knobs_path=path)
            self.assertEqual(g["SEED_THROTTLE_WEIGHT_FLOOR"], 0.05)
            self.assertIn(C.EXTRA_SEED_LADDER[0][0], g["SEED_KEEPERS"])
            self.assertEqual(applied["SEED_THROTTLE_WEIGHT_FLOOR"], 0.05)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            out = C.run_loop(
                _tick(remain=59),
                _quota(),
                state_path=td / "control_state.json",
                knobs_path=td / "knobs.json",
                dry_run=True,
            )
            self.assertEqual(out["side"], "below")
            self.assertFalse((td / "control_state.json").exists())
            self.assertFalse((td / "knobs.json").exists())

    def test_persist_and_idempotent_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            first = C.run_loop(
                _tick(remain=59, at="2026-09-20T12:00:40Z"),
                _quota(),
                state_path=td / "control_state.json",
                knobs_path=td / "knobs.json",
                dry_run=False,
            )
            self.assertEqual(first["adjustment"]["action"], "loosen")
            second = C.run_loop(
                _tick(remain=59, at="2026-09-20T12:00:40Z"),
                _quota(),
                state_path=td / "control_state.json",
                knobs_path=td / "knobs.json",
                dry_run=False,
            )
            self.assertTrue(second["idempotent"])
            knobs = json.loads((td / "knobs.json").read_text(encoding="utf-8"))
            self.assertEqual(knobs["SEED_THROTTLE_WEIGHT_FLOOR"], 0.05)
            self.assertFalse(knobs["copy_over_pi"])
            self.assertNotIn("token", json.dumps(knobs).lower())
            self.assertNotIn("refresh", json.dumps(first).lower())

    def test_timer_dropin_text(self):
        hourly = C.timer_dropin_text(24)
        self.assertIn("OnCalendar=hourly", hourly)
        half = C.timer_dropin_text(48)
        self.assertIn("OnCalendar=*:0/30", half)
        self.assertNotIn("ExecStart", half)

    def test_crank_without_quota_always_false(self):
        public = C.public_control(
            C.band_metrics(_tick(remain=59)),
            C.decide(C.band_metrics(_tick(remain=59)), dict(C.DEFAULT_KNOBS), {}, _quota()),
        )
        self.assertFalse(public["crank_without_quota"])
        self.assertFalse(public["copy_over_pi"])
        self.assertEqual(public["issue"], 852)


class TestLanding(unittest.TestCase):
    def test_module_is_not_a_writer(self):
        src = MOD.read_text(encoding="utf-8")
        self.assertNotIn("googleapiclient", src)
        self.assertNotIn("InstalledAppFlow", src)
        self.assertNotIn("build(", src)
        self.assertIn("Never copies", src)
        self.assertIn("apply_live_knobs", src)
        self.assertIn(C.WRITER_HOOK.strip().split("\n")[1].strip(), src)

    def test_extra_seeds_do_not_overlap_live_set(self):
        extra_ids = {cid for cid, _ in C.EXTRA_SEED_LADDER}
        self.assertEqual(len(extra_ids), 6)
        self.assertTrue(extra_ids.isdisjoint(LIVE_SEED_IDS))

    def test_docs_and_service_wire_control(self):
        text = CONTROL_MD.read_text(encoding="utf-8")
        self.assertIn("90–110", text.replace("90-110", "90–110"))
        self.assertIn("control_state.json", text)
        self.assertIn("knobs.json", text)
        self.assertIn("do not copy", text.lower())
        self.assertIn("HOUSE_TARGET_TOLERANCE", text)
        self.assertIn("apply_live_knobs", text)
        self.assertIn("Do not run `deploy/install_remote.sh`", text)

        caps = CAPS_MD.read_text(encoding="utf-8")
        self.assertIn("HOUSE_TARGET_TOLERANCE", caps)
        self.assertIn("YOUTUBE_GROOM_CONTROL.md", caps)

        queue = QUEUE_MD.read_text(encoding="utf-8")
        self.assertIn("test_youtube_groom_control", queue)
        self.assertIn("#852", queue)

        report = REPORT_MD.read_text(encoding="utf-8")
        self.assertIn("youtube_groom_control.py", report)

        service = SERVICE.read_text(encoding="utf-8")
        self.assertIn("youtube_groom_control.py", service)
        self.assertIn("youtube_groom_tick_report.py", service)
        self.assertRegex(service, r"ExecStart=.*/youtube_groom\.py")

        export = EXPORT.read_text(encoding="utf-8")
        self.assertIn("youtube_groom_control", export)

        self.assertIn("DO NOT copy this module over the Pi binary", POLICY.read_text(encoding="utf-8"))
        self.assertIn("Not a second playlist writer", HEALTH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
