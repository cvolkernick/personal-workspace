#!/usr/bin/env python3
"""Local server for the Orchestra top-level command center.

  GET  /api/health
  GET  /api/orchestra   — full payload (recommendations primary; domains, synergies, …)
  GET  /api/domains
  GET  /api/synergies
  GET  /api/priorities
  GET  /api/attention   — attention digest + freshness
  GET  /api/recommendations — automated recommended next actions (primary)
  GET  /api/strategy        — strategy brief (themes, goals, directives)
  GET  /api/conductor/status — Grok auth ready for Conductor
  POST /api/conductor       — {question} ask Grok about orchestration
  GET  /api/launch/status   — which domain servers are live
  POST /api/launch          — {domain} start server if down, return url
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

from conductor import (  # noqa: E402
    CONDUCTOR_SUGGESTIONS,
    ConductorError,
    ask_conductor,
    auth_status,
)
from ikigai import load_ikigai, save_ikigai  # noqa: E402
from intent import FOCUS_BRIEF_PROMPT, load_intent, save_intent  # noqa: E402
from launcher import ensure_domain, status_all  # noqa: E402
from payload import DEFAULT_PORT, WORKSPACE_ROOT, build_orchestra_payload  # noqa: E402
from public_base import public_hostname, rewrite_payload_urls  # noqa: E402


class OrchestraHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ORCHESTRA_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[orchestra] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        # Mac → Pi: rewrite 127.0.0.1 domain deep-links to the public host
        if isinstance(payload, dict) and payload.get("ok") is not False:
            host = public_hostname(request_host_header=self.headers.get("Host"))
            if host:
                payload = rewrite_payload_urls(payload, host)
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
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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

        if path in ("/api/conductor/status", "/api/ask/status"):
            st = auth_status()
            self._json(
                200,
                {
                    "ok": True,
                    "conductor": st,
                    "suggestions": CONDUCTOR_SUGGESTIONS,
                },
            )
            return

        if path in ("/api/intent", "/api/focus/intent"):
            try:
                data = load_intent(WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "intent": data})
            return

        if path in ("/api/ikigai", "/api/identity"):
            try:
                data = load_ikigai(WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "ikigai": data})
            return

        if path == "/api/strategy":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {"ok": True, "strategy": payload.get("strategy") or {}},
            )
            return

        if path in ("/api/launch/status", "/api/servers"):
            try:
                self._json(200, status_all(workspace=WORKSPACE_ROOT))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
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
                    "next_action": payload.get("next_action") or rec.get("next_action"),
                    "recommendations": rec,
                    "recommended_actions": payload.get("recommended_actions") or [],
                    "summary": rec.get("summary"),
                    "mode": rec.get("mode"),
                    "focus": rec.get("focus") or [],
                },
            )
            return

        if path in ("/api/next-action", "/api/next_action"):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            nxt = payload.get("next_action")
            self._json(
                200,
                {
                    "ok": True,
                    "next_action": nxt,
                    "mode": (payload.get("recommendations") or {}).get("mode"),
                    "summary": (payload.get("recommendations") or {}).get("summary"),
                },
            )
            return

        if path in ("/", "/index.html", "/orchestra", "/orchestra/"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/api/intent", "/api/focus/intent"):
            body = self._read_json_body()
            try:
                saved = save_intent(body, WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "intent": saved})
            return

        if path in ("/api/ikigai", "/api/identity"):
            body = self._read_json_body()
            try:
                saved = save_ikigai(body, WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "ikigai": saved})
            return

        if path in ("/api/focus-brief", "/api/conductor/focus-brief"):
            # Proactive focus brief grounded in intent + orchestration data
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
                result = ask_conductor(FOCUS_BRIEF_PROMPT, payload)
                result["kind"] = "focus_brief"
            except ConductorError as e:
                code = e.status if e.status in (400, 401, 403, 429) else 502
                if e.status and 400 <= e.status < 600:
                    code = e.status
                self._json(
                    code if code >= 400 else 502,
                    {
                        "ok": False,
                        "error": str(e),
                        "detail": (e.body or "")[:800],
                    },
                )
                return
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, result)
            return

        if path in ("/api/conductor", "/api/ask", "/api/conductor/ask"):
            body = self._read_json_body()
            question = (body.get("question") or body.get("prompt") or "").strip()
            if not question:
                self._json(400, {"ok": False, "error": "question is required"})
                return
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
                result = ask_conductor(question, payload)
            except ConductorError as e:
                code = e.status if e.status in (400, 401, 403, 429) else 502
                if e.status and 400 <= e.status < 600:
                    code = e.status
                self._json(
                    code if code >= 400 else 502,
                    {
                        "ok": False,
                        "error": str(e),
                        "detail": (e.body or "")[:800],
                    },
                )
                return
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, result)
            return

        if path in ("/api/launch", "/api/start", "/api/servers/start"):
            body = self._read_json_body()
            domain = (
                body.get("domain")
                or body.get("id")
                or body.get("service")
                or ""
            ).strip()
            if not domain:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "domain is required (workflow|finance|fitness|holistic|iot)",
                    },
                )
                return
            try:
                result = ensure_domain(
                    domain,
                    workspace=WORKSPACE_ROOT,
                    force_restart=bool(body.get("force")),
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        self._json(404, {"ok": False, "error": f"unknown path {path}"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orchestrator top-level dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 on the Pi for LAN/Tailscale access.",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force local API (Pi systemd unit flag).",
    )
    parser.add_argument("--backend", default=None, help="Reserved for frontend proxy mode.")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), OrchestraHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Orchestrator: {url}")
    print(f"API: {url}api/orchestra · Conductor: {url}api/conductor")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(url.replace("0.0.0.0", "127.0.0.1"))
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Orchestrator…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
