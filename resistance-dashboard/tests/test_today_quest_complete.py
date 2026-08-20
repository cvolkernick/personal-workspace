"""Today leaf quests must be real complete controls after first load.

Chris: items visible on Vercel Today but taps did nothing. Cover the
independent failure modes (IDs not bound, leftover overlay, wrong path)
without inventing quests or flipping preview_read_only.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import (
    daily_tasks_complete_body,
    dispatch_client_route,
    stamp_quest_list_ids,
)

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
VERCEL = (ROOT / "vercel.json").read_text(encoding="utf-8")
SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")


def _headers():
    token = make_session(
        {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
    )
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


class LeafQuestIsARealControl(unittest.TestCase):
    def test_leaf_with_ids_is_not_natively_disabled(self):
        self.assertIn("function questLeafIds", JS)
        self.assertIn("it.task_id || it.id", JS)
        self.assertIn("it.list_id || g.list_id || dailyListId", JS)
        self.assertIn("ready: !!(tid && lid)", JS)
        # Native disabled swallows clicks — ready leaves must stay a real button.
        render = JS.split("const renderCard = (it, g) =>", 1)[1].split(
            "Object.keys(byMeal)", 1
        )[0]
        self.assertIn("questLeafIds(it, g, listId)", render)
        self.assertNotIn('${ready ? "" : "disabled"}', render)
        self.assertIn('aria-disabled="true"', render)
        self.assertIn("data-task-id=", render)
        self.assertIn("data-list-id=", render)

    def test_click_posts_existing_complete_path(self):
        handler = JS.split("async function onDailyQuestClick", 1)[1].split(
            "function bindDailyQuestClicks", 1
        )[0]
        self.assertIn('fetch("/api/daily-tasks/complete"', handler)
        self.assertIn("method: \"POST\"", handler)
        self.assertIn("unlockQuestCard(btn)", handler)
        self.assertIn("Could not complete quest", handler)
        self.assertIn("function bindDailyQuestClicks", JS)
        self.assertIn('document.addEventListener("click", onDailyQuestClick)', JS)
        self.assertNotIn('fetch("/api/daily-tasks/complete/"', JS)

    def test_node_bind_and_click_hits_complete(self):
        script = ROOT / "tests" / "quest_complete_bind.js"
        proc = subprocess.run(
            ["node", str(script)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok", proc.stdout)


class OverlayLeavesHitBox(unittest.TestCase):
    def test_overlay_cannot_intercept_after_first_load(self):
        self.assertIn("#app-shell:not(.is-first-loading) > #today-first-load", CSS)
        start = CSS.find("#app-shell:not(.is-first-loading) > #today-first-load")
        self.assertGreaterEqual(start, 0)
        block = CSS[start - 80 : start + 280]
        self.assertIn("pointer-events: none !important", block)
        self.assertIn("display: none !important", block)
        self.assertIn("visibility: hidden !important", block)
        self.assertIn(".today-first-load[hidden]", block)
        # Finish path must drop the overlay from a11y + hit testing.
        finish = JS.split("function setFirstLoadVisible", 1)[1].split(
            "function finishFirstDashboardLoad", 1
        )[0]
        self.assertIn("el.hidden = !visible", finish)
        self.assertIn("el.inert = !visible", finish)
        self.assertIn("function finishFirstDashboardLoad", JS)
        self.assertIn("body.m-shell .quest-card", CSS)
        self.assertIn("pointer-events: auto", CSS)

    def test_overlay_is_not_inside_today_hub(self):
        overlay_at = HTML.find('id="today-first-load"')
        hub_at = HTML.find('id="today-hub"')
        self.assertGreater(overlay_at, 0)
        self.assertGreater(hub_at, overlay_at)


class CookieLessCompleteStill401(unittest.TestCase):
    def test_complete_401_json(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = daily_tasks_complete_body(
                {},
                {"list_id": "L1", "task_id": "t1", "completed": True},
            )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")
        self.assertFalse(body.get("ok", True))
        self.assertNotIn("<html", json.dumps(body).lower())

    def test_dispatch_cookie_less_401(self):
        payload = {"list_id": "L1", "task_id": "t1", "completed": True}
        with mock.patch.dict(os.environ, {}, clear=True):
            status, body = dispatch_client_route(
                {},
                "",
                "POST",
                payload=payload,
                path="/api/daily-tasks/complete",
            )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")


class StampListIdDoesNotInventQuests(unittest.TestCase):
    def test_stamps_list_id_only_when_task_id_exists(self):
        daily = stamp_quest_list_ids(
            {
                "list_id": "L1",
                "groups": [
                    {
                        "group": "training",
                        "items": [
                            {"title": "DB Press", "task_id": "t1"},
                            {"title": "Preview only"},
                        ],
                        "open_items": [{"title": "DB Press", "task_id": "t1"}],
                    }
                ],
            }
        )
        items = daily["groups"][0]["items"]
        self.assertEqual(items[0]["list_id"], "L1")
        self.assertEqual(items[0]["task_id"], "t1")
        self.assertNotIn("task_id", items[1])
        self.assertFalse(items[1].get("list_id"))
        self.assertEqual(daily["groups"][0]["open_items"][0]["list_id"], "L1")

    def test_does_not_invent_task_ids(self):
        daily = stamp_quest_list_ids(
            {
                "list_id": "L1",
                "groups": [{"group": "nutrition", "items": [{"title": "Oats"}]}],
            }
        )
        item = daily["groups"][0]["items"][0]
        self.assertIsNone(item.get("task_id"))
        self.assertFalse(item.get("list_id"))


class LayoutConstraints(unittest.TestCase):
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

    def test_ignore_command_kept(self):
        cfg = json.loads(VERCEL)
        self.assertEqual(
            cfg.get("ignoreCommand"),
            "python3 scripts/vercel_ignore.py || exit 1",
        )

    def test_no_preview_read_only_flip(self):
        util = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")
        complete = util.split("def daily_tasks_complete_body", 1)[1].split(
            "def inventory_write", 1
        )[0]
        self.assertNotIn("preview_read_only", complete)
        self.assertIn("complete_leaf", complete)


if __name__ == "__main__":
    unittest.main()
