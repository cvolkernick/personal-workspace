#!/usr/bin/env python3
"""Orchestrator dashboard server — local UI/API or Pi backend with terminal frontends.

  GET  /api/health
  GET  /api/orchestra   — full orchestration payload
  GET  /api/domains
  GET  /api/synergies
  GET  /api/priorities
  GET  /                — unified UI

Usage (local all-in-one):
  python3 orchestra/server.py --port 8790

Usage (Pi backend, bind all interfaces):
  python3 orchestra/server.py --host 0.0.0.0 --port 8790 --no-browser --local

Usage (Mac terminal frontend → Pi API):
  python3 orchestra/server.py --backend http://192.168.100.98:8790 --no-browser

Deploy: bash deploy/install_remote.sh prism-agent@HOST --only orchestra
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

ORCHESTRA_DIR = Path(__file__).resolve().parent
ROOT = ORCHESTRA_DIR.parent
if str(ORCHESTRA_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRA_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payload import DEFAULT_PORT, WORKSPACE_ROOT, build_orchestra_payload  # noqa: E402
from public_base import public_hostname, rewrite_payload_urls  # noqa: E402
from remote_backend import (  # noqa: E402
    annotate_health_json,
    forward_api,
    is_api_path,
    proxy_error_payload,
    resolve_backend,
)

# Set by main() for request handlers
_BACKEND_BASE: Optional[str] = None
_BACKEND_LABEL: str = ""


def _try_rich_imports() -> dict[str, Any]:
    """Optional richer modules present on full Pi / worktree checkouts."""
    extras: dict[str, Any] = {}
    try:
        from collectors import build_today_focus  # type: ignore

        extras["build_today_focus"] = build_today_focus
    except Exception:
        pass
    return extras


_EXTRAS = _try_rich_imports()


class OrchestraHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ORCHESTRA_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[orchestra] " + (fmt % args) + "\n")

    def _public_host(self) -> str:
        return public_hostname(request_host_header=self.headers.get("Host"))

    def _send_raw(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        # Rewrite loopback deep-links so Mac browsers hitting the Pi get LAN URLs
        if isinstance(payload, dict) and payload.get("ok") is not False:
            host = self._public_host()
            if host:
                payload = rewrite_payload_urls(payload, host)
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send_raw(code, body, "application/json; charset=utf-8")

    def _proxy_api(self, method: str = "GET") -> bool:
        """Forward /api/* to remote backend when configured. Returns True if handled."""
        if not _BACKEND_BASE or not is_api_path(self.path):
            return False
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 and method != "GET" else None
        try:
            code, raw, ctype = forward_api(
                _BACKEND_BASE,
                self.path,
                method,
                body=body,
                content_type=self.headers.get("Content-Type"),
            )
        except Exception as e:
            self._json(502, proxy_error_payload(_BACKEND_BASE, e, backend_label=_BACKEND_LABEL))
            return True
        if self.path.split("?", 1)[0] == "/api/health":
            raw = annotate_health_json(
                raw,
                backend_url=_BACKEND_BASE,
                backend_label=_BACKEND_LABEL,
                frontend="orchestra",
            )
        # Also rewrite loopback in proxied JSON bodies for client convenience
        if "json" in (ctype or ""):
            try:
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    host = self._public_host()
                    if host:
                        data = rewrite_payload_urls(data, host)
                    raw = json.dumps(data, default=str).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        self._send_raw(code, raw, ctype)
        return True

    def do_GET(self) -> None:  # noqa: N802
        if self._proxy_api("GET"):
            return

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
                    "name": "Orchestrator",
                    "workspace": str(WORKSPACE_ROOT),
                    "proxy": False,
                    "backend": None,
                    "public_host": self._public_host() or None,
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

        # Optional richer endpoints if modules exist on this checkout
        if path in ("/api/today", "/api/today-focus", "/api/focus") and "build_today_focus" in _EXTRAS:
            try:
                focus = _EXTRAS["build_today_focus"](WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, focus)
            return

        if path in ("/", "/index.html", "/orchestra", "/orchestra/"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self._proxy_api("POST"):
            return
        self._json(404, {"ok": False, "error": f"unknown path {urlparse(self.path).path}"})


def main(argv: list[str] | None = None) -> int:
    global _BACKEND_BASE, _BACKEND_LABEL

    parser = argparse.ArgumentParser(description="Orchestrator top-level dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 on Pi so LAN/Tailscale clients can connect.",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force local API (ignore --backend / backend.json). Used by Pi systemd units.",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="Remote API base URL (e.g. http://192.168.100.98:8790). Serves UI locally.",
    )
    parser.add_argument(
        "--backend-config",
        default=None,
        help="Path to backend.json (default: orchestra/backend.json)",
    )
    args = parser.parse_args(argv)

    cfg_path = (
        Path(args.backend_config)
        if args.backend_config
        else ORCHESTRA_DIR / "backend.json"
    )
    _BACKEND_BASE, _BACKEND_LABEL = resolve_backend(
        local=bool(args.local),
        backend=args.backend,
        config_path=cfg_path,
    )

    server = ThreadingHTTPServer((args.host, args.port), OrchestraHandler)
    bind_host, bind_port = server.server_address[0], int(server.server_address[1])
    url = f"http://{bind_host}:{bind_port}/"
    print(f"Orchestrator: {url}")
    if _BACKEND_BASE:
        print(f"API backend: {_BACKEND_BASE} ({_BACKEND_LABEL})")
    else:
        print(f"API: {url}api/orchestra (local)")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        # Prefer opening a client-reachable URL (not 0.0.0.0)
        open_url = url.replace("0.0.0.0", "127.0.0.1")
        try:
            webbrowser.open(open_url)
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
