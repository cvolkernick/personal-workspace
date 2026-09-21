"""CRM, morning-brief Roadside section, dedup, and manual actions (#876)."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.marketplace_leads.actions import ActionError, apply_action  # noqa: E402
from workflows.marketplace_leads.api import action_payload, public_lead, queue_payload  # noqa: E402
from workflows.marketplace_leads.brief import render_morning_brief  # noqa: E402
from workflows.marketplace_leads.ingest import ingest  # noqa: E402
from workflows.marketplace_leads.models import canonical_listing_url  # noqa: E402
from workflows.marketplace_leads.store import FileStore  # noqa: E402

COROLLA = {
    "listing_id": "100200300400",
    "year": "2021",
    "make": "Toyota",
    "model": "Corolla",
    "price": "$8500",
    "mileage": "92000",
    "location": "Cape Coral, FL",
    "listing_date": "2026-09-20",
    "listing_url": "https://www.facebook.com/marketplace/item/100200300400/old-slug/",
    "photo_url": "https://example.test/corolla.jpg",
    "seller_name": "Jane",
    "seller_rating": "4.8",
    "reason_flagged": "no phone on listing",
    "phone": "",
}

RDX = {
    "listing_id": "555666777888",
    "year": "2021",
    "make": "Acura",
    "model": "RDX",
    "price": "$18900",
    "mileage": "61000",
    "location": "Estero, FL",
    "listing_date": "2026-09-21",
    "reason_flagged": "solid no-phone lead",
    "photo_url": "",
    "seller_name": "",
    "seller_rating": "",
}


class LeadTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.store = FileStore(Path(self._tmp.name) / "store.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_phone_lead_is_on_the_morning_brief(self) -> None:
        result = ingest(self.store, COROLLA)
        self.assertTrue(result.created)
        self.assertEqual(result.lead.status, "surface_to_chairman")
        self.assertFalse(result.lead.phone_present)
        brief = render_morning_brief(self.store.all_leads())
        self.assertIn("## Roadside", brief)
        self.assertIn("2021 Toyota Corolla", brief)
        self.assertIn("https://example.test/corolla.jpg", brief)
        self.assertIn("$8500", brief)
        self.assertIn("92000", brief)
        self.assertIn("Cape Coral, FL", brief)
        self.assertIn("2026-09-20", brief)
        self.assertIn(canonical_listing_url(COROLLA["listing_id"]), brief)
        self.assertNotIn("old-slug", brief)
        self.assertIn("no phone on listing", brief)
        self.assertIn("Jane (4.8)", brief)
        self.assertIn("message the seller manually", brief)

    def test_missing_photo_and_seller_still_render(self) -> None:
        ingest(self.store, RDX)
        brief = render_morning_brief(self.store.all_leads())
        self.assertIn("2021 Acura RDX", brief)
        self.assertIn("- Photo: unavailable", brief)
        self.assertIn("- Seller: unavailable", brief)
        self.assertIn(canonical_listing_url(RDX["listing_id"]), brief)
        row = public_lead(self.store.open_queue()[0])
        self.assertTrue(row["photo_unavailable"])
        self.assertTrue(row["seller_unavailable"])
        self.assertEqual(row["photo_url"], "")

    def test_dedup_listing_id_and_phone_does_not_requeue(self) -> None:
        first = ingest(self.store, COROLLA)
        apply_action(self.store, first.lead.id, "contacted")
        again = ingest(self.store, dict(COROLLA, price="$1"))
        self.assertTrue(again.duplicate)
        self.assertFalse(again.created)
        self.assertEqual(again.lead.id, first.lead.id)
        self.assertEqual(again.lead.status, "contacted")
        self.assertEqual(again.lead.price, "$8500")
        self.assertEqual(self.store.open_queue(), [])
        brief = render_morning_brief(self.store.all_leads())
        self.assertIn("No Chairman marketplace leads.", brief)
        self.assertNotIn("Corolla", brief)

        phone_variant = dict(COROLLA, phone="239-555-0101")
        phone_hit = ingest(self.store, phone_variant)
        self.assertTrue(phone_hit.duplicate)
        self.assertEqual(phone_hit.lead.status, "contacted")

    def test_phone_lead_is_sms_path_and_absent_from_the_queue(self) -> None:
        result = ingest(self.store, dict(COROLLA, listing_id="999888777666", phone="(239) 555-0199"))
        self.assertEqual(result.lead.status, "sms_queued")
        self.assertTrue(result.lead.phone_present)
        self.assertEqual(result.lead.phone, "2395550199")
        self.assertEqual(queue_payload(self.store)["leads"], [])
        brief = render_morning_brief(self.store.all_leads())
        self.assertNotIn("Corolla", brief)

    def test_chairman_actions_leave_the_queue(self) -> None:
        corolla = ingest(self.store, COROLLA).lead
        rdx = ingest(self.store, RDX).lead
        apply_action(self.store, corolla.id, "disqualified")
        apply_action(self.store, rdx.id, "converted")
        self.assertEqual(self.store.get(corolla.id).status, "disqualified")
        self.assertEqual(self.store.get(rdx.id).status, "converted")
        self.assertEqual(self.store.open_queue(), [])

    def test_sms_eligible_leaves_the_queue_without_sending(self) -> None:
        lead = ingest(self.store, COROLLA).lead
        updated = apply_action(self.store, lead.id, "sms_eligible")
        self.assertEqual(updated.status, "sms_queued")
        self.assertEqual(self.store.open_queue(), [])

    def test_send_actions_are_refused(self) -> None:
        lead = ingest(self.store, COROLLA).lead
        for name in ("send", "message", "messenger", "sms"):
            with self.assertRaises(ActionError):
                apply_action(self.store, lead.id, name)
            payload = action_payload(self.store, lead.id, name)
            self.assertFalse(payload["ok"])
        self.assertEqual(self.store.get(lead.id).status, "surface_to_chairman")
        body = json.dumps(queue_payload(self.store))
        self.assertNotIn("graph.facebook", body)
        self.assertNotIn("messenger.com", body)

    def test_modules_do_not_call_the_network(self) -> None:
        pkg = ROOT / "workflows" / "marketplace_leads"
        banned = ("urllib", "requests", "httpx", "graph.facebook", "messenger.com/t/")
        for path in pkg.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in banned:
                self.assertNotIn(needle, text, f"{path.name} contains {needle}")

    def test_cli_brief_reads_the_store(self) -> None:
        ingest(self.store, RDX)
        env = dict(**{k: v for k, v in __import__("os").environ.items()})
        env["MARKETPLACE_LEAD_STORE"] = str(self.store.path)
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "workflows.marketplace_leads.run", "brief"],
            cwd=str(ROOT),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("2021 Acura RDX", proc.stdout)
        self.assertIn("- Photo: unavailable", proc.stdout)


if __name__ == "__main__":
    unittest.main()
