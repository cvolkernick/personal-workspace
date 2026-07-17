#!/usr/bin/env python3
"""Local dashboard for the time allocator.

  GET  /api/health
  GET  /api/state          — items + totals
  POST /api/seed           — load starter list
  POST /api/add            — {title, kind?, priority?, minutes?, id?}
  POST /api/remove         — {key}
  POST /api/allocate       — {total}
  POST /api/set            — {key, priority?, minutes?}

Usage:
  python3 holistic/server.py
  python3 holistic/server.py --port 8770 --no-browser
  python3 holistic/server.py --data /tmp/tasks.json
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
    allocate_total,
    list_items,
    remove_item,
    seed_starter,
    set_minutes,
    set_priority,
)
from holistic.time_allocator.store import (  # noqa: E402
    load_state,
    resolve_data_path,
    save_state,
)

HOLISTIC_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8770

# Set by main() before serving
_DATA_PATH: Path | None = None


def _data() -> Path | None:
    return _DATA_PATH


def state_payload() -> dict[str, Any]:
    path = resolve_data_path(_data())
    state = load_state(_data())
    items = list_items(state)
    total = sum(int(it.get("minutes") or 0) for it in items)
    return {
        "ok": True,
        "path": str(path),
        "items": items,
        "count": len(items),
        "total_minutes": total,
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
        if path == "/api/state":
            self._json(200, state_payload())
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()

        try:
            if path == "/api/seed":
                state = seed_starter(load_state(_data()))
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
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/remove":
                key = str(body.get("key") or "").strip()
                if not key:
                    self._json(400, {"ok": False, "error": "key is required"})
                    return
                state = remove_item(load_state(_data()), key)
                save_state(state, _data())
                self._json(200, state_payload())
                return

            if path == "/api/allocate":
                if body.get("total") is None:
                    self._json(400, {"ok": False, "error": "total is required"})
                    return
                total = int(body["total"])
                state = allocate_total(load_state(_data()), total)
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
                save_state(state, _data())
                self._json(200, state_payload())
                return

            self._json(404, {"ok": False, "error": f"unknown route: {path}"})
        except KeyError as e:
            self._json(404, {"ok": False, "error": str(e)})
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001 — surface to UI
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: list[str] | None = None) -> int:
    global _DATA_PATH
    parser = argparse.ArgumentParser(description="Time allocator local dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Tasks JSON path (default: holistic/data/tasks.json)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser tab",
    )
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
