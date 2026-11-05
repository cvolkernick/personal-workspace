"""S1: contact_type, controlled source tags, renter status. Fixture DB only.

No network, no secrets, no real contact data. Phone numbers are not used.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.crm import SCHEMA_VERSION  # noqa: E402
from workflows.crm.api import dispatch  # noqa: E402
from workflows.crm.auth import CrmError  # noqa: E402
from workflows.crm.cli import main  # noqa: E402
from workflows.crm.core import CrmDB  # noqa: E402
from workflows.crm.schema import CONTACT_TYPES, DEAL_TAGS, SOURCE_TAGS  # noqa: E402

ATLAS = "ref:atlasgo"
GUILLERMO = "ref:guillermo-group"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class S1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CrmDB(Path(self.tmp.name) / "crm.db")
        self.actor = self.db.actor_for_seat("grok")
        self.token = self.db.issue_token("grok")["token"]
        self.caller = self.db.issue_token("caller-context")["token"]

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def add(self, **payload: object) -> dict:
        doc, created = self.db.create_party(self.actor, payload)
        self.assertTrue(created)
        return doc

    def search_cli(self, *args: str) -> str:
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(["--db", str(self.db.path), "--api", "", "search", *args])
        self.assertEqual(code, 0, buf.getvalue())
        return buf.getvalue()

    def test_contact_type_column_and_check(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 2)
        version = self.db.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        self.assertEqual(version["value"], "2")
        indexes = {row[1] for row in self.db.conn.execute("PRAGMA index_list(party)")}
        self.assertIn("idx_party_contact_type", indexes)
        triggers = {
            row[0]
            for row in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        self.assertIn("party_renter_status_insert", triggers)
        self.assertIn("party_renter_status_update", triggers)

        legacy_path = Path(self.tmp.name) / "legacy.db"
        raw = sqlite3.connect(legacy_path)
        raw.executescript(
            """
            CREATE TABLE party (
              id TEXT PRIMARY KEY,
              type TEXT NOT NULL CHECK (type IN ('lead','vendor','supplier','customer','partner','other')),
              display_name TEXT NOT NULL DEFAULT '',
              org_name TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              owner_seat TEXT NOT NULL DEFAULT '',
              location TEXT NOT NULL DEFAULT '',
              ext_json TEXT NOT NULL DEFAULT '{}',
              archived INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              created_by TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              updated_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE schema_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        raw.execute(
            """
            INSERT INTO party(
              id, type, display_name, org_name, status, source, owner_seat, location,
              ext_json, archived, created_at, created_by, updated_at, updated_by
            ) VALUES ('legacy-1', 'lead', 'Legacy Lead', '', 'sms_sent', 'roadside', '', '', '{}', 0,
                      '2026-01-01T00:00:00Z', '', '2026-01-01T00:00:00Z', '')
            """
        )
        raw.execute("INSERT INTO schema_meta(key, value) VALUES ('version', '1')")
        raw.commit()
        raw.close()
        legacy = CrmDB(legacy_path)
        try:
            bumped = legacy.conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'version'"
            ).fetchone()
            self.assertEqual(bumped["value"], "2")
            row = legacy.conn.execute(
                """
                SELECT type, status, source, display_name, contact_type, updated_at
                FROM party WHERE id = 'legacy-1'
                """
            ).fetchone()
            self.assertIsNone(row["contact_type"])
            self.assertEqual(row["type"], "lead")
            self.assertEqual(row["status"], "sms_sent")
            self.assertEqual(row["source"], "roadside")
            self.assertEqual(row["display_name"], "Legacy Lead")
            self.assertEqual(row["updated_at"], "2026-01-01T00:00:00Z")
            kept = legacy.update_party(
                legacy.actor_for_seat("grok"), "legacy-1", {"contact_type": "owner"}
            )
            self.assertEqual(kept["contact_type"], "owner")
            self.assertEqual(kept["type"], "lead")
            self.assertEqual(kept["status"], "sms_sent")
        finally:
            legacy.close()

        plain = self.add(type="lead", display_name="Plain Lead", status="new")
        self.assertIsNone(plain["contact_type"])
        self.assertEqual(plain["type"], "lead")
        for party_type in ("lead", "vendor", "supplier", "customer", "partner", "other"):
            made = self.add(type=party_type, display_name=f"Type {party_type}")
            self.assertEqual(made["type"], party_type)
            self.assertIsNone(made["contact_type"])
        with self.assertRaises(CrmError) as bad_type:
            self.add(type="renter", display_name="Not A Party Type")
        self.assertEqual(bad_type.exception.status, 400)
        self.assertEqual(bad_type.exception.message, "bad type")

        renter = self.add(
            type="customer", display_name="Renter One", contact_type="renter", status="new"
        )
        owner = self.add(
            type="lead", display_name="Owner One", contact_type="owner", status="new"
        )
        partner = self.add(
            type="partner", display_name="Partner One", contact_type="partner", status="new"
        )
        self.assertEqual([renter["contact_type"], owner["contact_type"], partner["contact_type"]], list(CONTACT_TYPES))
        for bad in ("guest", "lead", "vendor", "", "RENTER"):
            with self.assertRaises(CrmError) as caught:
                self.add(type="customer", display_name="Bad", contact_type=bad)
            self.assertEqual(caught.exception.status, 400, bad)
            self.assertEqual(caught.exception.message, "bad contact_type", bad)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute(
                "UPDATE party SET contact_type = 'guest' WHERE id = ?", (renter["id"],)
            )
        self.db.conn.execute(
            "UPDATE party SET contact_type = NULL WHERE id = ?", (plain["id"],)
        )
        self.assertIsNone(self.db.get_party(self.actor, plain["id"])["contact_type"])

        code, body = dispatch(
            self.db,
            "GET",
            "/v1/search?contact_type=renter",
            _hdr(self.token),
            b"",
        )
        self.assertEqual(code, 200, body)
        self.assertEqual([row["id"] for row in body["results"]], [renter["id"]])
        denied, err = dispatch(
            self.db, "GET", "/v1/search?contact_type=guest", _hdr(self.token), b""
        )
        self.assertEqual((denied, err["error"]), (400, "bad contact_type"))
        listed = self.search_cli("--contact-type", "owner")
        self.assertIn("[LEAD] Owner One", listed)
        self.assertNotIn("Renter One", listed)
        self.assertNotIn("Partner One", listed)

    def test_deal_tags_mutually_exclusive(self) -> None:
        triggers = {
            row[0]
            for row in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        self.assertIn("party_tag_deal_exclusive_insert", triggers)
        self.assertIn("party_tag_deal_exclusive_update", triggers)

        before = self.db.count_parties()
        with self.assertRaises(CrmError) as both:
            self.add(
                type="customer",
                display_name="Both Deals",
                contact_type="renter",
                tags=[ATLAS, GUILLERMO],
            )
        self.assertEqual(both.exception.status, 400)
        self.assertEqual(both.exception.message, "deal tags are mutually exclusive")
        self.assertEqual(self.db.count_parties(), before)

        with self.assertRaises(CrmError) as unknown:
            self.add(
                type="customer",
                display_name="Other Deal",
                contact_type="renter",
                tags=["ref:other"],
            )
        self.assertEqual(unknown.exception.message, "unknown deal tag")

        shop = self.add(type="vendor", display_name="Shop", tags=["tire-shop"])
        self.assertEqual(shop["tags"], ["tire-shop"])

        atlas = self.add(
            type="customer",
            display_name="Deal Atlas",
            contact_type="renter",
            status="new",
            tags=["fb-marketplace", "referral", ATLAS],
        )
        self.assertEqual(atlas["tags"], ["fb-marketplace", ATLAS, "referral"])
        with self.assertRaises(CrmError) as stuck:
            self.db.update_party(
                self.actor, atlas["id"], {"tags": [ATLAS, GUILLERMO, "referral"]}
            )
        self.assertEqual(stuck.exception.message, "deal tags are mutually exclusive")
        self.assertEqual(
            self.db.get_party(self.actor, atlas["id"])["tags"],
            ["fb-marketplace", ATLAS, "referral"],
        )

        guillermo = self.add(
            type="customer",
            display_name="Deal Guillermo",
            contact_type="renter",
            status="contacted",
            tags=[GUILLERMO, "site-form"],
        )
        switched = self.add(
            type="customer",
            display_name="Switch Deal",
            contact_type="owner",
            tags=[ATLAS],
        )
        moved = self.db.update_party(self.actor, switched["id"], {"tags": [GUILLERMO]})
        self.assertEqual(moved["tags"], [GUILLERMO])

        bare = self.add(
            type="customer",
            display_name="Raw Tags",
            contact_type="renter",
            tags=["sms-904"],
        )
        self.db.conn.execute("BEGIN")
        try:
            self.db.conn.execute(
                "INSERT INTO party_tag(party_id, tag) VALUES (?, ?)",
                (bare["id"], ATLAS),
            )
            with self.assertRaises(sqlite3.IntegrityError) as raw:
                self.db.conn.execute(
                    "INSERT INTO party_tag(party_id, tag) VALUES (?, ?)",
                    (bare["id"], GUILLERMO),
                )
            self.assertIn("mutually exclusive", str(raw.exception).lower())
        finally:
            self.db.conn.execute("ROLLBACK")
        self.assertEqual(self.db.get_party(self.actor, bare["id"])["tags"], ["sms-904"])
        self.assertEqual(self.db.get_party(self.actor, switched["id"])["tags"], [GUILLERMO])

        left = self.add(
            type="customer", display_name="Merge Left", contact_type="renter", tags=[ATLAS]
        )
        right = self.add(
            type="customer",
            display_name="Merge Right",
            contact_type="renter",
            tags=[GUILLERMO],
        )
        with self.assertRaises(CrmError) as merged:
            self.db.merge_parties(self.actor, left["id"], right["id"])
        self.assertEqual(merged.exception.message, "deal tags are mutually exclusive")
        self.assertFalse(self.db.get_party(self.actor, right["id"])["archived"])
        self.assertEqual(self.db.get_party(self.actor, left["id"])["tags"], [ATLAS])
        same = self.add(
            type="customer", display_name="Merge Same", contact_type="renter", tags=[ATLAS]
        )
        survivor = self.db.merge_parties(self.actor, left["id"], same["id"])
        self.assertEqual(survivor["tags"], [ATLAS])
        self.assertNotIn(GUILLERMO, survivor["tags"])

        code, body = dispatch(
            self.db,
            "GET",
            f"/v1/search?source={ATLAS}",
            _hdr(self.token),
            b"",
        )
        self.assertEqual(code, 200, body)
        atlas_ids = {row["id"] for row in body["results"]}
        self.assertIn(atlas["id"], atlas_ids)
        self.assertNotIn(guillermo["id"], atlas_ids)
        code, other = dispatch(
            self.db,
            "GET",
            f"/v1/search?source={GUILLERMO}",
            _hdr(self.token),
            b"",
        )
        self.assertEqual(code, 200, other)
        guillermo_ids = {row["id"] for row in other["results"]}
        self.assertIn(guillermo["id"], guillermo_ids)
        self.assertTrue(atlas_ids.isdisjoint(guillermo_ids))

        mixed, err = dispatch(
            self.db,
            "GET",
            f"/v1/search?tag={ATLAS}&source={GUILLERMO}",
            _hdr(self.token),
            b"",
        )
        self.assertEqual((mixed, err["error"]), (400, "deal tags cannot be combined"))
        comma, err = dispatch(
            self.db,
            "GET",
            f"/v1/reports/sources?source={ATLAS},{GUILLERMO}",
            _hdr(self.token),
            b"",
        )
        self.assertEqual((comma, err["error"]), (400, "deal tags cannot be combined"))
        unknown_source, err = dispatch(
            self.db, "GET", "/v1/search?source=tire-shop", _hdr(self.token), b""
        )
        self.assertEqual((unknown_source, err["error"]), (400, "unknown source tag"))
        legacy, legacy_body = dispatch(
            self.db, "GET", "/v1/search?tag=tire-shop", _hdr(self.token), b""
        )
        self.assertEqual(legacy, 200, legacy_body)
        self.assertEqual([row["id"] for row in legacy_body["results"]], [shop["id"]])

        self.add(
            type="customer",
            display_name="Other Sources",
            contact_type="renter",
            status="new",
            tags=["call-904", "turo", "wheelbase"],
        )
        report_code, report = dispatch(
            self.db, "GET", "/v1/reports/sources", _hdr(self.token), b""
        )
        self.assertEqual(report_code, 200, report)
        self.assertEqual(
            [row["tag"] for row in report["source_tags"]],
            [tag for tag in SOURCE_TAGS if tag not in DEAL_TAGS],
        )
        self.assertEqual(set(report["deals"]), set(DEAL_TAGS))
        self.assertNotIn("total", report)
        self.assertNotIn("combined", report)
        by_tag = {row["tag"]: row["count"] for row in report["source_tags"]}
        self.assertEqual(by_tag["fb-marketplace"], 1)
        self.assertEqual(by_tag["referral"], 1)
        self.assertEqual(by_tag["site-form"], 1)
        self.assertEqual(by_tag["sms-904"], 1)
        self.assertEqual(by_tag["call-904"], 1)
        self.assertEqual(by_tag["turo"], 1)
        self.assertEqual(by_tag["wheelbase"], 1)
        # Deal Atlas + Merge Left. Switch Deal, Deal Guillermo, Merge Right.
        self.assertEqual(report["deals"][ATLAS]["count"], 2)
        self.assertEqual(report["deals"][GUILLERMO]["count"], 3)
        self.assertNotIn(f"{ATLAS}+{GUILLERMO}", json.dumps(report))
        self.assertNotIn(f"{ATLAS},{GUILLERMO}", json.dumps(report))
        blocked, blocked_body = dispatch(
            self.db, "GET", "/v1/reports/sources", _hdr(self.caller), b""
        )
        self.assertEqual(blocked, 403, blocked_body)

        cli_out = self.search_cli("--source", "fb-marketplace", "--contact-type", "renter")
        self.assertIn("[CUSTOMER] Deal Atlas", cli_out)
        self.assertNotIn("Deal Guillermo", cli_out)
        err_buf = StringIO()
        with redirect_stderr(err_buf):
            bad = main(
                ["--db", str(self.db.path), "--api", "", "search", "--source", "tire-shop"]
            )
        self.assertEqual(bad, 1)
        self.assertIn("unknown source tag", err_buf.getvalue())
        self.assertNotIn("Traceback", err_buf.getvalue())

    def test_renter_status_transitions(self) -> None:
        renter = self.add(
            type="customer", display_name="Renter Path", contact_type="renter", status="new"
        )
        for status in ("contacted", "quoted", "booked", "completed"):
            renter = self.db.update_party(self.actor, renter["id"], {"status": status})
            self.assertEqual(renter["status"], status)
        with self.assertRaises(CrmError) as done:
            self.db.update_party(self.actor, renter["id"], {"status": "lost"})
        self.assertEqual(done.exception.status, 400)
        self.assertEqual(done.exception.message, "invalid renter status transition")

        lost = self.add(
            type="customer", display_name="Renter Lost", contact_type="renter", status="new"
        )
        for status in ("contacted", "quoted", "booked", "lost"):
            lost = self.db.update_party(self.actor, lost["id"], {"status": status})
        self.assertEqual(lost["status"], "lost")
        with self.assertRaises(CrmError) as back:
            self.db.update_party(self.actor, lost["id"], {"status": "booked"})
        self.assertEqual(back.exception.message, "invalid renter status transition")

        fresh = self.add(
            type="customer", display_name="Renter Fresh", contact_type="renter", status="new"
        )
        for jump in ("quoted", "booked", "completed", "lost", "sms_sent"):
            code, body = dispatch(
                self.db,
                "PATCH",
                f"/v1/parties/{fresh['id']}",
                _hdr(self.token),
                json.dumps({"status": jump}).encode(),
            )
            self.assertEqual(code, 400, (jump, body))
            if jump == "sms_sent":
                self.assertEqual(body["error"], "bad renter status")
            else:
                self.assertEqual(body["error"], "invalid renter status transition")
        self.assertEqual(self.db.get_party(self.actor, fresh["id"])["status"], "new")
        same = self.db.update_party(self.actor, fresh["id"], {"status": "new"})
        self.assertEqual(same["status"], "new")

        started = self.add(
            type="customer",
            display_name="Renter Quoted",
            contact_type="renter",
            status="quoted",
        )
        stepped = self.db.update_party(self.actor, started["id"], {"status": "booked"})
        self.assertEqual(stepped["status"], "booked")
        with self.assertRaises(CrmError):
            self.db.update_party(self.actor, started["id"], {"status": "contacted"})

        with self.assertRaises(CrmError) as created:
            self.add(
                type="customer",
                display_name="Bad Start",
                contact_type="renter",
                status="sms_sent",
            )
        self.assertEqual(created.exception.message, "bad renter status")

        roadside = self.add(type="lead", display_name="Still Lead", status="sms_sent")
        with self.assertRaises(CrmError) as adopt:
            self.db.update_party(self.actor, roadside["id"], {"contact_type": "renter"})
        self.assertEqual(adopt.exception.message, "bad renter status")
        adopted = self.db.update_party(
            self.actor, roadside["id"], {"contact_type": "renter", "status": "new"}
        )
        self.assertEqual(adopted["contact_type"], "renter")
        self.assertEqual(adopted["type"], "lead")
        self.assertEqual(adopted["status"], "new")

        owner = self.add(
            type="lead", display_name="Owner Funnel", contact_type="owner", status="new"
        )
        for status in ("sms_sent", "call_attempted", "responded", "interested", "converted"):
            owner = self.db.update_party(self.actor, owner["id"], {"status": status})
            self.assertEqual(owner["status"], status)
            self.assertEqual(owner["contact_type"], "owner")
        partner = self.add(
            type="partner",
            display_name="Partner Funnel",
            contact_type="partner",
            status="needs-info",
        )
        partner = self.db.update_party(self.actor, partner["id"], {"status": "declined"})
        self.assertEqual(partner["status"], "declined")
        untouched = self.add(type="lead", display_name="Null Type", status="new")
        moved = self.db.update_party(self.actor, untouched["id"], {"status": "sms_sent"})
        self.assertIsNone(moved["contact_type"])
        self.assertEqual(moved["status"], "sms_sent")

        quoted = self.add(
            type="customer",
            display_name="Quoted Filter",
            contact_type="renter",
            status="quoted",
            tags=["referral"],
        )
        code, hit = dispatch(
            self.db,
            "GET",
            "/v1/search?contact_type=renter&status=quoted&source=referral",
            _hdr(self.token),
            b"",
        )
        self.assertEqual(code, 200, hit)
        self.assertEqual([row["id"] for row in hit["results"]], [quoted["id"]])
        cli_out = self.search_cli("--contact-type", "renter", "--status", "quoted", "--source", "referral")
        self.assertIn("[CUSTOMER] Quoted Filter", cli_out)
        self.assertNotIn("Owner Funnel", cli_out)
        self.assertNotIn("Renter Path", cli_out)


if __name__ == "__main__":
    unittest.main()
