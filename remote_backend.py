#!/usr/bin/env python3
"""Shared remote-backend URL resolution and /api/* reverse-proxy for dashboard frontends.

Contract (all monorepo dashboards):
  - Local mode (default): serve UI + API on this process (localhost-friendly).
  - Remote mode: serve UI locally; forward /api/* to a configurable backend base URL
    (LAN IP, Tailscale MagicDNS, etc.).
  - --local wins over --backend and backend.json.
  - --backend wins over backend.json.
  - backend.json shape: {"url": "http://host:port", "label": "optional"}
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


def load_backend_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load backend.json (or empty dict if missing/invalid)."""
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def normalize_base_url(url: str) -> str:
    """Strip trailing slash; require http(s) scheme for non-empty URLs."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"backend URL must be absolute http(s): {url!r}")
    return u


def resolve_backend(
    *,
    local: bool = False,
    backend: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> tuple[Optional[str], str]:
    """Return (base_url_or_None, label).

    Precedence: local=True → no remote; else CLI backend; else config file url.
    Accepts LAN hosts and mesh names (Tailscale MagicDNS) equally — no LAN-only filter.
    """
    if local:
        return None, ""
    if backend is not None and str(backend).strip():
        base = normalize_base_url(str(backend))
        label = urlparse(base).hostname or base
        return base, label
    cfg = load_backend_config(config_path)
    url = (cfg.get("url") or "").strip()
    if not url:
        return None, ""
    base = normalize_base_url(url)
    label = str(cfg.get("label") or "").strip() or (urlparse(base).hostname or base)
    return base, label


def is_api_path(path: str) -> bool:
    """True when request path (no host) is under /api/."""
    p = path or ""
    if "?" in p:
        p = p.split("?", 1)[0]
    return p.startswith("/api/")


def forward_api(
    backend_base: str,
    path_with_query: str,
    method: str,
    *,
    body: Optional[bytes] = None,
    content_type: Optional[str] = None,
    timeout: float = 90.0,
) -> tuple[int, bytes, str]:
    """HTTP-forward to backend. Returns (status, body_bytes, content_type).

    Network failures raise OSError/URLError (caller maps to 502).
    HTTPError is returned as status + body (not raised).
    """
    base = normalize_base_url(backend_base)
    if not path_with_query.startswith("/"):
        path_with_query = "/" + path_with_query
    url = base + path_with_query
    headers: dict[str, str] = {"Accept": "application/json"}
    data = body
    if method.upper() in ("POST", "PUT", "PATCH"):
        if data is None:
            data = b"{}"
        headers["Content-Type"] = content_type or "application/json"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = int(resp.status)
            ctype = resp.headers.get("Content-Type") or "application/json; charset=utf-8"
            return code, raw, ctype
    except urllib.error.HTTPError as e:
        raw = e.read()
        ctype = e.headers.get("Content-Type") or "application/json; charset=utf-8"
        return int(e.code), raw, ctype


def annotate_health_json(
    data: bytes,
    *,
    backend_url: str,
    backend_label: str = "",
    frontend: str = "",
) -> bytes:
    """If body is a JSON object (typical /api/health), stamp proxy metadata."""
    try:
        payload = json.loads(data.decode("utf-8") if data else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return data
    if not isinstance(payload, dict):
        return data
    payload["proxy"] = True
    payload["backend"] = backend_url
    payload["backend_label"] = backend_label or backend_url
    if frontend:
        payload["frontend"] = frontend
    return json.dumps(payload, default=str).encode("utf-8")


def proxy_error_payload(
    backend_url: str,
    err: BaseException,
    *,
    backend_label: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"backend unreachable: {type(err).__name__}: {err}",
        "backend": backend_url,
        "backend_label": backend_label or backend_url,
        "proxy": True,
    }


def add_backend_args(parser: Any) -> None:
    """Attach --backend and --local to an argparse parser."""
    parser.add_argument(
        "--backend",
        default=None,
        help="Proxy /api/* to this base URL (LAN or Tailscale hostname/IP)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force local API handling (ignore backend.json / --backend)",
    )


def write_proxied_response(
    handler: Any,
    code: int,
    data: bytes,
    content_type: str,
    *,
    backend_url: str = "",
) -> None:
    """Write a proxied HTTP response on a BaseHTTPRequestHandler-like object."""
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    if backend_url:
        handler.send_header("X-Dashboard-Proxy-Backend", backend_url)
    handler.end_headers()
    handler.wfile.write(data)


def try_proxy_api(
    handler: Any,
    backend_url: Optional[str],
    *,
    method: str,
    backend_label: str = "",
    frontend: str = "",
    timeout: float = 90.0,
    health_paths: tuple[str, ...] = ("/api/health", "/api/healthz"),
) -> bool:
    """If backend_url set and path is /api/*, proxy and write response. Return True if handled."""
    if not backend_url:
        return False
    raw_path = getattr(handler, "path", "") or ""
    if not is_api_path(raw_path):
        return False

    body: Optional[bytes] = None
    content_type: Optional[str] = None
    if method.upper() in ("POST", "PUT", "PATCH"):
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length > 0 else b"{}"
        content_type = handler.headers.get("Content-Type") or "application/json"

    try:
        code, data, ctype = forward_api(
            backend_url,
            raw_path,
            method,
            body=body,
            content_type=content_type,
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001 — map any transport failure to 502
        payload = proxy_error_payload(backend_url, e, backend_label=backend_label)
        raw = json.dumps(payload).encode("utf-8")
        write_proxied_response(
            handler, 502, raw, "application/json; charset=utf-8", backend_url=backend_url
        )
        return True

    path_only = raw_path.split("?", 1)[0]
    if path_only in health_paths:
        data = annotate_health_json(
            data,
            backend_url=backend_url,
            backend_label=backend_label,
            frontend=frontend,
        )
        ctype = "application/json; charset=utf-8"

    write_proxied_response(handler, code, data, ctype, backend_url=backend_url)
    return True
