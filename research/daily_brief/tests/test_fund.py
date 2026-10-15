"""Fund watch: monitored set, Naka's notes file, holdings/drift, status, macro, render, CLI."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from research.daily_brief.tests._common import FIXTURES, ROOT, TmpStore, sample

from research.daily_brief import cli, fund, fund_notes, fund_status, render, store

NOW = datetime(2026, 10, 16, 11, 44, tzinfo=timezone.utc)  # 7:44 AM ET
DATE = "2026-10-16"
EXAMPLE = (FIXTURES / "fund-notes-example.md").read_text(encoding="utf-8")
CS = json.loads((ROOT / "investment" / "consider_share.json").read_text(encoding="utf-8"))
POLICY = json.loads((ROOT / "investment" / "fund_manager.json").read_text(encoding="utf-8"))
SNAP = json.loads((FIXTURES / "fund-rh-snapshot.json").read_text(encoding="utf-8"))


def _root_with(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    for rel, text in files.items():
        p = Path(tmp.name) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp


def _notes(tmp: tempfile.TemporaryDirectory, date_s: str = DATE) -> dict:
    return fund_notes.load_notes(tmp.name, date_s, with_git=False)


class TestMonitoredSet(unittest.TestCase):
    def test_set_comes_from_consider_share_pins_first(self) -> None:
        m = fund.monitored_set(CS)
        self.assertEqual([x["symbol"] for x in m if x["pinned"]], ["SPCX", "TSLA"])
        self.assertEqual({x["symbol"] for x in m}, {"SPCX", "TSLA", "BE"})
        self.assertEqual([x["symbol"] for x in m][:2], ["SPCX", "TSLA"])

    def test_only_pins_read_pinned(self) -> None:
        by = {x["symbol"]: fund.relevance_line(x) for x in fund.monitored_set(CS)}
        self.assertIn("pinned", by["TSLA"])
        self.assertIn("pinned", by["SPCX"])
        self.assertEqual(by["BE"], "Allocation relevance: watch only")

    def test_junk_and_lowercase_names_are_not_tickers(self) -> None:
        cs = {"pins": {"TSLA": 50, "bad name": 1, "ZERO": 0, "BOOL": True}, "reallocate": {"non_spcx_tsla_equities": ["TSLA", "x y"]}}
        self.assertEqual([x["symbol"] for x in fund.monitored_set(cs)], ["TSLA", "BOOL", "ZERO"])
        self.assertEqual([x["pinned"] for x in fund.monitored_set(cs)], [True, False, False])


class TestNotesFile(unittest.TestCase):
    def test_example_round_trips(self) -> None:
        r = fund_notes.parse_notes(EXAMPLE)
        self.assertEqual(r["state"], "filed")
        self.assertEqual(r["problems"], [])
        self.assertEqual((r["date"], r["filed_at"]), (DATE, "2026-10-16T06:12:00-04:00"))
        self.assertEqual([d["ticker"] for d in r["developments"]], ["SPCX", "TSLA", "TSLA", "MSTR", "BE"])
        self.assertIsNone(r["developments"][-1]["url"])
        self.assertEqual(r["developments"][0]["url"], "https://example.com/sample/spcx-1")
        self.assertEqual([(x["ticker"], x["word"]) for x in r["relevance"]],
                         [("SPCX", "pinned"), ("TSLA", "pinned"), ("MSTR", "watch only"), ("BE", "watch only")])
        self.assertIn("SAMPLE bullet two", r["direction"])
        self.assertEqual(r["actions"], "none")
        self.assertEqual(r["missing_sections"], [])

    def test_format_md_embeds_the_exact_example(self) -> None:
        spec = (ROOT / "investment" / "brief" / "FORMAT.md").read_text(encoding="utf-8")
        m = re.search(r"````markdown\n(.*?)\n````", spec, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).rstrip("\n"), EXAMPLE.rstrip("\n"))
        for h in fund_notes.REQUIRED_SECTIONS:
            self.assertIn(f"## {h}", spec)

    def test_file_present(self) -> None:
        tmp = _root_with({f"investment/brief/{DATE}.md": EXAMPLE})
        self.addCleanup(tmp.cleanup)
        n = _notes(tmp)
        self.assertEqual((n["state"], n["date"], n["edition_date"]), ("filed", DATE, DATE))
        self.assertTrue(n["mtime"].endswith("+00:00"))
        blk = fund.build_fund(CS, notes=n, now=NOW)
        pins = {r["symbol"]: r for r in blk["monitored"]}
        self.assertEqual(len(pins["TSLA"]["developments"]), 2)
        self.assertEqual(pins["BE"]["developments"][0]["url"], None)
        self.assertEqual([o["symbol"] for o in blk["other_tickers"]], ["MSTR"])
        self.assertNotIn("file_relevance_disagrees", pins["TSLA"])

    def test_file_missing(self) -> None:
        tmp = _root_with({})
        self.addCleanup(tmp.cleanup)
        n = _notes(tmp)
        self.assertEqual(n["state"], "missing")
        self.assertEqual(n["note"], f"Naka's fund notes not yet filed for {DATE}.")
        blk = fund.build_fund(CS, notes=n, now=NOW)
        self.assertTrue(all(r["developments"] == [] for r in blk["monitored"]))
        html = render.render_fund(blk)
        self.assertIn(f"not yet filed for {DATE}", html)
        self.assertIn('data-notes-state="missing"', html)
        self.assertNotIn("SAMPLE", html)
        self.assertIn("Waiting on Naka", html)
        self.assertNotIn("Not covered in Naka", html)

    def test_previous_day_file_is_never_passed_off_as_today(self) -> None:
        tmp = _root_with({"investment/brief/2026-10-15.md": EXAMPLE.replace(DATE, "2026-10-15")})
        self.addCleanup(tmp.cleanup)
        n = _notes(tmp)
        self.assertEqual(n["state"], "missing")
        self.assertEqual(n["latest_on_disk"], "2026-10-15")
        html = render.render_fund(fund.build_fund(CS, notes=n, now=NOW))
        self.assertIn("Most recent file on disk: 2026-10-15 (not shown)", html)
        self.assertNotIn("SAMPLE", html)

    def test_todays_filename_with_yesterdays_front_matter_is_a_mismatch(self) -> None:
        stale = EXAMPLE.replace(f"date: {DATE}", "date: 2026-10-15")
        tmp = _root_with({f"investment/brief/{DATE}.md": stale})
        self.addCleanup(tmp.cleanup)
        n = _notes(tmp)
        self.assertEqual(n["state"], "mismatch")
        self.assertEqual(n["developments"], [])
        html = render.render_fund(fund.build_fund(CS, notes=n, now=NOW))
        self.assertIn("says it is dated 2026-10-15", html)
        self.assertIn('data-notes-state="mismatch"', html)
        self.assertNotIn("SAMPLE", html)

    def test_malformed_markdown_is_tolerant_and_flagged(self) -> None:
        bad = (
            "---\ndate: 2026-10-16\nfiled_at: yesterday-ish\n---\n\n"
            "## Developments\n- TSLA did a thing (no bold, no source)\n"
            "- **TSLA** — has a bad link. Source: javascript:alert(1)\n"
            "- **SPCX** — fine. Source: https://example.com/ok\n\n"
            "## Surprise section\n<script>alert(1)</script>\n"
            "## Naka: direction (advisory)\nHold the line.\n"
        )
        r = fund_notes.parse_notes(bad)
        self.assertEqual(r["state"], "filed")
        self.assertEqual([d["ticker"] for d in r["developments"]], ["SPCX"])
        self.assertEqual(len(r["unparsed"]), 2)
        self.assertEqual(r["missing_sections"], ["Relevance", "Naka: actions taken"])
        self.assertIsNone(r["filed_at"])
        self.assertEqual(r["unknown_sections"][0]["heading"], "Surprise section")
        tmp = _root_with({f"investment/brief/{DATE}.md": bad})
        self.addCleanup(tmp.cleanup)
        html = render.render_fund(fund.build_fund(CS, notes=_notes(tmp), now=NOW))
        self.assertIn("Missing required section(s): Relevance, Naka: actions taken", html)
        self.assertIn("extra section", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("javascript:alert", html.replace("&amp;", "&").split("Lines that did not match")[0])

    def test_garbage_file_is_invalid_not_rendered(self) -> None:
        tmp = _root_with({f"investment/brief/{DATE}.md": "lol\x00\x01 not the format <b>x</b>\n"})
        self.addCleanup(tmp.cleanup)
        n = _notes(tmp)
        self.assertEqual(n["state"], "invalid")
        html = render.render_fund(fund.build_fund(CS, notes=n, now=NOW))
        self.assertIn("not in the agreed format", html)
        self.assertNotIn("<b>x</b>", html)

    def test_untrusted_text_is_escaped_and_links_are_https_only(self) -> None:
        evil = EXAMPLE.replace("SAMPLE: one-line summary", "<img src=x onerror=alert(1)> summary").replace(
            "SAMPLE bullet one.", "see https://example.com/a?x=1&y=<b> and javascript:alert(1)")
        tmp = _root_with({f"investment/brief/{DATE}.md": evil})
        self.addCleanup(tmp.cleanup)
        html = render.render_fund(fund.build_fund(CS, notes=_notes(tmp), now=NOW))
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn('href="javascript', html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_oversize_file_is_capped(self) -> None:
        r = fund_notes.parse_notes(EXAMPLE + "\n" + ("x" * 70000))
        self.assertTrue(any("larger than" in p for p in r["problems"]))

    def test_file_disagreeing_about_pinned_is_flagged_and_json_wins(self) -> None:
        wrong = EXAMPLE.replace("- **TSLA**: pinned", "- **TSLA**: watch only")
        tmp = _root_with({f"investment/brief/{DATE}.md": wrong})
        self.addCleanup(tmp.cleanup)
        blk = fund.build_fund(CS, notes=_notes(tmp), now=NOW)
        tsla = next(r for r in blk["monitored"] if r["symbol"] == "TSLA")
        self.assertEqual(tsla["relevance"], "pinned")
        self.assertEqual(tsla["file_relevance_disagrees"], "watch only")
        self.assertIn("consider_share.json decides", render.render_fund(blk))


class TestNakaActionsLog(unittest.TestCase):
    def test_seed_file_has_no_entries(self) -> None:
        seed = (ROOT / "investment" / "naka-actions.md").read_text(encoding="utf-8")
        self.assertEqual(fund_status.parse_naka_actions(seed)["state"], "none")

    def test_empty_and_missing_log(self) -> None:
        for text in ("", "# header only\n", None):
            r = fund_status.parse_naka_actions(text)
            self.assertEqual(r["state"], "none")
        html = render.render_fund(fund.build_fund(CS, naka=fund_status.parse_naka_actions("")))
        self.assertIn('data-naka-state="none"', html)
        self.assertIn("No Naka entry on file yet.", html)

    def test_newest_entry_wins_and_fences_are_ignored(self) -> None:
        log = (
            "# log\n\n```\n## Naka: actions taken \u2014 2030-01-01\nexample\n```\n\n"
            "## Naka: actions taken \u2014 2026-10-14\n\nnone\n\n"
            "## Naka: actions taken \u2014 2026-10-16\n\n- No change to BE. Reason.\n\n"
            "## Naka: actions taken \u2014 2026-10-15\n\nnone\n"
        )
        r = fund_status.parse_naka_actions(log)
        self.assertEqual((r["state"], r["date"]), ("entry", "2026-10-16"))
        self.assertEqual(r["text"], "- No change to BE. Reason.")
        html = render.render_fund(fund.build_fund(CS, naka=r))
        self.assertIn("Entry dated 2026-10-16", html)
        self.assertIn("<li>No change to BE. Reason.</li>", html)

    def test_bad_dates_do_not_count(self) -> None:
        self.assertEqual(fund_status.parse_naka_actions("## Naka: actions taken \u2014 2026-13-45\nnone\n")["state"], "none")


class TestHoldings(unittest.TestCase):
    def test_weights_and_drift_vs_5050(self) -> None:
        h = fund_status.holdings_block(SNAP, POLICY, CS, NOW)
        self.assertEqual(h["state"], "ok")
        rows = {r["symbol"]: r for r in h["rows"]}
        self.assertEqual(rows["TSLA"]["stocks_sleeve_weight_pct"], 50.0)
        self.assertEqual(rows["TSLA"]["drift_pp"], 0.0)
        self.assertEqual(rows["SPCX"]["stocks_sleeve_weight_pct"], 37.5)
        self.assertEqual(rows["SPCX"]["drift_pp"], -12.5)
        self.assertEqual(rows["SPCX"]["target_pct"], 50.0)
        self.assertEqual(rows["TSLA"]["weight_nav_pct"], 20.0)
        self.assertNotIn("drift_pp", rows["MSTR"])
        self.assertFalse(rows["MSTR"]["pinned"])
        self.assertEqual(h["cash_weight_pct"], 10.0)
        self.assertEqual(h["basis"], "cost-basis estimate")
        self.assertIn("estimate", h["note"])
        self.assertEqual(h["age_hours"], 1.9)
        self.assertEqual({s["sleeve"] for s in h["sleeves"]}, {"btc_digital_credit", "stocks_growth"})

    def test_no_dollar_amounts_or_quantities_leak(self) -> None:
        blob = json.dumps(fund_status.holdings_block(SNAP, POLICY, CS, NOW))
        for k in ("quantity", "total_value", "market_value", "account"):
            self.assertNotIn(k, blob)

    def test_same_estimate_as_fund_manager(self) -> None:
        from treasury.fund_manager import _position_market_value

        p = SNAP["agentic"]["positions"][1]
        self.assertEqual(_position_market_value(p), 150.0)

    def test_stale_snapshot_is_no_data_and_shows_as_of(self) -> None:
        later = datetime(2026, 10, 16, 16, 50, tzinfo=timezone.utc)  # 7h after as_of, limit 6h
        h = fund_status.holdings_block(SNAP, POLICY, CS, later)
        self.assertEqual(h["state"], "no_data")
        self.assertEqual(h["rows"], [])
        self.assertIn("stale", h["note"])
        self.assertEqual(h["as_of"], "2026-10-16T09:50:00+00:00")
        html = render.render_fund(fund.build_fund(CS, holdings=h))
        self.assertIn('data-holdings-state="no_data"', html)
        self.assertIn("5:50 AM ET", html)

    def test_missing_snapshot_and_missing_as_of(self) -> None:
        self.assertEqual(fund_status.holdings_block(None, POLICY, CS, NOW)["state"], "no_data")
        bad = copy.deepcopy(SNAP)
        del bad["agentic"]["as_of"]
        self.assertEqual(fund_status.holdings_block(bad, POLICY, CS, NOW)["state"], "no_data")

    def test_priced_snapshot_uses_prices(self) -> None:
        s = copy.deepcopy(SNAP)
        for p in s["agentic"]["positions"]:
            p["last_price"] = "100"
        self.assertEqual(fund_status.holdings_block(s, POLICY, CS, NOW)["basis"], "snapshot prices")


class TestStatusAndMacro(unittest.TestCase):
    def test_live_flag_and_issue_state_are_context(self) -> None:
        st = fund_status.status_block(POLICY, {"state": "open", "title": "[fund] guard", "updated_at": "2026-10-14T20:28:35Z"})
        self.assertIs(st["fund_manager"]["live"], POLICY["live"])
        self.assertEqual(st["issue"]["state"], "open")
        self.assertIn("Not permission", st["note"])
        html = render.render_fund(fund.build_fund(CS, status=st))
        self.assertIn('data-issue-state="open"', html)
        self.assertIn("Not permission to trade", html)
        for word in ("buy signal", "sell signal"):
            self.assertNotIn(word, html.lower())

    def test_issue_unreachable_is_no_data(self) -> None:
        st = fund_status.status_block(POLICY, None)
        self.assertEqual(st["issue"]["state"], "no_data")
        self.assertIsNone(fund_status.gather_issue(fetch=lambda u: None))
        self.assertIsNone(fund_status.gather_issue(fetch=lambda u: "not json"))
        self.assertEqual(fund_status.gather_issue(fetch=lambda u: '{"state":"closed"}')["state"], "closed")

    def test_macro_ships_as_labeled_placeholder(self) -> None:
        m = fund.load_macro(None)
        self.assertEqual((m["state"], m["label"], m["held"]), ("placeholder", "Horizon read", True))
        self.assertEqual([ln["topic"] for ln in m["lines"]], ["btc_crypto", "rates_fomc", "ai_infra"])
        html = render.render_fund(fund.build_fund(CS, macro=m))
        self.assertIn("Horizon read", html)
        self.assertIn("held fixture", html)
        self.assertIn("Awaiting Meridian", html)
        self.assertIn('data-macro-state="placeholder"', html)

    def test_macro_lines_render_when_supplied_and_nothing_is_invented(self) -> None:
        m = fund.normalize_macro({"held": False, "lines": [
            {"topic": "rates_fomc", "text": "SAMPLE macro line", "link": "https://example.com/m", "as_of": "2026-10-16"},
            {"topic": "bogus", "text": "ignored"}]})
        self.assertEqual(m["state"], "items")
        self.assertEqual([bool(ln["text"]) for ln in m["lines"]], [False, True, False])
        html = render.render_fund(fund.build_fund(CS, macro=m))
        self.assertIn("SAMPLE macro line", html)
        self.assertNotIn("held fixture", html)
        self.assertNotIn("ignored", html)


class TestEditionIntegration(TmpStore):
    def _block(self, files: dict[str, str] | None = None) -> dict:
        tmp = _root_with(files or {})
        self.addCleanup(tmp.cleanup)
        return fund.build_fund(
            CS, notes=_notes(tmp), macro=fund.load_macro(None),
            holdings=fund_status.holdings_block(SNAP, POLICY, CS, NOW),
            status=fund_status.status_block(POLICY, {"state": "open"}),
            naka=fund_status.parse_naka_actions(""), now=NOW)

    def test_build_is_deterministic(self) -> None:
        a = self._block({f"investment/brief/{DATE}.md": EXAMPLE})
        b = self._block({f"investment/brief/{DATE}.md": EXAMPLE})
        a["notes"].pop("mtime"), b["notes"].pop("mtime")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(render.render_fund(a), render.render_fund(copy.deepcopy(a)))

    def test_edition_validates_and_renders_after_business(self) -> None:
        ed = sample("sample-am")
        ed["fund"] = self._block({f"investment/brief/{DATE}.md": EXAMPLE})
        self.assertEqual(store.validate(store.normalize(ed)), [])
        self.pub(ed)
        key = (ed["date"], ed["edition"])
        code, ctype, body = render.route(f"/daily-brief/{key[0]}/{key[1]}", root=self.root)
        self.assertEqual(code, 200)
        self.assertLess(body.index('id="sec-business"'), body.index('id="sec-fund"'))
        self.assertRegex(body, r'href="[^"]*#sec-fund"')
        self.assertIn("SAMPLE: a second ticker", body)

    def test_editions_without_fund_are_unchanged(self) -> None:
        ed = sample("sample-am")
        self.pub(ed)
        _, _, body = render.route("/daily-brief/2026-10-09/am", root=self.root)
        self.assertNotIn('id="sec-fund"', body)

    def test_validate_rejects_bad_fund_shapes(self) -> None:
        ed = store.normalize(sample("sample-am"))
        for bad in ("x", {"monitored": "nope"}, {"monitored": [{"symbol": "A", "relevance": "buy"}]},
                    {"notes": {"state": "weird"}}, {"holdings": {"state": "ok", "rows": "x"}},
                    {"macro": {"lines": [{"text": "t", "link": "javascript:x"}]}},
                    {"monitored": [{"symbol": "A", "relevance": "pinned", "developments": [{"text": "t", "url": "ftp://x"}]}]}):
            self.assertTrue(store.validate({**ed, "fund": bad}), bad)


class TestCli(unittest.TestCase):
    def test_build_fund_block_from_a_checkout(self) -> None:
        tmp = _root_with({
            f"investment/brief/{DATE}.md": EXAMPLE,
            "investment/consider_share.json": json.dumps(CS),
            "investment/fund_manager.json": json.dumps(POLICY),
            "investment/naka-actions.md": "## Naka: actions taken \u2014 2026-10-16\n\nnone\n",
            "treasury/snapshots/robinhood_latest.json": json.dumps(SNAP),
        })
        self.addCleanup(tmp.cleanup)
        blk = cli.build_fund_block(tmp.name, date_s=DATE, now=NOW, network=False)
        self.assertEqual(blk["notes"]["state"], "filed")
        self.assertEqual(blk["holdings"]["state"], "ok")
        self.assertEqual(blk["status"]["issue"]["state"], "no_data")
        self.assertEqual(blk["naka"]["state"], "entry")
        self.assertEqual(store.validate(store.normalize({**sample("sample-am"), "fund": blk})), [])

    def test_empty_checkout_degrades_everywhere(self) -> None:
        tmp = _root_with({})
        self.addCleanup(tmp.cleanup)
        blk = cli.build_fund_block(tmp.name, date_s=DATE, now=NOW, network=False)
        self.assertEqual(blk["notes"]["state"], "missing")
        self.assertEqual(blk["holdings"]["state"], "no_data")
        self.assertEqual(blk["monitored"], [])
        self.assertEqual(blk["macro"]["state"], "placeholder")
        self.assertEqual(store.validate(store.normalize({**sample("sample-am"), "fund": blk})), [])

    def test_fund_check_cli(self) -> None:
        self.assertEqual(cli.main(["fund-check", str(FIXTURES / "fund-notes-example.md")]), 0)
        tmp = _root_with({"bad.md": "no format at all\n"})
        self.addCleanup(tmp.cleanup)
        self.assertEqual(cli.main(["fund-check", str(Path(tmp.name) / "bad.md")]), 2)


if __name__ == "__main__":
    unittest.main()
