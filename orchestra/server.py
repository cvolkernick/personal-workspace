#!/usr/bin/env python3
"""Local server for the Orchestra top-level command center.

  GET  /api/health
  GET  /api/orchestra   — full payload (recommendations primary; domains, synergies, …)
  GET  /api/domains
  GET  /api/synergies
  GET  /api/priorities
  GET  /api/attention   — attention digest + freshness
  GET  /api/recommendations — automated recommended next actions (primary)
  GET  /api/today       — structured Today's Focus from strategy/today.md
  GET  /api/strategy/today.md — raw markdown (easy to preview / copy)
  GET  /api/strategy/bets.md  — raw bets markdown
  GET  /                — unified UI

Usage:
  python3 orchestra/server.py
  python3 orchestra/server.py --port 8790 --no-browser
  python3 launch.py
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ORCHESTRA_DIR = Path(__file__).resolve().parent
ROOT = ORCHESTRA_DIR.parent
if str(ORCHESTRA_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRA_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import build_today_focus  # noqa: E402
from payload import DEFAULT_PORT, WORKSPACE_ROOT, build_orchestra_payload  # noqa: E402

# Safe relative paths under the workspace that the UI may fetch as raw markdown.
_STRATEGY_RAW = {
    "/api/strategy/today.md": "strategy/today.md",
    "/api/strategy/today": "strategy/today.md",
    "/api/strategy/bets.md": "strategy/bets.md",
    "/api/strategy/bets": "strategy/bets.md",
}


class OrchestraHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ORCHESTRA_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[orchestra] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        probe = (qs.get("probe") or ["0"])[0] in ("1", "true", "yes")

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "orchestra",
                    "workspace": str(WORKSPACE_ROOT),
                },
            )
            return

        if path in (
            "/api/orchestra",
            "/api/status",
            "/api/payload",
        ):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, payload)
            return

        if path in ("/api/today", "/api/today-focus", "/api/focus"):
            try:
                focus = build_today_focus(WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, focus)
            return

        if path in _STRATEGY_RAW:
            rel = _STRATEGY_RAW[path]
            target = (WORKSPACE_ROOT / rel).resolve()
            try:
                target.relative_to(WORKSPACE_ROOT.resolve())
            except ValueError:
                self._json(403, {"ok": False, "error": "path outside workspace"})
                return
            if not target.is_file():
                self._json(404, {"ok": False, "error": f"missing {rel}"})
                return
            try:
                text = target.read_text(encoding="utf-8")
            except OSError as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._text(200, text, "text/markdown; charset=utf-8")
            return

        if path == "/api/domains":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "domains": payload.get("domains"),
                    "links": payload.get("links"),
                },
            )
            return

        if path == "/api/synergies":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {"ok": True, "synergies": payload.get("synergies") or []},
            )
            return

        if path in ("/api/priorities", "/api/action-plan"):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "priorities": payload.get("priorities") or [],
                    "action_plan": payload.get("action_plan") or [],
                },
            )
            return

        if path == "/api/attention":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "attention": payload.get("attention") or [],
                    "freshness": payload.get("freshness") or {},
                    "counts": {
                        "attention": (payload.get("counts") or {}).get("attention"),
                        "stale_sources": (payload.get("counts") or {}).get(
                            "stale_sources"
                        ),
                    },
                },
            )
            return

        if path in ("/api/recommendations", "/api/actions", "/api/next"):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            rec = payload.get("recommendations") or {}
            self._json(
                200,
                {
                    "ok": True,
                    "recommendations": rec,
                    "recommended_actions": payload.get("recommended_actions") or [],
                    "summary": rec.get("summary"),
                    "mode": rec.get("mode"),
                    "focus": rec.get("focus") or [],
                },
            )
            return

        if path in ("/", "/index.html", "/orchestra", "/orchestra/"):
            self.path = "/index.html"
        return super().do_GET()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orchestra top-level dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), OrchestraHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Orchestra Command Center: {url}")
    print(f"API: {url}api/orchestra")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping orchestra…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
