#!/usr/bin/env python3
"""Local dashboard for the time allocator.

  GET  /api/health
  GET  /api/state
  GET  /api/health-status     — Google / local metrics availability
  POST /api/seed
  POST /api/add | remove | allocate | set
  POST /api/targets/add | remove | update
  POST /api/log
  POST /api/progress          — log time on a next-action {key, minutes?, complete?}
  POST /api/plan
  POST /api/health/sync       — import sleep into logs
  POST /api/recommend         — optional body {limit}
  GET  /api/ask/status        — Grok auth ready?
  POST /api/ask               — {question} about current time allocations
  GET  /api/calendar/status   — Google Calendar OAuth + last sync
  POST /api/calendar/sync     — pull busy events into plan

Usage:
  python3 holistic/server.py
  python3 holistic/server.py --port 8770 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.domain import (  # noqa: E402
    add_item,
    add_log,
    add_target,
    allocate_total,
    apply_plan,
    build_rolling_plan,
    kpi_status,
    list_items,
    list_targets,
    log_action_progress,
    remove_item,
    remove_target,
    seed_starter,
    set_minutes,
    set_priority,
    update_target,
)
from holistic.time_allocator.activity_review import (  # noqa: E402
    pending_walk_candidates,
    review_walk,
    sync_walk_candidates,
)
from holistic.time_allocator.health_sync import (  # noqa: E402
    health_credentials_status,
    sync_sleep_logs,
)
from holistic.time_allocator.actual import (  # noqa: E402
    allocation_delta,
    build_actual_allocation,
)
from holistic.time_allocator.lyft_duty import (  # noqa: E402
    lyft_duty_status,
    set_lyft_driven,
)
from holistic.time_allocator.grok_ask import (  # noqa: E402
    GrokAskError,
    ask_about_time,
    auth_status as grok_auth_status,
)
from holistic.time_allocator.recommend import recommend_next  # noqa: E402
from holistic.time_allocator.sleep_battery import sleep_battery_for_state  # noqa: E402
from holistic.time_allocator.calendar_sync import (  # noqa: E402
    calendar_credentials_status,
    calendar_summary_for_state,
    sync_calendar,
)
from holistic.time_allocator.store import (  # noqa: E402
    load_state,
    resolve_data_path,
    save_state,
)

HOLISTIC_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8770

_DATA_PATH: Path | None = None


def _data() -> Path | None:
    return _DATA_PATH


def state_payload(*, refresh_walks: bool = False) -> dict[str, Any]:
    path = resolve_data_path(_data())
    state = load_state(_data())
    walk_sync_meta: dict[str, Any] | None = None
    if refresh_walks:
        try:
            state, walk_sync_meta = sync_walk_candidates(state, days=3)
            save_state(state, _data())
        except Exception as e:  # noqa: BLE001
            walk_sync_meta = {"ok": False, "error": str(e)}
    items = list_items(state)
    targets = list_targets(state)
    total = sum(int(it.get("minutes") or 0) for it in items)
    # Remaining work plan (drives next actions)
    plan = state.get("plan") or build_rolling_plan(state)
    # Full recommended split (ignores progress) for planned pie
    plan_recommended = build_rolling_plan(state, ignore_progress=True)
    actual = build_actual_allocation(state)
    delta = allocation_delta(plan_recommended, actual)
    suggestions = recommend_next(state, plan=plan)
    sleep_battery = sleep_battery_for_state(state)
    walk_candidates = pending_walk_candidates(state, days=2)
    lyft_tgt = next((t for t in targets if str(t.get("id")) == "lyft"), None)
    lyft_duty = lyft_duty_status(state, target=lyft_tgt)
    calendar = calendar_summary_for_state(state)
    payload = {
        "ok": True,
        "path": str(path),
        "items": items,
        "targets": targets,
        "logs": list(state.get("logs") or []),
        "sleep_intervals": list(state.get("sleep_intervals") or []),
        "activity_reviews": list(state.get("activity_reviews") or []),
        "calendar_events": list(state.get("calendar_events") or []),
        "calendar": calendar,
        "walk_candidates": walk_candidates,
        "lyft_duty": lyft_duty,
        "count": len(items),
        "total_minutes": total,
        "kpi_status": kpi_status(state),
        "plan": plan,
        "plan_recommended": plan_recommended,
        "actual": actual,
        "allocation_delta": delta,
        "suggestions": suggestions,
        "sleep_battery": sleep_battery,
        "health": health_credentials_status(),
    }
    if walk_sync_meta is not None:
        payload["walk_sync"] = walk_sync_meta
    return payload


class TimeAllocatorHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HOLISTIC_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[time-allocator] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "time-allocator",
                    "data": str(resolve_data_path(_data())),
                },
            )
            return
        if path == "/api/health-status":
            self._json(200, {"ok": True, **health_credentials_status()})
            return
        if path == "/api/state":
            qs = urlparse(self.path).query
            refresh = "refresh_walks=1" in qs or "refresh_walks=true" in qs
            try:
                self._json(200, state_payload(refresh_walks=refresh))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[time-allocator] /api/state failed: {e}\n")
                self._json(500, {"ok": False, "error": f"state failed: {e}"})
            return
        if path == "/api/ask/status":
            try:
                self._json(200, {"ok": True, **grok_auth_status()})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/calendar/status":
            try:
                auth = calendar_credentials_status()
                state = load_state(_data())
                summary = calendar_summary_for_state(state)
                # Keep transport ok=True even when calendar auth is not ready
                body = {**summary, "auth": auth, "ok": True, "calendar_ready": bool(auth.get("ok"))}
                self._json(200, body)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()

        try:
            if path == "/api/seed":
                state = seed_starter(load_state(_data()), personal=True)
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/add":
                title = str(body.get("title") or "").strip()
                if not title:
                    self._json(400, {"ok": False, "error": "title is required"})
                    return
                state = add_item(
                    load_state(_data()),
                    title,
                    kind=str(body.get("kind") or "task"),
                    priority=int(body.get("priority") if body.get("priority") is not None else 1),
                    minutes=int(body.get("minutes") if body.get("minutes") is not None else 0),
                    item_id=(str(body["id"]) if body.get("id") else None),
                )
                if body.get("replan", True):
                    state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/remove":
                key = str(body.get("key") or "").strip()
                if not key:
                    self._json(400, {"ok": False, "error": "key is required"})
                    return
                state = remove_item(load_state(_data()), key)
                if body.get("replan", True):
                    state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/allocate":
                if body.get("total") is None:
                    self._json(400, {"ok": False, "error": "total is required"})
                    return
                state = allocate_total(load_state(_data()), int(body["total"]))
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/set":
                key = str(body.get("key") or "").strip()
                if not key:
                    self._json(400, {"ok": False, "error": "key is required"})
                    return
                state = load_state(_data())
                if body.get("priority") is not None:
                    state = set_priority(state, key, int(body["priority"]))
                if body.get("minutes") is not None:
                    state = set_minutes(state, key, int(body["minutes"]))
                if body.get("priority") is None and body.get("minutes") is None:
                    self._json(400, {"ok": False, "error": "priority and/or minutes required"})
                    return
                if body.get("replan", True):
                    state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/targets/add":
                title = str(body.get("title") or "").strip()
                kind = str(body.get("kind") or "").strip()
                if not title or not kind:
                    self._json(400, {"ok": False, "error": "title and kind required"})
                    return
                fields = {
                    k: body[k]
                    for k in (
                        "minutes",
                        "minutes_min",
                        "minutes_max",
                        "session_minutes",
                        "min_days",
                        "max_days",
                        "window_days",
                        "unit",
                        "target",
                        "reserve_minutes",
                        "sessions_hint",
                        "notes",
                    )
                    if k in body
                }
                state = add_target(
                    load_state(_data()),
                    title,
                    kind=kind,
                    priority=int(body.get("priority") if body.get("priority") is not None else 5),
                    target_id=(str(body["id"]) if body.get("id") else None),
                    **fields,
                )
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/targets/remove":
                key = str(body.get("key") or "").strip()
                if not key:
                    self._json(400, {"ok": False, "error": "key is required"})
                    return
                state = remove_target(load_state(_data()), key)
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/targets/update":
                key = str(body.get("key") or "").strip()
                if not key:
                    self._json(400, {"ok": False, "error": "key is required"})
                    return
                patch = {k: v for k, v in body.items() if k != "key"}
                state = update_target(load_state(_data()), key, patch)
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/log":
                key = str(body.get("target_id") or body.get("key") or "").strip()
                if not key or body.get("value") is None:
                    self._json(400, {"ok": False, "error": "target_id and value required"})
                    return
                state = add_log(
                    load_state(_data()),
                    key,
                    float(body["value"]),
                    on=body.get("date"),
                    note=str(body.get("note") or ""),
                    accumulate=bool(body.get("accumulate", False)),
                )
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/progress":
                key = str(body.get("key") or body.get("id") or "").strip()
                if not key:
                    self._json(400, {"ok": False, "error": "key is required"})
                    return
                complete = bool(body.get("complete", False))
                minutes = body.get("minutes")
                if minutes is not None:
                    minutes = float(minutes)
                state = log_action_progress(
                    load_state(_data()),
                    key,
                    minutes=minutes,
                    complete=complete,
                    note=str(body.get("note") or ""),
                    on=body.get("date"),
                )
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/plan":
                state = load_state(_data())
                plan = build_rolling_plan(state)
                if body.get("apply", True):
                    state = apply_plan(state, plan)
                    save_state(state, _data())
                payload = state_payload()
                if not body.get("apply", True):
                    payload["plan"] = plan
                    payload["suggestions"] = recommend_next(load_state(_data()), plan=plan)
                self._json(200, payload)
                return

            if path == "/api/recommend":
                state = load_state(_data())
                plan = state.get("plan") or build_rolling_plan(state)
                limit = int(body.get("limit") if body.get("limit") is not None else 5)
                self._json(
                    200,
                    {
                        "ok": True,
                        "suggestions": recommend_next(state, plan=plan, limit=limit),
                    },
                )
                return

            if path == "/api/health/sync":
                days = int(body.get("days") if body.get("days") is not None else 14)
                overwrite = bool(body.get("overwrite", True))
                state, meta = sync_sleep_logs(
                    load_state(_data()), days=days, overwrite=overwrite
                )
                # Also refresh walk candidates when syncing health
                state, walk_meta = sync_walk_candidates(state, days=3)
                meta["walks"] = walk_meta
                # Best-effort calendar pull so allocation stays current
                try:
                    state, cal_meta = sync_calendar(state, days_ahead=2, days_back=0)
                    meta["calendar"] = cal_meta
                except Exception as e:  # noqa: BLE001
                    meta["calendar"] = {"ok": False, "error": str(e)}
                if meta.get("imported") or walk_meta.get("new_pending") or (
                    (meta.get("calendar") or {}).get("ok")
                ):
                    state = apply_plan(state)
                save_state(state, _data())
                payload = state_payload()
                payload["sync"] = meta
                if not meta.get("ok") and not meta.get("imported") and not walk_meta.get("fetched"):
                    self._json(200, payload)
                    return
                self._json(200, payload)
                return

            if path == "/api/calendar/sync":
                days_ahead = int(body.get("days") if body.get("days") is not None else 2)
                days_back = int(body.get("days_back") if body.get("days_back") is not None else 0)
                cals = body.get("calendar_ids")
                if isinstance(cals, str) and cals.strip():
                    cals = [c.strip() for c in cals.split(",") if c.strip()]
                elif not isinstance(cals, list):
                    cals = None
                state, meta = sync_calendar(
                    load_state(_data()),
                    days_ahead=days_ahead,
                    days_back=days_back,
                    calendar_ids=cals,
                )
                if meta.get("ok") or state.get("calendar_events"):
                    state = apply_plan(state)
                save_state(state, _data())
                payload = state_payload()
                payload["calendar_sync"] = meta
                if not meta.get("ok"):
                    # Still return state so UI can show cached events + error
                    payload["ok"] = True
                    payload["error"] = meta.get("error")
                self._json(200, payload)
                return

            if path == "/api/activity/sync":
                days = int(body.get("days") if body.get("days") is not None else 3)
                state, meta = sync_walk_candidates(load_state(_data()), days=days)
                save_state(state, _data())
                payload = state_payload()
                payload["walk_sync"] = meta
                self._json(200, payload)
                return

            if path == "/api/activity/review":
                rid = str(body.get("id") or body.get("review_id") or "").strip()
                decision = str(body.get("decision") or "").strip()
                if not rid or not decision:
                    self._json(400, {"ok": False, "error": "id and decision required"})
                    return
                state = review_walk(
                    load_state(_data()),
                    rid,
                    decision=decision,
                    note=str(body.get("note") or ""),
                )
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/ask":
                question = str(body.get("question") or body.get("q") or "").strip()
                if not question:
                    self._json(400, {"ok": False, "error": "question is required"})
                    return
                try:
                    # Fresh snapshot so Grok sees current allocations
                    snap = state_payload()
                    result = ask_about_time(question, snap)
                    self._json(200, result)
                except GrokAskError as e:
                    code = e.status if e.status in (400, 401, 403, 429) else 502
                    self._json(
                        code,
                        {
                            "ok": False,
                            "error": str(e),
                            "detail": (e.body or "")[:800],
                        },
                    )
                return

            if path == "/api/lyft/duty":
                # Set driven minutes (or hours) in the current 12h driver-mode block
                from holistic.time_allocator.domain import get_target as _get_target

                state = load_state(_data())
                lyft_tgt = _get_target(state, "lyft")
                cap = int((lyft_tgt or {}).get("drive_cap_minutes") or 12 * 60)
                brk = int((lyft_tgt or {}).get("break_minutes") or 6 * 60)
                if body.get("driven_minutes") is not None:
                    driven = float(body["driven_minutes"])
                elif body.get("driven_hours") is not None:
                    driven = float(body["driven_hours"]) * 60.0
                elif body.get("hours") is not None:
                    driven = float(body["hours"]) * 60.0
                else:
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": "driven_minutes or driven_hours required",
                        },
                    )
                    return
                state = set_lyft_driven(
                    state,
                    driven,
                    note=str(body.get("note") or ""),
                    drive_cap_minutes=cap,
                    break_minutes=brk,
                )
                state = apply_plan(state)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            self._json(404, {"ok": False, "error": f"unknown route: {path}"})
        except KeyError as e:
            self._json(404, {"ok": False, "error": str(e)})
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: list[str] | None = None) -> int:
    global _DATA_PATH
    parser = argparse.ArgumentParser(description="Time allocator local dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    _DATA_PATH = args.data.resolve() if args.data else None

    server = ThreadingHTTPServer((args.host, args.port), TimeAllocatorHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Time allocator dashboard → {url}")
    print(f"data → {resolve_data_path(_DATA_PATH)}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
