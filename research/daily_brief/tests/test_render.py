"""Daily Brief pages on the Horizon host: routes, sections, no FCC chrome, no FCC money."""

from __future__ import annotations

import json
import os
import re
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest import mock

from research.daily_brief import redact, render
from research.daily_brief.tests._common import ROOT, TmpStore, sample, store

NOW = datetime(2026, 10, 9, 21, 0, tzinfo=store.ET)

# Anything that would make this page look like (or lead back into) FCC.
FCC_NAV_MARKERS = (
    "FCC", "financial-command", "nav-horizon.js", "mobile-tabbar", "data-m-tab",
    "manifest.webmanifest", "favicon.svg", 'href="/"', "Financial Command",
)
# Horizon model widgets that must not appear on this page.
MODEL_MARKERS = ("regime", "world-state", "world_state", "scenario", "version_id", "Weather")
# Money strings planted in the fixtures; none may reach the Horizon host.
PLANTED_MONEY = (
    "USDC", "$154", "$750", "$900", "$245", "$41,200", "BTC", "Coinbase", "Robinhood", "balance",
    "Fund manager", "Burn rate", "treasury", "Business", "buyout", "new dashboard card",
)


def visible_text(page: str) -> str:
    page = re.sub(r"<style>.*?</style>", " ", page, flags=re.S)
    return re.sub(r"<[^>]+>", " ", page)


class RenderBase(TmpStore):
    def setUp(self):
        super().setUp()
        self.pub_all()

    def get(self, path, now=NOW):
        return render.route(path, root=self.root, now=now)


class TestRoutes(RenderBase):
    def test_latest_shows_newest_edition(self):
        code, ctype, body = self.get("/daily-brief")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("Evening Edition", body)
        self.assertIn(redact.HELD_HEADLINE, body)  # pm headline carried money
        self.assertNotIn('class="stale"', body)
        self.assertEqual(self.get("/daily-brief/")[0], 200)

    def test_permalinks(self):
        for path, needle in (("/daily-brief/2026-10-09/am", "Morning Edition"),
                             ("/daily-brief/2026-10-09/pm", "Evening Edition"),
                             ("/daily-brief/2026-10-08/pm/", "Nothing open")):
            code, _, body = self.get(path)
            self.assertEqual(code, 200, path)
            self.assertIn(needle, body, path)
        self.assertEqual(self.get("/daily-brief/2026-01-01/am")[0], 404)
        self.assertEqual(self.get("/daily-brief/nope")[0], 404)

    def test_not_a_daily_brief_path(self):
        for p in ("/", "/brief", "/api/brief", "/daily-briefing", "/api/dashboard"):
            self.assertIsNone(self.get(p), p)

    def test_archive(self):
        code, _, body = self.get("/daily-brief/archive")
        self.assertEqual(code, 200)
        self.assertIn("October 2026", body)
        self.assertLess(body.index("Friday, October 9, 2026"), body.index("Thursday, October 8, 2026"))
        for href in ("/daily-brief/2026-10-09/am", "/daily-brief/2026-10-09/pm", "/daily-brief/2026-10-08/pm"):
            self.assertIn(f'href="{href}"', body)

    def test_prev_next(self):
        _, _, body = self.get("/daily-brief/2026-10-09/am")
        self.assertIn('rel="prev" href="/daily-brief/2026-10-08/pm"', body)
        self.assertIn('rel="next" href="/daily-brief/2026-10-09/pm"', body)

    def test_empty_store(self):
        code, _, body = render.route("/daily-brief", root=self.root / "none")
        self.assertEqual(code, 200)
        self.assertIn("No edition published yet", body)

    def test_stale_banner(self):
        _, _, body = self.get("/daily-brief", now=datetime(2026, 10, 11, 9, 0, tzinfo=store.ET))
        self.assertIn('class="stale"', body)

    def test_api_latest_is_scrubbed(self):
        code, ctype, body = self.get("/daily-brief/api/latest")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["url"], "/daily-brief/2026-10-09/pm")
        self.assertEqual(data["edition"]["front_page"]["headline"], redact.HELD_HEADLINE)
        self.assertNotIn("USDC", body)
        self.assertNotIn("weather", data["edition"])

    def test_html_escaped(self):
        ed = sample("sample-am")
        ed["date"] = "2026-10-10"
        ed["front_page"]["headline"] = "<script>alert(1)</script>"
        ed["todos"][0]["title"] = '"><img src=x onerror=alert(1)>'
        self.pub(ed)
        _, _, body = self.get("/daily-brief/2026-10-10/am")
        self.assertNotIn("<script>alert", body)
        self.assertNotIn("<img src=x", body)


