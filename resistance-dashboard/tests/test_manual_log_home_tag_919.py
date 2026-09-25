"""Manual log groups a multi-tag lift by its catalog home tag (#919).

The optgroup is display. A Legs save that includes Back Extension Machine
merges into that legs day. A same-day pull row stays a separate session.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.models import ExerciseEntry, Session, SetEntry  # noqa: E402
from rt_dashboard.workout_log import (  # noqa: E402
    merge_log_with_history,
    parse_log_body,
)


class ManualLogHomeTagNode(unittest.TestCase):
    def test_catalog_order_optgroup(self):
        script = ROOT / "tests" / "manual_log_home_tag.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok manual-log-home-tag-919", proc.stdout)


class ManualLogHomeTagSave(unittest.TestCase):
    def test_legs_save_merges_back_extension_into_legs_day(self):
        day = "2026-09-24"
        legs = Session(
            date=day,
            session_type="legs",
            exercises=[
                ExerciseEntry(name="Leg Press", sets=[SetEntry(180, 3, 10)])
            ],
        )
        pull = Session(
            date=day,
            session_type="pull",
            exercises=[
                ExerciseEntry(name="Pulldowns", sets=[SetEntry(100, 3, 8)])
            ],
        )
        incoming = parse_log_body(
            {
                "session_type": "legs",
                "date": day,
                "notes": "",
                "exercises": [
                    {
                        "name": "Back Extension Machine",
                        "sets": [{"weight_lbs": 90, "sets": 3, "reps": 12}],
                    }
                ],
                "optgroup": "Pull",
            }
        )
        self.assertEqual(incoming.session_type, "legs")
        self.assertEqual(incoming.date, day)

        merged = merge_log_with_history(incoming, [legs, pull])
        self.assertEqual(merged.session_type, "legs")
        self.assertEqual(merged.date, day)
        self.assertEqual(
            [e.name for e in merged.exercises],
            ["Leg Press", "Back Extension Machine"],
        )
        self.assertEqual(merged.exercises[1].sets[0].weight_lbs, 90)

        # Same exercise on a Legs save does not fold into the pull row.
        only_pull = merge_log_with_history(incoming, [pull])
        self.assertEqual(only_pull.session_type, "legs")
        self.assertEqual(
            [e.name for e in only_pull.exercises],
            ["Back Extension Machine"],
        )


if __name__ == "__main__":
    unittest.main()
