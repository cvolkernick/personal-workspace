"""Turo invoice-ready strip — Turso read/complete, no local store, no Google Tasks."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import invoice_ready  # noqa: E402
import turo_tasks  # noqa: E402


def _store(rows: list[dict] | None = None) -> invoice_ready.MemoryStore:
    return invoice_ready.MemoryStore(rows)


class TuroTasksTests(unittest.TestCase):
    def test_open_items_are_title_and_notes_only(self) -> None:
        store = _store(
            [
                {
                    "id": "task-1",
                    "subject": "Rebill toll — trip 8841",
                    "notes": "Guest left a SunPass charge. File on Turo.",
                    "status": "open",
                    "updated_at": "2026-08-21T00:00:00Z",
                    "source_email_ref": "task-1",
                },
                {
                    "id": "task-done",
                    "subject": "already invoiced",
                    "notes": "",
                    "status": "completed",
                },
            ]
        )
        payload = turo_tasks.list_open_tasks(store=store)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "turso")
        self.assertEqual(payload["list_id"], "turso")
        self.assertEqual(len(payload["items"]), 1)
        item = payload["items"][0]
        self.assertEqual(item["id"], "task-1")
        self.assertEqual(item["title"], "Rebill toll — trip 8841")
        self.assertEqual(item["notes"], "Guest left a SunPass charge. File on Turo.")
        self.assertNotIn("vin", item)
        self.assertNotIn("amount", item)
        self.assertNotIn("trip", item)

    def test_empty_open_list_returns_no_items(self) -> None:
        payload = turo_tasks.list_open_tasks(store=_store([]))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["count"], 0)

    def test_complete_writes_back_to_turso(self) -> None:
        store = _store(
            [
                {
                    "id": "task-1",
                    "subject": "Invoice cleaning fee",
                    "notes": "",
                    "status": "open",
                }
            ]
        )
        result = turo_tasks.complete_task("task-1", "turso", store=store)
        self.assertTrue(result["ok"])
        listed = turo_tasks.list_open_tasks(store=store)
        self.assertEqual(listed["items"], [])

    def test_missing_turso_is_empty_not_google_tasks_error(self) -> None:
        from unittest import mock

        with mock.patch.object(invoice_ready, "configured", return_value=False):
            listed = turo_tasks.list_open_tasks()
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["items"], [])
        self.assertEqual(listed["source"], "turso")
        self.assertNotIn("Google Tasks", str(listed.get("error") or ""))


class SurfaceContractTests(unittest.TestCase):
    def test_no_local_task_json(self) -> None:
        self.assertFalse((PKG / "data" / "turo_tasks.json").exists())
        self.assertFalse((PKG / "data" / "tasks.json").exists())
        self.assertFalse((PKG / "data" / "invoice_ready.json").exists())

    def test_index_omits_empty_theater_and_keeps_favicon(self) -> None:
        html = (PKG / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Auto Fleet</title>", html)
        self.assertIn("🚗", html)
        self.assertIn('rel="icon"', html)
        self.assertIn('id="host-ops"', html)
        self.assertIn('id="host-ops" hidden', html)
        self.assertIn("api/turo-tasks", html)
        self.assertIn("api/turo-tasks/complete", html)
        self.assertIn("function renderHostOps", html)
        self.assertIn("function awaitingStrip", html)
        self.assertIn("<h3>Awaiting</h3>", html)
        self.assertNotIn("nothing to do", html.lower())
        self.assertNotIn("no invoice-ready", html.lower())
        self.assertNotIn("Orchestra", html)
        self.assertNotIn("NOW/NEXT", html)
        self.assertNotIn("gmail", html.lower())
        self.assertNotIn("Google Tasks", html)
        self.assertNotIn("Google Task", html)
        self.assertIn("static/fleet/tesla-model-3-2020.jpg", html)
        self.assertIn("static/fleet/rivian-r1s-2023.jpg", html)
        self.assertNotIn('"/static/fleet/', html)
        self.assertNotIn('fetch("/api/', html)
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
        path = ROOT / "resistance-dashboard" / "vercel.json"
        if not path.is_file():
            self.skipTest("resistance-dashboard/vercel.json not on this pin")
        vercel = path.read_text(encoding="utf-8")
        self.assertNotIn("auto-fleet", vercel)
        dumped = json.dumps(json.loads(vercel))
        self.assertNotIn("GOOGLE_TASKS", dumped)


class FleetInvoiceReadySnapshotTests(unittest.TestCase):
    """#747 — /api/fleet units carry awaiting fields from Turso."""

    def _build(self, store, inbox=None):
        import fleet

        return fleet.build_fleet(
            roster_path=PKG / "data" / "roster.json",
            notes_path=PKG / "data" / "notes.json",
            expenses_path=PKG / "tests" / "fixtures" / "expenses_no_fleet.json",
            inbox_path=inbox or (PKG / "data" / "turo_inbox.json"),
            dimo_env={},
            now="2026-08-23T12:00:00+00:00",
            gt=store,
        )

    def test_honest_empty_when_turo_list_has_no_open_items(self) -> None:
        payload = self._build(_store([]))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["invoice_unmatched"], [])
        for unit in payload["units"]:
            self.assertEqual(unit["invoice_ready"], [])
            self.assertEqual(unit["turo"]["invoice_ready"], [])
        self.assertEqual(payload["sources"]["turo_tasks"]["source"], "turso")
        self.assertTrue(payload["sources"]["turo_tasks"]["ok"])
        import glance

        html = glance.render_unit_card_html(payload["units"][2], now="2026-08-23T12:00:00+00:00")
        self.assertNotIn("<h3>Awaiting</h3>", html)

    def test_open_item_matches_car_completed_hidden(self) -> None:
        store = _store(
            [
                {
                    "id": "open-1",
                    "subject": "Rebill toll — 2024 Corolla",
                    "notes": "SunPass. File on Turo.",
                    "status": "open",
                },
                {
                    "id": "done-1",
                    "subject": "2024 Corolla already invoiced",
                    "notes": "",
                    "status": "completed",
                },
                {
                    "id": "plate-1",
                    "subject": "Follow-up — plate 24EWUH",
                    "notes": "",
                    "status": "open",
                },
                {
                    "id": "loose-1",
                    "subject": "Garage insurance shared",
                    "notes": "No car in this note.",
                    "status": "open",
                },
            ]
        )
        inbox = PKG / "tests" / "fixtures" / "turo_mike_corolla_body_year.json"
        payload = self._build(store, inbox=inbox)
        by_id = {u["id"]: u for u in payload["units"]}
        c24 = by_id["corolla-2024"]["invoice_ready"]
        c22 = by_id["corolla-2022"]["invoice_ready"]
        self.assertEqual([i["title"] for i in c24], ["Rebill toll — 2024 Corolla"])
        self.assertEqual([i["title"] for i in c22], ["Follow-up — plate 24EWUH"])
        self.assertEqual(payload["invoice_unmatched"][0]["title"], "Garage insurance shared")
        self.assertEqual(by_id["m3-2020"]["invoice_ready"], [])
        for item in c24 + c22:
            self.assertNotIn("amount", item)
            self.assertNotIn("vin", item)
        painted = " ".join(i["title"] for u in payload["units"] for i in u["invoice_ready"])
        self.assertNotIn("already invoiced", painted)
        self.assertTrue(by_id["corolla-2024"]["turo"]["bookings"])
        import glance

        html24 = glance.render_unit_card_html(by_id["corolla-2024"], now="2026-08-23T12:00:00+00:00")
        self.assertIn("<h3>Awaiting</h3>", html24)
        self.assertIn("Rebill toll — 2024 Corolla", html24)
        self.assertNotIn("already invoiced", html24)
        html20 = glance.render_unit_card_html(by_id["m3-2020"], now="2026-08-23T12:00:00+00:00")
        self.assertNotIn("<h3>Awaiting</h3>", html20)

    def test_trip_id_matches_booking_not_name_guess(self) -> None:
        store = _store(
            [
                {
                    "id": "trip-1",
                    "subject": "Invoice cleaning fee #60615645",
                    "notes": "",
                    "status": "open",
                }
            ]
        )
        inbox = PKG / "tests" / "fixtures" / "turo_mike_corolla_body_year.json"
        payload = self._build(store, inbox=inbox)
        by_id = {u["id"]: u for u in payload["units"]}
        self.assertEqual(
            [i["title"] for i in by_id["corolla-2024"]["invoice_ready"]],
            ["Invoice cleaning fee #60615645"],
        )
        self.assertEqual(by_id["corolla-2022"]["invoice_ready"], [])


class TuroTasksHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        import threading
        from http.server import ThreadingHTTPServer
        from unittest import mock

        import server as fleet_server

        self.store = _store(
            [
                {
                    "id": "task-1",
                    "subject": "Rebill toll",
                    "notes": "File on Turo.",
                    "status": "open",
                }
            ]
        )
        self.list_patch = mock.patch(
            "server.list_open_tasks",
            side_effect=lambda **kwargs: turo_tasks.list_open_tasks(
                store=self.store, **kwargs
            ),
        )
        self.complete_patch = mock.patch(
            "server.complete_task",
            side_effect=lambda task_id, list_id=None: turo_tasks.complete_task(
                task_id, list_id, store=self.store
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
        self.assertEqual(payload["source"], "turso")
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["title"], "Rebill toll")
        self.assertEqual(payload["items"][0]["notes"], "File on Turo.")

    def test_post_completes_in_turso(self) -> None:
        code, payload = self._json(
            "POST",
            "/api/turo-tasks/complete",
            {"task_id": "task-1", "list_id": "turso"},
        )
        self.assertEqual(code, 200, payload)
        self.assertTrue(payload["ok"])
        listed = turo_tasks.list_open_tasks(store=self.store)
        self.assertEqual(listed["items"], [])


if __name__ == "__main__":
    unittest.main()
