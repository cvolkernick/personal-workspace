"""Manual log: changing the exercise select loads that lift's last performance (#920)."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


class ManualLogLastPerfNode(unittest.TestCase):
    def test_select_swap_loads_last_performance(self):
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
    def test_help_and_cache_name_the_select_fill(self):
        log = HTML[HTML.find('id="log-card"') : HTML.find('id="history-card"')]
        self.assertIn("loads that lift's last weight, sets, and reps", log)
        self.assertIn("clears the weight", log)
        self.assertIn("/app.js?v=last-perf-920-1", HTML)
        self.assertNotIn("function logPlanToForm", JS)
        self.assertIn("function applyManualLogPlanPrefill", JS)
        self.assertIn("function onManualLogExerciseChange", JS)
        self.assertIn("function lastPerformanceForLog", JS)


if __name__ == "__main__":
    unittest.main()
