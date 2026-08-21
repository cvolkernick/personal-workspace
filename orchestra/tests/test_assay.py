"""Assay AC for the thin Orchestra pulse cut.

NOW/NEXT come from day_plan.next3 (personal next moves + same-day
actionable Time Allocator blocks). Recommendations, today.md examples,
unfilled / “user to fill” / empty creative-slot placeholders, Buzz-board
pull/ready jargon, July-17 tasks.json, stale backlog, sleep-reserve,
fill_remainder / Lyft, other-day windows, and quests without a GT id
stay off the page.

Chris-actionable only: no Cadence / Ready-count team status, no one-liner
that restates a NOW/NEXT title, no “nothing to do”. ADVICE is COS-only
and blank when there is no real call.
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

from advice import build_advice, load_advice_packet  # noqa: E402
from collectors import collect_holistic, collect_strategy  # noqa: E402
from complete import complete_item, is_checkable, writable_source  # noqa: E402
from day_plan import compose_day_plan, is_ta_actionable_block  # noqa: E402
from payload import build_orchestra_payload  # noqa: E402
from priorities import synthesize_priorities  # noqa: E402
from pulse import (  # noqa: E402
    build_blocked,
    build_dock,
    build_one_liners,
    build_pulse,
    build_world,
    is_example_today_line,
    keep_action_item,
    keep_blocked_item,
    next_api_payload,
    now_api_payload,
    now_from_next3,
    one_liner_duplicates_next,
    personal_next3,
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
            "day_actions": [
                {"kind": "ltv_check", "title": "Confirm Morpho LTV"},
                {"kind": "fill_manual", "title": "Fill missing Coinbase fields"},
                {"kind": "card_float", "title": "Top up card float"},
            ]
            if actions is None
            else actions,
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
                    {"id": "fit-session", "title": "Train push", "domain": "fitness", "kind": "train"}
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
        blob = json.dumps(plan["next3"]).lower()
        self.assertNotIn("pull candidate", blob)
        self.assertNotIn("ready supply", blob)
        self.assertNotIn("free agent", blob)
        self.assertTrue(
            "train" in kinds or "train" in titles,
            msg=plan["next3"],
        )
        self.assertTrue(
            any((i.get("title") or "").startswith("Train ") for i in plan["next3"]),
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
                {"id": "fit-session", "title": "Train push", "domain": "fitness", "kind": "train"},
            ]
            first = now_from_next3(leaked)
            self.assertIsNotNone(first)
            self.assertEqual(first.get("title"), "Train push")
            pulse = build_pulse(day_plan={"next3": leaked}, now=NOW)
            self.assertEqual((pulse.get("now") or {}).get("title"), "Train push")
            next_titles = [x.get("title") for x in pulse.get("next") or []]
            self.assertEqual(next_titles, ["Train push"])
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


class SeatFacingNextAssayTests(unittest.TestCase):
    LIVE_PULL = {
        "id": "wf-ready-110",
        "title": (
            "Pull candidate #110: Geo+time flight plan — "
            "living day logistics layer (orchestration)"
        ),
        "why": "Ready supply + free agent",
        "domain": "workflow",
        "kind": "ready",
    }

    def test_pull_candidate_ready_supply_style_item_not_now_or_next(self) -> None:
        """Exact live :8790 row must not become NOW/NEXT or /api/next."""
        seat = {"id": "fit-session", "title": "Train push", "domain": "fitness", "kind": "train"}
        dirty = [self.LIVE_PULL, seat]
        self.assertFalse(keep_action_item(self.LIVE_PULL))
        self.assertEqual([x["title"] for x in personal_next3(dirty)], ["Train push"])
        first = now_from_next3(dirty)
        self.assertIsNotNone(first)
        self.assertEqual(first.get("title"), "Train push")
        pulse = build_pulse(day_plan={"next3": dirty}, now=NOW)
        self.assertEqual((pulse.get("now") or {}).get("title"), "Train push")
        next_titles = [x.get("title") for x in pulse.get("next") or []]
        self.assertEqual(next_titles, ["Train push"])
        blob = json.dumps({"now": pulse.get("now"), "next": pulse.get("next")}).lower()
        self.assertNotIn("pull candidate", blob)
        self.assertNotIn("ready supply", blob)
        self.assertNotIn("#110", blob)
        api = next_api_payload({"day_plan": {"next3": dirty}})
        self.assertEqual(api["next"], personal_next3(dirty))
        self.assertEqual(api["next3"], api["next"])
        self.assertNotIn("pull candidate", json.dumps(api).lower())
        self.assertNotIn("ready supply", json.dumps(api).lower())
        code, now_body = now_api_payload({"day_plan": {"next3": dirty}})
        self.assertEqual(code, 200)
        self.assertEqual((now_body or {}).get("now", {}).get("title"), "Train push")

    def test_ready_pull_and_epic_titles_omitted_from_now_next(self) -> None:
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(
                    ready_count=2,
                    free_agent_count=2,
                    ready_top=[
                        {
                            "number": 110,
                            "title": "Geo+time flight plan — living day logistics layer (orchestration)",
                        }
                    ],
                ),
                _fit(session_due=True, session_type="push", train_recommendation="train"),
                _finance((NOW - timedelta(hours=1)).isoformat()),
            ],
            now=NOW,
        )
        blob = json.dumps(plan["next3"]).lower()
        self.assertNotIn("pull candidate", blob)
        self.assertNotIn("ready supply", blob)
        self.assertNotIn("free agent", blob)
        self.assertNotIn("geo+time", blob)
        self.assertNotIn("#110", blob)
        self.assertFalse(any(i.get("kind") == "ready" for i in plan["next3"]))
        pulse = build_pulse(day_plan=plan, now=NOW)
        now_title = ((pulse.get("now") or {}).get("title") or "").lower()
        next_blob = json.dumps(pulse.get("next") or []).lower()
        self.assertNotIn("pull candidate", now_title)
        self.assertNotIn("geo+time", now_title)
        self.assertNotIn("pull candidate", next_blob)
        self.assertNotIn("ready supply", next_blob)

    def test_fitness_titles_are_human_actions(self) -> None:
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(
                    session_due=True,
                    session_type="push",
                    train_recommendation="train",
                    protein_gap_band="watch",
                    protein_remaining_g=72,
                ),
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        titles = [i.get("title") or "" for i in plan["next3"]]
        self.assertIn("Train push", titles)
        self.assertTrue(any(t == "Watch protein · ~72g left" for t in titles), msg=titles)
        self.assertFalse(any("train session" in t.lower() for t in titles))
        self.assertFalse(any("watch protein remaining" in t.lower() for t in titles))
        self.assertFalse(any("band=watch" in (i.get("why") or "") for i in plan["next3"]))

    def test_cadence_one_liner_never_emitted(self) -> None:
        """Board Ready/free is team status — not a Chris action. Do not replace."""
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=2, free_agent_count=2),
                _fit(session_due=False, train_recommendation="rest"),
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        pulse = build_pulse(day_plan=plan, now=NOW)
        texts = [ln.get("text") or "" for ln in pulse["one_liners"]]
        blob = " ".join(texts).lower()
        self.assertFalse(any((ln.get("id") == "cadence") for ln in pulse["one_liners"]))
        self.assertNotIn("cadence", blob)
        self.assertNotIn("ready ·", blob)
        self.assertNotIn("free agent", blob)
        self.assertNotIn("pull candidate", blob)
        self.assertFalse(any("nothing to do" in t.lower() for t in texts))
        live = build_one_liners(
            {
                "next3": [],
                "sources": {
                    "workflow": {
                        "stale": False,
                        "fetch_ok": True,
                        "ready_count": 1,
                        "free_agent_count": 4,
                    }
                },
            },
            now=NOW,
        )
        live_blob = json.dumps(live).lower()
        self.assertNotIn("cadence", live_blob)
        self.assertNotIn("1 ready", live_blob)
        self.assertNotIn("4 free", live_blob)
        unknown = build_one_liners(
            {"sources": {"workflow": {"stale": True, "fetch_ok": False}}},
            now=NOW,
        )
        self.assertFalse(any((ln.get("id") == "cadence") for ln in unknown))
        missing = build_one_liners(
            {"sources": {"workflow": {"stale": False, "fetch_ok": True}}},
            now=NOW,
        )
        self.assertFalse(any((ln.get("id") == "cadence") for ln in missing))

    def test_next3_chronological_when_time_exists(self) -> None:
        later = (NOW + timedelta(hours=3)).isoformat()
        earlier = (NOW + timedelta(hours=1)).isoformat()
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                {
                    "id": "fitness",
                    "available": True,
                    "url": "http://127.0.0.1:8787/",
                    "signals": {
                        "day": {
                            "as_of": NOW.isoformat(),
                            "session_due": True,
                            "session_type": "push",
                            "train_recommendation": "train",
                            "recovery_score": 70,
                            "protein_gap_band": "watch",
                            "protein_remaining_g": 72,
                            "protein_as_of": NOW.isoformat(),
                            "day_actions": [],
                        }
                    },
                },
                _finance(
                    (NOW - timedelta(hours=1)).isoformat(),
                    actions=[
                        {
                            "kind": "ltv_check",
                            "title": "Confirm Morpho LTV",
                            "start": later,
                        }
                    ],
                ),
            ],
            now=NOW,
        )
        # Inject timed protein vs later LTV via compose output: fitness has no start,
        # finance LTV has start=later. Untimed train/protein effective-now should
        # precede the later LTV row when both are present.
        titles = [i.get("title") for i in plan["next3"]]
        if "Confirm Morpho LTV" in titles and "Train push" in titles:
            self.assertLess(titles.index("Train push"), titles.index("Confirm Morpho LTV"))

        # Direct sort: earlier timed action before later
        from day_plan import _next_sort_key

        a = {"title": "Walk Duchess", "start": later, "kind": "fixed", "severity": "info"}
        b = {"title": "Watch protein · ~72g left", "start": earlier, "kind": "protein", "severity": "info"}
        ordered = sorted([a, b], key=lambda x: _next_sort_key(x, now=NOW))
        self.assertEqual([x["title"] for x in ordered], [
            "Watch protein · ~72g left",
            "Walk Duchess",
        ])


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
        now_title = ((pulse.get("now") or {}).get("title") or "")
        next_titles = [x.get("title") or "" for x in pulse.get("next") or []]
        self.assertTrue(
            now_title.startswith("Train ") or any(t.startswith("Train ") for t in next_titles),
            msg=(now_title, next_titles),
        )
        texts = [ln.get("text") or "" for ln in pulse["one_liners"]]
        train_lines = [t for t in texts if t.lower().startswith("train")]
        # Train is already NOW/NEXT — do not repeat it as a one-liner.
        self.assertFalse(train_lines, msg=texts)

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
        self.assertFalse(any(re.search(r"^train\s+train\b", t, re.I) for t in bare_texts))
        # no invented single-letter PPL
        self.assertFalse(any(re.search(r"^Train [PplL]\b", t) for t in bare_texts))

        kept = build_one_liners(
            {
                "next3": [],
                "sources": {
                    "fitness": {
                        "train_recommendation": "train",
                        "session_type": "push",
                        "recovery_label": "Ready",
                        "stale": False,
                    }
                },
            },
            now=NOW,
        )
        kept_train = [
            ln.get("text") or ""
            for ln in kept
            if (ln.get("text") or "").startswith("Train:")
        ]
        self.assertEqual(kept_train, ["Train: push · Ready"])
        self.assertIn(" · ", kept_train[0])

    def test_protein_one_liner_omitted_when_next_already_has_protein(self) -> None:
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(
                    session_due=False,
                    train_recommendation="rest",
                    protein_gap_band="watch",
                    protein_remaining_g=72,
                ),
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        pulse = build_pulse(day_plan=plan, now=NOW)
        next_titles = [x.get("title") or "" for x in pulse.get("next") or []]
        self.assertTrue(
            any("protein" in t.lower() for t in next_titles),
            msg=next_titles,
        )
        texts = [ln.get("text") or "" for ln in pulse["one_liners"]]
        self.assertFalse(
            any("protein" in t.lower() for t in texts),
            msg=texts,
        )
        self.assertTrue(
            one_liner_duplicates_next(
                "Protein watch · remaining≈72g",
                [{"title": "Watch protein · ~72g left", "kind": "protein"}],
            )
        )

    def test_nothing_to_do_and_team_status_stay_off(self) -> None:
        self.assertFalse(
            keep_action_item({"id": "idle", "title": "Nothing for me to do"})
        )
        self.assertFalse(keep_action_item({"id": "idle2", "title": "nothing to do"}))
        self.assertFalse(keep_action_item({"id": "empty", "title": "Empty"}))
        pulse = build_pulse(
            day_plan={
                "next3": [
                    {"id": "idle", "title": "Nothing for me to do", "kind": "fyi"},
                    {"id": "fit-session", "title": "Train push", "domain": "fitness", "kind": "train"},
                ]
            },
            now=NOW,
        )
        self.assertEqual((pulse.get("now") or {}).get("title"), "Train push")
        blob = json.dumps({"now": pulse.get("now"), "next": pulse.get("next")}).lower()
        self.assertNotIn("nothing for me to do", blob)
        self.assertNotIn("nothing to do", blob)

    def test_blocked_named_gate_is_constraint_not_code(self) -> None:
        plan = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(session_due=False, train_recommendation="rest", recovery_score=30),
                {
                    "id": "finance",
                    "available": True,
                    "url": "http://127.0.0.1:8000/financial-command/",
                    "signals": {
                        "as_of": NOW.isoformat(),
                        "stress_overall": "red",
                        "red_mode": True,
                        "free_cash_gate": "block_new_risk",
                        "freshness": "fresh",
                        "day_actions": [],
                    },
                },
            ],
            now=NOW,
        )
        pulse = build_pulse(day_plan=plan, now=NOW)
        blocked = pulse["blocked"]
        titles = [i.get("title") or "" for i in blocked.get("items") or []]
        title_blob = " ".join(titles)
        self.assertTrue(
            any("new risk" in t.lower() for t in titles)
            or any("rest" in t.lower() for t in titles),
            msg=titles,
        )
        self.assertNotIn("free_cash", title_blob)
        self.assertNotIn("capital_red_mode", title_blob)
        self.assertNotIn("body_rest", title_blob)
        self.assertFalse(
            keep_blocked_item(
                {
                    "id": "workflow_blocked",
                    "title": "2 blocked",
                    "domain": "workflow",
                    "source": "gate",
                    "severity": "warn",
                }
            )
        )
        self.assertTrue(
            keep_blocked_item(
                {
                    "id": "free_cash",
                    "title": "No new risk",
                    "domain": "finance",
                    "source": "gate",
                    "severity": "block",
                }
            )
        )

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
            self.assertIn("advice", payload)
            self.assertTrue((payload.get("advice") or {}).get("blank"))
            self.assertEqual((payload.get("advice") or {}).get("items"), [])
            self.assertTrue((payload["pulse"].get("advice") or {}).get("blank"))

    def test_index_is_thin_pulse_page(self) -> None:
        html = (ORCH / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="sec-now"', html)
        self.assertIn('id="sec-next"', html)
        self.assertIn('id="sec-blocked"', html)
        self.assertIn('id="sec-advice"', html)
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
        # ADVICE seat reads pulse.advice only — not old recs / today.md / Ready / Cadence
        self.assertIn("pulse.advice", html)
        self.assertNotIn("/api/recommendations", html)
        self.assertNotIn("today.md", html)
        self.assertNotIn("Cadence", html)
        self.assertNotIn("Ready ·", html)
        self.assertNotIn("WEEK", html)
        self.assertNotIn("GATES", html)
        self.assertNotIn("HELD", html)


def _hol_timed_blocks() -> dict:
    """Same-day TA plan with actionable + skip rows."""
    earlier = (NOW + timedelta(hours=1)).isoformat()
    later = (NOW + timedelta(hours=3)).isoformat()
    other = (NOW - timedelta(days=1)).isoformat()
    return {
        "id": "holistic",
        "available": True,
        "url": "http://127.0.0.1:8770/",
        "signals": {
            "plan_as_of": NOW.isoformat(),
            "as_of": NOW.isoformat(),
            "targets": ["Sleep", "Walk Duchess"],
            "plan_blocks": [
                {
                    "id": "sleep",
                    "title": "Sleep",
                    "minutes": 480,
                    "role": "reserve",
                    "kind": "rolling_avg",
                },
                {
                    "id": "duchess-walk",
                    "title": "Walk Duchess",
                    "minutes": 45,
                    "role": "fixed",
                    "kind": "daily_duration",
                    "start": earlier,
                },
                {
                    "id": "deep-work",
                    "title": "Deep work",
                    "minutes": 90,
                    "role": "work",
                    "kind": "capacity",
                    "start": later,
                },
                {
                    "id": "lyft",
                    "title": "Lyft driving",
                    "minutes": 855,
                    "role": "fill",
                    "kind": "fill_remainder",
                },
                {
                    "id": "other-day-meet",
                    "title": "Yesterday leftover",
                    "minutes": 30,
                    "role": "work",
                    "start": other,
                },
            ],
        },
    }


class SameDayTaNowNextAssayTests(unittest.TestCase):
    def test_same_day_ta_actionable_blocks_in_now_next_chronological(self) -> None:
        plan = compose_day_plan(
            [
                _hol_timed_blocks(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(session_due=False, train_recommendation="rest"),
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        titles = [i.get("title") for i in plan["next3"]]
        self.assertIn("Walk Duchess", titles, msg=plan["next3"])
        self.assertIn("Deep work", titles, msg=plan["next3"])
        self.assertLess(titles.index("Walk Duchess"), titles.index("Deep work"))
        starts = [i.get("start") for i in plan["next3"] if i.get("title") in ("Walk Duchess", "Deep work")]
        self.assertEqual(len(starts), 2)
        self.assertLess(starts[0], starts[1])

        pulse = build_pulse(day_plan=plan, now=NOW)
        now_title = (pulse.get("now") or {}).get("title")
        next_titles = [x.get("title") for x in pulse.get("next") or []]
        self.assertEqual(now_title, next_titles[0])
        self.assertIn("Walk Duchess", next_titles)
        self.assertIn("Deep work", next_titles)
        self.assertLess(next_titles.index("Walk Duchess"), next_titles.index("Deep work"))
        self.assertEqual(pulse["now"], plan["next3"][0])
        api = next_api_payload({"day_plan": plan})
        self.assertEqual(api["next"], plan["next3"])
        self.assertEqual(api["next3"], api["next"])

    def test_sleep_reserve_fill_remainder_other_day_not_in_now_next(self) -> None:
        hol = _hol_timed_blocks()
        self.assertFalse(
            is_ta_actionable_block(hol["signals"]["plan_blocks"][0], now=NOW)
        )  # sleep
        self.assertTrue(
            is_ta_actionable_block(hol["signals"]["plan_blocks"][1], now=NOW)
        )  # duchess
        self.assertFalse(
            is_ta_actionable_block(
                {"id": "lyft", "title": "Lyft driving", "kind": "fill_remainder", "role": "fill"},
                now=NOW,
            )
        )
        self.assertFalse(
            is_ta_actionable_block(
                {
                    "id": "other-day-meet",
                    "title": "Yesterday leftover",
                    "role": "work",
                    "start": (NOW - timedelta(days=1)).isoformat(),
                },
                now=NOW,
            )
        )
        plan = compose_day_plan(
            [
                hol,
                _wf(ready_count=0, free_agent_count=0),
                _fit(session_due=False, train_recommendation="rest"),
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        blob = json.dumps(plan["next3"]).lower()
        self.assertNotIn("sleep", blob)
        self.assertNotIn("lyft", blob)
        self.assertNotIn("fill_remainder", blob)
        self.assertNotIn("yesterday leftover", blob)
        self.assertNotIn("855", blob)
        pulse = build_pulse(day_plan=plan, now=NOW)
        seat = json.dumps({"now": pulse.get("now"), "next": pulse.get("next")}).lower()
        self.assertNotIn("sleep", seat)
        self.assertNotIn("lyft", seat)
        self.assertNotIn("yesterday leftover", seat)
        self.assertNotIn("week", json.dumps(pulse).lower().split("non_goals")[0])
        self.assertNotIn("gates", [k for k in pulse.keys()])
        self.assertNotIn("held", pulse)


class AdviceSeatAssayTests(unittest.TestCase):
    def test_advice_blank_without_cos_and_ignores_old_streams(self) -> None:
        self.assertTrue(build_advice(None)["blank"])
        self.assertEqual(build_advice(None)["items"], [])
        self.assertTrue(build_advice({"items": [{"title": "Invented course"}]})["blank"])
        self.assertTrue(
            build_advice(
                {
                    "source": "recommendations",
                    "items": [{"title": "Do now (from today’s plan): e.g. review DCA"}],
                }
            )["blank"]
        )
        self.assertTrue(
            build_advice(
                {
                    "source": "grok_cos",
                    "items": [
                        {"title": "Cadence: 1 Ready · 4 free"},
                        {"title": "Pull candidate #110: Geo+time"},
                        {"title": "Do now (from today’s plan): e.g. review DCA"},
                    ],
                }
            )["blank"]
        )

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
            _write_json(
                ws / "ops" / "board" / "day_constraints.json",
                {
                    "as_of": NOW.isoformat(),
                    "fetch_ok": True,
                    "ready_count": 3,
                    "ready_top": [{"number": 92, "title": "day plan"}],
                    "in_progress": [],
                    "pending_review_count": 0,
                    "blocked": [],
                    "wip_overload": False,
                    "free_agent_count": 2,
                    "pipeline_pressure": "ok",
                    "summary": "Ready 3 · 2 free",
                },
            )
            _write_json(ws / "ops" / "backlog" / "items.json", {"items": []})
            self.assertIsNone(load_advice_packet(ws))
            payload = build_orchestra_payload(ws, probe_ports=False)
            advice = payload.get("advice") or {}
            self.assertTrue(advice.get("blank"))
            self.assertEqual(advice.get("items"), [])
            pulse_advice = (payload.get("pulse") or {}).get("advice") or {}
            self.assertTrue(pulse_advice.get("blank"))
            self.assertEqual(pulse_advice.get("items"), [])
            blob = json.dumps(advice).lower() + json.dumps(pulse_advice).lower()
            self.assertNotIn("review dca", blob)
            self.assertNotIn("cadence", blob)
            self.assertNotIn("ready ·", blob)
            self.assertNotIn("pull candidate", blob)
            rec_items = (payload.get("recommendations") or {}).get("items") or []
            # Old stream may still exist on the payload — ADVICE must not copy it.
            if rec_items:
                rec_titles = [i.get("title") or i.get("action") or "" for i in rec_items]
                advice_titles = [i.get("title") for i in (advice.get("items") or [])]
                for t in rec_titles:
                    self.assertNotIn(t, advice_titles)

    def test_advice_cos_packet_is_the_only_feed(self) -> None:
        packet = {
            "source": "grok_cos",
            "as_of": NOW.isoformat(),
            "items": [
                {
                    "title": "Collapse Time into NOW/NEXT, keep :8770 as editor",
                    "check": "No Time Allocator iframe on :8790",
                }
            ],
        }
        seat = build_advice(packet)
        self.assertFalse(seat["blank"])
        self.assertEqual(len(seat["items"]), 1)
        self.assertEqual(
            seat["items"][0]["title"],
            "Collapse Time into NOW/NEXT, keep :8770 as editor",
        )
        pulse = build_pulse(day_plan={"next3": []}, now=NOW, advice=packet)
        self.assertEqual(pulse["advice"]["items"], seat["items"])
        self.assertNotIn("week", pulse)
        self.assertNotIn("held", pulse)

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write(ws / "strategy" / "today.md", "# Today\n- [ ] e.g. review DCA\n")
            _write(ws / "strategy" / "bets.md", "# Bets\n- **AI**\n")
            _write_json(
                ws / "orchestra" / "data" / "advice.json",
                packet,
            )
            payload = build_orchestra_payload(ws, probe_ports=False)
            titles = [i.get("title") for i in (payload.get("advice") or {}).get("items") or []]
            self.assertEqual(
                titles,
                ["Collapse Time into NOW/NEXT, keep :8770 as editor"],
            )
            self.assertFalse((payload.get("advice") or {}).get("blank"))
            self.assertNotIn("review dca", json.dumps(payload.get("advice")).lower())


class NowNextCheckAssayTests(unittest.TestCase):
    """Checkboxes write the source. No Orchestra task store."""

    def test_ta_source_id_is_checkable_composer_ids_are_not(self) -> None:
        plan = compose_day_plan(
            [
                _hol_timed_blocks(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(session_due=False, train_recommendation="rest"),
                _finance((NOW - timedelta(hours=1)).isoformat(), actions=[]),
            ],
            now=NOW,
        )
        by_title = {i.get("title"): i for i in plan["next3"]}
        duchess = by_title.get("Walk Duchess") or {}
        self.assertTrue(duchess.get("checkable"), msg=plan["next3"])
        self.assertEqual(duchess.get("source"), "time_allocator")
        self.assertEqual(duchess.get("source_id"), "duchess-walk")
        self.assertTrue(is_checkable(duchess))

        finance_only = compose_day_plan(
            [
                _hol_today(),
                _wf(ready_count=0, free_agent_count=0),
                _fit(session_due=False, train_recommendation="rest"),
                _finance((NOW - timedelta(hours=1)).isoformat()),
            ],
            now=NOW,
        )
        for item in finance_only["next3"]:
            if item.get("domain") == "finance":
                self.assertFalse(item.get("checkable"), msg=item)
                self.assertIsNone(writable_source(item))

        self.assertFalse(
            is_checkable({"id": "fit-session", "title": "Train push", "kind": "train"})
        )
        self.assertFalse(
            is_checkable({"source": "fitdash", "source_id": "t1"})
        )  # quest without list id
        self.assertTrue(
            is_checkable(
                {"source": "fitdash", "source_id": "t1", "list_id": "L1"}
            )
        )

    def test_index_checkbox_only_on_now_next(self) -> None:
        html = (ORCH / "index.html").read_text(encoding="utf-8")
        self.assertIn("function checkboxHtml", html)
        self.assertIn('itemHtml(item, "", { check: true })', html)
        self.assertIn('itemHtml(item, "blocked")', html)
        self.assertNotIn('itemHtml(item, "blocked",', html)
        self.assertIn("items.map((item) => itemHtml(item))", html)
        self.assertIn("#now-seat, #next-list", html)
        self.assertIn("/api/complete", html)
        self.assertNotIn("orchestra/data/tasks", html)
        self.assertFalse((ORCH / "data" / "tasks.json").exists())
        complete_src = (ORCH / "complete.py").read_text(encoding="utf-8")
        self.assertNotIn("orchestra/data/tasks", complete_src)
        self.assertNotIn("orchestra/data/done", complete_src)

    def test_complete_without_source_id_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write(ws / "strategy" / "today.md", "# Today\n- [ ] Ship\n")
            result = complete_item({"title": "No source"}, workspace=ws)
            self.assertFalse(result.get("accepted"))
            self.assertFalse(result.get("ok"))
            result2 = complete_item({"source": "time_allocator"}, workspace=ws)
            self.assertFalse(result2.get("accepted"))
            self.assertFalse((ws / "orchestra" / "data" / "tasks.json").exists())
            self.assertFalse((ORCH / "data" / "tasks.json").exists())

    def test_complete_ta_source_drops_now(self) -> None:
        from holistic.time_allocator.domain import (  # noqa: WPS433
            apply_plan,
            empty_state,
            seed_starter,
        )
        from holistic.time_allocator.store import save_state  # noqa: WPS433

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _write(ws / "strategy" / "today.md", "# Today\n- [ ] Ship pulse\n")
            _write(ws / "strategy" / "bets.md", "# Bets\n- **AI**\n")
            _write_json(ws / "ops" / "backlog" / "items.json", {"items": []})
            state = apply_plan(seed_starter(empty_state(), personal=True))
            save_state(state, ws / "holistic" / "data" / "tasks.json")
            payload = build_orchestra_payload(ws, probe_ports=False)
            pulse = payload.get("pulse") or {}
            rows = [pulse.get("now")] + list(pulse.get("next") or [])
            ta = next(
                (
                    r
                    for r in rows
                    if isinstance(r, dict)
                    and r.get("source") == "time_allocator"
                    and r.get("source_id")
                ),
                None,
            )
            self.assertIsNotNone(ta, msg=rows)
            result = complete_item(ta, workspace=ws)
            self.assertTrue(result.get("accepted"), msg=result)
            self.assertFalse((ws / "orchestra" / "data" / "tasks.json").exists())
            after = build_orchestra_payload(ws, probe_ports=False)
            after_pulse = after.get("pulse") or {}
            titles = [
                (after_pulse.get("now") or {}).get("title"),
                *[x.get("title") for x in after_pulse.get("next") or []],
            ]
            self.assertNotIn(ta.get("title"), titles, msg=titles)

    def test_fitdash_complete_uses_existing_path_not_local_store(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            with patch(
                "complete._complete_fitdash_quest",
                return_value={"ok": True, "task": {"id": "t1"}},
            ) as fn:
                result = complete_item(
                    {"source": "fitdash", "source_id": "t1", "list_id": "L1"},
                    workspace=ws,
                )
            self.assertTrue(result.get("accepted"), msg=result)
            fn.assert_called_once()
            self.assertFalse((ws / "orchestra" / "data" / "tasks.json").exists())
            with patch(
                "complete._complete_fitdash_quest",
                return_value={"ok": False, "error": "source rejected"},
            ):
                refused = complete_item(
                    {"source": "fitdash", "source_id": "t1", "list_id": "L1"},
                    workspace=ws,
                )
            self.assertFalse(refused.get("accepted"))

    def test_http_complete_without_source_id_does_not_write(self) -> None:
        import urllib.error
        import urllib.request

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), OrchestraHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = httpd.server_address
            req = urllib.request.Request(
                f"http://{host}:{port}/api/complete",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                urllib.request.urlopen(req, timeout=10)
                self.fail("expected HTTP error for missing source id")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 400)
                body = json.loads(e.read().decode("utf-8"))
            self.assertFalse(body.get("accepted"))
            self.assertFalse((ORCH / "data" / "tasks.json").exists())
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
