#!/usr/bin/env python3
"""Horizon visual dashboard server.

  GET  /                 — front page: the Daily Brief (FRONT_PAGE flag; query → /model)
  GET  /model            — macro-model dashboard UI (index.html, unchanged)
  GET  /api/health
  GET  /api/brief        — latest synthesis brief JSON
  GET  /api/world-state  — latest world-state JSON
  GET  /api/dashboard    — combined payload for UI
  POST /api/refresh      — re-run pipeline (body: {"offline": true})
  GET  /daily-brief/*    — Daily Brief newspaper (research/daily_brief; not model output)

Usage:
  python3 research/horizon/server.py
  python3 research/horizon/server.py --port 8795 --no-browser
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HORIZON_DIR = Path(__file__).resolve().parent
ROOT = HORIZON_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.pipeline import run_pipeline  # noqa: E402
from research.horizon.store import (  # noqa: E402
    DEFAULT_DATA_DIR,
    brief_latest_paths,
    load_json,
    load_world_state,
    world_state_latest_path,
)

DEFAULT_PORT = 8795
DEFAULT_BIND_HOST = "127.0.0.1"


def resolve_bind_host(
    explicit: str | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Bind address. Explicit --host wins, else HORIZON_BIND_HOST, else 127.0.0.1.

    HORIZON_BIND_HOST=0.0.0.0 restores the previous all-interfaces listen.
    FCC's /horizon/* proxy always dials 127.0.0.1, which 0.0.0.0 still accepts.
    """
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    env = os.environ if environ is None else environ
    raw = (env.get("HORIZON_BIND_HOST") or "").strip()
    return raw or DEFAULT_BIND_HOST


