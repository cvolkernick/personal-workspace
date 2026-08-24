"""HTTP surface — real server process, no live DIMO/Turo network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "auto-fleet"
SERVER = PKG / "server.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EMPTY_ENV = None  # set per test


def _http_json(method: str, url: str, timeout: float = 8.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw}
        return exc.code, payload


class AutoFleetServerTests(unittest.TestCase):
    def test_health_and_fleet_schema(self) -> None:
        port = 18796
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "env"
            env_path.write_text("# empty on purpose\n", encoding="utf-8")
            gtasks_dir = Path(td) / "gtasks"
            gtasks_dir.mkdir()
            proc_env = {
                **os.environ,
                "PYTHONPATH": str(ROOT),
                "GOOGLE_TASKS_CONFIG_DIR": str(gtasks_dir),
                "GOOGLE_TASKS_TOKEN_JSON": "",
                "GOOGLE_TASKS_REFRESH_TOKEN": "",
                "GOOGLE_TASKS_CLIENT_ID": "",
                "GOOGLE_TASKS_CLIENT_SECRET": "",
            }
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(SERVER),
                    "--port",
                    str(port),
                    "--host",
                    "127.0.0.1",
                    "--no-browser",
                    "--env",
                    str(env_path),
                    "--expenses",
                    str(FIXTURES / "expenses_with_fleet.json"),
                    "--turo-inbox",
                    str(PKG / "data" / "turo_inbox.json"),
                ],
                cwd=str(ROOT),
                env=proc_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            base = f"http://127.0.0.1:{port}"
            try:
                deadline = time.time() + 10
                last_err: Exception | None = None
                health: dict = {}
                while time.time() < deadline:
                    try:
                        code, health = _http_json("GET", f"{base}/api/health")
                        if code == 200 and health.get("ok"):
                            break
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
                        time.sleep(0.1)
                else:
                    err = (proc.stderr.read() if proc.stderr else "") or str(last_err)
                    self.fail(f"server did not become ready: {err}")

                self.assertEqual(health.get("service"), "auto-fleet")
                self.assertEqual(health.get("port"), port)
                self.assertTrue(health.get("ok"))

                code, fleet = _http_json("GET", f"{base}/api/fleet")
                self.assertEqual(code, 200, fleet)
                self.assertTrue(fleet.get("ok"))
                self.assertEqual(fleet.get("unit_count"), 5)
                self.assertEqual(len(fleet["units"]), 5)
                by_id = {u["id"]: u for u in fleet["units"]}
                r1s = by_id["r1s-2023"]
                self.assertEqual(r1s["identity"]["year"], 2023)
                self.assertEqual(r1s["identity"]["make"], "Rivian")
                self.assertEqual(r1s["identity"]["model"], "R1S")
                self.assertEqual(r1s["glance"]["title"], "2023 Rivian R1S")
                self.assertEqual(
                    r1s["glance"]["photo"], "/static/fleet/rivian-r1s-2023.jpg"
                )
                self.assertIsNone(r1s["identity"]["vin"])
                self.assertEqual(r1s["finance"]["sheet_lines"], [])
                self.assertEqual(
                    by_id["m3-2020"]["glance"]["photo"],
                    "/static/fleet/tesla-model-3-2020.jpg",
                )
                self.assertEqual(
                    by_id["m3-2022"]["glance"]["photo"],
                    "/static/fleet/tesla-model-3-2022.jpg",
                )
                self.assertEqual(
                    by_id["corolla-2024"]["glance"]["photo"],
                    "/static/fleet/toyota-corolla-2024.jpg",
                )
                self.assertEqual(
                    by_id["corolla-2022"]["glance"]["photo"],
                    "/static/fleet/toyota-corolla-2022.jpg",
                )
                for still in (
                    "rivian-r1s-2023.jpg",
                    "tesla-model-3-2020.jpg",
                    "tesla-model-3-2022.jpg",
                    "toyota-corolla-2022.jpg",
                    "toyota-corolla-2024.jpg",
                ):
                    req = urllib.request.Request(f"{base}/static/fleet/{still}")
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        self.assertEqual(resp.status, 200, still)
                        ctype = resp.headers.get("Content-Type", "")
                        self.assertEqual(
                            ctype.split(";")[0].strip(), "image/jpeg", f"{still}: {ctype}"
                        )
                        jpeg = resp.read()
                        self.assertGreater(len(jpeg), 10_000, still)
                        self.assertTrue(jpeg.startswith(b"\xff\xd8"), still)
                for unit in fleet["units"]:
                    self.assertIn("identity", unit)
                    self.assertIn("year", unit["identity"])
                    self.assertIn("role", unit["identity"])
                    self.assertIn(unit["identity"]["role"], ("personal", "turo", "unknown"))
                    self.assertIn("finance", unit)
                    self.assertIn("dimo", unit)
                    self.assertIn(unit["dimo"]["status"], ("unconfigured", "ok", "error"))
                    self.assertEqual(unit["turo"]["bookings"], [])
                    self.assertIn("inbox_status", unit["turo"])
                    self.assertNotIn("live_payoff", unit["finance"])
                    self.assertNotIn("combined_monthly", unit["finance"])
                    self.assertIn("glance", unit)
                    self.assertIn(unit["glance"]["freshness"], ("live", "stale", "dead", "unknown"))
                    if unit["finance"].get("sheet_lines"):
                        self.assertFalse(unit["finance"].get("stale"), unit["id"])
                        self.assertEqual(
                            unit["finance"]["source"], "expenses_sync.tabs.Fleet"
                        )
                self.assertFalse(fleet["sources"]["expenses"]["uses_combined_monthly"])
                self.assertNotIn("combined_monthly", fleet)

                code, tasks = _http_json("GET", f"{base}/api/turo-tasks")
                self.assertEqual(code, 200, tasks)
                self.assertFalse(tasks.get("ok"))
                self.assertEqual(tasks.get("items"), [])
                self.assertTrue(tasks.get("error"))
                self.assertIn("Google Tasks", str(tasks.get("error")))

                code, page = self._http_text(f"{base}/")
                self.assertEqual(code, 200)
                self.assertIn("Auto Fleet", page)
                self.assertIn("/api/fleet", page)
                self.assertIn("/api/turo-tasks", page)
                self.assertIn('id="host-ops"', page)
                self.assertIn("🚗", page)
                self.assertIn("<h3>Vehicle", page)
                self.assertIn("<h3>Schedule", page)
                self.assertIn("<h3>Money", page)
                self.assertNotIn("<h3>Finance", page)
                self.assertNotIn("combined_monthly", page)
                self.assertNotIn("FCC", page)
                self.assertNotIn("Mercury", page)
                self.assertNotIn("nothing to do", page.lower())
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def test_index_html_car_centric_strip_order(self) -> None:
        """#265: Vehicle → Schedule → Money → Trip detail. Bookings nest under the car."""
        html = (PKG / "index.html").read_text(encoding="utf-8")
        vehicle_fn = html.find("function vehicleStrip")
        schedule_fn = html.find("function scheduleStrip")
        money_fn = html.find("function moneyStrip")
        trip_fn = html.find("function tripDetailStrip")
        self.assertGreater(vehicle_fn, 0)
        self.assertGreater(schedule_fn, vehicle_fn)
        self.assertGreater(money_fn, schedule_fn)
        self.assertGreater(trip_fn, money_fn)
        render = html[html.find("function renderUnit") :]
        self.assertLess(render.find("vehicleStrip("), render.find("scheduleStrip("))
        self.assertLess(render.find("scheduleStrip("), render.find("moneyStrip("))
        self.assertLess(render.find("moneyStrip("), render.find("tripDetailStrip("))
        self.assertNotIn("<h3>Finance", html)
        self.assertIn("<h3>Vehicle", html)
        self.assertIn("<h3>Schedule", html)
        self.assertIn("<h3>Money", html)
        self.assertIn("<h3>Trip detail", html)
        self.assertNotIn("combined_monthly", html)
        self.assertNotIn("FCC", html)
        self.assertNotIn("Mercury", html)
        self.assertNotIn("expenses ${expAt}", html)
        self.assertIn("DIMO ${dimoLabel}", html)
        self.assertIn("Turo ${turoAt}", html)
        self.assertIn("costs sheet ${expAt}", html)
        readme = (PKG / "README.md").read_text(encoding="utf-8")
        self.assertIn("X Money", readme)
        self.assertNotIn("Mercury", readme)
        self.assertIn("every 15 minutes", readme.lower())
        self.assertIn("after:2026/08/18", readme)
        self.assertIn("financial-command/", readme)
        self.assertIn("setInterval(load, 15 * 60 * 1000)", html)
        sched_src = html[schedule_fn:money_fn]
        self.assertNotIn("inbox_status", sched_src)
        self.assertIn("function bookingRow", html)
        self.assertIn("bookingRow(", sched_src)
        self.assertIn('class="glance"', html)
        self.assertIn("function renderGlanceCell", html)
        self.assertIn('id="turo-inbox"', html)
        self.assertIn("min-height: 44px", html)
        self.assertIn("grid-template-columns: minmax(5.6rem, auto) minmax(0, 1fr)", html)
        self.assertIn("/static/fleet/tesla-model-3-2020.jpg", html)
        self.assertIn("/static/fleet/rivian-r1s-2023.jpg", html)
        self.assertIn("/static/fleet/tesla-model-3-2022.jpg", html)
        self.assertIn("/static/fleet/toyota-corolla-2022.jpg", html)
        self.assertIn("/static/fleet/toyota-corolla-2024.jpg", html)
        self.assertNotIn("TREAD", html)
        self.assertNotIn("SafeWheels", html)
        self.assertNotIn("Mercury", html)
        self.assertLess(html.find('id="glance"'), html.find('id="cards"'))
        self.assertIn('id="host-ops"', html)
        self.assertIn("🚗", html)
        self.assertNotIn("nothing to do", html.lower())
        self.assertNotIn(":8796", html)
        self.assertNotIn("8796", html)

    def test_turo_inbox_media_serves_photo_and_blocks_traversal(self) -> None:
        port = 18797
        jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            + b"\xff\xfe\x00\x0cfixture\xff\xd9"
        )
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "turo_inbox.json"
            media = Path(td) / "turo_inbox_media" / "m-photo"
            media.mkdir(parents=True)
            photo = media / "fuel.jpg"
            photo.write_bytes(jpeg)
            inbox.write_text(
                json.dumps(
                    {
                        "as_of": "2026-08-20",
                        "source": "test_fixture",
                        "messages": [
                            {
                                "id": "m-photo",
                                "from": "Turo <noreply@mail.turo.com>",
                                "subject": "Pat has sent you a message about your Toyota Corolla 2024",
                                "date": "Wed, 20 Aug 2026 12:00:00 +0000",
                                "body": "Contains photo(s).\nReservation ID #60619999\nToyota Corolla 2024\n",
                                "attachments": [
                                    {
                                        "filename": "fuel.jpg",
                                        "mime": "image/jpeg",
                                        "size": len(jpeg),
                                        "path": str(photo),
                                        "relpath": "m-photo/fuel.jpg",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env_path = Path(td) / "env"
            env_path.write_text("# empty\n", encoding="utf-8")
            gtasks_dir = Path(td) / "gtasks"
            gtasks_dir.mkdir()
            proc_env = {
                **os.environ,
                "PYTHONPATH": str(ROOT),
                "GOOGLE_TASKS_CONFIG_DIR": str(gtasks_dir),
                "GOOGLE_TASKS_TOKEN_JSON": "",
                "GOOGLE_TASKS_REFRESH_TOKEN": "",
                "GOOGLE_TASKS_CLIENT_ID": "",
                "GOOGLE_TASKS_CLIENT_SECRET": "",
            }
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(SERVER),
                    "--port",
                    str(port),
                    "--host",
                    "127.0.0.1",
                    "--no-browser",
                    "--env",
                    str(env_path),
                    "--turo-inbox",
                    str(inbox),
                ],
                cwd=str(ROOT),
                env=proc_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            base = f"http://127.0.0.1:{port}"
            try:
                deadline = time.time() + 10
                last_err: Exception | None = None
                while time.time() < deadline:
                    try:
                        code, health = _http_json("GET", f"{base}/api/health")
                        if code == 200 and health.get("ok"):
                            break
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
                        time.sleep(0.1)
                else:
                    err = (proc.stderr.read() if proc.stderr else "") or str(last_err)
                    self.fail(f"server did not become ready: {err}")

                code, fleet = _http_json("GET", f"{base}/api/fleet")
                self.assertEqual(code, 200, fleet)
                self.assertEqual(fleet.get("sources", {}).get("turo", {}).get("photo_count"), 1)
                self.assertEqual(len(fleet.get("turo_photos") or []), 1)
                req = urllib.request.Request(f"{base}/api/turo-inbox-media/m-photo/fuel.jpg")
                with urllib.request.urlopen(req, timeout=8) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(
                        resp.headers.get("Content-Type", "").split(";")[0].strip(),
                        "image/jpeg",
                    )
                    body = resp.read()
                self.assertEqual(body, jpeg)
                code, missing = _http_json(
                    "GET", f"{base}/api/turo-inbox-media/nope/missing.jpg"
                )
                self.assertEqual(code, 404)
                self.assertFalse(missing.get("ok", True))
                code, traversal = _http_json(
                    "GET", f"{base}/api/turo-inbox-media/m-photo/%2e%2e/secret.jpg"
                )
                self.assertEqual(code, 404)
                self.assertFalse(traversal.get("ok", True))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def test_financial_command_has_no_fleet_surface(self) -> None:
        fcc = ROOT / "financial-command" / "index.html"
        html = fcc.read_text(encoding="utf-8")
        lowered = html.lower()
        self.assertNotIn("auto-fleet", lowered)
        self.assertNotIn("/api/fleet", html)
        self.assertNotIn('id="fleet"', lowered)
        self.assertNotIn("data-tab=\"fleet\"", lowered)

    @staticmethod
    def _http_text(url: str) -> tuple[int, str]:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, resp.read().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
