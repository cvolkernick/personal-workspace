"""Manual log numbers stay empty (#949). #920 still matches the last lift, as text only."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


class ManualLogLastPerfNode(unittest.TestCase):
    def test_select_swap_leaves_inputs_empty(self):
        script = ROOT / "tests" / "manual_log_last_perf.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok manual-log-last-perf-920", proc.stdout)


class ManualLogLastPerfContract(unittest.TestCase):
    def test_help_and_cache_name_empty_inputs(self):
        log = HTML[HTML.find('id="log-card"') : HTML.find('id="history-card"')]
        self.assertIn("start empty", log)
        self.assertIn("does not", log)
        self.assertIn("read-only text", log)
        self.assertNotIn("loads that lift's last weight", log)
        self.assertIn("/app.js?v=recovery-1040-1", HTML)
        self.assertNotIn("/app.js?v=partial-workout-999-1", HTML)
        self.assertNotIn("/app.js?v=calorie-7d-946-1", HTML)
        sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
        self.assertIn('const CACHE = "fitdash-shell-v133"', sw)
        self.assertNotIn("fitdash-shell-v124", sw)
        self.assertNotIn("fitdash-shell-v116", sw)
        self.assertIn("/styles.css?v=brand-1027-1", HTML)
        self.assertNotIn("/styles.css?v=pace-rows-650-1", HTML)
        self.assertNotIn("function logPlanToForm", JS)
        self.assertIn("function applyManualLogPlanPrefill", JS)
        self.assertIn("function onManualLogExerciseChange", JS)
        self.assertIn("function lastPerformanceForLog", JS)


if __name__ == "__main__":
    unittest.main()
