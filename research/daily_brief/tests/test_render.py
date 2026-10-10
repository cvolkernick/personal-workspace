"""Daily Brief pages on the Horizon host: routes, full edition, no FCC chrome."""

from __future__ import annotations

import html
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

# Page chrome that must not appear. Edition links (including prism and
# financial-command URLs published in the fixture) are content and stay.
FCC_NAV_MARKERS = (
    "nav-horizon.js", "mobile-tabbar", "data-m-tab",
    "manifest.webmanifest", "favicon.svg", 'href="/"', "Financial Command",
)
# Horizon model widgets that must not appear on this page.
MODEL_MARKERS = ("regime", "world-state", "world_state", "scenario", "version_id", "Weather")


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
        self.assertIn("SAMPLE: USDC send is $245 short for tomorrow", body)
        self.assertNotIn("Front page held for the private edition", body)
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

    def test_api_latest_matches_stored_edition(self):
        code, ctype, body = self.get("/daily-brief/api/latest")
        self.assertEqual(code, 200)
        self.assertIn("application/json", ctype)
        data = json.loads(body)
        stored = store.load_edition("2026-10-09", "pm", self.root)
        self.assertEqual(data["url"], "/daily-brief/2026-10-09/pm")
        self.assertEqual(data["edition"], stored)
        self.assertEqual(data["edition"], redact.scrub_edition(stored)[0])
        self.assertEqual(data["edition"]["front_page"]["headline"], "SAMPLE: USDC send is $245 short for tomorrow")
        self.assertIn("we hold ETH, target 5%", body)
        self.assertEqual(data["edition"]["weather"], "Tomorrow: 87° / 73°, sunny")
        self.assertIn("ETH steadies after a quiet session", body)

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
        self.assertEqual(body.count('class="mac-item"'), 4)
        self.assertIn("SAMPLE: Set up SuperTake", body)
        self.assertIn("SAMPLE: check the Robinhood balance on the Mac", body)
        self.assertIn("4 Mac tasks", body)

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

    def test_store_keeps_full_edition(self):
        raw = store.load_edition("2026-10-09", "am", self.root)
        self.assertIn("Business", [s["title"] for s in raw["sections"]])

    def test_renderer_never_reads_horizon_model(self):
        for mod in ("store", "render", "redact", "cli"):
            src = (ROOT / "research" / "daily_brief" / f"{mod}.py").read_text(encoding="utf-8")
            self.assertNotIn("research.horizon", src, mod)
            self.assertNotIn("world_state", src, mod)
            self.assertNotIn("from treasury", src, mod)


class TestNewsAndWeatherKept(RenderBase):
    def test_markets_business_books_and_tickers(self):
        _, _, body = self.get("/daily-brief/2026-10-09/am")
        for sid, title in (("markets", "Markets"), ("business", "Business"), ("books", "Books")):
            self.assertIn(f'id="sec-{sid}"', body)
            self.assertIn(title, body)
        for line in (
            "NVDA rises after the chipmaker lifts its outlook",
            "AAPL tops $180 as the buyback continues",
            "BTC holds near the prior close; ETF flows turn positive",
            "Senate tax bill advances; credit markets calm as interest rates hold",
            "Cash levels at funds rise and loan demand cools",
            "Treasury yields ease on the long end",
            "Coinbase plans a new listing next month",
            "Inflation target 2% holds in the latest print",
            "AAPL price target 20% is unchanged",
            "Panamerica opens a driver handbook",
            "SAMPLE: a short novel about a lighthouse keeper",
            "Review rent ledger",
            "Call the shop, then review rent ledger before the $25 to Alex Sample",
            "$25 to Alex Sample",
            "SAMPLE money todo: move $900 USDC",
            "SAMPLE money paragraph: Coinbase USDC is about $154, so the Saturday send is $750 short.",
        ):
            self.assertIn(line, body, line)

    def test_local_weather_box(self):
        _, _, am = self.get("/daily-brief/2026-10-09/am")
        self.assertIn("<h4>Local weather</h4>", am)
        self.assertIn("Partly cloudy, afternoon storms", am)
        self.assertIn("88°", am)
        self.assertIn("74°", am)
        self.assertIn("40% after 2 PM", am)
        _, _, pm = self.get("/daily-brief/2026-10-09/pm")
        self.assertIn("<h4>Local weather</h4>", pm)
        self.assertIn("Tomorrow: 87° / 73°, sunny", pm)
        _, _, old = self.get("/daily-brief/2026-10-08/pm")
        self.assertIn("<h4>Local weather</h4>", old)
        self.assertIn("Not reported", old)
        self.assertNotIn(">Weather<", old)

    def test_not_model_line(self):
        for p in ("/daily-brief", "/daily-brief/2026-10-09/am", "/daily-brief/2026-10-09/pm",
                  "/daily-brief/archive", "/daily-brief/2026-10-08/pm"):
            self.assertIn("Daily Brief, not Horizon model output", self.get(p)[2], p)

    def test_original_font_weights(self):
        _, _, body = self.get("/daily-brief/2026-10-09/am")
        self.assertIn(".masthead h1{font-family:var(--serif);font-weight:900;", body)
        self.assertIn(".not-model{margin:.2rem 0 0;font:500 .68rem var(--sans);", body)
        self.assertIn(".sec-count{font:500 .7rem var(--sans);", body)


