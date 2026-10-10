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
# Personal-book strings planted in the fixtures. News tickers, the Business
# section title, and a Coinbase headline are not in this list: those are the
# paper, and the tests below require them to survive.
PLANTED_BOOK = (
    "USDC", "$154", "$750", "$900", "$245", "$41,200", "$25", "0.05 BTC",
    "Coinbase USDC", "Robinhood", "balance", "Fund manager", "Burn rate",
    "treasury snapshot", "buyout", "we hold", "may_change", "trim SOL",
    "Morpho", "LTV", "card balance", "0x5550000000000000000000000000000000000001",
    "financial-command", "Turo revenue", "Panamerica payout", "Alex Sample",
    "prism-gateway", ".ts.net", "target 8%", "target 5%", "so-what",
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
        self.assertNotIn("we hold", body)
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
            for m in PLANTED_BOOK:
                self.assertNotIn(m, body, f"{m!r} on {p}")
            text = visible_text(body) if not p.endswith("latest") else body
            hit = redact.book_match(text)
            self.assertIsNone(hit, f"personal-book {hit!r} on {p}")

    def test_withheld_count_and_clean_parts_kept(self):
        _, _, body = self.get("/daily-brief/2026-10-09/am")
        self.assertIn("lines with money details kept off this page", body)
        self.assertIn("SAMPLE: Daily Brief moves to the Horizon host", body)
        self.assertIn("2 pickups, 1 dropoff today", body)
        self.assertIn('id="sec-shipped"', body)
        self.assertIn("Reply to the shop about the Thursday pickup", body)
        self.assertNotIn("0.05 BTC", body)
        # A private href is stripped; the line of text stays.
        self.assertIn("SAMPLE: new dashboard card", body)
        self.assertNotIn("tailb1085a", body)

    def test_ynab_section_withheld(self):
        self.assertTrue(redact.is_fcc_section({"id": "ynab", "title": "YNAB"}))
        self.assertTrue(redact.is_fcc_section({"id": "portfolio", "title": "Portfolio"}))
        self.assertFalse(redact.is_fcc_section({"id": "fleet", "title": "Fleet"}))
        self.assertFalse(redact.is_fcc_section({"id": "markets", "title": "Markets"}))
        self.assertFalse(redact.is_fcc_section({"id": "business", "title": "Business"}))
        self.assertFalse(redact.is_fcc_section({"id": "books", "title": "Books"}))
        ed = sample("sample-empty-todos")
        ed["sections"].append({"id": "ynab", "title": "YNAB", "items": ["Review categories"]})
        clean, held = redact.scrub_edition(ed)
        self.assertNotIn("ynab", [s["id"] for s in clean["sections"]])
        self.assertGreaterEqual(held, 1)

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
            "Call the shop, then review rent ledger",
        ):
            self.assertIn(line, body, line)
        # The amount and the recipient are gone; the to-do around them stays.
        self.assertNotIn("$25", body)
        self.assertNotIn("Alex Sample", body)
        self.assertNotIn("before the $25", body)

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


# Secrets planted in fixtures/sample-leak.json. Each one must be in the raw
# edition and absent from both the rendered page and /daily-brief/api/latest.
LEAK_SECRETS = (
    "$12,400", "$8,250.17", "$31,415", "$250k", "$18,300", "$45k",
    "+$3,240", "-$1,120", "$2,100", "$58,000", "$61,200", "$612", "$450",
    "$900", "$190", "900 USDC", "USDC", "0.85 BTC", "2.5 SOL", "0.1 BTC",
    "Northwind", "Alex Sample", "to alex", "Jordan Example", "prism-gateway",
    ".ts.net", "prism:8790", "127.0.0.1", "localhost", "100.64.0.2", "8795",
    "8790", "fcc_snapshot", "secret-nav-250000", "secret-weather-key",
    "secret-section-nav", "secret-item-note", "todo-secret-field",
    "financial-command", "/treasury", "/fcc", "Allocation", "avg cost",
    "Unrealized", "P&L", "PnL", "Car loan", "Brokerage cash", "Net worth",
    "1NorthwindFakeAddrzzzzzzzzz", "3NorthwindFakeP2SHzzzzzzzzzz",
    "7FakeNorthwindMintzzzzzzzzzzzzzzzz",
    "bc1q555qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    "0x5551111111111111111111111111111111111111", "snap.png",
    "NW now",
    "12.4k", "USD 900", "900", "€500", "500", "450", "Jordan", "@maria",
    "chris-mac:3000", "fd7a:115c:a1e0", "[::1]", "http://prism/",
    "7xKXtg2CW87d97TXJSDpbD", "5jBkheTqA83TZRuJosgAsU",
    "7xKXtg2CW87d97TXJSDp", "bD5jBkheTqA83TZRuJosgAsU",
)

LEAK_KEEPS = (
    "Pay rent",
    "Review rent ledger",
    "AAPL tops $180 on a quiet tape",
    "TSLA jumps 8% to $250 after deliveries beat",
    "Gold climbs past $2,400 an ounce",
    "Acme raises $12.4M Series A led by Example Ventures",
    "Senate tax bill advances and credit markets stay calm",
    "Panamerica opens a driver handbook",
    "Call the dentist",
    "Ops board update",
    "Ops board refresh",
    "SAMPLE: a short novel about a lighthouse keeper",
    "Clear and mild",
    "SAMPLE: leak check edition",
    "The leak check edition is a fixture with fake figures only",
    "9:00 AM  Standup with Grok",
    "2 pickups today",
    "SAMPLE: reboot the studio Mac",
    "SAMPLE: open the studio notes",
    "SAMPLE: dashboard note",
    "SAMPLE: leak check ships",
    "Tailnet book",
    "Local debug",
    "A public sentence for the masthead.",
)

