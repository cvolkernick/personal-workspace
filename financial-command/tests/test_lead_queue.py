"""FCC marketplace lead queue: nav reachability and status actions (#876)."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FCC = ROOT / "financial-command"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_leads", FCC / "server.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class TestLeadQueueSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["MARKETPLACE_LEAD_STORE"] = str(Path(cls._tmp.name) / "store.json")
        cls.mod = _load_fcc_server()
        cls.port = _free_port()
        cls.httpd = cls.mod.ThreadingHTTPServer(("127.0.0.1", cls.port), cls.mod.FCCHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()
        os.environ.pop("MARKETPLACE_LEAD_STORE", None)

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str) -> tuple[int, str]:
        with urllib.request.urlopen(self._url(path), timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_index_links_the_queue_on_desktop_and_phone_paths(self) -> None:
        html = (FCC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="nav-lead-queue"', html)
        self.assertIn('href="lead-queue.html"', html)
        self.assertIn('id="link-lead-queue"', html)
        self.assertIn('id="lead-queue-card"', html)
        self.assertIn('id="link-lead-queue-full"', html)
        self.assertIn("#nav-lead-queue", html)
        self.assertNotRegex(html, r"#link-lead-queue\s*[,\{]")
        self.assertNotRegex(html, r"#link-lead-queue-full\s*[,\{]")
        self.assertNotRegex(html, r"#lead-queue-card\s*[,\{]")

    def test_page_and_actions_round_trip(self) -> None:
        from workflows.marketplace_leads.ingest import ingest
        from workflows.marketplace_leads.store import open_store

        store = open_store()
        lead = ingest(
            store,
            {
                "listing_id": "4242424242",
                "year": "2021",
                "make": "Toyota",
                "model": "Corolla",
                "price": "$8500",
                "mileage": "92000",
                "location": "Cape Coral, FL",
                "listing_date": "2026-09-20",
                "reason_flagged": "no phone on listing",
            },
        ).lead
        status, page = self._get("/lead-queue.html")
        self.assertEqual(status, 200)
        self.assertIn("Open Marketplace listing", page)
        self.assertNotIn("messenger.com", page)
        self.assertNotIn("graph.facebook", page)
        status, body = self._get("/api/marketplace-leads")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["outreach"], "manual")
        self.assertEqual(len(data["leads"]), 1)
        self.assertEqual(data["leads"][0]["listing_id"], "4242424242")
        self.assertTrue(data["leads"][0]["photo_unavailable"])
        self.assertIn("/marketplace/item/4242424242/", data["leads"][0]["listing_url"])

        code, denied = self._post(
            "/api/marketplace-leads/action",
            {"id": lead.id, "action": "messenger"},
        )
        self.assertEqual(code, 400)
        self.assertFalse(denied["ok"])

        code, done = self._post(
            "/api/marketplace-leads/action",
            {"id": lead.id, "action": "contacted"},
        )
        self.assertEqual(code, 200)
        self.assertTrue(done["ok"])
        self.assertEqual(done["lead"]["status"], "contacted")
        status, body = self._get("/api/marketplace-leads")
        self.assertEqual(json.loads(body)["leads"], [])


