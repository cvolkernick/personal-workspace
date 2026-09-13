"""Shared Auto Fleet machine-client auth (Pi + snapshot / Vercel export).

Same scheme as FitDash ``rt_dashboard/service_auth.py`` (#293). Do not invent
a second gate. Used by ``GET /api/agent/fleet`` only — ``/api/fleet`` stays
intranet; invoice-ready ``/api/turo-tasks`` is unchanged.

Env:
  AUTO_FLEET_SERVICE_TOKEN — required for non-loopback machine access
  AUTO_FLEET_SERVICE_LOOPBACK — when 1 (default), 127.0.0.1/::1 may call
    the agent route without a token
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def service_token_from_headers(headers) -> str:
    """Bearer or X-Auto-Fleet-Service-Token for machine clients (Helm)."""
    auth = (headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("X-Auto-Fleet-Service-Token") or "").strip()


def service_auth_ok(headers, client_host: Optional[str] = None) -> bool:
    """Allow service clients via shared token, or loopback if enabled."""
    expected = (os.environ.get("AUTO_FLEET_SERVICE_TOKEN") or "").strip()
    provided = service_token_from_headers(headers)
    if expected and provided and provided == expected:
        return True
    # Vercel / public export: token only. Loopback must not open the packet.
    if (os.environ.get("VERCEL") or "").strip():
        return False
    loopback_ok = (os.environ.get("AUTO_FLEET_SERVICE_LOOPBACK") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if loopback_ok and client_host in ("127.0.0.1", "::1", "localhost"):
        return True
    return False


def service_auth_denied(purpose: str = "this route") -> Dict[str, Any]:
    """Same 401 JSON shape as FitDash service-token routes."""
    return {
        "ok": False,
        "error": "auth_required",
        "message": (
            f"Call from loopback / with AUTO_FLEET_SERVICE_TOKEN for {purpose}."
        ),
    }


_service_token_from_headers = service_token_from_headers
_service_auth_ok = service_auth_ok
