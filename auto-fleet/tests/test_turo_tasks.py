"""Turo invoice-ready strip — Google Tasks read/complete, no local store."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gtasks as gtb  # noqa: E402
import turo_tasks  # noqa: E402


class FakeGT:
    def __init__(
        self,
        *,
        lists: list[dict] | None = None,
        tasks: list[dict] | None = None,
        create_list_id: str = "list-turo-new",
    ) -> None:
        self.lists = list(lists or [])
        self.tasks = list(tasks or [])
        self.create_list_id = create_list_id
        self.created_titles: list[str] = []
        self.completed: list[tuple[str, str, bool]] = []

    def list_tasklists(self) -> dict:
        return {"ok": True, "lists": self.lists}

    def create_tasklist(self, title: str) -> dict:
        self.created_titles.append(title)
        row = {"id": self.create_list_id, "title": title}
        self.lists.append(row)
        return {"ok": True, "list": row}

    def list_tasks(self, list_id: str, **kwargs) -> dict:
        return {
            "ok": True,
            "list_id": list_id,
            "tasks": [t for t in self.tasks if t.get("list_id") == list_id],
        }

    def complete_task(self, list_id: str, task_id: str, *, completed: bool = True) -> dict:
        self.completed.append((list_id, task_id, completed))
        for t in self.tasks:
            if t.get("id") == task_id:
                t["status"] = "completed" if completed else "needsAction"
                return {"ok": True, "task": t}
        return {"ok": False, "error": "not found"}


class TuroTasksTests(unittest.TestCase):
    def test_finds_existing_turo_list_without_creating(self) -> None:
        gt = FakeGT(
            lists=[
                {"id": "fitness", "title": "Fitness"},
                {"id": "turo-1", "title": "Turo"},
            ]
        )
        found = turo_tasks.find_or_create_turo_list(gt)
        self.assertTrue(found["ok"])
        self.assertEqual(found["list_id"], "turo-1")
        self.assertFalse(found["created"])
        self.assertEqual(gt.created_titles, [])

    def test_creates_only_the_turo_list_when_missing(self) -> None:
        gt = FakeGT(lists=[{"id": "fitness", "title": "Fitness"}])
        found = turo_tasks.find_or_create_turo_list(gt)
        self.assertTrue(found["ok"])
        self.assertTrue(found["created"])
        self.assertEqual(found["list_id"], "list-turo-new")
        self.assertEqual(gt.created_titles, ["Turo"])

    def test_open_items_are_title_and_notes_only(self) -> None:
        gt = FakeGT(
            lists=[{"id": "turo-1", "title": "Turo"}],
            tasks=[
                {
                    "id": "task-1",
                    "list_id": "turo-1",
                    "title": "Rebill toll — trip 8841",
                    "notes": "Guest left a SunPass charge. File on Turo.",
                    "status": "needsAction",
                    "updated": "2026-08-21T00:00:00Z",
                },
                {
                    "id": "task-done",
                    "list_id": "turo-1",
                    "title": "already invoiced",
                    "notes": "",
                    "status": "completed",
                },
            ],
        )
        payload = turo_tasks.list_open_tasks(gt=gt)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "google_tasks")
        self.assertEqual(payload["list_title"], "Turo")
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["id"], "task-1")
        self.assertEqual(item["title"], "Rebill toll — trip 8841")
        self.assertEqual(item["notes"], "Guest left a SunPass charge. File on Turo.")
        self.assertNotIn("vin", item)
        self.assertNotIn("amount", item)
        self.assertNotIn("trip", item)

    def test_empty_open_list_returns_no_items(self) -> None:
        gt = FakeGT(lists=[{"id": "turo-1", "title": "Turo"}], tasks=[])
        payload = turo_tasks.list_open_tasks(gt=gt)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["count"], 0)

    def test_complete_writes_back_to_turo_list(self) -> None:
        gt = FakeGT(
            lists=[{"id": "turo-1", "title": "Turo"}],
            tasks=[
                {
                    "id": "task-1",
                    "list_id": "turo-1",
                    "title": "Invoice cleaning fee",
                    "notes": "",
                    "status": "needsAction",
                }
            ],
        )
        result = turo_tasks.complete_task("task-1", "turo-1", gt=gt)
        self.assertTrue(result["ok"])
        self.assertEqual(gt.completed, [("turo-1", "task-1", True)])

    def test_complete_rejects_other_list(self) -> None:
        gt = FakeGT(lists=[{"id": "turo-1", "title": "Turo"}])
        result = turo_tasks.complete_task("task-1", "fitness", gt=gt)
        self.assertFalse(result["ok"])
        self.assertIn("Turo", result["error"])
        self.assertEqual(gt.completed, [])

    def test_missing_creds_is_honest_error(self) -> None:
        with mock.patch.object(
            turo_tasks.gtb,
            "credentials_status",
            return_value={"ok": False, "error": "Google Tasks not configured"},
        ):
            listed = turo_tasks.list_open_tasks()
            done = turo_tasks.complete_task("task-1")
        self.assertFalse(listed["ok"])
        self.assertEqual(listed["items"], [])
        self.assertIn("Google Tasks", listed["error"])
        self.assertFalse(done["ok"])
        self.assertIn("Google Tasks", done["error"])


class GtasksBridgePathTests(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop(gtb._MOD_NAME, None)

    def test_nest_candidate_is_first_and_exists(self) -> None:
        nest = (ROOT / "projects-dashboard" / "google_tasks.py").resolve()
        bundle = (
            ROOT / "resistance-dashboard" / "projects-dashboard" / "google_tasks.py"
        ).resolve()
        cands = gtb._google_tasks_candidates()
        self.assertIn(nest, cands)
        self.assertTrue(nest.is_file())
        self.assertLess(cands.index(nest), cands.index(bundle))

    def test_load_returns_complete_task(self) -> None:
        sys.modules.pop(gtb._MOD_NAME, None)
        mod = gtb.load_google_tasks()
        self.assertTrue(hasattr(mod, "credentials_status"))
        self.assertTrue(hasattr(mod, "complete_task"))
        self.assertTrue(hasattr(mod, "create_tasklist"))
        self.assertTrue(hasattr(mod, "list_tasks"))


class SurfaceContractTests(unittest.TestCase):
    def test_no_local_task_json(self) -> None:
        self.assertFalse((PKG / "data" / "turo_tasks.json").exists())
        self.assertFalse((PKG / "data" / "tasks.json").exists())

    def test_index_omits_empty_theater_and_keeps_favicon(self) -> None:
        html = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Auto Fleet</title>", html)
        self.assertIn("🚗", html)
        self.assertIn('rel="icon"', html)
        self.assertIn('id="host-ops"', html)
        self.assertIn('id="host-ops" hidden', html)
        self.assertIn("/api/turo-tasks", html)
        self.assertIn("/api/turo-tasks/complete", html)
        self.assertIn("function renderHostOps", html)
        self.assertNotIn("nothing to do", html.lower())
        self.assertNotIn("no invoice-ready", html.lower())
        self.assertNotIn("Orchestra", html)
        self.assertNotIn("NOW/NEXT", html)
        self.assertNotIn("gmail", html.lower())
        self.assertIn("/static/fleet/tesla-model-3-2020.jpg", html)
        self.assertIn("/static/fleet/rivian-r1s-2023.jpg", html)
        self.assertLess(html.find('id="host-ops"'), html.find('id="glance"'))
        self.assertLess(html.find('id="glance"'), html.find('id="cards"'))

    def test_no_orchestra_or_fcc_layout_change(self) -> None:
        orch = (ROOT / "orchestra" / "index.html").read_text(encoding="utf-8")
        fcc = (ROOT / "financial-command" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("turo-tasks", orch)
        self.assertNotIn("invoice-ready", orch.lower())
        self.assertNotIn("host-ops", orch)
        self.assertNotIn("turo-tasks", fcc)
        self.assertNotIn('id="host-ops"', fcc)

    def test_fleet_not_added_to_vercel_gtasks_env(self) -> None:
        vercel = (ROOT / "resistance-dashboard" / "vercel.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("auto-fleet", vercel)
        dumped = json.dumps(
            json.loads((ROOT / "resistance-dashboard" / "vercel.json").read_text())
        )
        self.assertNotIn("GOOGLE_TASKS", dumped)


class TuroTasksHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        import threading
        from http.server import ThreadingHTTPServer

        import server as fleet_server

        self.gt = FakeGT(
            lists=[{"id": "turo-1", "title": "Turo"}],
            tasks=[
                {
                    "id": "task-1",
                    "list_id": "turo-1",
                    "title": "Rebill toll",
                    "notes": "File on Turo.",
                    "status": "needsAction",
                }
            ],
        )
        self.list_patch = mock.patch(
            "server.list_open_tasks",
            side_effect=lambda **kwargs: turo_tasks.list_open_tasks(gt=self.gt, **kwargs),
        )
        self.complete_patch = mock.patch(
            "server.complete_task",
            side_effect=lambda task_id, list_id=None: turo_tasks.complete_task(
                task_id, list_id, gt=self.gt
            ),
        )
        self.list_patch.start()
        self.complete_patch.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fleet_server.AutoFleetHandler)
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.list_patch.stop()
        self.complete_patch.stop()

    def _json(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        import urllib.error
        import urllib.request

        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    def test_get_lists_open_turo_tasks(self) -> None:
        code, payload = self._json("GET", "/api/turo-tasks")
        self.assertEqual(code, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["title"], "Rebill toll")
        self.assertEqual(payload["items"][0]["notes"], "File on Turo.")

    def test_post_completes_in_google_tasks(self) -> None:
        code, payload = self._json(
            "POST",
            "/api/turo-tasks/complete",
            {"task_id": "task-1", "list_id": "turo-1"},
        )
        self.assertEqual(code, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.gt.completed, [("turo-1", "task-1", True)])
        listed = turo_tasks.list_open_tasks(gt=self.gt)
        self.assertEqual(listed["items"], [])


if __name__ == "__main__":
    unittest.main()
