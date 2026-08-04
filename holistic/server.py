#!/usr/bin/env python3
"""Local dashboard for the time allocator.

  GET  /api/health
  GET  /api/state
  GET  /api/health-status     — Google / local metrics availability
  POST /api/seed
  POST /api/add | remove | allocate | set
  POST /api/targets/add | remove | update
  POST /api/log
  POST /api/plan
  POST /api/health/sync       — import sleep into logs
  POST /api/recommend         — optional body {limit}

Usage:
  python3 holistic/server.py
  python3 holistic/server.py --port 8770 --no-browser
  python3 holistic/server.py --backend http://pi-host:8770
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote_backend import add_backend_args, resolve_backend, try_proxy_api  # noqa: E402
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
    remove_item,
    remove_target,
    seed_starter,
    set_minutes,
    set_priority,
    update_target,
)
from holistic.time_allocator.health_sync import (  # noqa: E402
    health_credentials_status,
    sync_sleep_logs,
)
from holistic.time_allocator.recommend import recommend_next  # noqa: E402
from holistic.time_allocator.store import (  # noqa: E402
    load_state,
    resolve_data_path,
    save_state,
)

HOLISTIC_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8770
DEFAULT_BACKEND_CONFIG = HOLISTIC_DIR / "backend.json"

_DATA_PATH: Path | None = None
_BACKEND_URL: Optional[str] = None
_BACKEND_LABEL: str = ""
_FRONTEND: str = ""


def _data() -> Path | None:
    return _DATA_PATH


def state_payload() -> dict[str, Any]:
    path = resolve_data_path(_data())
    state = load_state(_data())
    items = list_items(state)
    targets = list_targets(state)
    total = sum(int(it.get("minutes") or 0) for it in items)
    plan = state.get("plan") or build_rolling_plan(state)
    suggestions = recommend_next(state, plan=plan)
    return {
        "ok": True,
        "path": str(path),
        "items": items,
        "targets": targets,
        "logs": list(state.get("logs") or []),
        "count": len(items),
        "total_minutes": total,
        "kpi_status": kpi_status(state),
        "plan": plan,
        "suggestions": suggestions,
        "health": health_credentials_status(),
    }


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
        if try_proxy_api(
            self,
            _BACKEND_URL,
            method="GET",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
        ):
            return
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "time-allocator",
                    "data": str(resolve_data_path(_data())),
                    "proxy": False,
                    "backend": None,
                },
            )
            return
        if path == "/api/health-status":
            self._json(200, {"ok": True, **health_credentials_status()})
            return
        if path == "/api/state":
            self._json(200, state_payload())
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if try_proxy_api(
            self,
            _BACKEND_URL,
            method="POST",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
        ):
            return
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
                if meta.get("imported"):
                    state = apply_plan(state)
                    save_state(state, _data())
                payload = state_payload()
                payload["sync"] = meta
                if not meta.get("ok") and not meta.get("imported"):
                    self._json(200, payload)  # still return state; UI shows sync.error
                    return
                self._json(200, payload)
                return

            self._json(404, {"ok": False, "error": f"unknown route: {path}"})
        except KeyError as e:
            self._json(404, {"ok": False, "error": str(e)})
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: list[str] | None = None) -> int:
    global _DATA_PATH, _BACKEND_URL, _BACKEND_LABEL, _FRONTEND
    parser = argparse.ArgumentParser(description="Time allocator local dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--no-browser", action="store_true")
    add_backend_args(parser)
    args = parser.parse_args(argv)
    _DATA_PATH = args.data.resolve() if args.data else None
    _BACKEND_URL, _BACKEND_LABEL = resolve_backend(
        local=bool(args.local),
        backend=args.backend,
        config_path=DEFAULT_BACKEND_CONFIG,
    )
    url = f"http://{args.host}:{args.port}/"
    _FRONTEND = url

    server = ThreadingHTTPServer((args.host, args.port), TimeAllocatorHandler)
    print(f"Time allocator dashboard → {url}")
    if _BACKEND_URL:
        print(f"backend  → {_BACKEND_URL} ({_BACKEND_LABEL or 'remote'}) [proxy mode]")
    else:
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
