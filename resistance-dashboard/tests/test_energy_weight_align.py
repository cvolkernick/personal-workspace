"""Energy vs scale aligned band: 1.25 lb tight match, 5 lb absolute cap.

55% relative cannot paint “Lines up” when |gap| > 5 lb.
Does not rewrite AZM, calories window, hydration, or Recovery .sb-shell.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OVERLAY = (ROOT / "static" / "energy-weight-align.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


class EnergyWeightAlignMarkup(unittest.TestCase):
    def test_overlay_wired_before_app_js(self):
        self.assertIn("/energy-weight-align.js?v=ewi-cap-1", HTML)
        self.assertIn("/app.js?v=labs-1", HTML)
        self.assertLess(
            HTML.find("/energy-weight-align.js?v=ewi-cap-1"),
            HTML.find("/app.js?v=labs-1"),
        )
        self.assertIn("FitDashEnergyWeightAlign", APP_JS)
        self.assertIn("energyWeightAlignment", APP_JS)
        self.assertIn("function isAligned", OVERLAY)
        self.assertIn("var CAP_LB = 5", OVERLAY)
        self.assertIn("var TIGHT_LB = 1.25", OVERLAY)

    def test_old_unguarded_55_percent_gone(self):
        self.assertNotIn(
            "absRes <= 1.25 || (absExp >= 0.75 && absRes / absExp <= 0.55)",
            OVERLAY,
        )
        self.assertNotIn(
            "absRes <= 1.25 || (absExp >= 0.75 && absRes / absExp <= 0.55)",
            APP_JS,
        )
        self.assertIn("if (absRes > CAP_LB) return false", OVERLAY)
        self.assertIn("Do not deepen the cut on this gap.", OVERLAY)

    def test_does_not_touch_other_surfaces(self):
        self.assertIn("const CAL_IN_OUT_SPAN_DAYS = 60;", APP_JS)
        self.assertIn("Calories intake vs burned · 60d", HTML)
        self.assertIn("/trends-azm.js?v=azm-90d-3", HTML)
        self.assertNotIn("chart.js", OVERLAY.lower())
        self.assertNotIn(".sb-shell", OVERLAY)
        self.assertIn(".sb-fill-wrap {\n  height: 44px;", CSS)
        self.assertIn("hidrate-bottle-charge", HTML)


class EnergyWeightAlignNode(unittest.TestCase):
    def test_node_math(self):
        script = ROOT / "tests" / "energy_weight_align.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok energy-weight-align", proc.stdout)


class EnergyWeightAlignCache(unittest.TestCase):
    def test_cache_bumped(self):
        self.assertIn('const CACHE = "fitdash-shell-v77"', SW)
        self.assertIn("/app.js?v=labs-1", SW)
        self.assertNotIn("fitdash-shell-v71", SW)
        self.assertNotIn("fitdash-shell-v72", SW)
        self.assertNotIn("fitdash-shell-v73", SW)
        self.assertNotIn("fitdash-shell-v74", SW)
        self.assertNotIn("fitdash-shell-v75", SW)
        self.assertNotIn("fitdash-shell-v76", SW)
        self.assertNotIn("ewi-cap-5lb-1", HTML)
        self.assertNotIn("ewi-cap-5lb-1", SW)


if __name__ == "__main__":
    unittest.main()
