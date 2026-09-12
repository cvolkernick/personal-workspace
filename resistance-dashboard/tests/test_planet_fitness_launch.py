"""#570 Club Pass chip: phone-only PF app launch from Today Training."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


def _slice(src: str, start_token: str, end_token: str) -> str:
    start = src.find(start_token)
    end = src.find(end_token)
    if start < 0 or end < 0 or end <= start:
        raise AssertionError(f"slice {start_token!r} → {end_token!r} failed")
    return src[start:end]


class ClubPassMarkup(unittest.TestCase):
    def test_chip_is_in_today_training_head(self):
        lift = _slice(HTML, 'data-today-panel="lift"', 'id="recovery-card"')
        self.assertIn('id="btn-planet-fitness"', lift)
        self.assertIn('class="today-lift-head"', lift)
        self.assertIn("Club Pass", lift)
        self.assertIn('aria-label="Open Planet Fitness app for Club Pass"', lift)
        head = _slice(lift, 'class="today-lift-head"', 'class="collapsible-body"')
        self.assertIn('id="btn-planet-fitness"', head)

    def test_not_in_header_or_log(self):
        header = _slice(HTML, 'class="app-header"', 'id="mobile-tabbar"')
        self.assertNotIn("btn-planet-fitness", header)
        self.assertNotIn("Club Pass", header)
        log = _slice(HTML, 'id="log-card"', 'id="history-card"')
        self.assertNotIn("btn-planet-fitness", log)
        self.assertNotIn("Club Pass", log)

    def test_copy_does_not_claim_deeplink(self):
        self.assertNotIn("Crowd Meter", HTML)
        self.assertNotIn("deep-link", HTML.lower())
        self.assertIn("do not deep-link check-in", JS)


class ClubPassCss(unittest.TestCase):
    def test_hidden_on_desktop_visible_on_phone(self):
        self.assertIn("#btn-planet-fitness", CSS)
        self.assertIn(".pf-club-pass", CSS)
        block = CSS[CSS.find("#btn-planet-fitness") :]
        self.assertIn("display: none", block.split("@media", 1)[0])
        self.assertIn("@media (max-width: 720px)", block)
        phone = block.split("@media (max-width: 720px)", 1)[1][:500]
        self.assertIn("display: inline-flex", phone)


class ClubPassJs(unittest.TestCase):
    def test_launch_helpers_wired(self):
        self.assertIn("function planetFitnessLaunchHref", JS)
        self.assertIn("function openPlanetFitnessApp", JS)
        self.assertIn("function bindPlanetFitnessLaunch", JS)
        self.assertIn('PF_ANDROID_PACKAGE = "com.planetfitness"', JS)
        self.assertIn("intent://#Intent;scheme=planetfitness;package=", JS)
        self.assertIn("encodeURIComponent(PF_PLAY_STORE)", JS)
        self.assertNotIn("scheme=https;package=", JS)
        self.assertNotIn("intent://open", JS)
        self.assertNotIn("action=android.intent.action.MAIN", JS)
        self.assertNotIn("category=android.intent.category.LAUNCHER", JS)
        self.assertIn(
            "https://play.google.com/store/apps/details?id=com.planetfitness", JS
        )
        self.assertIn("https://apps.apple.com/app/id399857015", JS)
        self.assertIn("planetfitness://", JS)
        self.assertIn("bindPlanetFitnessLaunch()", JS)

    def test_node_behavior(self):
        script = ROOT / "tests" / "planet_fitness_launch.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok planet-fitness-launch", proc.stdout)


class ClubPassCacheAndHobby(unittest.TestCase):
    def test_cache_bumped(self):
        self.assertIn("/app.js?v=recipes-1", HTML)
        self.assertIn("/app.js?v=recipes-1", SW)
        self.assertIn("/styles.css?v=recipes-1", HTML)
        self.assertIn("/styles.css?v=recipes-1", SW)
        self.assertIn('const CACHE = "fitdash-shell-v98"', SW)
        self.assertNotIn("fitdash-shell-v93", SW)
        self.assertNotIn("fitdash-shell-v91", SW)
        self.assertNotIn("fitdash-shell-v96", SW)
        self.assertNotIn("fitdash-shell-v90", SW)
        self.assertNotIn("fitdash-shell-v89", SW)
        self.assertNotIn("fitdash-shell-v88", SW)
        self.assertNotIn("fitdash-shell-v87", SW)
        self.assertNotIn("/app.js?v=pf-launcher-2", HTML)
        self.assertNotIn("/app.js?v=pf-launcher-2", SW)
        self.assertNotIn("/app.js?v=pf-launcher-1", HTML)
        self.assertNotIn("/app.js?v=pf-launcher-1", SW)
        self.assertNotIn("/app.js?v=phase-baro-1", HTML)
        self.assertNotIn("/app.js?v=phase-baro-1", SW)
        self.assertNotIn("/app.js?v=edit-log-date-1", HTML)
        self.assertNotIn("/app.js?v=edit-log-date-1", SW)
        self.assertNotIn("fitdash-shell-v83", SW)
        self.assertNotIn("fitdash-shell-v86", SW)
        self.assertNotIn("/styles.css?v=meal-carousel-fill-3", HTML)
        self.assertNotIn("/styles.css?v=meal-carousel-fill-3", SW)
        self.assertNotIn("/styles.css?v=library-1", HTML)
        self.assertNotIn("/styles.css?v=library-1", SW)

    def test_hobby_function_count_stays_at_12(self):
        api = ROOT / "api"
        fns = []
        for path in api.rglob("*.py"):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "class handler" in src or "\ndef app(" in src or "\napp = " in src:
                fns.append(path)
        self.assertEqual(len(fns), 12, [str(x.relative_to(ROOT)) for x in fns])

    def test_ignore_build_unchanged(self):
        self.assertIn(
            '"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"',
            VERCEL,
        )


if __name__ == "__main__":
    unittest.main()
