"""#609: applied nutrition targets live in Turso. GitHub-as-database dropped."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.nutrition_planner import TARGETS_PATH, normalize_targets
from rt_dashboard.nutrition_store import (
    FALLBACK_TURSO_DARK,
    NAMED_TARGETS_SOTS,
    SOT_FILE,
    SOT_TURSO,
    applied_targets_write,
    canonicalize_targets_source,
    load_preview_targets,
    load_workspace_targets,
    nutrition_write_ok,
    persist_targets,
    targets_source_fields,
)

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
DASHBOARD_PY = ROOT / "api" / "dashboard.py"
SERVER_PY = ROOT / "server.py"
UTIL_PY = ROOT / "api" / "workout" / "_util.py"
REPO_TARGETS = REPO_ROOT / TARGETS_PATH


LIVE_SEED = {
    "calories": 2100.0,
    "protein_g": 210.0,
    "carbs_g": 180.0,
    "fat_g": 55.0,
    "fiber_g": 30,
    "sugar_g": 50,
    "sodium_mg": 2300,
    "weight_goal_lbs": 150.0,
}


class NamedSourceOfTruth(unittest.TestCase):
    def test_named_sots_are_turso_or_file(self):
        self.assertEqual(NAMED_TARGETS_SOTS, (SOT_TURSO, SOT_FILE))
        self.assertEqual(SOT_FILE, TARGETS_PATH)
        self.assertNotIn("github", NAMED_TARGETS_SOTS)
        self.assertNotIn("unset", NAMED_TARGETS_SOTS)

    def test_canonicalize_never_github(self):
        self.assertEqual(canonicalize_targets_source("turso"), SOT_TURSO)
        self.assertEqual(canonicalize_targets_source("turso-default"), SOT_TURSO)
        self.assertEqual(canonicalize_targets_source(TARGETS_PATH), SOT_FILE)
        self.assertEqual(canonicalize_targets_source("github"), SOT_FILE)
        self.assertEqual(canonicalize_targets_source("unset"), SOT_FILE)
        self.assertEqual(canonicalize_targets_source(""), SOT_FILE)

    def test_fields_name_sot_and_honest_fallback(self):
        live = targets_source_fields("turso")
        self.assertEqual(live["targets"], SOT_TURSO)
        self.assertEqual(live["targets_sot"], SOT_TURSO)
        self.assertIsNone(live["targets_fallback"])
        dark = targets_source_fields(TARGETS_PATH)
        self.assertEqual(dark["targets"], SOT_FILE)
        self.assertEqual(dark["targets_sot"], SOT_FILE)
        self.assertEqual(dark["targets_fallback"], FALLBACK_TURSO_DARK)


class LiveSeed(unittest.TestCase):
    def test_file_seed_is_current_live_values(self):
        raw = json.loads(REPO_TARGETS.read_text(encoding="utf-8"))
        for key, value in LIVE_SEED.items():
            self.assertEqual(raw[key], value, key)
        file_t, src = load_workspace_targets()
        self.assertEqual(src, TARGETS_PATH)
        self.assertEqual(file_t["calories"], 2100.0)
        self.assertEqual(file_t["fiber_g"], 30)
        self.assertEqual(file_t["sugar_g"], 50)
        self.assertEqual(file_t["sodium_mg"], 2300)
        self.assertEqual(file_t["weight_goal_lbs"], 150.0)


class SameReadPath(unittest.TestCase):
    def test_public_and_pi_call_load_preview_targets(self):
        dash = DASHBOARD_PY.read_text(encoding="utf-8")
        server = SERVER_PY.read_text(encoding="utf-8")
        util = UTIL_PY.read_text(encoding="utf-8")
        self.assertIn("load_preview_targets", dash)
        self.assertIn("targets_source_fields", dash)
        self.assertIn("load_preview_targets", server)
        self.assertIn("targets_source_fields", server)
        self.assertIn("applied_targets_write", server)
        self.assertIn("applied_targets_write", util)
        self.assertNotIn("write_nutrition_file(", util)
        overlay = server.split("Named pantry", 1)[1].split("Cached remote layers", 1)[0]
        self.assertIn('nut["targets"] = targets', overlay)
        self.assertIn("targets_source_fields", overlay)

    def test_turso_live_is_named_turso(self):
        stored = {
            "calories": 1950,
            "protein_g": 200,
            "carbs_g": 160,
            "fat_g": 50,
            "fiber_g": 25,
            "sugar_g": 45,
            "sodium_mg": 2000,
        }
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_get_targets",
            return_value=stored,
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_put_targets",
        ) as put:
            targets, src = load_preview_targets("sub-1")
        fields = targets_source_fields(src)
        self.assertEqual(src, SOT_TURSO)
        self.assertEqual(fields["targets_sot"], SOT_TURSO)
        self.assertIsNone(fields["targets_fallback"])
        self.assertEqual(targets["calories"], 1950)
        self.assertEqual(targets["fiber_g"], 25)
        put.assert_not_called()

    def test_empty_turso_row_seeds_live_file(self):
        puts = []

        def put(uid, targets):
            puts.append((uid, dict(targets)))

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_get_targets",
            return_value=None,
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_put_targets",
            side_effect=put,
        ):
            targets, src = load_preview_targets("sub-1")
        self.assertEqual(src, SOT_TURSO)
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][0], "sub-1")
        self.assertEqual(targets["calories"], 2100.0)
        self.assertEqual(targets["fiber_g"], 30)
        self.assertEqual(targets["sugar_g"], 50)
        self.assertEqual(targets["sodium_mg"], 2300)
        self.assertEqual(targets["weight_goal_lbs"], 150.0)

    def test_turso_dark_is_named_file_fallback(self):
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False):
            targets, src = load_preview_targets("sub-1")
        fields = targets_source_fields(src)
        self.assertEqual(src, SOT_FILE)
        self.assertEqual(fields["targets_sot"], SOT_FILE)
        self.assertEqual(fields["targets_fallback"], FALLBACK_TURSO_DARK)
        self.assertEqual(targets["calories"], 2100.0)

    def test_persist_turso_does_not_write_github(self):
        puts = []

        def put(uid, targets):
            puts.append((uid, dict(targets)))

        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_put_targets",
            side_effect=put,
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_get_targets",
            side_effect=lambda uid: puts[-1][1] if puts else None,
        ), mock.patch(
            "rt_dashboard.nutrition_store.write_nutrition_file",
        ) as file_write:
            out = persist_targets(
                {
                    "calories": 2000,
                    "protein_g": 180,
                    "carbs_g": 160,
                    "fat_g": 50,
                    "fiber_g": 30,
                    "sugar_g": 50,
                    "sodium_mg": 2300,
                },
                "sub-1",
                file_client=object(),
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], SOT_TURSO)
        self.assertTrue(out["turso"])
        self.assertFalse(out["github"])
        self.assertTrue(out["verified_on_readback"])
        self.assertEqual(out["targets"]["calories"], 2000)
        self.assertEqual(len(puts), 1)
        file_write.assert_not_called()

    def test_persist_file_when_turso_dark_never_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = mock.Mock(local_fallback_dir=tmp)
            with mock.patch(
                "rt_dashboard.turso_http.turso_enabled", return_value=False
            ), mock.patch(
                "rt_dashboard.nutrition_store.write_nutrition_file",
            ) as file_write:
                out = persist_targets(
                    {
                        "calories": 1900,
                        "protein_g": 200,
                        "carbs_g": 150,
                        "fat_g": 45,
                    },
                    "sub-1",
                    file_client=client,
                )
            self.assertTrue(out["ok"])
            self.assertEqual(out["source"], SOT_FILE)
            self.assertTrue(out["local"])
            self.assertFalse(out["github"])
            self.assertFalse(out["turso"])
            file_write.assert_not_called()
            saved = json.loads((Path(tmp) / TARGETS_PATH).read_text(encoding="utf-8"))
            self.assertEqual(saved["calories"], 1900)

    def test_persist_turso_dark_without_local_raises(self):
        with mock.patch("rt_dashboard.turso_http.turso_enabled", return_value=False), mock.patch.dict(
            os.environ, {}, clear=True
        ):
            with self.assertRaisesRegex(RuntimeError, "turso env missing"):
                persist_targets({"calories": 2100}, "sub-1")

    def test_persist_readback_miss_is_not_ok(self):
        with mock.patch(
            "rt_dashboard.turso_http.turso_enabled", return_value=True
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_put_targets",
        ), mock.patch(
            "rt_dashboard.nutrition_store._turso_get_targets",
            return_value=None,
        ):
            stuck, err, write, _persisted = applied_targets_write(
                {"calories": 2100, "protein_g": 210, "carbs_g": 180, "fat_g": 55},
                "sub-1",
            )
        self.assertFalse(stuck)
        self.assertIn("readback", err)
        self.assertFalse(write.get("ok", True))


class NutritionWriteOkTurso(unittest.TestCase):
    def test_turso_verified(self):
        ok, err = nutrition_write_ok(
            {
                "turso": True,
                "source": "turso",
                "verified_on_readback": True,
                "github": False,
            }
        )
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_turso_error(self):
        ok, err = nutrition_write_ok(
            {
                "source": "turso",
                "turso": False,
                "verified_on_readback": False,
                "error": "turso env missing",
            }
        )
        self.assertFalse(ok)
        self.assertEqual(err, "turso env missing")

    def test_turso_unverified(self):
        ok, err = nutrition_write_ok({"turso": True, "source": "turso", "github": False})
        self.assertFalse(ok)
        self.assertEqual(err, "turso write did not persist")


class NormalizeKeepsLiveMicros(unittest.TestCase):
    def test_seed_normalize_keeps_fiber_sugar_sodium(self):
        t = normalize_targets(LIVE_SEED)
        self.assertEqual(t["fiber_g"], 30)
        self.assertEqual(t["sugar_g"], 50)
        self.assertEqual(t["sodium_mg"], 2300)
        self.assertEqual(t["weight_goal_lbs"], 150.0)


if __name__ == "__main__":
    unittest.main()
