"""First-load Today indicator is client-only and cannot spin forever.

Chris: a slow/empty first paint looked broken. Markup + app.js boot must
show a visible Loading Today state, then dismiss it after /api/dashboard
settles (render or honest error). No extra Vercel function. Ignore-build
stays the repo-owned skip command.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


class TodayFirstLoadMarkup(unittest.TestCase):
    def test_index_has_visible_first_load_indicator(self):
        self.assertIn('id="today-first-load"', HTML)
        self.assertIn('id="today-first-load-msg"', HTML)
        self.assertIn("Loading Today", HTML)
        self.assertIn("is-first-loading", HTML)
        # Default first paint of the shell must not hide the loader.
        self.assertNotIn('id="today-first-load" hidden', HTML)
        self.assertNotIn("id='today-first-load' hidden", HTML)
        self.assertIn('role="status"', HTML)
        self.assertIn("today-first-load-spinner", HTML)

    def test_loader_copy_does_not_invent_domain_data(self):
        start = HTML.find('id="today-first-load"')
        end = HTML.find('id="ask-card"', start)
        self.assertGreaterEqual(start, 0)
        self.assertGreater(end, start)
        block = HTML[start:end].lower()
        self.assertIn("loading today", block)
        for needle in ("chicken", "oats", "bench press", "add 1", "subtract"):
            self.assertNotIn(needle, block, needle)

    def test_css_makes_loader_obvious_and_hidable(self):
        self.assertIn(".today-first-load", CSS)
        self.assertIn(".today-first-load-spinner", CSS)
        self.assertIn("@keyframes today-first-load-spin", CSS)
        self.assertIn(".today-first-load[hidden]", CSS)
        self.assertIn("#app-shell.is-first-loading", CSS)


class TodayFirstLoadBoot(unittest.TestCase):
    def test_boot_shows_loader_then_finishes_on_blocking_load(self):
        self.assertIn("function setFirstLoadVisible", JS)
        self.assertIn("function finishFirstDashboardLoad", JS)
        self.assertIn("firstDashboardSettled", JS)
        self.assertIn('setFirstLoadVisible(true, "Loading Today…")', JS)
        # Quiet live-poll must not drive the first-load overlay.
        self.assertIn("if (!quiet && !firstDashboardSettled)", JS)
        # Success and failure both hit finally → finish (no forever spinner).
        self.assertIn("finishFirstDashboardLoad()", JS)
        self.assertIn("else {\n        blockingLoadInFlight = false;\n        finishFirstDashboardLoad();", JS)

    def test_failed_fetch_is_honest_error_not_spinner(self):
        self.assertIn("Failed to load dashboard:", JS)
        self.assertIn('showAlert(`Failed to load dashboard: ${e.message}`, "err")', JS)
        # Auth failure also drops the spinner (gate, not infinite Loading…).
        self.assertIn("function showLoginGate", JS)
        gate = JS.split("function showLoginGate", 1)[1].split("function showAppShell", 1)[0]
        self.assertIn("finishFirstDashboardLoad()", gate)

    def test_no_new_serverless_function(self):
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
        self.assertIn('"ignoreCommand": "python3 scripts/vercel_ignore.py || exit 1"', VERCEL)
        paths = (ROOT / "vercel-ignore-paths.txt").read_text(encoding="utf-8")
        self.assertIn("resistance-dashboard/", paths)


if __name__ == "__main__":
    unittest.main()
