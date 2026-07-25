"""Public host rewrite for Orchestrator when served from a remote host (Pi).

Domain deep-links default to 127.0.0.1 (correct for single-machine). When the
dashboard is opened from another machine (Mac → Pi LAN/Tailscale), those
loopback URLs must be rewritten to the host the client used (or an explicit env).

Precedence for public hostname:
  1. ORCHESTRA_PUBLIC_HOST / DASHBOARD_PUBLIC_HOST env (hostname only, no scheme)
  2. Host header of the incoming request (hostname part)
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import urlparse


def public_hostname(
    *,
    request_host_header: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> str:
    """Return bare hostname for public deep-links, or empty if none configured."""
    e = env if env is not None else os.environ
    for key in ("ORCHESTRA_PUBLIC_HOST", "DASHBOARD_PUBLIC_HOST"):
        raw = (e.get(key) or "").strip()
        if raw:
            # allow accidental full URL in env
            if "://" in raw:
                host = urlparse(raw).hostname or ""
            else:
                host = raw.split("/")[0].split(":")[0]
            if host and host not in ("0.0.0.0",):
                return host
    if request_host_header:
        # Host: 192.168.100.98:8790  or  prism-gateway:8790
        host = request_host_header.split(",")[0].strip()
        host = host.split("/")[0]
        if "@" in host:
            host = host.split("@", 1)[-1]
        host = host.split(":")[0].strip()
        if host and host not in ("0.0.0.0",):
            return host
    return ""


def rewrite_loopback_url(url: Optional[str], public_host: str) -> Optional[str]:
    """Replace 127.0.0.1 / localhost in an absolute URL with public_host."""
    if not url or not public_host:
        return url
    out = url
    out = out.replace("://127.0.0.1", f"://{public_host}")
    out = out.replace("://localhost", f"://{public_host}")
    out = out.replace("://[::1]", f"://{public_host}")
    return out


def rewrite_payload_urls(payload: dict[str, Any], public_host: str) -> dict[str, Any]:
    """Deep-rewrite loopback URLs in a payload dict (domains, links, bridge, meta)."""
    if not public_host or not isinstance(payload, dict):
        return payload

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                if k in ("url", "workflow_url", "allocator_url") and isinstance(v, str):
                    out[k] = rewrite_loopback_url(v, public_host)
                elif k == "urls" and isinstance(v, dict):
                    out[k] = {
                        sk: rewrite_loopback_url(sv, public_host)
                        if isinstance(sv, str)
                        else walk(sv)
                        for sk, sv in v.items()
                    }
                else:
                    out[k] = walk(v)
            return out
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, str) and (
            "://127.0.0.1" in obj or "://localhost" in obj or "://[::1]" in obj
        ):
            # only rewrite URL-shaped strings
            if re.match(r"^https?://", obj):
                return rewrite_loopback_url(obj, public_host)
        return obj

    return walk(payload)
