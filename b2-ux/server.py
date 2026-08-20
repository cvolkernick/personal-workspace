#!/usr/bin/env python3
"""B2 / knowledge graph dashboard (:8792).

Primary on finley-gateway (role b2-puller). Prism (app-books) queries this
host; do not rsync the graph onto prism.

  GET /              — status page
  GET /api/health
  GET /api/status    — graph root + last pull (no snapshot bodies, no keys)

Usage:
  python3 b2-ux/server.py --host 0.0.0.0 --port 8792 --no-browser
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

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from b2_kb.vault import resolve_vault_path  # noqa: E402

DEFAULT_PORT = 8792
DEFAULT_BIND = "127.0.0.1"


def graph_path() -> Path:
    env = (os.environ.get("B2_GRAPH_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return resolve_vault_path()


def last_pull_summary() -> dict:
    dest = Path(os.environ.get("B2_PULL_DEST") or Path.home() / "b2-pulls" / "prism")
    man = dest / "MANIFEST.json"
    if not man.is_file():
        return {"present": False, "dest": str(dest)}
    try:
        data = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"present": False, "dest": str(dest), "error": "manifest unreadable"}
    pulled = data.get("pulled") or []
    return {
        "present": True,
        "dest": str(dest),
        "pulled_at": data.get("pulled_at"),
        "item_count": len(pulled),
        "skipped_count": len(data.get("skipped") or []),
        "wrote_only_pulled": data.get("wrote_only_pulled"),
    }


def status_payload() -> dict:
    gp = graph_path()
    return {
        "ok": True,
        "service": "b2",
        "label": "B2 / knowledge graph",
        "role": (os.environ.get("B2_ROLE") or "").strip() or "b2-puller",
        "host": (os.environ.get("B2_HOST") or "").strip() or "finley-gateway",
        "port": DEFAULT_PORT,
        "knowledge_graph": str(gp),
        "graph_exists": gp.exists(),
        "last_pull": last_pull_summary(),
        "notes": [
            "App Pi (prism-gateway, app-books) queries this URL.",
            "Do not rsync the knowledge graph onto prism.",
            "Pull dest is books/state only — no venue keys.",
        ],
    }


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            st = status_payload()
            self._json(200, {"ok": True, "service": "b2", "role": st["role"]})
            return
        if path == "/api/status":
            self._json(200, status_payload())
            return
        if path in ("/", "/index.html"):
            st = status_payload()
            pull = st["last_pull"]
            pull_line = (
                f"last pull {pull.get('pulled_at') or 'none'} "
                f"({pull.get('item_count', 0)} items)"
                if pull.get("present")
                else "no pull manifest yet"
            )
            html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>B2 / knowledge graph</title>
<style>
 body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; max-width: 40rem; }}
 code {{ background: #f4f4f0; padding: 0.1rem 0.3rem; }}
</style></head>
<body>
<h1>B2 / knowledge graph</h1>
<p>Role <code>{st['role']}</code> on <code>{st['host']}</code> · port {st['port']}</p>
<p>Graph root: <code>{st['knowledge_graph']}</code> ({'present' if st['graph_exists'] else 'not created yet'})</p>
<p>{pull_line}</p>
<p>Prism (app-books) queries this host. Do not copy the graph onto prism.</p>
<p><a href="/api/health">/api/health</a> · <a href="/api/status">/api/status</a></p>
</body></html>
"""
            self._html(200, html)
            return
        self._json(404, {"ok": False, "error": f"unknown path {path}"})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="B2 / knowledge graph dashboard")
    p.add_argument("--host", default=DEFAULT_BIND)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--bind", dest="host", help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    httpd = ThreadingHTTPServer((args.host, int(args.port)), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"B2 / knowledge graph on {url}", flush=True)
    if not args.no_browser and args.host in ("127.0.0.1", "localhost"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
