"""FitDash refresh scopes stay targeted: inventory=meals, plan=workout, data=Health.

Chris: do not brute-force everything. Inventory add/edit/remove/stock rebuilds
the meal plan with a visible busy state. Refresh plan stays workout-only (#224).
Refresh data stays /api/dashboard?refresh=1 and does not call generateWorkoutPlan.
No extra meal-section button.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")


def _fn(name: str, until: str) -> str:
    return JS.split(name, 1)[1].split(until, 1)[0]


class NoExtraMealButton(unittest.TestCase):
    def test_meal_card_has_busy_banner_not_a_refresh_button(self):
        self.assertIn('id="meal-plan-refreshing"', HTML)
        self.assertIn("Refreshing meal plan", HTML)
        self.assertNotIn("Refresh meal plan", HTML)
        self.assertNotIn('id="btn-generate-meal"', HTML)
        self.assertNotIn('id="btn-refresh-meal"', HTML)
        banner_at = HTML.find('id="meal-plan-refreshing"')
        card_at = HTML.find('id="meal-plan-card"')
        result_at = HTML.find('id="meal-plan-result"')
        self.assertGreater(card_at, 0)
        self.assertGreater(banner_at, card_at)
        self.assertGreater(result_at, banner_at)
        block = HTML[banner_at : banner_at + 420]
        self.assertIn("hidden", block)
        self.assertIn('role="status"', block)

    def test_copy_names_the_three_scopes(self):
        meal = HTML[HTML.find('id="meal-plan-card"') : HTML.find('id="meal-plan-result"')]
        self.assertIn("rebuilds this meal plan", meal)
        self.assertIn("workout only", meal)
        self.assertIn("Health and food logs", meal)
        self.assertNotIn("workout + meals", meal)
        self.assertNotIn("then both plans", meal)
        self.assertIn("Rebuild today's workout plan only", HTML)
        self.assertIn("pull Google Health and food logs now", HTML)


class InventoryMealOnly(unittest.TestCase):
    def test_generate_plan_busy_and_restore(self):
        self.assertIn("function setMealPlanBusy", JS)
        self.assertIn("Refreshing meal plan…", HTML)
        gen = _fn("async function generatePlan()", "const WORKOUT_PLAN_TRIGGER_IDS")
        self.assertIn("setMealPlanBusy(true)", gen)
        self.assertIn("setMealPlanBusy(false)", gen)
        self.assertIn("finally {", gen)
        self.assertIn('fetch("/api/meal-plan/generate"', gen)
        self.assertNotIn("/api/workout-plan", gen)
        self.assertNotIn("generateWorkoutPlan", gen)
        self.assertNotIn("loadDashboard", gen)
        self.assertNotIn("/api/dashboard", gen)
        self.assertNotIn("setFirstLoadVisible", gen)
        self.assertIn("renderMealPlan(data.plan)", gen)

    def test_inventory_mutations_call_generate_plan_only(self):
        submit = _fn("async function submitIngredient", "async function submitTargets")
        self.assertIn("await generatePlan()", submit)
        self.assertNotIn("generateWorkoutPlan", submit)
        self.assertNotIn("/api/workout-plan", submit)

        actions = _fn('if (action === "edit-save")', "function macroCalPct")
        self.assertIn("await generatePlan()", actions)
        self.assertIn('action === "stock"', actions)
        self.assertIn('action === "remove"', actions)
        self.assertNotIn("generateWorkoutPlan", actions)
        self.assertNotIn("/api/workout-plan", actions)

        self.assertGreaterEqual(JS.count("await generatePlan();"), 6)

    def test_node_meal_busy_path_sets_then_restores(self):
        script = ROOT / "tests" / "generate_meal_plan_busy.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok", proc.stdout)


class RefreshPlanWorkoutOnly(unittest.TestCase):
    def test_generate_workout_stays_workout_only(self):
        gen = _fn("async function generateWorkoutPlan", "async function submitExerciseCatalog")
        self.assertIn('fetch("/api/workout-plan/generate"', gen)
        self.assertIn("setWorkoutPlanBusy(true)", gen)
        self.assertIn("setWorkoutPlanBusy(false)", gen)
        self.assertIn("renderWorkoutPlan(data.plan)", gen)
        self.assertNotIn("generatePlan", gen)
        self.assertNotIn("/api/meal-plan", gen)
        self.assertNotIn("setMealPlanBusy", gen)
        self.assertNotIn("loadDashboard", gen)
        self.assertNotIn("/api/dashboard", gen)


class RefreshDataHealthOnly(unittest.TestCase):
    def test_force_refresh_hits_dashboard_not_plan_generate(self):
        fn = _fn("async function loadDashboard", "function startQuietPoll")
        self.assertIn('"/api/dashboard?refresh=1"', fn)
        self.assertIn('"/api/dashboard"', fn)
        self.assertNotIn("generateWorkoutPlan", fn)
        self.assertNotIn("generatePlan", fn)
        self.assertNotIn("/api/workout-plan", fn)
        self.assertNotIn("/api/meal-plan", fn)
        self.assertIn('() => loadDashboard(true)', JS)
        self.assertIn('$("btn-refresh").addEventListener("click", () => loadDashboard(true))', JS)
        self.assertIn('$("btn-refresh-mobile").addEventListener("click", () => loadDashboard(true))', JS)


class CacheAndHobbyLock(unittest.TestCase):
    def test_static_cache_bumped(self):
        self.assertIn("?v=meal-plan-busy-1", HTML)
        self.assertIn('const CACHE = "fitdash-shell-v51"', SW)
        self.assertIn("/styles.css?v=meal-plan-busy-1", SW)
        self.assertIn("/app.js?v=meal-plan-busy-1", SW)
        self.assertNotIn("refresh-plan-1", HTML)
        self.assertNotIn("refresh-plan-1", SW)

    def test_css_dims_meal_card_without_new_overlay(self):
        self.assertIn("#meal-plan-result.is-refreshing", CSS)
        self.assertIn(".today-first-load", CSS)
        self.assertIn("#app-shell.is-first-loading", CSS)

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


if __name__ == "__main__":
    unittest.main()
