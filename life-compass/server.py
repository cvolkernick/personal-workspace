#!/usr/bin/env python3
"""Private Life Compass dashboard. Financial + health data — never public.

Default bind is 127.0.0.1. No CORS *. No share link. Cache-Control: no-store.

  python3 life-compass/server.py
  python3 life-compass/server.py --host 127.0.0.1 --port 8793 --no-browser
  python3 life-compass/server.py --host 0.0.0.0 --port 8793 --no-browser --local
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain import local_hour, missing_anything, one_thing  # noqa: E402
from engine import build_dashboard, consume_pings, run_sweep  # noqa: E402
from radar import dismiss_candidate, promote_candidate  # noqa: E402
from sensors import collect_snapshot  # noqa: E402
from store import load_state, resolve_state_path, save_state  # noqa: E402

DEFAULT_PORT = 8793
DEFAULT_HOST = "127.0.0.1"
INDEX = PKG / "index.html"

_STATE_PATH: Optional[Path] = None
_WORKSPACE = ROOT


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state() -> dict[str, Any]:
    return load_state(_STATE_PATH)


def _save(state: dict[str, Any]) -> None:
    save_state(state, _STATE_PATH)


class CompassHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PKG), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[life-compass] " + (fmt % args) + "\n")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Intentionally no Access-Control-Allow-Origin.
        super().end_headers()

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        # No CORS. Preflight is 403 so browsers cannot treat this as a public API.
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "life-compass",
                    "private": True,
                    "bind_default": DEFAULT_HOST,
                },
            )
            return

        if path in ("/", "/index.html"):
            raw = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        now = _now()
        state = _state()
        hour = None
        if qs.get("hour"):
            try:
                hour = int(qs["hour"][0])
            except (TypeError, ValueError):
                hour = None

        if path == "/api/state":
            if not state.get("last_sweep_at"):
                # First load: sweep so the page is never empty/unexplained.
                snapshot = collect_snapshot(
                    now=now,
                    workspace=_WORKSPACE,
                    payoff_history=state.get("payoff_history") or {},
                    prepped_event_ids={str(x) for x in (state.get("prepped_event_ids") or [])},
                )
                state, _pings, dashboard = run_sweep(state, snapshot, now=now, hour=hour)
                _save(state)
                self._json(200, dashboard)
                return
            self._json(200, build_dashboard(state, now=now, hour=hour))
            return

        if path == "/api/one-thing":
            self._json(
                200,
                one_thing(
                    state.get("watchlist") or [],
                    now=now,
                    hour=hour if hour is not None else local_hour(now),
                ),
            )
            return

        if path == "/api/missing":
            self._json(200, missing_anything(state.get("watchlist") or [], now=now))
            return

        if path == "/api/pings":
            self._json(200, {"ok": True, "pings": list(state.get("pending_pings") or [])})
            return

        self.send_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()
        now = _now()
        state = _state()

        try:
            if path == "/api/sweep":
                snapshot = body.get("snapshot") if isinstance(body.get("snapshot"), dict) else None
                if snapshot is None:
                    snapshot = collect_snapshot(
                        now=now,
                        workspace=_WORKSPACE,
                        payoff_history=state.get("payoff_history") or {},
                        prepped_event_ids={
                            str(x) for x in (state.get("prepped_event_ids") or [])
                        },
                    )
                state, pings, dashboard = run_sweep(state, snapshot, now=now)
                _save(state)
                dashboard["pings"] = pings
                self._json(200, dashboard)
                return

            if path == "/api/pings/consume":
                state, pings = consume_pings(state, now=now)
                _save(state)
                self._json(200, {"ok": True, "pings": pings, "count": len(pings)})
                return

            if path == "/api/radar/dismiss":
                cid = str(body.get("id") or "")
                if not cid:
                    self._json(400, {"ok": False, "error": "id required"})
                    return
                state = dismiss_candidate(state, cid, now=now)
                _save(state)
                self._json(200, build_dashboard(state, now=now))
                return

            if path == "/api/radar/promote":
                cid = str(body.get("id") or "")
                if not cid:
                    self._json(400, {"ok": False, "error": "id required"})
                    return
                state, target = promote_candidate(state, cid, now=now)
                if target is None:
                    self._json(404, {"ok": False, "error": "candidate not found"})
                    return
                _save(state)
                payload = build_dashboard(state, now=now)
                payload["promoted_target"] = target
                self._json(200, payload)
                return

            if path == "/api/ops/prep":
                eid = str(body.get("event_id") or body.get("id") or "")
                if not eid:
                    self._json(400, {"ok": False, "error": "event_id required"})
                    return
                prepped = list(state.get("prepped_event_ids") or [])
                if eid not in prepped:
                    prepped.append(eid)
                state["prepped_event_ids"] = prepped
                snapshot = state.get("last_snapshot") or collect_snapshot(
                    now=now,
                    workspace=_WORKSPACE,
                    prepped_event_ids=set(prepped),
                )
                # Mark matching trip prepped on the stored snapshot so the next
                # sweep without live calendar still sees it.
                ops = dict(snapshot.get("operations") or {})
                trips = []
                for t in ops.get("turo_trips") or []:
                    if isinstance(t, dict):
                        row = dict(t)
                        if str(row.get("id")) == eid:
                            row["prepped"] = True
                        trips.append(row)
                ops["turo_trips"] = trips
                snapshot = dict(snapshot)
                snapshot["operations"] = ops
                state, _pings, dashboard = run_sweep(state, snapshot, now=now)
                _save(state)
                self._json(200, dashboard)
                return

            self._json(404, {"ok": False, "error": f"unknown route: {path}"})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: Optional[list[str]] = None) -> int:
    global _STATE_PATH
    parser = argparse.ArgumentParser(description="Private Life Compass dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST, help="Default 127.0.0.1 (private)")
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Kept for install_remote.sh compatibility; API is always local.",
    )
    args = parser.parse_args(argv)
    _STATE_PATH = resolve_state_path(args.state)

    # Refuse accidental public bind without an explicit host override that
    # the operator passed. Default remains loopback.
    url = f"http://{args.host}:{args.port}/"
    server = ThreadingHTTPServer((args.host, args.port), CompassHandler)
    print(f"Life Compass (private) → {url}")
    print(f"state → {_STATE_PATH}")
    if not args.no_browser and args.host in ("127.0.0.1", "localhost"):
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
