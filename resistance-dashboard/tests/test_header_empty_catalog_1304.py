"""#1304 follow-up: header letter must not depend on the catalog loading.

On prism (~/apps/fitdash overlay) ``default_catalog()`` loaded 0 exercises,
so 'Assisted Pullups' + 'Machine Row' rendered under 'PUSH session (2 lifts)'.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rt_dashboard.daily_plan_tasks import (  # noqa: E402
    TRAIN_SESSION_SLUG,
    letter_for_lift_name,
    plan_from_today_board,
    session_header_from_lifts,
)

EMPTY = {"exercises": [], "updated_at": "", "notes": ""}


def _board(names, letter="push", stale_n=5, meta=None):
    exs = []
    for n in names:
        row = {"name": n, "sets": 3, "reps": 8}
        if meta and n in meta:
            row["session_types"] = meta[n]
        exs.append(row)
    return {
        "date": "2026-10-21",
        "actions": [
            {
                "id": "train-session",
                "kind": "training",
                "text": f"Complete today's {letter.upper()} session ({stale_n} lifts as prescribed).",
            }
        ],
        "workout": {"session_type": letter, "is_rest_day": False, "exercises": exs},
    }


def _header(board):
    groups = plan_from_today_board(board, day="2026-10-21")
    training = [g for g in groups if g.group == "training"][0]
    return [it for it in training.items if it.slug == TRAIN_SESSION_SLUG][0].title


class EmptyCatalogHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch("rt_dashboard.workout_planner.default_catalog", return_value=EMPTY)
        p.start()
        self.addCleanup(p.stop)

    def test_prism_1021_case_reads_pull(self) -> None:
        # Stale plan letter is push; the lifts are pull.
        title = _header(_board(["Assisted Pullups", "Machine Row"], letter="push"))
        self.assertEqual(title, "Complete today's PULL session (2 lifts as prescribed).")

    def test_prism_push_names(self) -> None:
        names = [
            "Barbell Flat Bench Press",
            "DB Incline Press",
            "DB Shoulder Press",
            "Lateral Raises",
            "Tricep Pushdowns (Rope)",
        ]
        self.assertEqual(session_header_from_lifts(names, "pull"), ("PUSH", 5))

    def test_legs_names(self) -> None:
        self.assertEqual(
            session_header_from_lifts(["Leg Press", "Leg Curl", "Calf Raise"], "push"),
            ("LEGS", 3),
        )

    def test_plan_metadata_wins_over_name(self) -> None:
        title = _header(
            _board(["Mystery A", "Mystery B"], letter="push", meta={"Mystery A": ["legs"], "Mystery B": "legs"})
        )
        self.assertEqual(title, "Complete today's LEGS session (2 lifts as prescribed).")

    def test_unknown_names_fall_back_to_plan_letter(self) -> None:
        self.assertEqual(session_header_from_lifts(["Mystery Lift"], "legs"), ("LEGS", 1))

    def test_name_rules(self) -> None:
        for name, want in (
            ("Assisted Pullups", "pull"),
            ("Machine Row", "pull"),
            ("Lat Pulldown", "pull"),
            ("Tricep Pushdowns (Rope)", "push"),
            ("Leg Curl", "legs"),
            ("Hammer Curl", "pull"),
            ("Something", ""),
        ):
            self.assertEqual(letter_for_lift_name(name), want, name)


class OverlayLayoutCatalogTests(unittest.TestCase):
    """Standalone ~/apps/fitdash layout: no repo root above the app."""

    def test_catalog_loads_from_app_bundle(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        app = tmp / "apps" / "fitdash"
        shutil.copytree(ROOT / "rt_dashboard", app / "rt_dashboard",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (app / "fitness" / "exercises").mkdir(parents=True)
        shutil.copy(ROOT / "fitness" / "exercises" / "catalog.json",
                    app / "fitness" / "exercises" / "catalog.json")
        out = subprocess.run(
            [sys.executable, "-c",
             "from rt_dashboard.workout_planner import default_catalog, session_types_for_lift_name as f;"
             "print(len(default_catalog()['exercises']), f('Assisted Pullups'))"],
            cwd=str(app), capture_output=True, text=True, timeout=60,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(app)},
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        count, types = out.stdout.strip().split(" ", 1)
        self.assertGreater(int(count), 0)
        self.assertIn("pull", types)


if __name__ == "__main__":
    unittest.main()
