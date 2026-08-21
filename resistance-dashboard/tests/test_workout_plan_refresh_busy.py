"""Refresh plan shows an inline busy state while generateWorkoutPlan is in flight.

Chris: the button only flipped disabled. Cover markup + app.js + CSS so
Refresh plan and force-session (push/pull/legs) show a visible busy path,
then restore in finally. Workout generate only — no meals, no first-load
overlay, no new Vercel function.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


def _generate_fn() -> str:
    return JS.split("async function generateWorkoutPlan", 1)[1].split(
        "async function submitExerciseCatalog", 1
    )[0]


class WorkoutPlanRefreshMarkup(unittest.TestCase):
    def test_index_has_inline_refresh_indicator(self):
        self.assertIn('id="btn-generate-workout"', HTML)
        self.assertIn("Refresh plan", HTML)
        self.assertIn('id="workout-plan-refreshing"', HTML)
        self.assertIn("Refreshing workout plan", HTML)
        self.assertIn("workout-plan-refreshing-spinner", HTML)
        self.assertIn('id="workout-plan-refreshing"', HTML)
        banner_at = HTML.find('id="workout-plan-refreshing"')
        block = HTML[banner_at : banner_at + 420]
        self.assertIn("hidden", block)
        self.assertIn('role="status"', block)
        self.assertIn('id="today-workout"', HTML)
        self.assertIn('id="workout-plan-result"', HTML)

    def test_banner_is_inside_lift_not_first_load_overlay(self):
        overlay_at = HTML.find('id="today-first-load"')
        banner_at = HTML.find('id="workout-plan-refreshing"')
        hub_at = HTML.find('id="today-hub"')
        self.assertGreater(overlay_at, 0)
        self.assertGreater(hub_at, overlay_at)
        self.assertGreater(banner_at, hub_at)
        lift_at = HTML.find('data-today-panel="lift"')
        self.assertGreater(banner_at, lift_at)

    def test_css_has_inline_busy_not_full_page_reuse(self):
        self.assertIn(".workout-plan-refreshing", CSS)
        self.assertIn(".workout-plan-refreshing-spinner", CSS)
        self.assertIn(".workout-plan-refreshing[hidden]", CSS)
        self.assertIn("#today-workout.is-refreshing", CSS)
        self.assertIn("#workout-plan-result.is-refreshing", CSS)
        self.assertIn("animation: today-first-load-spin", CSS)
        # First-load overlay stays a first-fetch-only control.
        self.assertIn(".today-first-load", CSS)
        self.assertIn("#app-shell.is-first-loading", CSS)


class WorkoutPlanRefreshBoot(unittest.TestCase):
    def test_generate_sets_busy_and_restores_in_finally(self):
        self.assertIn("function setWorkoutPlanBusy", JS)
        self.assertIn("Refreshing plan…", JS)
        gen = _generate_fn()
        self.assertIn("setWorkoutPlanBusy(true)", gen)
        self.assertIn("setWorkoutPlanBusy(false)", gen)
        self.assertIn("finally {", gen)
        self.assertIn('fetch("/api/workout-plan/generate"', gen)
        self.assertIn('setAttribute("aria-busy", "true")', JS)
        self.assertIn("is-refreshing", JS)

    def test_generate_does_workout_then_meal(self):
        gen = _generate_fn()
        self.assertIn('fetch("/api/workout-plan/generate"', gen)
        self.assertIn("await generatePlan({ busyAlready: true })", gen)
        self.assertIn("setRefreshPlanBusy(true)", gen)
        self.assertIn("setRefreshPlanBusy(false)", gen)
        self.assertNotIn("/api/dashboard", gen)
        self.assertNotIn("loadDashboard", gen)
        self.assertNotIn("setFirstLoadVisible", gen)
        self.assertIn("renderWorkoutPlan(data.plan)", gen)
        self.assertIn("showAlert(`Workout plan failed:", gen)

    def test_force_session_buttons_share_busy_helper(self):
        self.assertIn("btn-force-session-push", JS)
        self.assertIn("btn-force-session-pull", JS)
        self.assertIn("btn-force-session-legs", JS)
        helper = JS.split("const WORKOUT_PLAN_TRIGGER_IDS", 1)[1].split(
            "async function generateWorkoutPlan", 1
        )[0]
        self.assertIn("btn-force-session-push", helper)
        self.assertIn("btn-force-session-pull", helper)
        self.assertIn("btn-force-session-legs", helper)
        self.assertIn("btn-generate-workout", helper)
        self.assertIn("function setWorkoutPlanBusy", helper)
        self.assertIn("workout-plan-refreshing", helper)
        self.assertIn("today-workout", helper)
        self.assertIn("workout-plan-result", helper)

    def test_force_session_clicks_still_call_generate(self):
        self.assertIn('generateWorkoutPlan("push")', JS)
        self.assertIn('generateWorkoutPlan("pull")', JS)
        self.assertIn('generateWorkoutPlan("legs")', JS)
        self.assertIn("() => generateWorkoutPlan()", JS)

    def test_node_busy_path_sets_then_restores(self):
        script = ROOT / "tests" / "generate_workout_plan_busy.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok", proc.stdout)

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
