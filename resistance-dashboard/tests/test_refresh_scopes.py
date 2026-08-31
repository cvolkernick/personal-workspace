"""FitDash refresh scopes: inventory=meals, Refresh plan=workout, Refresh data=Health.

Chris lock (2026-08-22): add/edit/remove/stock rebuilds the meal plan with a
visible busy state (meal only — do not re-roll the lift). Generate meal is the
dedicated meal control (same /api/meal-plan/generate). Refresh plan is
workout-only. Refresh data is Health / logs only.
Do not recouple Refresh plan or Refresh data.
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


class GenerateMealControl(unittest.TestCase):
    def test_one_generate_meal_control_and_busy_banner(self):
        self.assertIn('id="btn-generate-meal"', HTML)
        self.assertIn("Generate meal", HTML)
        self.assertNotIn("Refresh meal plan", HTML)
        self.assertNotIn('id="btn-refresh-meal"', HTML)
        self.assertIn('id="meal-plan-refreshing"', HTML)
        self.assertIn("Refreshing meal plan", HTML)
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
        self.assertIn("removing", meal)
        self.assertIn("workout plan only", meal)
        self.assertIn("Health and logs only", meal)
        self.assertNotIn("workout + meals", HTML)
        self.assertNotIn("Health, then both plans", HTML)
        self.assertIn("Rebuild today's workout plan only", HTML)
        self.assertIn(
            "Bypass cache — pull Google Health (weight, sleep, food, hydration) now",
            HTML,
        )
        self.assertIn("force Health pull", HTML)


class InventoryMealOnly(unittest.TestCase):
    def test_generate_plan_busy_and_restore(self):
        self.assertIn("function setMealPlanBusy", JS)
        self.assertIn("Refreshing meal plan…", HTML)
        gen = _fn("async function generatePlan", "const WORKOUT_PLAN_TRIGGER_IDS")
        self.assertIn("setMealPlanBusy(true)", gen)
        self.assertIn("setMealPlanBusy(false)", gen)
        self.assertIn("finally {", gen)
        self.assertIn('fetch("/api/meal-plan/generate"', gen)
        self.assertNotIn("/api/workout-plan", gen)
        self.assertNotIn("generateWorkoutPlan", gen)
        self.assertNotIn("loadDashboard", gen)
        self.assertNotIn("/api/dashboard", gen)
        self.assertNotIn("setFirstLoadVisible", gen)
        self.assertNotIn("setWorkoutPlanBusy", gen)
        self.assertIn("renderMealPlan(data.plan)", gen)

    def test_meal_bucket_head_shows_local_eat_at_clock(self):
        self.assertIn("function mealBucketClock", JS)
        self.assertIn("m.eat_at", JS)
        self.assertIn("m.eat_at_label", JS)
        render = _fn("function renderMealPlan", "function liveFingerprint")
        self.assertIn("mealBucketClock(m)", render)
        self.assertIn("meal-bucket-time", render)
        today = _fn('if ($("today-meal"))', 'if ($("today-purchases"))')
        self.assertIn("mealBucketClock(bucket)", today)
        self.assertIn("today-meal-bucket-time", today)
        self.assertIn(".meal-bucket-time", CSS)

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

        remove = _fn('if (action === "remove")', 'else if (action === "stock")')
        self.assertIn("await generatePlan()", remove)

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
    def test_generate_workout_does_not_call_meal(self):
        gen = _fn("async function generateWorkoutPlan", "async function submitEquipmentInventory")
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
    def test_force_refresh_does_not_generate_plans(self):
        fn = _fn("async function loadDashboard", "function startQuietPoll")
        self.assertIn('"/api/dashboard?refresh=1"', fn)
        self.assertIn('"/api/dashboard"', fn)
        self.assertNotIn("generateWorkoutPlan", fn)
        self.assertNotIn("generatePlan", fn)
        self.assertNotIn("/api/workout-plan", fn)
        self.assertNotIn("/api/meal-plan", fn)
        self.assertIn('() => loadDashboard(true)', JS)
        self.assertIn(
            '$("btn-refresh").addEventListener("click", () => loadDashboard(true))',
            JS,
        )
        self.assertIn(
            '$("btn-refresh-mobile").addEventListener("click", () => loadDashboard(true))',
            JS,
        )


class QuestRolloverNotOnRefreshButtons(unittest.TestCase):
    def test_refresh_plan_does_not_sync_quests(self):
        gen = _fn("async function generateWorkoutPlan", "async function submitEquipmentInventory")
        self.assertNotIn("syncDailyTasksFromServer", gen)
        self.assertNotIn("/api/daily-tasks", gen)

    def test_inventory_meal_does_not_sync_quests(self):
        gen = _fn("async function generatePlan", "const WORKOUT_PLAN_TRIGGER_IDS")
        self.assertNotIn("syncDailyTasksFromServer", gen)
        self.assertNotIn("/api/daily-tasks", gen)

    def test_refresh_data_does_not_call_daily_tasks(self):
        fn = _fn("async function loadDashboard", "function startQuietPoll")
        self.assertNotIn("syncDailyTasksFromServer", fn)
        self.assertNotIn("/api/daily-tasks", fn)
        self.assertIn('"/api/dashboard?refresh=1"', fn)

    def test_today_hub_gates_sync_to_new_local_date(self):
        hub = _fn("function renderTodayHub", "function prefillsFromWorkoutPlan")
        self.assertIn("lastQuestSyncedDay", hub)
        self.assertIn("alreadySyncedToday", hub)
        self.assertIn("lastSyncedDailyTasks", hub)
        self.assertIn("syncDailyTasksFromServer()", hub)
        self.assertIn("foodLogsForceMealRegen", hub)
        self.assertIn("lastSyncedFoodLogsFp", hub)
        self.assertIn("food_logs_fp", hub)


class CacheAndHobbyLock(unittest.TestCase):
    def test_static_cache_bumped(self):
        self.assertIn("?v=azm-90d-2", HTML)
        self.assertIn("?v=meal-slot-1", HTML)
        self.assertIn("?v=bottle-charge-7", HTML)
        self.assertIn("?v=paired-avgs-6", HTML)
        self.assertIn('const CACHE = "fitdash-shell-v75"', SW)
        self.assertIn("/styles.css?v=azm-90d-2", SW)
        self.assertNotIn("bottle-charge-3", HTML)
        self.assertNotIn("bottle-charge-4", HTML)
        self.assertNotIn("bottle-charge-5", HTML)
        self.assertNotIn("bottle-charge-6", HTML)
        self.assertNotIn("bottle-inline-1", HTML)
        self.assertNotIn("bottle-tall-2", HTML)
        self.assertNotIn("bottle-sideways-1", HTML)
        self.assertNotIn("hydration-meta-bottom-1", HTML)
        self.assertNotIn("fitdash-shell-v55", SW)
        self.assertNotIn("fitdash-shell-v56", SW)
        self.assertNotIn("fitdash-shell-v57", SW)
        self.assertNotIn("fitdash-shell-v58", SW)
        self.assertNotIn("fitdash-shell-v59", SW)
        self.assertNotIn("fitdash-shell-v60", SW)
        self.assertNotIn("fitdash-shell-v61", SW)
        self.assertNotIn("fitdash-shell-v62", SW)
        self.assertNotIn("fitdash-shell-v63", SW)
        self.assertNotIn("fitdash-shell-v64", SW)
        self.assertNotIn("fitdash-shell-v65", SW)
        self.assertNotIn("fitdash-shell-v66", SW)
        self.assertNotIn("fitdash-shell-v67", SW)
        self.assertNotIn("fitdash-shell-v68", SW)
        self.assertNotIn("fitdash-shell-v69", SW)
        self.assertNotIn("fitdash-shell-v70", SW)
        self.assertNotIn("fitdash-shell-v71", SW)
        self.assertNotIn("fitdash-shell-v72", SW)
        self.assertNotIn("fitdash-shell-v73", SW)
        self.assertNotIn("azm-week-1", HTML)
        self.assertNotIn("azm-week-2", HTML)
        self.assertNotIn("azm-spark-1", HTML)
        self.assertNotIn("calorie-meta-bottom-1", HTML)
        self.assertNotIn("calorie-meta-bottom-1", SW)
        self.assertIn("?v=ewi-cap-1", HTML)
        self.assertIn("/app.js?v=history-sets-1", SW)
        self.assertIn("/app.js?v=history-sets-1", HTML)
        self.assertNotIn("ewi-cap-5lb-1", HTML)
        self.assertNotIn("ewi-cap-5lb-1", SW)
        self.assertNotIn("cals-60d-1", HTML)
        self.assertNotIn("cals-60d-1", SW)
        self.assertNotIn("cals-75d-1", HTML)
        self.assertNotIn("cals-75d-1", SW)
        self.assertNotIn("meal-gtasks-321-1", HTML)
        self.assertNotIn("meal-gtasks-321-1", SW)
        self.assertNotIn("paired-avgs-5", HTML)
        self.assertNotIn("quest-log-paint-1", HTML)
        self.assertNotIn("quest-log-paint-1", SW)
        self.assertIn("/meal-snapshot.js?v=meal-slot-1", HTML)
        self.assertNotIn("quest-rollover-1", HTML)
        self.assertNotIn("quest-rollover-1", SW)
        self.assertNotIn("refresh-scopes-1", HTML)
        self.assertNotIn("refresh-scopes-1", SW)
        self.assertNotIn("refresh-plan-1", HTML)
        self.assertNotIn("refresh-plan-1", SW)
        self.assertNotIn("meal-plan-busy-2", HTML)
        self.assertNotIn("meal-plan-busy-2", SW)

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