def assert_full_edition(testcase, stored: dict, html_body: str, api_edition: dict) -> None:
    """Every stored field the page shows is present, and the API is that edition."""
    testcase.assertEqual(api_edition, stored)
    testcase.assertEqual(redact.scrub_edition(stored), (stored, 0))
    testcase.assertNotIn("Front page held for the private edition", html_body)
    testcase.assertNotIn("lines with money details kept off this page", html_body)
    testcase.assertNotIn('data-held=', html_body)
    fp = stored.get("front_page") or {}
    testcase.assertIn(html.escape(fp.get("headline") or ""), html_body)
    if fp.get("subhead"):
        testcase.assertIn(html.escape(fp["subhead"]), html_body)
    for para in re.split(r"\n\s*\n", fp.get("body") or ""):
        if para.strip():
            testcase.assertIn(html.escape(para.strip()), html_body, para.strip()[:80])
    testcase.assertIn("<h4>Local weather</h4>", html_body)
    weather = stored.get("weather")
    if isinstance(weather, str):
        testcase.assertIn(html.escape(weather), html_body)
    elif isinstance(weather, dict):
        for key in ("summary", "icon", "rain", "wind", "note"):
            if weather.get(key):
                testcase.assertIn(html.escape(str(weather[key])), html_body, key)
        if weather.get("high") is not None:
            testcase.assertIn(f"{html.escape(str(weather['high']))}°", html_body)
        if weather.get("low") is not None:
            testcase.assertIn(f"{html.escape(str(weather['low']))}°", html_body)
    for section in stored.get("sections") or []:
        sid = section.get("id") or ""
        testcase.assertIn(f'id="sec-{html.escape(sid)}"', html_body, sid)
        if section.get("title"):
            testcase.assertIn(html.escape(section["title"]), html_body, section["title"])
        for item in section.get("items") or []:
            text = item if isinstance(item, str) else item.get("text", "")
            if text:
                testcase.assertIn(html.escape(text), html_body, text)
            if isinstance(item, dict) and item.get("link"):
                testcase.assertIn(f'href="{html.escape(item["link"])}"', html_body, item["link"])
    for todo in stored.get("todos") or []:
        title = todo.get("title") or ""
        testcase.assertIn(html.escape(title), html_body, title)
        if todo.get("notion_url"):
            testcase.assertIn(f'href="{html.escape(todo["notion_url"])}"', html_body, title)
    mt = stored.get("mac_tasks") if isinstance(stored.get("mac_tasks"), dict) else None
    if mt:
        if isinstance(mt.get("source_url"), str) and mt["source_url"].startswith("https://"):
            testcase.assertIn(f'href="{html.escape(mt["source_url"])}"', html_body)
        for item in mt.get("items") or []:
            if isinstance(item, dict) and item.get("checked") is True:
                continue
            text = item if isinstance(item, str) else (item.get("text") or "")
            if text:
                testcase.assertIn(html.escape(text), html_body, text)


class TestFullEdition(TmpStore):
    def test_morning_edition_renders_unchanged(self):
        self.pub(sample("sample-am"))
        stored = store.load_edition("2026-10-09", "am", self.root)
        _, _, page = render.route("/daily-brief/2026-10-09/am", root=self.root, now=NOW)
        code, _, api_body = render.route("/daily-brief/api/latest", root=self.root, now=NOW)
        self.assertEqual(code, 200)
        api_edition = json.loads(api_body)["edition"]
        assert_full_edition(self, stored, page, api_edition)
        self.assertIn(
            'href="https://prism.example.ts.net/financial-command/index.html"',
            page,
        )
        self.assertIn("SAMPLE: Rivian buyout quote is $41,200", page)
        self.assertIn("SAMPLE book: wallet 0x5550000000000000000000000000000000000001", page)
        self.assertIn("Daily Brief, not Horizon model output", page)

    def test_evening_page_matches_latest_api(self):
        self.pub(sample("sample-pm"))
        stored = store.load_edition("2026-10-09", "pm", self.root)
        _, _, page = render.route("/daily-brief", root=self.root, now=NOW)
        _, _, api_body = render.route("/daily-brief/api/latest", root=self.root, now=NOW)
        assert_full_edition(self, stored, page, json.loads(api_body)["edition"])
        self.assertIn("SAMPLE: USDC send is $245 short for tomorrow", page)
        self.assertIn("Tomorrow: 87° / 73°, sunny", page)

    def test_ynab_section_renders(self):
        ed = sample("sample-empty-todos")
        ed["sections"].append({
            "id": "ynab",
            "title": "YNAB",
            "items": ["Review categories", "SAMPLE: card balance is $245"],
        })
        self.pub(ed)
        stored = store.load_edition(ed["date"], ed["edition"], self.root)
        _, _, page = render.route(
            f"/daily-brief/{ed['date']}/{ed['edition']}", root=self.root, now=NOW,
        )
        _, _, api_body = render.route("/daily-brief/api/latest", root=self.root, now=NOW)
        assert_full_edition(self, stored, page, json.loads(api_body)["edition"])
        self.assertIn('id="sec-ynab"', page)
        self.assertIn("SAMPLE: card balance is $245", page)


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
                        self.assertIn(b"Daily Brief, not Horizon model output", page)
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
