"""#1097 slice 1: GET /v1/contacts/by-phone. Fixture DB only: no network, no prod token, no sends."""

from __future__ import annotations

import json
import re
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.crm.api import dispatch, serve  # noqa: E402
from workflows.crm.contacts import to_et  # noqa: E402
from workflows.crm.core import CrmDB  # noqa: E402
from workflows.crm.ids import ulid  # noqa: E402
from workflows.crm.pii import scrub  # noqa: E402

PN = "+12395550101"
PII_RE = [
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),  # card
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s+){1,3}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Dr|Drive|Ln|Lane)\b"),
    re.compile(r"\b(?:lockbox|gate code|access code|pin)\b\D{0,6}\d", re.IGNORECASE),
    re.compile(r"\b(?=[A-Z0-9]{5,8}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]+\b"),  # plate
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
]


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


class ContactsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CrmDB(Path(self.tmp.name) / "crm.db")
        self.grok_actor = self.db.actor_for_seat("grok")
        self.grok = self.db.issue_token("grok")["token"]
        self.cc = self.db.issue_token("caller-context")["token"]
        self.alex = self.db.issue_token("alexandra")["token"]

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def get(self, pn: str, token: str, extra: str = "") -> tuple[int, dict]:
        from urllib.parse import quote

        return dispatch(self.db, "GET", f"/v1/contacts/by-phone?pn={quote(pn)}{extra}", _hdr(token), b"")

    def seed(self, phone: str = PN, tags: list[str] | None = None, identities: list[dict] | None = None) -> str:
        doc, _ = self.db.create_party(
            self.grok_actor,
            {
                "type": "customer",
                "display_name": "Sam Rivera",
                "status": "new",
                "phone": phone,
                "email": "sam@example.com",
                "tags": tags or [],
                "identities": identities or [],
            },
        )
        return doc["id"]