class TestSections(RenderBase):
    def test_todo_buckets_and_mac_after_todo(self):
        _, _, body = self.get("/daily-brief/2026-10-09/am")
        order = [body.index(f'data-bucket="{b}"') for b in store.BUCKETS]
        self.assertEqual(order, sorted(order))
        self.assertLess(body.index('id="sec-day-ahead"'), body.index('id="sec-todo"'))
        self.assertLess(body.index('id="sec-todo"'), body.index('id="sec-mac"'))
        self.assertIn('href="https://www.notion.so/sample-overdue"', body)
        self.assertIn("Roadside: call the shop at 555-0142", body)

    def test_mac_tasks_items(self):
        _, _, body = self.get("/daily-brief/2026-10-09/am")
        self.assertIn('data-mac-state="items"', body)
        self.assertEqual(body.count('class="mac-item"'), 3)
        self.assertIn("SAMPLE: Set up SuperTake", body)
        self.assertIn("3 Mac tasks", body)

    def test_mac_tasks_empty_states(self):
        _, _, pm = self.get("/daily-brief/2026-10-09/pm")  # every box checked
        self.assertIn('data-mac-state="clear"', pm)
        self.assertIn("Nothing waiting at the Mac", pm)
        _, _, old = self.get("/daily-brief/2026-10-08/pm")  # no block at all
        self.assertIn('data-mac-state="absent"', old)
        self.assertIn("Nothing waiting at the Mac", old)
        self.assertIn("no snapshot in this edition", old)
        ed = sample("sample-am")
        ed["date"] = "2026-10-11"
        ed["mac_tasks"] = store.mac_tasks_from_markdown(None, fetched_at="2026-10-11T08:00:00-04:00")
        self.pub(ed)
        _, _, missing = self.get("/daily-brief/2026-10-11/am")
        self.assertIn('data-mac-state="missing"', missing)
        self.assertIn("Nothing waiting at the Mac", missing)

    def test_pm_titles(self):
        _, _, body = self.get("/daily-brief/2026-10-09/pm")
        self.assertIn("To-Do (coming due)", body)
        self.assertIn("Tomorrow", body)

    def test_in_page_anchors_carry_page_path(self):
        # FCC's /horizon lens injects <base href="/horizon/">; bare "#x" would leave the page.
        _, _, perma = self.get("/daily-brief/2026-10-09/am")
        self.assertNotIn('href="#', perma)
        self.assertIn('href="/daily-brief/2026-10-09/am#sec-mac"', perma)
        _, _, latest = self.get("/daily-brief")
        self.assertIn('href="/daily-brief#sec-front"', latest)


class TestNoFccOnHorizonHost(RenderBase):
    PAGES = ("/daily-brief", "/daily-brief/2026-10-09/am", "/daily-brief/2026-10-09/pm",
             "/daily-brief/2026-10-08/pm", "/daily-brief/archive")

    def test_no_fcc_nav_markers(self):
        for p in self.PAGES:
            _, _, body = self.get(p)
            for m in FCC_NAV_MARKERS:
                self.assertNotIn(m, body, f"{m!r} on {p}")

    def test_no_model_widgets(self):
        for p in self.PAGES:
            _, _, body = self.get(p)
            for m in MODEL_MARKERS:
                self.assertNotIn(m, body, f"{m!r} on {p}")
        self.assertIn("Not Horizon model output", self.get("/daily-brief")[2])

    def test_no_fcc_money_fields_in_rendered_html(self):
        for p in self.PAGES + ("/daily-brief/api/latest",):
            _, _, body = self.get(p)
            for m in PLANTED_MONEY:
                self.assertNotIn(m, body, f"{m!r} on {p}")
            text = visible_text(body) if not p.endswith("latest") else body
            hit = redact.MONEY_RE.search(text)
            self.assertIsNone(hit, f"money-like {hit and hit.group(0)!r} on {p}")
            self.assertNotRegex(text, r"[$]\s?\d")

    def test_withheld_count_and_clean_parts_kept(self):
        _, _, body = self.get("/daily-brief/2026-10-09/am")
        self.assertIn("lines with money details kept off this page", body)
        self.assertIn("SAMPLE: Daily Brief moves to the Horizon host", body)
        self.assertIn("2 pickups, 1 dropoff today", body)
        self.assertNotIn('id="sec-business"', body)
        self.assertNotIn('id="sec-loose-ends"', body)  # every line was money
        self.assertIn('id="sec-shipped"', body)

    def test_ynab_section_withheld(self):
        self.assertTrue(redact.is_fcc_section({"id": "ynab", "title": "YNAB"}))
        self.assertFalse(redact.is_fcc_section({"id": "fleet", "title": "Fleet"}))

    def test_store_keeps_full_edition(self):
        raw = store.load_edition("2026-10-09", "am", self.root)
        self.assertIn("Business", [s["title"] for s in raw["sections"]])

    def test_renderer_never_reads_horizon_model(self):
        for mod in ("store", "render", "redact", "cli"):
            src = (ROOT / "research" / "daily_brief" / f"{mod}.py").read_text(encoding="utf-8")
            self.assertNotIn("research.horizon", src, mod)
            self.assertNotIn("world_state", src, mod)
            self.assertNotIn("from treasury", src, mod)


class TestHorizonServerIntegration(RenderBase):
    def test_horizon_host_serves_daily_brief(self):
        from research.horizon import server as hz

        data_dir = ROOT / "research" / "horizon" / "data"
        before = sorted(p.name for p in data_dir.rglob("*")) if data_dir.exists() else []
        with mock.patch.dict(os.environ, {"DAILY_BRIEF_DIR": str(self.root)}):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), hz.HorizonHandler)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                for path in ("/daily-brief", "/daily-brief/2026-10-09/am", "/daily-brief/archive"):
                    with urllib.request.urlopen(base + path, timeout=10) as r:
                        self.assertEqual(r.status, 200, path)
                        self.assertIn("text/html", r.headers["Content-Type"])
                        page = r.read()
                        self.assertIn(b"The Daily Brief", page)
                        self.assertNotIn(b"USDC", page)
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(base + "/daily-brief/2020-01-01/am", timeout=10)
                self.assertEqual(cm.exception.code, 404)
                req = urllib.request.Request(base + "/daily-brief", method="HEAD")
                with urllib.request.urlopen(req, timeout=10) as r:
                    self.assertEqual(r.status, 200)
                with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
                    self.assertEqual(json.loads(r.read())["service"], "horizon-macro")
            finally:
                httpd.shutdown()
                httpd.server_close()
        after = sorted(p.name for p in data_dir.rglob("*")) if data_dir.exists() else []
        self.assertEqual(before, after, "Daily Brief must not write Horizon model artifacts")


if __name__ == "__main__":
    unittest.main()
