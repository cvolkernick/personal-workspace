"""#1304: an open home-screen page must re-pull /api/daily-tasks on a later
same-day render, not stick to the first payload of the day."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
SW = (STATIC / "sw.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


class QuestResync1304(unittest.TestCase):
    def test_stale_same_day_triggers_sync(self):
        self.assertIn("const QUEST_RESYNC_MS = 5 * 60 * 1000;", APP)
        self.assertRegex(APP, r"Date\.now\(\) - lastQuestSyncAt > QUEST_RESYNC_MS")
        block = APP[APP.index("const questSyncStale"):]
        block = block[: block.index("// Targets with motivations")]
        self.assertRegex(block, r"foodLogsForceMealRegen \|\|\s*questSyncStale\s*\)")
        self.assertIn("if (ok) lastQuestSyncAt = Date.now();", block)

    def test_cache_bump(self):
        self.assertIn('const CACHE = "fitdash-shell-v145"', SW)
        self.assertIn("/app.js?v=quest-resync-1304-1", SW)
        self.assertIn("/app.js?v=quest-resync-1304-1", HTML)
        self.assertNotRegex(SW + HTML, re.escape("compound-muscles-1299-1"))


if __name__ == "__main__":
    unittest.main()