class LookupTests(ContactsCase):
    def test_e164_variants_match(self) -> None:
        self.seed()
        for raw in ("(239) 555-0101", "239-555-0101", "+12395550101", "12395550101", "239.555.0101"):
            status, body = self.get(raw, self.grok)
            self.assertEqual(status, 200, raw)
            self.assertEqual(body["party"]["display_name"], "Sam Rivera")

    def test_unknown_number_404(self) -> None:
        self.seed()
        status, body = self.get("+12075550199", self.grok)
        self.assertEqual((status, body), (404, {"error": "not found"}))

    def test_invalid_phone_400(self) -> None:
        self.assertEqual(self.get("12345", self.grok)[0], 400)
        self.assertEqual(self.get("", self.grok)[0], 400)

    def test_auth(self) -> None:
        self.seed()
        self.assertEqual(self.get(PN, "")[0], 401)
        self.assertEqual(self.get(PN, "crm_wrong")[0], 401)
        # alexandra holds read:party only, not the caller-context scope
        self.assertEqual(self.get(PN, self.alex)[0], 403)

    def test_caller_context_scope_is_narrow(self) -> None:
        pid = self.seed()
        status, body = self.get(PN, self.cc)
        self.assertEqual(status, 200)
        self.assertEqual(body["identities"], [{"kind": "email"}, {"kind": "phone"}])
        # cannot read or write anything else
        for method, path, payload in (
            ("GET", f"/v1/parties/{pid}", b""),
            ("GET", f"/v1/parties/{pid}/timeline", b""),
            ("GET", "/v1/parties/by-identity?phone=%2B12395550101", b""),
            ("GET", "/v1/search?q=Sam", b""),
            ("GET", "/v1/tasks", b""),
            ("POST", "/v1/interactions", json.dumps({"party_id": pid, "summary": "x"}).encode()),
            ("POST", "/v1/tasks", json.dumps({"party_id": pid, "title": "x"}).encode()),
            ("PATCH", f"/v1/parties/{pid}", json.dumps({"status": "x"}).encode()),
            ("DELETE", f"/v1/parties/{pid}", b""),
        ):
            status, _ = dispatch(self.db, method, path, _hdr(self.cc), payload)
            self.assertEqual(status, 403, (method, path))

    def test_full_scope_sees_identity_values(self) -> None:
        self.seed()
        _, body = self.get(PN, self.grok)
        self.assertIn({"kind": "phone", "value": PN}, body["identities"])

    def test_shape_followups_and_last_n_in_et(self) -> None:
        pid = self.seed()
        base = datetime(2026, 10, 9, 14, 20, tzinfo=timezone.utc)  # 10:20 AM EDT
        for i in range(8):
            self.db.add_interaction(
                self.grok_actor,
                {
                    "party_id": pid,
                    "channel": "call",
                    "direction": "out",
                    "provider": "bland",
                    "provider_msg_id": f"call:fx-{i}",
                    "summary": f"call {i}",
                    "outcome": "voicemail",
                    "body": "SECRET BODY TEXT",
                    "occurred_at": (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        self.db.add_task(self.grok_actor, {"party_id": pid, "title": "Chris to call back about storage", "due_at": "2026-10-10T13:00:00Z"})
        done = self.db.add_task(self.grok_actor, {"party_id": pid, "title": "old", "due_at": "2026-10-01T13:00:00Z"})
        self.db.update_task(self.grok_actor, done["id"], {"status": "done"})
        status, body = self.get(PN, self.cc, "&limit=3")
        self.assertEqual(status, 200)
        self.assertEqual(
            set(body),
            {"party", "type", "tags", "identities", "turo_booking", "open_followups", "followups", "interactions", "tz"},
        )
        self.assertEqual([i["summary"] for i in body["interactions"]], ["call 7", "call 6", "call 5"])
        self.assertEqual(body["interactions"][-1]["occurred_at"], "2026-10-09T15:20:00-04:00")
        self.assertEqual(body["interactions"][-1]["occurred_text"], "Fri Oct 9 3:20 PM ET")
        self.assertEqual(body["open_followups"], ["Chris to call back about storage"])
        self.assertEqual(body["followups"][0]["due_at"], "2026-10-10T09:00:00-04:00")
        self.assertNotIn("SECRET BODY TEXT", json.dumps(body))
        self.assertFalse(body["turo_booking"])
        self.assertEqual(len(self.get(PN, self.cc)[1]["interactions"]), 5)  # default N
        self.assertEqual(len(self.get(PN, self.cc, "&limit=999")[1]["interactions"]), 8)
        self.assertEqual(self.get(PN, self.cc, "&limit=x")[0], 400)

    def test_et_handles_dst(self) -> None:
        self.assertEqual(to_et("2026-11-02T15:00:00Z")[0], "2026-11-02T10:00:00-05:00")
        self.assertEqual(to_et("2026-07-02T15:00:00+00:00")[0], "2026-07-02T11:00:00-04:00")
        self.assertEqual(to_et("garbage"), ("", ""))


class TuroFlagTests(ContactsCase):
    def test_tag(self) -> None:
        self.seed(tags=["turo-guest"])
        self.assertTrue(self.get(PN, self.cc)[1]["turo_booking"])

    def test_turo_guest_identity(self) -> None:
        self.seed(identities=[{"kind": "turo_guest_id", "value": "g-12345"}])
        _, body = self.get(PN, self.cc)
        self.assertTrue(body["turo_booking"])
        self.assertIn({"kind": "turo_guest_id"}, body["identities"])

    def test_ext_flag(self) -> None:
        pid = self.seed()
        self.db.conn.execute("UPDATE party SET ext_json = ? WHERE id = ?", (json.dumps({"turo_booking": True}), pid))
        self.assertTrue(self.get(PN, self.cc)[1]["turo_booking"])

    def test_bland_relay_view_contract(self) -> None:
        """The doc must render through bland-relay's caller_context.view_from_crm (#1096)."""
        relay = ROOT / "deploy" / "bland-relay"
        if not (relay / "caller_context.py").exists():
            self.skipTest("bland-relay not in tree")
        sys.path.insert(0, str(relay))
        try:
            import caller_context  # type: ignore
        finally:
            sys.path.remove(str(relay))
        pid = self.seed(tags=["turo-guest"])
        self.db.add_interaction(self.grok_actor, {"party_id": pid, "channel": "call", "direction": "out", "provider_msg_id": "call:v1", "summary": "storage lot", "occurred_at": "2026-10-09T14:20:00Z"})
        view = caller_context.view_from_crm(self.get(PN, self.cc)[1])
        self.assertTrue(view["turo_booking"])
        self.assertEqual(view["interactions"][0]["direction"], "out")


class PiiTests(ContactsCase):
    DIRTY = [
        "Card 4111 1111 1111 1111 exp soon",
        "SSN 123-45-6789 on file",
        "Lives at 1420 Ocean View Drive near the beach",
        "lockbox code 4417, gate code is 9921",
        "Plate 25EWUH, other plate ABC1234",
        "email sam@example.com or call 239-555-0101",
    ]

    def test_scrub_unit(self) -> None:
        for text in self.DIRTY:
            out = scrub(text)
            for pat in PII_RE:
                self.assertIsNone(pat.search(out), (text, out))
        self.assertEqual(scrub("Asked about storage lot lease"), "Asked about storage lot lease")

    def test_lookup_payload_clean(self) -> None:
        pid = self.seed()
        for i, text in enumerate(self.DIRTY):
            self.db.add_interaction(self.grok_actor, {"party_id": pid, "channel": "sms", "direction": "in", "provider_msg_id": f"sms:p{i}", "summary": text, "body": text, "occurred_at": f"2026-10-09T1{i}:00:00Z"})
        self.db.add_task(self.grok_actor, {"party_id": pid, "title": "Call back re plate 25EWUH and lockbox 4417"})
        _, body = self.get(PN, self.cc, "&limit=20")
        # Free-text fields only; ids and ISO timestamps are structural.
        texts = [i["summary"] for i in body["interactions"]] + body["open_followups"]
        texts += [f["title"] for f in body["followups"]] + [body["party"]["display_name"]]
        self.assertEqual(len(body["interactions"]), len(self.DIRTY))
        for text in texts:
            for pat in PII_RE:
                self.assertIsNone(pat.search(text), (pat.pattern, text))
        self.assertNotIn("4111", json.dumps(body))
        self.assertNotIn("25EWUH", json.dumps(body))


class PerfTests(unittest.TestCase):
    def test_p95_under_100ms_10k_rows(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = CrmDB(Path(tmp.name) / "crm.db")
        self.addCleanup(db.close)
        grok = db.actor_for_seat("grok")
        token = db.issue_token("caller-context")["token"]
        phones = []
        for i in range(200):
            phone = f"+1239555{i:04d}"
            phones.append(phone)
            db.create_party(grok, {"type": "lead", "display_name": f"P{i}", "phone": phone})
        party_ids = [r["id"] for r in db.conn.execute("SELECT party_id AS id FROM identity WHERE kind='phone' ORDER BY value_norm")]
        rows = []
        base = datetime(2026, 4, 1, tzinfo=timezone.utc)
        for n in range(10_000):
            pid = party_ids[n % len(party_ids)]
            rows.append((ulid(), pid, "call", "out", "bland", f"call:perf-{n}", "", "", "", f"summary {n}", "", "voicemail", "", "", "", (base + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:%SZ"), "grok"))
        db.conn.execute("BEGIN")
        db.conn.executemany("INSERT INTO interaction VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.conn.execute("COMMIT")
        self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM interaction").fetchone()[0], 10_000)
        httpd = serve(db, "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        times = []
        for i in range(300):
            pn = phones[i % len(phones)].replace("+", "%2B")
            req = urllib.request.Request(f"{base_url}/v1/contacts/by-phone?pn={pn}", headers={"Authorization": f"Bearer {token}"})
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                json.loads(resp.read())
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p95 = times[int(len(times) * 0.95) - 1]
        print(f"\nby-phone over HTTP, 10k interactions / 200 parties: p50={times[len(times)//2]:.2f}ms p95={p95:.2f}ms max={times[-1]:.2f}ms", file=sys.stderr)
        self.assertLess(p95, 100.0)


if __name__ == "__main__":
    unittest.main()
