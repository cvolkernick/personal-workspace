#!/usr/bin/env python3
"""Local server for the Orchestra top-level command center.

  GET  /api/health
  GET  /api/fan-in      — host ok/as_of + regime + top implications strip (#51)
  GET  /api/heartbeat  — Pi runtime heartbeat (schema v1 latest.json)
  GET  /api/orchestra   — full payload (recommendations primary; domains, synergies, …)
  GET  /api/domains
  GET  /api/launch?domain=  — publicized open URL + live probe (nav v1)
  GET  /api/launch/status   — which domain servers are live
  POST /api/launch          — {domain} ensure server if offline, return url
  GET  /api/synergies
  GET  /api/priorities
  GET  /api/attention   — attention digest + freshness
  GET  /api/recommendations — automated recommended next actions (primary)
  GET  /                — unified UI

Usage:
  python3 orchestra/server.py
  python3 orchestra/server.py --port 8790 --no-browser
  python3 orchestra/server.py --backend http://pi-host:8790
  python3 launch.py
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

from fan_in import build_fan_in  # noqa: E402
from heartbeat import heartbeat_api_payload  # noqa: E402
from launcher import domain_spec, ensure_domain, probe_port, status_all  # noqa: E402
from payload import DEFAULT_PORT, WORKSPACE_ROOT, build_orchestra_payload  # noqa: E402
from public_base import (  # noqa: E402
    public_hostname,
    rewrite_loopback_url,
    rewrite_payload_urls,
)
from remote_backend import add_backend_args, resolve_backend, try_proxy_api  # noqa: E402

DEFAULT_BACKEND_CONFIG = ORCHESTRA_DIR / "backend.json"
_BACKEND_URL: Optional[str] = None
_BACKEND_LABEL: str = ""
_FRONTEND: str = ""


def _public_host_for(handler: SimpleHTTPRequestHandler) -> str:
    return public_hostname(request_host_header=handler.headers.get("Host"))


def _with_public_urls(handler: SimpleHTTPRequestHandler, payload: dict) -> dict:
    host = _public_host_for(handler)
    if not host or host in ("127.0.0.1", "localhost"):
        return payload
    return rewrite_payload_urls(payload, host)


def _domain_lookup(domain_id: str) -> Optional[dict[str, Any]]:
    return domain_spec(domain_id)


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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _launch_payload(self, domain_id: str) -> dict:
        """Publicized open URL + live probe for a nav chip (no domain write)."""
        spec = _domain_lookup(domain_id)
        if not spec:
            return {"ok": False, "error": f"unknown domain: {domain_id}"}
        url = spec.get("url")
        if not url and spec.get("port"):
            url = f"http://127.0.0.1:{int(spec['port'])}/"
        host = _public_host_for(self)
        if host and host not in ("127.0.0.1", "localhost"):
            url = rewrite_loopback_url(url, host)
        live = probe_port(int(spec["port"])) if spec.get("port") else None
        return {
            "ok": True,
            "domain": spec.get("id"),
            "label": spec.get("label"),
            "url": url,
            "port": spec.get("port"),
            "live": live,
            "note": None
            if live
            else "offline — use POST /api/launch to ensure-and-open when a local script exists",
        }

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if try_proxy_api(
            self,
            _BACKEND_URL,
            method="POST",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
        ):
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/api/launch", "/api/start", "/api/servers/start"):
            body = self._read_json_body()
            domain = (
                body.get("domain") or body.get("id") or body.get("service") or ""
            ).strip()
            if not domain:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "domain is required (workflow|finance|fitness|holistic|iot|horizon|horizon_macro|b2)",
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
            # publicize URL for remote clients
            host = _public_host_for(self)
            if host and host not in ("127.0.0.1", "localhost") and result.get("url"):
                result = dict(result)
                result["url"] = rewrite_loopback_url(result.get("url"), host)
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return
        self._json(404, {"ok": False, "error": f"unknown path {path}"})

    def do_GET(self) -> None:  # noqa: N802
        if try_proxy_api(
            self,
            _BACKEND_URL,
            method="GET",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
        ):
            return
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        probe = (qs.get("probe") or ["0"])[0] in ("1", "true", "yes")

        if path in ("/api/launch/status", "/api/servers"):
            try:
                self._json(200, status_all())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "orchestra",
                    "workspace": str(WORKSPACE_ROOT),
                    "proxy": False,
                    "backend": None,
                },
            )
            return

        if path in ("/api/fan-in", "/api/fan_in", "/api/awareness"):
            try:
                self._json(200, build_fan_in(WORKSPACE_ROOT))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/heartbeat":
            try:
                self._json(200, heartbeat_api_payload(WORKSPACE_ROOT))
            except Exception as e:
                self._json(500, {"ok": False, "available": False, "error": str(e)})
            return

        if path in ("/api/launch", "/api/open"):
            domain = (qs.get("domain") or qs.get("id") or [""])[0]
            if not domain:
                self._json(400, {"ok": False, "error": "domain required"})
                return
            self._json(200, self._launch_payload(domain))
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
            self._json(200, _with_public_urls(self, payload))
            return

        if path == "/api/domains":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            payload = _with_public_urls(self, payload)
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
    global _BACKEND_URL, _BACKEND_LABEL, _FRONTEND
    parser = argparse.ArgumentParser(description="Orchestra top-level dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    add_backend_args(parser)
    args = parser.parse_args(argv)

    _BACKEND_URL, _BACKEND_LABEL = resolve_backend(
        local=bool(args.local),
        backend=args.backend,
        config_path=DEFAULT_BACKEND_CONFIG,
    )
    _FRONTEND = f"http://{args.host}:{args.port}/"

    server = ThreadingHTTPServer((args.host, args.port), OrchestraHandler)
    url = f"http://{args.host}:{args.port}/"
    _FRONTEND = url
    print(f"Orchestra Command Center: {url}")
    if _BACKEND_URL:
        print(f"backend  → {_BACKEND_URL} ({_BACKEND_LABEL or 'remote'}) [proxy mode]")
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
