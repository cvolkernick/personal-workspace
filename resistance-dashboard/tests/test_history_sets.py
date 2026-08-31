"""Workout history shows weight × sets × reps next to volume.

Chris (#fitness): history listed lift + total volume only. Set triples already
exist on Session.to_dict(); the card just never rendered them.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HIST_JS = (ROOT / "static" / "history-sets.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


class HistorySetsModels(unittest.TestCase):
    def test_sets_label_matches_log_triple(self):
        from rt_dashboard.models import ExerciseEntry, SetEntry
        from rt_dashboard.parse import format_set_entry, parse_workout_markdown

        one = SetEntry(weight_lbs=47.5, sets=3, reps=12)
        self.assertEqual(one.label(), "47.5 lbs x 3 x 12")
        self.assertEqual(format_set_entry(one), one.label())

        multi = ExerciseEntry(
            name="DB Flat Press",
            sets=[
                SetEntry(50, 1, 12),
                SetEntry(45, 1, 12),
                SetEntry(40, 1, 12),
            ],
        )
        self.assertEqual(
            multi.sets_label,
            "50 lbs x 1 x 12, 45 lbs x 1 x 12, 40 lbs x 1 x 12",
        )
        payload = multi.to_dict()
        self.assertEqual(payload["sets_label"], multi.sets_label)
        self.assertEqual(payload["sets"][0]["weight_lbs"], 50)
        self.assertEqual(payload["sets"][0]["sets"], 1)
        self.assertEqual(payload["sets"][0]["reps"], 12)

        md = (ROOT / "tests" / "fixtures" / "sample_push.md").read_text(
            encoding="utf-8"
        )
        sessions = parse_workout_markdown(md, session_type="push", source_file="push.md")
        self.assertTrue(sessions)
        first = sessions[0]
        names = {e.name: e for e in first.exercises}
        self.assertIn("50 lbs x 1 x 12", names["DB Flat Press"].sets_label)
        self.assertIn("47.5 lbs x 3 x 12", names["Tricep Pushdowns"].sets_label)


class HistorySetsMarkup(unittest.TestCase):
    def test_helper_wired_before_app_js(self):
        self.assertIn("/history-sets.js?v=history-sets-1", HTML)
        self.assertIn("/app.js?v=history-sets-1", HTML)
        self.assertLess(
            HTML.find("/history-sets.js?v=history-sets-1"),
            HTML.find("/app.js?v=history-sets-1"),
        )
        self.assertIn("FitDashHistorySets", APP_JS)
        self.assertIn("formatExerciseLine", APP_JS)
        self.assertIn("function renderHistory", APP_JS)
        self.assertNotIn(
            ".map((e) => `${e.name} (${Math.round(e.volume)} vol)`)",
            APP_JS,
        )
        self.assertIn("ex-row", APP_JS)
        self.assertIn("root.FitDashHistorySets = api", HIST_JS)

    def test_cache_bumped(self):
        self.assertIn('const CACHE = "fitdash-shell-v75"', SW)
        self.assertIn("/history-sets.js?v=history-sets-1", SW)
        self.assertIn("/app.js?v=history-sets-1", SW)
        self.assertNotIn("fitdash-shell-v73", SW)
        self.assertNotIn("coach-targets-1", HTML)
        self.assertNotIn("coach-targets-1", SW)


class HistorySetsNode(unittest.TestCase):
    def test_node_format(self):
        script = ROOT / "tests" / "history_sets.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok history-sets", proc.stdout)


if __name__ == "__main__":
    unittest.main()
