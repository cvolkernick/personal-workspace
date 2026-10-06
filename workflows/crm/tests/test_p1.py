"""P1 acceptance tests. Fixture database, no network, no prod token."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROADSIDE = ROOT / "workflows" / "panamerica-roadside-crm"
FIXTURE = ROADSIDE / "tests" / "fixtures" / "photo_sets.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.crm.api import dispatch, serve  # noqa: E402
from workflows.crm.core import CrmDB  # noqa: E402
from workflows.crm.importer import backup_file, import_all  # noqa: E402
from workflows.crm.schema import mode_bits  # noqa: E402

NOW = datetime(2026, 9, 17, 20, 0, 0, tzinfo=timezone.utc)


def _request(base: str, method: str, path: str, token: str = "", body: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw or "{}")


class ApiCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CrmDB(Path(self.tmp.name) / "crm.db")
        self.grok = self.db.issue_token("grok")["token"]
        self.alex = self.db.issue_token("alexandra")["token"]
        self.buzz = self.db.issue_token("buzz")["token"]
        self.chris = self.db.issue_token("chris")["token"]
        httpd = serve(self.db, "127.0.0.1", 0)
        self.httpd = httpd
        self.thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.db.close()
        self.tmp.cleanup()

    def call(self, method: str, path: str, token: str = "", body: dict | None = None):
        return _request(self.base, method, path, token, body)

    def test_db_mode_denies_group_and_other(self) -> None:
        path = Path(self.tmp.name) / "crm.db"
        self.assertEqual(mode_bits(path) & 0o077, 0)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_token_round_trip_and_missing_token(self) -> None:
        status, created = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "lead", "display_name": "Kevin", "status": "new", "phone": "+12394648445"},
        )
        self.assertEqual(status, 201, created)
        got, body = self.call("GET", f"/v1/parties/{created['id']}", self.grok)
        self.assertEqual(got, 200)
        self.assertEqual(body, created)
        denied, err = self.call("POST", "/v1/parties", "", {"type": "lead", "display_name": "Nope"})
        self.assertEqual(denied, 401, err)

    def test_alexandra_cannot_delete_or_create_vendor(self) -> None:
        status, created = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "lead", "display_name": "Kevin", "phone": "+12395550100"},
        )
        self.assertEqual(status, 201, created)
        deleted, err = self.call("DELETE", f"/v1/parties/{created['id']}", self.alex)
        self.assertEqual(deleted, 403, err)
        vendor, verr = self.call(
            "POST",
            "/v1/parties",
            self.alex,
            {"type": "vendor", "display_name": "Next Tires"},
        )
        self.assertEqual(vendor, 403, verr)

    def test_buzz_interaction_audits_seat(self) -> None:
        status, party = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "lead", "display_name": "Kevin", "phone": "+12395550101"},
        )
        self.assertEqual(status, 201, party)
        posted, row = self.call(
            "POST",
            "/v1/interactions",
            self.buzz,
            {
                "party_id": party["id"],
                "channel": "sms",
                "direction": "out",
                "provider": "bland",
                "provider_msg_id": "msg-buzz-1",
                "outcome": "sent",
                "occurred_at": "2026-10-03T16:10:00Z",
            },
        )
        self.assertEqual(posted, 201, row)
        code, audit = self.call("GET", f"/v1/audit?entity_id={row['id']}", self.grok)
        self.assertEqual(code, 200, audit)
        self.assertTrue(any(item["actor_seat"] == "buzz" for item in audit["results"]))

    def test_vendor_search_and_cli_badges(self) -> None:
        status, vendor = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {
                "type": "vendor",
                "display_name": "Next Tires",
                "tags": ["tire-shop"],
                "location": "Cape Coral",
                "status": "new",
            },
        )
        self.assertEqual(status, 201, vendor)
        code, found = self.call("GET", "/v1/search?type=vendor&tag=tire-shop", self.grok)
        self.assertEqual(code, 200, found)
        self.assertEqual([row["id"] for row in found["results"]], [vendor["id"]])
        proc = subprocess.run(
            [sys.executable, "-m", "workflows.crm.cli", "--db", str(self.db.path), "list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[VENDOR] Next Tires", proc.stdout)
        lead_status, _lead = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "lead", "display_name": "Kevin", "phone": "+12395550111"},
        )
        self.assertEqual(lead_status, 201)
        again = subprocess.run(
            [sys.executable, "-m", "workflows.crm.cli", "--db", str(self.db.path), "list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn("[LEAD] Kevin", again.stdout)
        self.assertIn("[VENDOR] Next Tires", again.stdout)

    def test_dedup_phone_email_vin_listing(self) -> None:
        status, kevin = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {
                "type": "lead",
                "display_name": "Kevin",
                "phone": "+12394648445",
                "email": "Kevin@Example.com",
                "vin": "1hgcm82633a004352",
                "listing_id": "LIST-9",
            },
        )
        self.assertEqual(status, 201, kevin)
        before = self.db.count_parties()
        for body in (
            {"type": "lead", "display_name": "Other", "phone": "(239) 464-8445"},
            {"type": "lead", "display_name": "Other", "email": "kevin@example.com"},
            {"type": "lead", "display_name": "Other", "vin": "1HGCM82633A004352"},
            {"type": "lead", "display_name": "Other", "listing_id": "LIST-9"},
        ):
            code, row = self.call("POST", "/v1/parties", self.grok, body)
            self.assertEqual(code, 200, row)
            self.assertEqual(row["result"], "existing")
            self.assertEqual(row["id"], kevin["id"])
            self.assertEqual(self.db.count_parties(), before)

    def test_interaction_idempotent_and_timeline(self) -> None:
        _status, party = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "lead", "display_name": "Kevin", "phone": "+12395550122"},
        )
        payload = {
            "party_id": party["id"],
            "channel": "sms",
            "direction": "out",
            "provider": "bland",
            "provider_msg_id": "msg-once",
            "outcome": "delivered",
            "occurred_at": "2026-10-03T16:10:00Z",
        }
        first, row = self.call("POST", "/v1/interactions", self.grok, payload)
        second, again = self.call("POST", "/v1/interactions", self.grok, payload)
        self.assertEqual(first, 201, row)
        self.assertEqual(second, 200, again)
        self.assertEqual(again["id"], row["id"])
        self.assertEqual(self.db.count_interactions(party["id"]), 1)
        code, timeline = self.call("GET", f"/v1/parties/{party['id']}/timeline", self.grok)
        self.assertEqual(code, 200, timeline)
        item = timeline["results"][0]
        self.assertEqual(item["channel"], "sms")
        self.assertEqual(item["direction"], "out")
        self.assertEqual(item["outcome"], "delivered")
        self.assertEqual(item["occurred_at"], "2026-10-03T16:10:00Z")

    def test_search_names_under_half_second(self) -> None:
        self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {
                "type": "lead",
                "display_name": "Kevin",
                "phone": "+12395550133",
                "vehicle": {"make": "Jeep", "model": "Wrangler"},
            },
        )
        self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "vendor", "display_name": "Next Tires", "tags": ["tire-shop"], "status": "new"},
        )
        started = time.perf_counter()
        code, kevin = self.call("GET", "/v1/search?q=wrangler", self.grok)
        tires_code, tires = self.call("GET", "/v1/search?q=tire", self.grok)
        elapsed = time.perf_counter() - started
        self.assertEqual(code, 200, kevin)
        self.assertEqual(tires_code, 200, tires)
        self.assertEqual([row["display_name"] for row in kevin["results"]], ["Kevin"])
        self.assertEqual([row["display_name"] for row in tires["results"]], ["Next Tires"])
        self.assertLess(elapsed, 0.5)

    def test_landline_and_stop(self) -> None:
        _status, party = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "lead", "display_name": "Linda", "phone": "+12395550144", "status": "new"},
        )
        code, row = self.call(
            "POST",
            "/v1/interactions",
            self.grok,
            {
                "party_id": party["id"],
                "channel": "sms",
                "direction": "out",
                "provider_msg_id": "msg-landline",
                "outcome": "undelivered",
                "error_code": "30006",
                "occurred_at": "2026-10-03T16:00:00Z",
            },
        )
        self.assertEqual(code, 201, row)
        self.assertTrue(self.db.phone_has_tag(party["id"], "landline"))
        _status, stopper = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "lead", "display_name": "Sam", "phone": "+12395550155", "status": "new"},
        )
        stopped, _row = self.call(
            "POST",
            "/v1/interactions",
            self.grok,
            {
                "party_id": stopper["id"],
                "channel": "sms",
                "direction": "in",
                "provider_msg_id": "msg-stop",
                "body": "STOP",
                "outcome": "replied",
                "occurred_at": "2026-10-03T17:00:00Z",
            },
        )
        self.assertEqual(stopped, 201)
        fetched = self.db.get_party(self.db.actor_for_seat("grok"), stopper["id"])
        self.assertEqual(fetched["status"], "declined")
        self.assertTrue(self.db.is_suppressed("phone:2395550155"))

    def test_tasks_notes_archive_and_audit(self) -> None:
        _status, party = self.call(
            "POST",
            "/v1/parties",
            self.grok,
            {"type": "customer", "display_name": "Guest", "status": "new"},
        )
        today = datetime.now(timezone.utc).date().isoformat()
        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        made, task = self.call(
            "POST",
            "/v1/tasks",
            self.grok,
            {"party_id": party["id"], "title": "Call back", "due_at": "today"},
        )
        self.assertEqual(made, 201, task)
        self.assertEqual(task["due_at"], today)
        listed, tasks = self.call("GET", "/v1/tasks?due_before=tomorrow", self.grok)
        self.assertEqual(listed, 200, tasks)
        self.assertIn(task["id"], [row["id"] for row in tasks["results"]])
        self.assertTrue(all(row["due_at"] < tomorrow for row in tasks["results"]))
        done, finished = self.call(
            "PATCH", "/v1/tasks/" + task["id"], self.grok, {"status": "done"}
        )
        self.assertEqual(done, 200, finished)
        self.assertEqual(finished["status"], "done")
        self.assertEqual(finished["completed_by"], "grok")
        self.assertTrue(finished["completed_at"])

        noted, note = self.call(
            "POST",
            "/v1/notes",
            self.grok,
            {"party_id": party["id"], "body": "left a card"},
        )
        self.assertEqual(noted, 201, note)
        read, notes = self.call("GET", f"/v1/notes?party_id={party['id']}", self.grok)
        self.assertEqual([row["body"] for row in notes["results"]], ["left a card"])
        updated, edited = self.call(
            "PATCH", f"/v1/notes/{note['id']}", self.grok, {"body": "left a card on the dash"}
        )
        self.assertEqual(updated, 200, edited)
        archived, hidden = self.call(
            "PATCH", f"/v1/notes/{note['id']}", self.grok, {"archived": True}
        )
        self.assertEqual(archived, 200, hidden)
        self.assertTrue(hidden["archived"])
        empty, remaining = self.call("GET", f"/v1/notes?party_id={party['id']}", self.grok)
        self.assertEqual(empty, 200)
        self.assertEqual(remaining["results"], [])

        vendor_status, vendor = self.call(
            "POST",
            "/v1/parties",
            self.chris,
            {"type": "vendor", "display_name": "Shop", "status": "new"},
        )
        self.assertEqual(vendor_status, 201, vendor)
        changed, after = self.call(
            "PATCH", f"/v1/parties/{vendor['id']}", self.chris, {"status": "active"}
        )
        self.assertEqual(changed, 200, after)
        audit_code, audit = self.call("GET", f"/v1/audit?entity_id={vendor['id']}", self.chris)
        self.assertEqual(audit_code, 200, audit)
        updates = [row for row in audit["results"] if row["op"] == "update"]
        self.assertTrue(updates)
        before_doc = updates[-1]["before"]
        after_doc = updates[-1]["after"]
        changed_keys = {
            key
            for key in set(before_doc) | set(after_doc)
            if before_doc.get(key) != after_doc.get(key)
        }
        self.assertIn("status", changed_keys)
        self.assertEqual(updates[-1]["actor_seat"], "chris")
        self.assertTrue(updates[-1]["at"])
        blocked, err = self.call("POST", "/v1/audit", self.chris, {"op": "update"})
        self.assertEqual(blocked, 405, err)
        blocked_patch, err = self.call("PATCH", "/v1/audit", self.chris, {"op": "update"})
        self.assertEqual(blocked_patch, 405, err)
        blocked_del, err = self.call("DELETE", "/v1/audit", self.chris)
        self.assertEqual(blocked_del, 405, err)
        archived_party, gone = self.call(
            "PATCH", f"/v1/parties/{vendor['id']}", self.chris, {"archived": True}
        )
        self.assertEqual(archived_party, 200, gone)
        self.assertTrue(gone["archived"])
        customer_read, customer = self.call("GET", f"/v1/parties/{party['id']}", self.grok)
        self.assertEqual(customer_read, 200)
        self.assertEqual(customer["type"], "customer")


class ImportCase(unittest.TestCase):
    def test_importer_parity_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CrmDB(root / "crm.db")
            lead_id = "lead-kevin"
            roadside = {
                "version": 1,
                "leads": {
                    lead_id: {
                        "id": lead_id,
                        "folder_id": "folder-1",
                        "contact_name": "Kevin",
                        "state": "new",
                        "phone": "2394648445",
                        "year": "2018",
                        "make": "Jeep",
                        "model": "Wrangler",
                        "notes": ["sign in the window"],
                        "events": [{"at": "2026-10-01T00:00:00Z", "op": "intake"}],
                    }
                },
                "suppression": [{"key": "phone:2395550199", "reason": "opt-out", "at": "2026-10-01T00:00:00Z"}],
                "outbox": [
                    {
                        "lead_id": lead_id,
                        "channel": "sms",
                        "to": "+12394648445",
                        "body": "hello",
                        "provider_msg_id": "outbox-msg-1",
                        "at": "2026-10-02T00:00:00Z",
                    }
                ],
                "processed_folders": {"folder-1": {"lead_id": lead_id, "reason": "new", "at": "2026-10-01T00:00:00Z"}},
                "alerts": [],
                "sms_by_date": {},
            }
            roadside_path = root / "store.json"
            roadside_path.write_text(json.dumps(roadside), encoding="utf-8")
            market = {
                "leads": {
                    "mkt-1": {
                        "id": "mkt-1",
                        "listing_id": "100200300400",
                        "phone": "2395550177",
                        "seller_name": "Pat",
                        "year": "2020",
                        "make": "Honda",
                        "model": "Civic",
                        "status": "sms_queued",
                    }
                }
            }
            market_path = root / "market.json"
            market_path.write_text(json.dumps(market), encoding="utf-8")
            log_path = root / "outreach_log.jsonl"
            log_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "name": "Steven Foldon",
                                "outcome": "delivered",
                                "provider_msg_id": "e2e0cae8-4e8a-469c-9754-3fe50e9a5faa",
                                "occurred_at": "2026-10-03T16:10:00-04:00",
                                "channel": "sms",
                                "direction": "out",
                            }
                        ),
                        json.dumps(
                            {
                                "name": "Linda Quiroz",
                                "outcome": "undelivered",
                                "error_code": "30006",
                                "provider_msg_id": "b6383a4c-7294-4156-b1b2-49f2ed1c6f9c",
                                "occurred_at": "2026-10-03",
                                "channel": "sms",
                                "direction": "out",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            vendors_path = root / "vendors.json"
            vendors_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "vendor-next-tires",
                            "name": "Next Tires",
                            "location": "Cape Coral",
                            "type": "vendor",
                            "tags": ["tire-shop"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            backup = backup_file(roadside_path)
            self.assertIsNotNone(backup)
            before = backup.read_bytes() if backup else b""
            report = import_all(
                db,
                roadside=roadside_path,
                marketplace=market_path,
                outreach_log=log_path,
                vendors=vendors_path,
            )
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["roadside_leads"]["source"], 1)
            self.assertEqual(report["outreach_log"]["source"], 2)
            self.assertEqual(report["vendors"]["imported"], 1)
            self.assertEqual(report["marketplace_leads"]["imported"], 1)
            again = import_all(
                db,
                roadside=roadside_path,
                marketplace=market_path,
                outreach_log=log_path,
                vendors=vendors_path,
            )
            self.assertTrue(again["ok"], again)
            self.assertEqual(db.count_interactions(), 3)
            roadside_path.write_text(roadside_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.assertEqual(backup.read_bytes() if backup else b"", before)
            db.close()

            bad = root / "bad-vendors.json"
            bad.write_text(json.dumps([{"id": "x", "type": "robot", "name": "Nope"}]), encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workflows.crm.importer",
                    "--db",
                    str(root / "other.db"),
                    "--vendors",
                    str(bad),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class RoadsideBackendCase(unittest.TestCase):
    def _run(self, store: Path, backend: str, db: Path, cmd: str) -> dict:
        env = os.environ.copy()
        env["PANAMERICA_ROADSIDE_BACKEND"] = backend
        env["PANAMERICA_CRM_DB"] = str(db)
        env.pop("PANAMERICA_ROADSIDE_LIVE", None)
        proc = subprocess.run(
            [
                sys.executable,
                str(ROADSIDE / "run.py"),
                "--dry-run",
                "--store",
                str(store),
                "--fixture",
                str(FIXTURE),
                cmd,
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return json.loads(proc.stdout)

    def test_daily_pass_matches_file_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_out = self._run(root / "file.json", "file", root / "unused.db", "daily-pass")
            crm_out = self._run(root / "crm.json", "crm", root / "crm.db", "daily-pass")
            self.assertEqual(crm_out, file_out)
            exported = json.loads((root / "crm.json").read_text(encoding="utf-8"))
            self.assertEqual(set(exported["leads"]), {row["lead_id"] for row in crm_out["created"] + crm_out["needs_info"]})

    def test_batches_skip_non_leads_and_match_ids(self) -> None:
        sys.path.insert(0, str(ROADSIDE))
        from adapters import FakeDrive, FakeOcr, RecordingBland  # noqa: WPS433
        from config import Config  # noqa: WPS433
        from crm_store import CrmStore  # noqa: WPS433
        from models import Lead  # noqa: WPS433
        from pipeline import Pipeline, make_pipeline  # noqa: WPS433

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sets = json.loads(FIXTURE.read_text(encoding="utf-8"))["sets"]
            drive = FakeDrive([dict(row) for row in sets])
            ocr = FakeOcr({k: v for row in sets for k, v in (row.get("ocr") or {}).items()})
            file_cfg = Config(
                dry_run=True,
                store_path=root / "file.json",
                backend="file",
                now=lambda: NOW,
            )
            file_pipe = make_pipeline(file_cfg, drive=drive, ocr=ocr, bland=RecordingBland())
            file_pipe.daily_pass()
            file_sms = [row["lead_id"] for row in file_pipe.sms_batch()]

            drive2 = FakeDrive([dict(row) for row in sets])
            crm_cfg = Config(
                dry_run=True,
                store_path=root / "crm.json",
                backend="crm",
                crm_db_path=root / "crm.db",
                now=lambda: NOW,
            )
            crm_pipe = make_pipeline(crm_cfg, drive=drive2, ocr=ocr, bland=RecordingBland())
            crm_pipe.daily_pass()
            store = crm_pipe.store
            self.assertIsInstance(store, CrmStore)
            actor = store.db.actor_for_seat("chris")
            phones = {
                "vendor": "2395550181",
                "supplier": "2395550182",
                "customer": "2395550183",
                "partner": "2395550184",
                "other": "2395550185",
            }
            for party_type, phone in phones.items():
                store.db.create_party(
                    actor,
                    {
                        "type": party_type,
                        "display_name": party_type,
                        "status": "new",
                        "phone": phone,
                        "source": "manual",
                    },
                )
            sms = crm_pipe.sms_batch()
            calls = crm_pipe.call_batch()
            selected = {row["lead_id"] for row in sms + calls}
            for party_type in ("vendor", "supplier", "customer", "partner", "other"):
                self.assertNotIn(party_type, selected)
            non_leads = [
                row
                for row in store.db.list_parties()
                if row["type"] != "lead"
            ]
            for party in non_leads:
                self.assertEqual(store.db.count_interactions(party["id"]), 0)
                self.assertNotIn(party["id"], store.db.outreach_eligible_ids())
            self.assertEqual([row["lead_id"] for row in sms], file_sms)
            again = crm_pipe.daily_pass()
            created_ids = {row["lead_id"] for row in again["created"]}
            for party in non_leads:
                self.assertNotIn(party["id"], created_ids)

    def test_sms_batch_skips_landline_and_call_batch_does_not(self) -> None:
        sys.path.insert(0, str(ROADSIDE))
        from config import Config  # noqa: WPS433
        from models import Lead  # noqa: WPS433
        from pipeline import make_pipeline  # noqa: WPS433

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sent_at = (NOW - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
            cfg = Config(
                dry_run=True,
                store_path=root / "store.json",
                backend="crm",
                crm_db_path=root / "crm.db",
                now=lambda: NOW,
            )
            pipe = make_pipeline(cfg, drive=None, ocr=None)
            pipe.store.put(
                Lead(id="lead-linda", folder_id="f-linda", phone="2395550144", state="new", contact_name="Linda")
            )
            pipe.store.put(
                Lead(
                    id="lead-voice",
                    folder_id="f-voice",
                    phone="2395550145",
                    state="sms_sent",
                    contact_name="Voice",
                    sms_sent_at=sent_at,
                    sms_variant_id="A",
                )
            )
            actor = pipe.store.db.actor_for_seat("buzz")
            for lead_id, msg_id in (("lead-linda", "land-linda"), ("lead-voice", "land-voice")):
                created = pipe.store.db.add_interaction(
                    actor,
                    {
                        "party_id": lead_id,
                        "channel": "sms",
                        "direction": "out",
                        "provider_msg_id": msg_id,
                        "outcome": "undelivered",
                        "error_code": "30006",
                        "occurred_at": sent_at,
                    },
                )
                self.assertTrue(created[1])
            sms_ids = {row["lead_id"] for row in pipe.sms_batch()}
            self.assertNotIn("lead-linda", sms_ids)
            self.assertEqual(pipe.store.db.count_interactions("lead-linda"), 1)
            call_ids = {row["lead_id"] for row in pipe.call_batch() if not row.get("skipped")}
            self.assertIn("lead-voice", call_ids)

    def test_duplicate_skip_and_export_and_rollback(self) -> None:
        sys.path.insert(0, str(ROADSIDE))
        from adapters import FakeDrive, FakeOcr  # noqa: WPS433
        from config import Config  # noqa: WPS433
        from models import Lead  # noqa: WPS433
        from pipeline import make_pipeline  # noqa: WPS433

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "crm.db"
            export_path = root / "store.json"
            cfg = Config(
                dry_run=True,
                store_path=export_path,
                backend="crm",
                crm_db_path=db_path,
                now=lambda: NOW,
            )
            drive = FakeDrive(
                [
                    {
                        "id": "set-dup",
                        "name": "2026-09-10-dup",
                        "photos": [{"id": "p", "name": "sign.jpg"}],
                        "ocr": {"p": "FOR SALE Call 239-555-0101"},
                    }
                ]
            )
            ocr = FakeOcr({"p": "FOR SALE Call 239-555-0101"})
            pipe = make_pipeline(cfg, drive=drive, ocr=ocr)
            pipe.store.put(
                Lead(id="lead-kevin", folder_id="already", phone="2395550101", state="new", contact_name="Kevin")
            )
            result = pipe.daily_pass()
            self.assertEqual(result["created"], [])
            skipped = [row for row in result["skipped"] if row["reason"] == "duplicate-phone"]
            self.assertEqual(len(skipped), 1)
            stored = pipe.store.get("lead-kevin")
            self.assertIsNotNone(stored)
            self.assertTrue(any(event.get("op") == "duplicate_skip" for event in stored.events))  # type: ignore[union-attr]

            exported = json.loads(export_path.read_text(encoding="utf-8"))
            self.assertIn("lead-kevin", exported["leads"])
            pipe.store.put(stored)  # type: ignore[arg-type]
            again = json.loads(export_path.read_text(encoding="utf-8"))
            self.assertEqual(set(again["leads"]), set(exported["leads"]))

            backup = backup_file(export_path)
            self.assertIsNotNone(backup)
            snapshot = backup.read_bytes() if backup else b""
            stored.state = "sms_sent"  # type: ignore[union-attr]
            pipe.store.put(stored)  # type: ignore[arg-type]
            self.assertEqual(backup.read_bytes() if backup else b"", snapshot)

            file_cfg = Config(dry_run=True, store_path=backup, backend="file", now=lambda: NOW)
            file_pipe = make_pipeline(file_cfg, drive=FakeDrive([]), ocr=FakeOcr({}))
            restored = file_pipe.store.get("lead-kevin")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.state, "new")  # type: ignore[union-attr]
            self.assertTrue((root / "store.json").is_file())


class DispatchCase(unittest.TestCase):
    def test_existing_json_imports_before_export_is_armed(self) -> None:
        sys.path.insert(0, str(ROADSIDE))
        from crm_store import CrmStore  # noqa: WPS433
        from models import Lead  # noqa: WPS433
        from store import FileStore  # noqa: WPS433

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "store.json"
            file_store = FileStore(path)
            file_store.put(Lead(id="lead-kept", folder_id="folder-kept", phone="2395550191", state="new"))
            original = path.read_bytes()
            store = CrmStore(root / "crm.db", path)
            kept = store.get("lead-kept")
            self.assertIsNotNone(kept)
            self.assertEqual(kept.phone, "2395550191")  # type: ignore[union-attr]
            self.assertEqual(path.read_bytes(), original)
            store.put(kept)  # type: ignore[arg-type]
            exported = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("lead-kept", exported["leads"])



    def test_audit_method_not_allowed_without_a_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CrmDB(Path(tmp) / "crm.db")
            status, body = dispatch(db, "DELETE", "/v1/audit", {}, b"")
            self.assertEqual(status, 405, body)
            db.close()


if __name__ == "__main__":
    unittest.main()
