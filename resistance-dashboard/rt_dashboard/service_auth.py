"""Shared FitDash machine-client auth (Pi + Vercel).

Same gate already used by ``/api/day_constraints``, ``/api/sleep_battery``,
``/api/warm``, and ``/api/agent/today``. Do not invent a second scheme.

Env:
  FITDASH_SERVICE_TOKEN — required for non-loopback machine access
  FITDASH_SERVICE_LOOPBACK — when 1 (default), 127.0.0.1/::1 may call
    service routes without a browser session
  FITDASH_INVENTORY_AGENT_TOKEN — least-privilege pantry stock-write credential
  FITDASH_INVENTORY_AGENT_USER_ID — Google ``user_id`` / ``sub`` the agent
    token is bound to (Chris). Required with the token; never ``default``.

House ``FITDASH_SERVICE_TOKEN`` does **not** write inventory. Loopback does
**not** write inventory. Body ``userId`` is not the trust boundary.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Dict, Optional, Tuple

INVENTORY_AGENT_TOKEN_ENV = "FITDASH_INVENTORY_AGENT_TOKEN"
INVENTORY_AGENT_USER_ID_ENV = "FITDASH_INVENTORY_AGENT_USER_ID"
_ACCOUNT_HINT_KEYS = ("userId", "user_id", "account_id", "accountId", "tenant")


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


def _token_match(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def inventory_agent_denied() -> Dict[str, Any]:
    """401 for pantry stock writes. House service token is not enough."""
    return {
        "ok": False,
        "error": "auth_required",
        "message": (
            "Sign in with Google, or call with FITDASH_INVENTORY_AGENT_TOKEN "
            "bound to FITDASH_INVENTORY_AGENT_USER_ID."
        ),
    }


def inventory_forbidden(reason: str = "forbidden") -> Dict[str, Any]:
    return {"ok": False, "error": "forbidden", "message": reason}


def inventory_session_uid(user: Optional[dict]) -> str:
    if not user:
        return ""
    return str(user.get("id") or user.get("user_id") or "").strip()


def account_hint_from_payload(payload: Optional[dict]) -> str:
    """Presentation-only account fields. ``id`` is the ingredient id — ignore it."""
    if not isinstance(payload, dict):
        return ""
    for key in _ACCOUNT_HINT_KEYS:
        raw = payload.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def account_hint_mismatch(
    principal_uid: str, payload: Optional[dict]
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Fail closed when a body/query hint names a different tenant (AC10)."""
    hint = account_hint_from_payload(payload)
    uid = (principal_uid or "").strip()
    if hint and uid and hint != uid:
        return 403, inventory_forbidden("account mismatch")
    return None


def inventory_agent_principal(headers) -> Optional[Dict[str, Any]]:
    """Chris-bound stock-write principal, or None.

    Requires both env vars. Rejects the unbound house ``FITDASH_SERVICE_TOKEN``
    even when the header matches it. Missing ``USER_ID`` is not a principal.
    """
    expected = (os.environ.get(INVENTORY_AGENT_TOKEN_ENV) or "").strip()
    bound = (os.environ.get(INVENTORY_AGENT_USER_ID_ENV) or "").strip()
    provided = service_token_from_headers(headers)
    if not expected or not bound or not provided:
        return None
    house = (os.environ.get("FITDASH_SERVICE_TOKEN") or "").strip()
    if house and _token_match(expected, house):
        return None
    if not _token_match(provided, expected):
        return None
    return {
        "id": bound,
        "user_id": bound,
        "email": "",
        "display_name": "inventory-agent",
        "agent_inventory": True,
    }


# Aliases matching server.py names so callers can import either spelling.
_service_token_from_headers = service_token_from_headers
_service_auth_ok = service_auth_ok
