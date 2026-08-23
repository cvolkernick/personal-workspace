"""Shared FitDash machine-client auth (Pi + Vercel).

Same gate already used by ``/api/day_constraints``, ``/api/sleep_battery``,
``/api/warm``, and ``/api/agent/today``. Do not invent a second scheme.

Env:
  FITDASH_SERVICE_TOKEN — required for non-loopback machine access
  FITDASH_SERVICE_LOOPBACK — when 1 (default), 127.0.0.1/::1 may call
    service routes without a browser session
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def service_token_from_headers(headers) -> str:
    """Bearer or X-FitDash-Service-Token for machine clients (e.g. IoT worker)."""
    auth = (headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (headers.get("X-FitDash-Service-Token") or "").strip()


def service_auth_ok(headers, client_host: Optional[str] = None) -> bool:
    """Allow service clients via shared token, or loopback if enabled."""
    expected = (os.environ.get("FITDASH_SERVICE_TOKEN") or "").strip()
    provided = service_token_from_headers(headers)
    if expected and provided and provided == expected:
        return True
    loopback_ok = (os.environ.get("FITDASH_SERVICE_LOOPBACK") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if loopback_ok and client_host in ("127.0.0.1", "::1", "localhost"):
        return True
    return False


def service_auth_denied(purpose: str = "this route") -> Dict[str, Any]:
    """Same 401 JSON shape as other service-token routes."""
    return {
        "ok": False,
        "error": "auth_required",
        "message": (
            f"Sign in, or call from loopback / with FITDASH_SERVICE_TOKEN for {purpose}."
        ),
    }


# Aliases matching server.py names so callers can import either spelling.
_service_token_from_headers = service_token_from_headers
_service_auth_ok = service_auth_ok
