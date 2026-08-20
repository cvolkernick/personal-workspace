"""Assay AC for the thin Orchestra pulse cut.

NOW/NEXT come from day_plan.next3. Recommendations, today.md examples,
unfilled / “user to fill” / empty creative-slot placeholders, July-17
tasks.json, stale backlog, and quests without a GT id stay off the page.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "orchestra"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import collect_holistic, collect_strategy  # noqa: E402
from day_plan import compose_day_plan  # noqa: E402
from payload import build_orchestra_payload  # noqa: E402
from priorities import synthesize_priorities  # noqa: E402
from pulse import (  # noqa: E402
    build_blocked,
    build_dock,
    build_one_liners,
    build_pulse,
    build_world,
    is_example_today_line,
    next_api_payload,
    now_api_payload,
    now_from_next3,
)
from recommendations import synthesize_recommendations  # noqa: E402
from server import OrchestraHandler  # noqa: E402

NOW = datetime(2026, 8, 20, 16, 0, 0, tzinfo=timezone.utc)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: object) -> None:
    _write(path, json.dumps(data, indent=2))


def _finance(as_of: str, actions: list | None = None) -> dict:
    return {
        "id": "finance",
        "available": True,
        "url": "http://127.0.0.1:8000/financial-command/",
        "signals": {
            "as_of": as_of,
            "stress_overall": "green",
            "red_mode": False,
            "free_cash_gate": "allow",
            "freshness": "fresh",
            "day_actions": actions
            or [
                {"kind": "ltv_check", "title": "Confirm Morpho LTV"},
                {"kind": "fill_manual", "title": "Fill missing Coinbase fields"},
                {"kind": "card_float", "title": "Top up card float"},
            ],
        },
    }


def _fit(**day_fields: object) -> dict:
    day = {
        "as_of": NOW.isoformat(),
        "session_due": True,
        "session_type": "push",
        "train_recommendation": "train",
        "recovery_score": 70,
        "recovery_label": "Ready",
        "protein_gap_band": "ok",
        "protein_as_of": NOW.isoformat(),
        **day_fields,
    }
    return {
        "id": "fitness",
        "available": True,
        "url": "http://127.0.0.1:8787/",
        "signals": {"day": day, "as_of": day.get("as_of")},
    }


def _wf(**board_fields: object) -> dict:
    board = {
        "as_of": NOW.isoformat(),
        "fresh_for_hours": 4,
        "fetch_ok": True,
        "stale": False,
        "ready_count": 2,
        "ready_top": [{"number": 92, "title": "day plan", "size": "M"}],
        "in_progress": [],
        "pending_review_count": 0,
        "blocked": [],
        "wip_overload": False,
        "free_agent_count": 2,
        "pipeline_pressure": "ok",
        **board_fields,
    }
    return {
        "id": "workflow",
        "available": True,
        "url": "http://127.0.0.1:8765/",
        "signals": {
            "board": board,
            "backlog": {
                "ok": True,
                "not_board_status": True,
                "role": "session_hint",
                "updated_at": "2026-07-17T17:39:37+00:00",
                "active": [{"id": "old", "title": "Stale backlog rec", "status": "planning"}],
            },
        },
    }


def _hol_today() -> dict:
    return {
        "id": "holistic",
        "available": True,
        "url": "http://127.0.0.1:8770/",
        "signals": {
            "plan_as_of": NOW.isoformat(),
            "as_of": NOW.isoformat(),
            "targets": ["Sleep", "Walk Duchess"],
            "plan_blocks": [
                {"id": "sleep", "title": "Sleep", "minutes": 480, "role": "reserve"},
                {"id": "deep-work", "title": "Deep work", "minutes": 120, "role": "work"},
            ],
        },
    }


JULY17_TASKS = {
    "version": 2,
    "items": [],
    "targets": [
        {"id": "sleep", "title": "Sleep", "reserve_minutes": 480},
        {"id": "duchess-walk", "title": "Walk Duchess", "minutes": 45},
        {"id": "lyft", "title": "Lyft driving", "kind": "fill_remainder"},
    ],
    "plan": {
        "window_start": "2026-07-17T15:12:34-04:00",
        "window_end": "2026-07-18T15:12:34-04:00",
        "unallocated_active_minutes": 0,
        "blocks": [
            {"id": "sleep", "title": "Sleep", "minutes": 480, "role": "reserve"},
            {
                "id": "lyft",
                "title": "Lyft driving",
                "minutes": 855,
                "role": "fill",
                "kind": "fill_remainder",
            },
        ],
    },
}


class ApiNextAssayTests(unittest.TestCase):
    def test_next_body_equals_day_plan_next3_not_recommendations(self) -> None:
        payload = {
            "ok": True,
            "day_plan": {
                "next3": [
                    {"id": "wf-ready-92", "title": "Pull candidate #92", "domain": "workflow"}
                ]
            },
            "recommendations": {
                "items": [
                    {"id": "rec-1", "title": "Do now (from today’s plan): e.g. review DCA"}
                ]
            },
            "recommended_actions": [
                {"id": "rec-1", "title": "Do now (from today’s plan): e.g. review DCA"}
            ],
        }
        body = next_api_payload(payload)
        self.assertEqual(body["next"], payload["day_plan"]["next3"])
        self.assertEqual(body["next3"], payload["day_plan"]["next3"])
        blob = json.dumps(body)
        self.assertNotIn("recommendations", blob)
        self.assertNotIn("e.g. review DCA", blob)

    def test_now_empty_is_204(self) -> None:
        code, body = now_api_payload({"day_plan": {"next3": []}})
        self.assertEqual(code, 204)
        self.assertIsNone(body)

    def test_http_api_next_matches_orchestra_day_plan(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), OrchestraHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            from urllib.request import urlopen

            with urlopen(f"http://{host}:{port}/api/orchestra", timeout=10) as resp:
                orch = json.loads(resp.read().decode("utf-8"))
            with urlopen(f"http://{host}:{port}/api/next", timeout=10) as resp:
                nxt = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(nxt.get("next"), (orch.get("day_plan") or {}).get("next3"))
            self.assertNotIn("recommendations", nxt)
            self.assertNotIn("recommended_actions", nxt)
        finally:
            httpd.shutdown()
            httpd.server_close()


class July17BlocksAssayTests(unittest.TestCase):
    def test_july17_tasks_json_not_todays_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write_json(ws / "holistic" / "data" / "tasks.json", JULY17_TASKS)
            hol = collect_holistic(ws)
            titles = [b.get("title") or b.get("id") for b in hol["signals"].get("plan_blocks") or []]
            self.assertNotIn("Lyft driving", titles)
            plan = compose_day_plan(
                [
                    hol,
                    _wf(),
                    _fit(),
                    _finance((NOW - timedelta(hours=1)).isoformat()),
                ],
                now=NOW,
            )
            block_titles = " ".join(str(b.get("title") or "") for b in plan["blocks"]).lower()
            self.assertNotIn("lyft", block_titles)
            self.assertNotIn("855", json.dumps(plan["blocks"]))
            # dated July-17 window is not today's spine
            self.assertFalse(
                any(
                    (b.get("id") or "").lower() == "sleep" and b.get("source") == "holistic"
                    for b in plan["blocks"]
                )
            )


class Next3FinanceTheaterAssayTests(unittest.TestCase):
    def test_next3_not_three_finance_when_session_and_ready_fresh(self) -> None:
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=2, free_agent_count=2),
                _fit(session_due=True, train_recommendation="train"),
                _finance((NOW - timedelta(hours=1)).isoformat()),
            ],
            now=NOW,
        )
        domains = [i.get("domain") for i in plan["next3"]]
        self.assertNotEqual(domains, ["finance", "finance", "finance"])
        self.assertLess(
            sum(1 for d in domains if d == "finance"),
            3,
            msg=plan["next3"],
        )
        kinds = {i.get("kind") for i in plan["next3"]}
        titles = " ".join(str(i.get("title") or "") for i in plan["next3"]).lower()
        self.assertTrue(
            "ready" in kinds
            or "train" in kinds
            or "pull" in titles
            or "train" in titles,
            msg=plan["next3"],
        )


class TodayExampleAssayTests(unittest.TestCase):
    def test_today_md_eg_does_not_emit_kind_today(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write(
                ws / "strategy" / "today.md",
                "# Today\n"
                "- [ ] **Investment / thematic bet maintenance** "
                "(e.g. review DCA, research one Energy name).\n"
                "- [ ] Ship the thin pulse cut\n",
            )
            _write(ws / "strategy" / "bets.md", "# Bets\n- **AI**\n")
            strat = collect_strategy(ws)
            open_items = strat["signals"]["today_open"]
            self.assertTrue(any("Ship the thin pulse" in x for x in open_items))
            self.assertFalse(any("e.g." in x.lower() for x in open_items))
            pris = synthesize_priorities(today_items=open_items + [
                "Investment / thematic bet maintenance (e.g. review DCA)"
            ])
            today_kinds = [p for p in pris if p.get("kind") == "today"]
            for p in today_kinds:
                self.assertNotIn("e.g.", (p.get("title") or "").lower())
            self.assertFalse(
                any("review dca" in (p.get("title") or "").lower() for p in today_kinds)
            )

    def test_unfilled_user_to_fill_creative_slot_not_today_now_or_do_now(self) -> None:
        live = (
            "**Creative or other domain next action** that has high impact this week "
            "(user to fill based on current weightings and energy)."
        )
        empty_slot = "Creative or other domain next action"
        unfilled = "Unfilled creative slot"
        filled = "Creative or other domain next action: finish Energy video cut"
        real = "Ship the thin pulse cut"

        self.assertTrue(is_example_today_line(live))
        self.assertTrue(is_example_today_line(empty_slot))
        self.assertTrue(is_example_today_line(unfilled))
        self.assertFalse(is_example_today_line(filled))
        self.assertFalse(is_example_today_line(real))

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write(
                ws / "strategy" / "today.md",
                "# Today\n"
                f"- [ ] {live}\n"
                f"- [ ] {empty_slot}\n"
                f"- [ ] {unfilled}\n"
                f"- [ ] {filled}\n"
                f"- [ ] {real}\n",
            )
            _write(ws / "strategy" / "bets.md", "# Bets\n- **AI**\n")
            strat = collect_strategy(ws)
            open_items = strat["signals"]["today_open"]
            blob_open = " ".join(open_items).lower()
            self.assertTrue(any("Ship the thin pulse" in x for x in open_items))
            self.assertTrue(any("finish Energy video" in x for x in open_items))
            self.assertNotIn("user to fill", blob_open)
            self.assertFalse(any("unfilled" in x.lower() for x in open_items))
            self.assertFalse(
                any(
                    "creative or other domain next action" in x.lower()
                    and "finish energy" not in x.lower()
                    for x in open_items
                )
            )

            pris = synthesize_priorities(
                today_items=open_items + [live, empty_slot, unfilled, filled, real]
            )
            today_kinds = [p for p in pris if p.get("kind") == "today"]
            today_blob = " ".join(p.get("title") or "" for p in today_kinds).lower()
            self.assertNotIn("user to fill", today_blob)
            self.assertNotIn("unfilled", today_blob)
            self.assertTrue(any("ship the thin pulse" in (p.get("title") or "").lower() for p in today_kinds))
            self.assertTrue(
                any("finish energy video" in (p.get("title") or "").lower() for p in today_kinds)
            )
            self.assertFalse(
                any(
                    "creative or other domain next action" in (p.get("title") or "").lower()
                    and "finish energy" not in (p.get("title") or "").lower()
                    for p in today_kinds
                )
            )

            rec = synthesize_recommendations(priorities=pris)
            rec_blob = json.dumps(rec).lower()
            self.assertNotIn("user to fill", rec_blob)
            self.assertFalse(
                any(
                    "do now" in (i.get("title") or i.get("action") or "").lower()
                    and (
                        "user to fill" in (i.get("title") or i.get("action") or "").lower()
                        or (
                            "creative or other domain next action"
                            in (i.get("title") or i.get("action") or "").lower()
                            and "finish energy" not in (i.get("title") or i.get("action") or "").lower()
                        )
                    )
                    for i in rec["items"]
                )
            )

            leaked = [
                {"id": "pri-1", "title": live.replace("**", ""), "kind": "today"},
                {"id": "pri-2", "title": empty_slot, "kind": "today"},
                {"id": "wf-ready-92", "title": "Pull candidate #92", "domain": "workflow"},
            ]
            first = now_from_next3(leaked)
            self.assertIsNotNone(first)
            self.assertEqual(first.get("title"), "Pull candidate #92")
            pulse = build_pulse(day_plan={"next3": leaked}, now=NOW)
            self.assertEqual((pulse.get("now") or {}).get("title"), "Pull candidate #92")
            next_titles = [x.get("title") for x in pulse.get("next") or []]
            self.assertEqual(next_titles, ["Pull candidate #92"])
            self.assertFalse(any("user to fill" in (t or "").lower() for t in next_titles))

            payload = build_orchestra_payload(ws, probe_ports=False)
            pri_today = [p for p in payload.get("priorities") or [] if p.get("kind") == "today"]
            self.assertFalse(
                any("user to fill" in (p.get("title") or "").lower() for p in pri_today)
            )
            rec_items = (payload.get("recommendations") or {}).get("items") or []
            self.assertFalse(
                any(
                    "do now" in (i.get("title") or i.get("action") or "").lower()
                    and "user to fill" in (i.get("title") or i.get("action") or "").lower()
                    for i in rec_items
                )
            )
            now_title = ((payload.get("pulse") or {}).get("now") or {}).get("title") or ""
            next_blob = json.dumps((payload.get("day_plan") or {}).get("next3") or []).lower()
            self.assertNotIn("user to fill", now_title.lower())
            self.assertNotIn("user to fill", next_blob)


class BacklogRecsAssayTests(unittest.TestCase):
    def test_not_board_status_does_not_feed_recs(self) -> None:
        rec = synthesize_recommendations(
            domains=[
                {
                    "id": "workflow",
                    "available": True,
                    "signals": {
                        "backlog": {
                            "not_board_status": True,
                            "updated_at": NOW.isoformat(),
                            "active": [{"id": "b1", "title": "Old ops row"}],
                        }
                    },
                }
            ],
            priorities=[
                {
                    "title": "Old ops row",
                    "kind": "backlog",
                    "source": "ops/backlog",
                    "domains": ["workflow"],
                    "priority": "high",
                    "rationale": "should not feed",
                }
            ],
            attention=[],
            synergies=[],
            bridge={"candidates": [{"backlog_id": "b1", "title": "Old ops row"}]},
            freshness={"stale_count": 0},
        )
        blob = json.dumps(rec).lower()
        self.assertNotIn("old ops row", blob)
        self.assertFalse(any(i.get("kind") == "bridge" for i in rec["items"]))

    def test_backlog_older_than_48h_does_not_feed_recs(self) -> None:
        old = (NOW - timedelta(hours=72)).isoformat()
        rec = synthesize_recommendations(
            domains=[
                {
                    "id": "workflow",
                    "available": True,
                    "signals": {
                        "backlog": {
                            "not_board_status": False,
                            "updated_at": old,
                            "active": [{"id": "b2", "title": "Aged backlog rec"}],
                        }
                    },
                }
            ],
            priorities=[
                {
                    "title": "Aged backlog rec",
                    "kind": "backlog",
                    "source": "ops/backlog",
                    "domains": ["workflow"],
                    "priority": "high",
                }
            ],
            attention=[],
            synergies=[],
            bridge={"candidates": [{"backlog_id": "b2", "title": "Aged backlog rec"}]},
            freshness={
                "sources": [
                    {"id": "backlog", "as_of": old, "age_hours": 72, "stale": True}
                ]
            },
        )
        blob = json.dumps(rec).lower()
        self.assertNotIn("aged backlog rec", blob)


class QuestAssayTests(unittest.TestCase):
    def test_quest_without_gt_id_omitted(self) -> None:
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(
                    session_due=False,
                    quests=[{"title": "Invented quest", "kind": "quest"}],
                ),
                _finance(
                    (NOW - timedelta(hours=1)).isoformat(),
                    actions=[],
                ),
            ],
            now=NOW,
        )
        blob = json.dumps(plan).lower()
        self.assertNotIn("invented quest", blob)
        pulse = build_pulse(day_plan=plan, now=NOW)
        self.assertFalse(
            any("invented quest" in (ln.get("text") or "").lower() for ln in pulse["one_liners"])
        )

    def test_quest_with_gt_id_kept_as_one_liner(self) -> None:
        fit = _fit(
            session_due=False,
            quests=[
                {
                    "title": "Close protein gap",
                    "kind": "quest",
                    "gt_task_id": "GT-abc123",
                }
            ],
        )
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                fit,
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        # Composer surfaces GT quests on the fitness source; pulse may one-line them
        fit_src = plan["sources"]["fitness"]
        quests = fit_src.get("quests") or []
        self.assertTrue(any(q.get("gt_task_id") == "GT-abc123" for q in quests))
        pulse = build_pulse(day_plan=plan, now=NOW)
        texts = [ln.get("text") for ln in pulse["one_liners"]]
        self.assertTrue(any("Close protein gap" in (t or "") for t in texts), msg=texts)


class PulseChromeAssayTests(unittest.TestCase):
    def test_world_blank_without_meridian_packet(self) -> None:
        world = build_world({"regime": {"available": False}, "implications": {"available": False}})
        self.assertEqual(world["line"], "")
        self.assertTrue(world["blank"])
        self.assertFalse(world["live"])
        self.assertFalse(world["embed"])
        self.assertIn(":8795", world["url"])

    def test_world_line_when_meridian_live(self) -> None:
        world = build_world(
            {
                "sources": {"packet_exists": True},
                "regime": {"available": True, "primary_label": "risk-off"},
                "implications": {"available": True, "top": [{"action": "Hold dry powder"}]},
            }
        )
        self.assertTrue(world["live"])
        self.assertIn("risk-off", world["line"])
        self.assertIn("Hold dry powder", world["line"])

    def test_dock_only_allocator_and_workflow(self) -> None:
        dock = build_dock(
            [
                {"id": "holistic", "url": "http://127.0.0.1:8770/"},
                {"id": "workflow", "url": "http://127.0.0.1:8765/"},
                {"id": "finance", "url": "http://127.0.0.1:8000/"},
                {"id": "fitness", "url": "http://127.0.0.1:8787/"},
            ]
        )
        self.assertEqual([d["id"] for d in dock], ["holistic", "workflow"])
        self.assertEqual(dock[0]["port"], 8770)
        self.assertEqual(dock[1]["port"], 8765)

    def test_train_recommendation_not_train_train(self) -> None:
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(session_due=True, session_type="push", train_recommendation="train"),
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        pulse = build_pulse(day_plan=plan, now=NOW)
        texts = [ln.get("text") or "" for ln in pulse["one_liners"]]
        train_lines = [t for t in texts if t.lower().startswith("train")]
        self.assertTrue(train_lines, msg=texts)
        self.assertFalse(any(re.search(r"^train\s+train\b", t, re.I) for t in train_lines))
        self.assertTrue(all(t.startswith("Train:") for t in train_lines))
        self.assertIn("push", train_lines[0])
        self.assertIn(" · ", train_lines[0])
        # no invented single-letter PPL
        self.assertFalse(any(re.search(r"^Train [PplL]\b", t) for t in train_lines))

        bare = build_one_liners(
            {
                "sources": {
                    "fitness": {
                        "train_recommendation": "train",
                        "session_type": None,
                        "stale": False,
                    }
                }
            },
            now=NOW,
        )
        bare_texts = [ln.get("text") or "" for ln in bare]
        self.assertEqual(bare_texts, ["Train: Rest · train"])

    def test_blocked_unknown_without_clock(self) -> None:
        blocked = build_blocked(
            {"gates": [{"id": "body_rest", "title": "Rest", "domain": "fitness", "severity": "block"}]},
            workflow={"signals": {"board": {"blocked": [{"title": "old card"}]}}},
            now=NOW,
        )
        self.assertEqual(blocked["status"], "unknown")
        self.assertEqual(blocked["items"], [])
        self.assertFalse(blocked["timeline"])

    def test_payload_has_pulse_not_week_gates_held(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write(ws / "strategy" / "today.md", "# Today\n- [ ] Ship pulse\n")
            _write(ws / "strategy" / "bets.md", "# Bets\n- **AI**\n")
            _write_json(ws / "ops" / "backlog" / "items.json", {"items": []})
            payload = build_orchestra_payload(ws, probe_ports=False)
            self.assertIn("pulse", payload)
            self.assertIn("world", payload["pulse"])
            self.assertNotIn("week", payload["pulse"])
            self.assertNotIn("held", payload["pulse"])
            self.assertNotIn("chrome", payload)
            self.assertEqual([d["id"] for d in payload["pulse"]["dock"]], ["holistic", "workflow"])

    def test_index_is_thin_pulse_page(self) -> None:
        html = (ORCH / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="sec-now"', html)
        self.assertIn('id="sec-next"', html)
        self.assertIn('id="sec-blocked"', html)
        self.assertIn('id="world-strip"', html)
        self.assertIn("Time Allocator", html)
        self.assertIn("Workflow", html)
        self.assertIn("8795", html)
        self.assertNotIn('id="sec-week"', html)
        self.assertNotIn('id="sec-held"', html)
        self.assertNotIn('id="sec-gates"', html)
        self.assertNotIn("<iframe", html.lower())
        self.assertNotIn('short: "FitDash"', html)
        self.assertNotIn('short: "FCC"', html)
        self.assertNotIn("port: 8000", html)
        self.assertNotIn("port: 8787", html)
        self.assertNotIn("port: 8780", html)
        self.assertNotIn("port: 8792", html)
        self.assertNotIn("Fleet", html)
        self.assertNotIn("IoT", html)


if __name__ == "__main__":
    unittest.main()
