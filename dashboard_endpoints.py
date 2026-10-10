#!/usr/bin/env python3
"""Always-on dashboard endpoint resolution (Pi host by default).

Dashboards run 24/7 on the Raspberry Pi. Terminal launchers should open these
URLs in the browser — not start a localhost server.

Host precedence:
  1. env PI_HOST or DASHBOARD_HOST
  2. deploy/endpoints.json → pi_host
  3. fallback FALLBACK_HOST (prism's tailnet MagicDNS short name)

FCC (financial-command, :8000) and Horizon (:8795) bind 127.0.0.1 on prism,
so they are never linked or probed on pi_host:
  - links come from PRISM_PUBLIC_BASE (prism_public.py):
    <base>/financial-command/index.html and <base>/horizon/
  - with PRISM_PUBLIC_BASE unset or invalid, links fall back to loopback
  - health URLs are always loopback (127.0.0.1)
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
# Tailnet MagicDNS short name, not a LAN IP. PI_HOST / DASHBOARD_HOST override.
FALLBACK_HOST = "prism-gateway"
LOOPBACK_HOST = "127.0.0.1"

# Services that bind 127.0.0.1 only. Links: PRISM_PUBLIC_BASE + public_path,
# else loopback. Probes and health checks: always loopback.
LOOPBACK_ONLY_SERVICES: dict[str, dict[str, Any]] = {
    "financial-command": {"port": 8000, "public_path": "/financial-command/index.html"},
    "horizon": {"port": 8795, "public_path": "/horizon/"},
}
LOOPBACK_ONLY_PORTS = frozenset(int(v["port"]) for v in LOOPBACK_ONLY_SERVICES.values())

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
    "auto-fleet": {"port": 8796, "path": "/", "health": "/api/health"},
    "horizon": {"port": 8795, "path": "/", "health": "/api/health"},
    "oomwoo": {"port": 8798, "path": "/", "health": "/api/health"},
    "life-compass": {"port": 8793, "path": "/", "health": "/api/health"},
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


def _prism_public_origin(environ: Optional[dict[str, str]] = None) -> str:
    """Validated PRISM_PUBLIC_BASE origin, or "" when unset or invalid."""
    try:
        import prism_public
    except ImportError:  # pragma: no cover - repo root not on sys.path
        return ""
    return prism_public.prism_public_origin(environ)


def is_loopback_only(name: str) -> bool:
    return name in LOOPBACK_ONLY_SERVICES


def loopback_base_url(name: str, cfg: Optional[dict[str, Any]] = None) -> str:
    """http://127.0.0.1:port for a service (probes, health, unset-base links)."""
    data = cfg if cfg is not None else load_endpoints()
    svc = services(data).get(name)
    if not svc:
        raise KeyError(f"unknown dashboard service: {name}")
    return f"http://{LOOPBACK_HOST}:{int(svc['port'])}"


def service_base_url(name: str, cfg: Optional[dict[str, Any]] = None) -> str:
    """http://host:port (no trailing path).

    Loopback-only services (FCC, Horizon) return the loopback base, never
    pi_host: they do not listen there.
    """
    data = cfg if cfg is not None else load_endpoints()
    svc = services(data).get(name)
    if not svc:
        raise KeyError(f"unknown dashboard service: {name}")
    if is_loopback_only(name):
        return loopback_base_url(name, data)
    host = pi_host(data)
    port = int(svc["port"])
    return f"{scheme(data)}://{host}:{port}"


def service_url(
    name: str,
    cfg: Optional[dict[str, Any]] = None,
    environ: Optional[dict[str, str]] = None,
) -> str:
    """Full UI URL including path.

    FCC and Horizon: <PRISM_PUBLIC_BASE>/financial-command/index.html and
    <PRISM_PUBLIC_BASE>/horizon/ when the base is valid, else loopback.
    """
    data = cfg if cfg is not None else load_endpoints()
    svc = services(data).get(name)
    if not svc:
        raise KeyError(f"unknown dashboard service: {name}")
    if is_loopback_only(name):
        origin = _prism_public_origin(environ)
        if origin:
            return origin + str(LOOPBACK_ONLY_SERVICES[name]["public_path"])
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
