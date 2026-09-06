"""Log workout exercise name is a library dropdown, not free text."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


class LogLibrarySelect(unittest.TestCase):
    def test_log_row_is_select_from_available_library(self):
        self.assertIn('<select class="ex-name" required aria-label="Exercise">', JS)
        self.assertNotIn('<input type="text" class="ex-name"', JS)
        self.assertIn("function libraryLogExercises", JS)
        self.assertIn("if (!ex || !ex.available) continue", JS)
        self.assertIn("function fillExerciseNameSelect", JS)
        self.assertIn("function refreshExerciseNameSelects", JS)
        self.assertIn('#exercise-rows select.ex-name', JS)
        self.assertIn('session: "logged"', JS)
        self.assertIn("Not in library", JS)
        self.assertIn("fillExerciseNameSelect(card.querySelector(\".ex-name\")", JS)
        self.assertIn("No exercises in library", JS)
        self.assertIn("Select exercise", JS)
        self.assertIn("loaded && items.length === 0 && !wanted", JS)

    def test_catalog_render_refreshes_log_dropdowns(self):
        catalog = JS.split("function renderExerciseCatalog", 1)[1].split(
            "function renderLibrarySuggestions", 1
        )[0]
        self.assertIn("refreshExerciseNameSelects()", catalog)
        self.assertIn("available", catalog)

    def test_collect_still_posts_name(self):
        collect = JS.split("function collectExercises", 1)[1].split(
            "function destroyChart", 1
        )[0]
        self.assertIn('card.querySelector(".ex-name").value.trim()', collect)

    def test_seed_library_has_available_names(self):
        catalog = json.loads(
            (ROOT.parent / "fitness" / "exercises" / "catalog.json").read_text(
                encoding="utf-8"
            )
        )
        names = [
            e["name"]
            for e in catalog.get("exercises") or []
            if isinstance(e, dict) and e.get("available") and e.get("name")
        ]
        off = [
            e["name"]
            for e in catalog.get("exercises") or []
            if isinstance(e, dict) and not e.get("available") and e.get("name")
        ]
        self.assertIn("DB Flat Press", names)
        self.assertTrue(off, "catalog should keep some movements off-library")
        self.assertNotIn("DB Floor Press", names)
        self.assertGreaterEqual(len(names), 5)

    def test_help_copy_points_at_library(self):
        log = HTML[HTML.find('id="log-card"') : HTML.find('id="history-card"')]
        self.assertIn("exercise library", log)
        self.assertNotIn("e.g. DB Flat Press", log)

    def test_cache_bumped(self):
        self.assertIn('const CACHE = "fitdash-shell-v81"', SW)
        self.assertIn("/app.js?v=restock-stock-1", HTML)
        self.assertIn("/app.js?v=restock-stock-1", SW)
        self.assertNotIn("/app.js?v=log-lib-select-1", HTML)
        self.assertNotIn("/app.js?v=log-lib-select-1", SW)
        self.assertNotIn("/app.js?v=ask-429-1", HTML)
        self.assertNotIn("/app.js?v=ask-429-1", SW)
        self.assertNotIn("fitdash-shell-v80", SW)
        self.assertNotIn("fitdash-shell-v79", SW)


if __name__ == "__main__":
    unittest.main()