_PUBLIC_KEYS = {
    "edition": {"date", "edition", "vol", "no", "published_at", "theme", "quote",
                "weather", "front_page", "sections", "todos", "mac_tasks"},
    "front": {"headline", "subhead", "body", "image"},
    "section": {"id", "title", "items"},
    "item": {"text", "link", "checked"},
    "todo": {"title", "status", "due", "owner", "priority", "notion_url"},
    "mac": {"source_url", "fetched_at", "page_found", "items"},
    "quote": {"text", "author"},
    "weather": {"summary", "high", "low", "icon", "rain", "wind", "note"},
}


def _hrefs(page: str) -> list[str]:
    return re.findall(r'(?:href|src)="([^"]*)"', page)


class TestAdversarialLeak(TmpStore):
    """The reviewer's leak edition, with fake names, hosts, and addresses."""

    def setUp(self):
        super().setUp()
        self.raw = sample("sample-leak")
        self.pub(self.raw)

    def get(self, path):
        return render.route(path, root=self.root, now=NOW)

    def test_secrets_absent_from_page_and_api(self):
        raw_text = json.dumps(self.raw, ensure_ascii=False)
        for secret in LEAK_SECRETS:
            self.assertIn(secret, raw_text, secret)
        _, _, html = self.get("/daily-brief")
        _, _, api_body = self.get("/daily-brief/api/latest")
        for secret in LEAK_SECRETS:
            self.assertNotIn(secret, html, f"html leaked {secret!r}")
            self.assertNotIn(secret, api_body, f"api leaked {secret!r}")
        self.assertNotIn("P&amp;L", html)
        self.assertIsNone(redact.book_match(visible_text(html)))
        self.assertIsNone(redact.book_match(api_body))
        for url in _hrefs(html):
            self.assertFalse(redact.is_private_link(url), url)
            self.assertIsNone(redact.book_match(url), url)

        data = json.loads(api_body)
        edition = data["edition"]
        self.assertNotIn("fcc_snapshot", edition)
        self.assertTrue(set(edition) <= _PUBLIC_KEYS["edition"])
        self.assertTrue(set(edition["front_page"]) <= _PUBLIC_KEYS["front"])
        self.assertNotIn("image", edition["front_page"])
        self.assertTrue(set(edition["quote"]) <= _PUBLIC_KEYS["quote"])
        self.assertTrue(set(edition["weather"]) <= _PUBLIC_KEYS["weather"])
        self.assertNotIn("theme", edition)
        for section in edition["sections"]:
            self.assertTrue(set(section) <= _PUBLIC_KEYS["section"], section.get("id"))
            for item in section["items"]:
                if isinstance(item, dict):
                    self.assertTrue(set(item) <= _PUBLIC_KEYS["item"])
        for todo in edition["todos"]:
            self.assertTrue(set(todo) <= _PUBLIC_KEYS["todo"])
        mac = edition["mac_tasks"]
        self.assertTrue(set(mac) <= _PUBLIC_KEYS["mac"])
        self.assertNotIn("source_url", mac)
        for item in mac["items"]:
            self.assertTrue(set(item) <= _PUBLIC_KEYS["item"])

        stored = store.load_edition("2026-10-12", "am", self.root)
        self.assertEqual(stored["fcc_snapshot"]["note"], "secret-nav-250000")
        self.assertIn("secret-weather-key", json.dumps(stored["weather"]))

    def test_plain_todos_news_and_weather_stay(self):
        _, _, html = self.get("/daily-brief")
        _, _, api_body = self.get("/daily-brief/api/latest")
        edition = json.loads(api_body)["edition"]
        api_text = json.dumps(edition)
        for line in LEAK_KEEPS:
            self.assertIn(line, html, line)
            self.assertIn(line, api_text, line)
        self.assertIn("<h4>Local weather</h4>", html)
        self.assertIn("Daily Brief, not Horizon model output", html)
        self.assertIn("70°", html)
        self.assertIn("55°", html)
        self.assertIn('href="https://www.notion.so/sample-leak-ledger"', html)
        self.assertIn('href="https://example.com/leak-check"', html)
        titles = {t["title"]: t for t in edition["todos"]}
        for title in ("Pay rent", "Review rent ledger", "Call the dentist"):
            self.assertIn(title, titles)
        self.assertNotIn("notion_url", titles["Pay rent"])
        self.assertEqual(
            titles["Review rent ledger"]["notion_url"],
            "https://www.notion.so/sample-leak-ledger",
        )
        shipped = next(s for s in edition["sections"] if s["id"] == "shipped")
        by_text = {it["text"]: it for it in shipped["items"]}
        self.assertNotIn("link", by_text["SAMPLE: dashboard note"])
        self.assertNotIn("link", by_text["SAMPLE: command link"])
        self.assertNotIn("link", by_text["Tailnet book"])
        self.assertNotIn("link", by_text["Local debug"])
        self.assertEqual(by_text["SAMPLE: leak check ships"]["link"], "https://example.com/leak-check")
        self.assertNotIn("Notes live at", api_text)
        mac_by_text = {it["text"]: it for it in edition["mac_tasks"]["items"]}
        self.assertNotIn("link", mac_by_text["SAMPLE: open the studio notes"])
        self.assertEqual(
            [s["id"] for s in edition["sections"]],
            ["day-ahead", "markets", "business", "books", "fleet", "shipped"],
        )


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
