#!/usr/bin/env python3
"""Always-on dashboard endpoint resolution (Pi host by default).

Dashboards run 24/7 on the Raspberry Pi. Terminal launchers should open these
URLs in the browser — not start a localhost server.

Host precedence:
  1. env PI_HOST or DASHBOARD_HOST
  2. deploy/endpoints.json → pi_host
  3. fallback 192.168.100.98
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
DEFAULT_ENDPOINTS_PATH = ROOT / "deploy" / "endpoints.json"
FALLBACK_HOST = "192.168.100.98"

# service key → (port, path, health)
_DEFAULT_SERVICES: dict[str, dict[str, Any]] = {
    "orchestra": {"port": 8790, "path": "/", "health": "/api/health"},
    "financial-command": {
        "port": 8000,
        "path": "/financial-command/index.html",
        "health": "/api/health",
    },
    "projects-dashboard": {"port": 8765, "path": "/", "health": "/api/health"},
    "holistic": {"port": 8770, "path": "/", "health": "/api/health"},
    "iot": {"port": 8780, "path": "/", "health": "/api/health"},
    "resistance-dashboard": {
        "port": 8787,
        "path": "/",
        "health": "/api/healthz",
    },
}


def load_endpoints(path: Optional[Path] = None) -> dict[str, Any]:
    p = path or DEFAULT_ENDPOINTS_PATH
    if not p.is_file():
        return {
            "pi_host": FALLBACK_HOST,
            "scheme": "http",
            "services": dict(_DEFAULT_SERVICES),
        }
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {
            "pi_host": FALLBACK_HOST,
            "scheme": "http",
            "services": dict(_DEFAULT_SERVICES),
        }


def pi_host(cfg: Optional[dict[str, Any]] = None) -> str:
    env = (os.environ.get("PI_HOST") or os.environ.get("DASHBOARD_HOST") or "").strip()
    if env:
        # allow host:port in env — strip port for host-only
        return env.split("://")[-1].split("/")[0].split(":")[0]
    data = cfg if cfg is not None else load_endpoints()
    host = str(data.get("pi_host") or FALLBACK_HOST).strip()
    return host or FALLBACK_HOST


def scheme(cfg: Optional[dict[str, Any]] = None) -> str:
    data = cfg if cfg is not None else load_endpoints()
    s = str(data.get("scheme") or "http").strip().lower()
    return s if s in ("http", "https") else "http"


def services(cfg: Optional[dict[str, Any]] = None) -> dict[str, dict[str, Any]]:
    data = cfg if cfg is not None else load_endpoints()
    raw = data.get("services") if isinstance(data.get("services"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for key, default in _DEFAULT_SERVICES.items():
        merged = dict(default)
        if isinstance(raw.get(key), dict):
            merged.update(raw[key])  # type: ignore[arg-type]
        out[key] = merged
    return out


def service_base_url(name: str, cfg: Optional[dict[str, Any]] = None) -> str:
    """http://host:port (no trailing path)."""
    data = cfg if cfg is not None else load_endpoints()
    svc = services(data).get(name)
    if not svc:
        raise KeyError(f"unknown dashboard service: {name}")
    host = pi_host(data)
    port = int(svc["port"])
    return f"{scheme(data)}://{host}:{port}"


def service_url(name: str, cfg: Optional[dict[str, Any]] = None) -> str:
    """Full UI URL including path."""
    data = cfg if cfg is not None else load_endpoints()
    svc = services(data).get(name)
    if not svc:
        raise KeyError(f"unknown dashboard service: {name}")
    base = service_base_url(name, data)
    path = str(svc.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    if path == "/":
        return base + "/"
    return base + path


def health_url(name: str, cfg: Optional[dict[str, Any]] = None) -> str:
    data = cfg if cfg is not None else load_endpoints()
    svc = services(data).get(name)
    if not svc:
        raise KeyError(f"unknown dashboard service: {name}")
    base = service_base_url(name, data)
    health = str(svc.get("health") or "/api/health")
    if not health.startswith("/"):
        health = "/" + health
    return base + health


def probe_service(name: str, timeout: float = 3.0) -> bool:
    """True if health endpoint returns HTTP 2xx."""
    url = health_url(name)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def all_service_urls() -> dict[str, str]:
    return {name: service_url(name) for name in services()}


def domain_url_map() -> dict[str, str]:
    """Map orchestra domain ids → always-on UI URLs."""
    return {
        "workflow": service_url("projects-dashboard"),
        "finance": service_url("financial-command"),
        "fitness": service_url("resistance-dashboard"),
        "holistic": service_url("holistic"),
        "iot": service_url("iot"),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Print dashboard endpoint URLs")
    parser.add_argument(
        "service",
        nargs="?",
        default=None,
        help="Service key (orchestra, iot, …) or omit for all",
    )
    parser.add_argument("--health", action="store_true", help="Print health URL")
    parser.add_argument("--probe", action="store_true", help="Probe health (exit 1 if down)")
    parser.add_argument("--host", action="store_true", help="Print pi_host only")
    args = parser.parse_args()
    if args.host:
        print(pi_host())
        raise SystemExit(0)
    if args.service:
        if args.probe:
            ok = probe_service(args.service)
            print("ok" if ok else "down", health_url(args.service))
            raise SystemExit(0 if ok else 1)
        print(health_url(args.service) if args.health else service_url(args.service))
    else:
        for k, v in all_service_urls().items():
            print(f"{k}\t{v}")
