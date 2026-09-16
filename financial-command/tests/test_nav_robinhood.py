"""#782 top-nav Robinhood: native app on mobile, agentic URL on desktop."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

FCC = Path(__file__).resolve().parents[1]
JS = (FCC / "nav-robinhood.js").read_text(encoding="utf-8")
HTML = (FCC / "index.html").read_text(encoding="utf-8")
SW = (FCC / "sw.js").read_text(encoding="utf-8")


class TestNavRobinhoodMarkup(unittest.TestCase):
    def test_anchor_unchanged_for_desktop_and_nojs(self) -> None:
        self.assertIn('id="nav-robinhood"', HTML)
        self.assertIn('class="broker-link"', HTML)
        self.assertIn('href="https://robinhood.com/agentic?classic=1"', HTML)
        self.assertIn('id="nav-robinhood"', HTML[HTML.find('class="broker-links"') :])
        self.assertIn('target="_blank"', HTML[HTML.find('id="nav-robinhood"') :][:400])
        self.assertIn('rel="noopener noreferrer"', HTML[HTML.find('id="nav-robinhood"') :][:400])
        self.assertIn('title="Open Robinhood"', HTML)
        self.assertIn(">Robinhood</a>", HTML)
        self.assertIn('src="nav-robinhood.js"', HTML)

    def test_sw_precaches_nav_robinhood(self) -> None:
        self.assertIn('"/nav-robinhood.js"', SW)
        self.assertIn("fcc-shell-v4", SW)


class TestNavRobinhoodJs(unittest.TestCase):
    def test_helpers_and_contracts(self) -> None:
        self.assertIn("function isMobileUa", JS)
        self.assertIn("function launchHref", JS)
        self.assertIn("function openRobinhood", JS)
        self.assertIn("function wireRobinhoodNav", JS)
        self.assertIn('ANDROID_PACKAGE = "com.robinhood.android"', JS)
        self.assertIn("intent://robinhood.com/#Intent;scheme=https;package=", JS)
        self.assertIn("encodeURIComponent(MOBILE_WEB_HREF)", JS)
        self.assertIn('IOS_SCHEME = "robinhood://"', JS)
        self.assertIn("IOS_FALLBACK_MS = 900", JS)
        self.assertIn("https://robinhood.com/agentic?classic=1", JS)
        self.assertIn('MOBILE_WEB_HREF = "https://robinhood.com/"', JS)
        self.assertNotIn("action=android.intent.action.MAIN", JS)
        self.assertNotIn("category=android.intent.category.LAUNCHER", JS)
        self.assertIn("getElementById(\"nav-robinhood\")", JS)
        self.assertIn("wireRobinhoodNav", JS)

    def test_detection_is_ua_not_viewport(self) -> None:
        self.assertIn("/Android/i", JS)
        self.assertIn("/iPhone|iPad|iPod/i", JS)
        self.assertNotIn("matchMedia", JS)
        self.assertNotIn("maxTouchPoints", JS)
        self.assertNotIn("innerWidth", JS)

    def test_node_behavior(self) -> None:
        script = Path(__file__).resolve().parent / "nav_robinhood.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(FCC),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok nav-robinhood", proc.stdout)


if __name__ == "__main__":
    unittest.main()