def build_dashboard_payload(workspace: Path | None = None, data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    workspace = Path(workspace or ROOT)
    state = load_world_state(data_dir)
    brief_path, _ = brief_latest_paths(data_dir)
    brief = None
    if brief_path.is_file():
        try:
            brief = load_json(brief_path)
        except (OSError, json.JSONDecodeError):
            brief = None

    domains = (state or {}).get("domains") or {}
    domain_stats = []
    for d, bucket in domains.items():
        nodes = bucket.get("nodes") or []
        top_score = max((float(n.get("priority_score") or 0) for n in nodes), default=0.0)
        avg_conf = (
            sum(float(n.get("confidence") or 0) for n in nodes) / len(nodes) if nodes else 0.0
        )
        domain_stats.append(
            {
                "id": d,
                "label": bucket.get("label") or d,
                "node_count": len(nodes),
                "top_score": round(top_score, 3),
                "avg_confidence": round(avg_conf, 3),
                "summary": bucket.get("summary") or "",
                "intensity": min(1.0, top_score / 3.5) if top_score else 0.0,
            }
        )
    domain_stats.sort(key=lambda x: x["top_score"], reverse=True)

    return {
        "ok": True,
        "service": "horizon",
        "workspace": str(workspace),
        "data_dir": str(data_dir),
        "has_world_state": state is not None,
        "has_brief": brief is not None,
        "version_id": (brief or state or {}).get("version_id"),
        "generated_at": (brief or {}).get("generated_at") or (state or {}).get("updated_at"),
        "domain_stats": domain_stats,
        "world_state": state,
        "brief": brief,
        "paths": {
            "world_state": str(world_state_latest_path(data_dir)),
            "brief_json": str(brief_path),
        },
    }


def daily_brief_route(path: str):
    """(status, content_type, body) for /daily-brief*, else None.

    Sibling page, not part of the Horizon model: research/daily_brief never
    reads world-state or model briefs. Lazy and error-isolated so a bad
    edition can never take the dashboard down.
    """
    if not (path == "/daily-brief" or path.startswith("/daily-brief/")):
        return None
    try:
        from research.daily_brief.render import route

        return route(path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[horizon] daily-brief error: {exc!r}\n")
        body = (
            "<!doctype html><meta charset=utf-8><title>The Daily Brief</title>"
            "<p>The Daily Brief could not be rendered. Check the Horizon log.</p>"
        )
        return 500, "text/html; charset=utf-8", body


# ---------------------------------------------------------------- front page
# One flag. "daily-brief": / serves the Daily Brief and the dashboard lives at
# /model. "model": / serves the dashboard as before (/model keeps working).
# Only HTML landing routes move; /api/*, /data/* and static assets are untouched.
FRONT_PAGE = "daily-brief"
MODEL_PATH = "/model"

# Dashboard element ids an old "/#<id>" deep link may target (index.html).
DASHBOARD_ANCHORS = (
    "kpis", "tabs", "domain-heat", "overview-top", "overview-strat",
    "panel-overview", "panel-brief", "panel-world", "panel-strategy",
    "panel-watchlist", "panel-graph", "brief-list", "world-domains",
    "strategy-head", "strategy-grid", "watch-body", "graph-svg", "edge-list",
)

# Relative href: resolves under the /horizon prefix (FCC injects <base href="/horizon/">).
_MODEL_LINK = '<a href="model" id="nav-model" title="Horizon macro-model dashboard">Macro Model</a>'

# Old "/#panel-…" links: hashes never reach the server, so hop client-side.
# Prefix-aware: builds the target from location.pathname (/ or /horizon/).
_ANCHOR_HOP = (
    "<script>(function(){var ids=" + json.dumps(list(DASHBOARD_ANCHORS)) + ";"
    "var h=(location.hash||'').slice(1);if(!h||ids.indexOf(h)<0)return;"
    "var p=location.pathname||'';if(p.slice(-1)!=='/')p+='/';"
    "location.replace(p+'model'+location.search+location.hash);})();</script>"
)


def front_page_html(body: str) -> str:
    """Daily Brief HTML for the root: adds the Macro Model link + anchor hop.

    /daily-brief itself is served byte-for-byte from the renderer.
    """
    if '<div class="topbar">' in body:
        body = body.replace('<div class="topbar">', '<div class="topbar">' + _MODEL_LINK, 1)
    else:
        body = body.replace('<div class="paper">', '<div class="paper"><div class="topbar">' + _MODEL_LINK + "</div>", 1)
    if "</head>" in body:
        body = body.replace("</head>", _ANCHOR_HOP + "</head>", 1)
    else:
        body = _ANCHOR_HOP + body
    return body


def landing_route(path: str, query: str):
    """Map an HTML landing path to an action, or None to fall through.

    ("redirect", location) | ("brief", None) | ("model", None)
    """
    if FRONT_PAGE != "daily-brief":
        if path in ("/", "/index.html", MODEL_PATH):
            return ("model", None)
        if path == MODEL_PATH + "/":
            return ("redirect", MODEL_PATH + (f"?{query}" if query else ""))
        return None
    q = f"?{query}" if query else ""
    if path == "/":
        # The brief takes no query params; any query is an old dashboard deep link.
        return ("redirect", MODEL_PATH + q) if query else ("brief", None)
    if path in ("/index.html", MODEL_PATH + "/"):
        return ("redirect", MODEL_PATH + q)
    if path == MODEL_PATH:
        return ("model", None)
    return None


class HorizonHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HORIZON_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[horizon] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _maybe_serve_daily_brief(self) -> bool:
        res = daily_brief_route(urlparse(self.path).path)
        if res is None:
            return False
        code, ctype, body = res
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        return True

    def _send_html(self, code: int, raw: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _maybe_serve_landing(self) -> bool:
        parsed = urlparse(self.path)
        res = landing_route(parsed.path, parsed.query)
        if res is None:
            return False
        kind, location = res
        if kind == "redirect":
            # Root-absolute: FCC's /horizon proxy rewrites Location under its prefix.
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return True
        if kind == "brief":
            code, _ctype, body = daily_brief_route("/daily-brief")
            if code == 200:
                body = front_page_html(body)
            self._send_html(code, body.encode("utf-8"))
            return True
        self._send_html(200, (HORIZON_DIR / "index.html").read_bytes())
        return True

    def do_HEAD(self) -> None:  # noqa: N802
        if self._maybe_serve_daily_brief() or self._maybe_serve_landing():
            return
        return super().do_HEAD()

    def do_GET(self) -> None:  # noqa: N802
        if self._maybe_serve_daily_brief():
            return
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "horizon-macro",
                    "label": "Horizon Macro",
                    "port": DEFAULT_PORT,
                    "note": "Global macro intelligence — seasonal plan is horizon/ on :8791",
                },
            )
            return

        if path == "/api/dashboard":
            try:
                self._json(200, build_dashboard_payload(ROOT, DEFAULT_DATA_DIR))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/brief":
            brief_path, _ = brief_latest_paths(DEFAULT_DATA_DIR)
            if not brief_path.is_file():
                self._json(404, {"ok": False, "error": "no brief yet — run refresh"})
                return
            try:
                self._json(200, {"ok": True, "brief": load_json(brief_path)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path in ("/api/world-state", "/api/world_state"):
            state = load_world_state(DEFAULT_DATA_DIR)
            if state is None:
                self._json(404, {"ok": False, "error": "no world-state yet — run refresh"})
                return
            self._json(200, {"ok": True, "world_state": state})
            return

        if self._maybe_serve_landing():
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/refresh":
            self._json(404, {"ok": False, "error": "not found"})
            return
        body = self._read_json_body()
        offline = body.get("offline", True)
        link_only = bool(body.get("link_only", False))
        try:
            result = run_pipeline(
                workspace=ROOT,
                data_dir=DEFAULT_DATA_DIR,
                offline=bool(offline),
                link_only=link_only,
            )
            payload = build_dashboard_payload(ROOT, DEFAULT_DATA_DIR)
            payload["refresh"] = {
                "ok": result.get("ok"),
                "version_id": result.get("version_id"),
                "source_modes": result.get("source_modes"),
                "sections": result.get("sections"),
                "linkage_count": result.get("linkage_count"),
            }
            self._json(200, payload)
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Horizon visual dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "Bind address. Default 127.0.0.1, or HORIZON_BIND_HOST when set. "
            "HORIZON_BIND_HOST=0.0.0.0 listens on all interfaces (previous behavior). "
            "An explicit --host wins over the env var."
        ),
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Run offline pipeline once before serving if no brief exists",
    )
    args = parser.parse_args(argv)
    bind_host = resolve_bind_host(args.host)

    brief_path, _ = brief_latest_paths(DEFAULT_DATA_DIR)
    if args.bootstrap or not brief_path.is_file():
        print("[horizon] bootstrapping offline world-state + brief…", flush=True)
        run_pipeline(workspace=ROOT, data_dir=DEFAULT_DATA_DIR, offline=True)

    server = ThreadingHTTPServer((bind_host, args.port), HorizonHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[horizon] bind {bind_host}:{args.port}", flush=True)
    print(f"[horizon] front page at {url} (macro model at {url.rstrip('/')}{MODEL_PATH})", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[horizon] stopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
