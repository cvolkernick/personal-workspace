"""Public Grok-CLI device-code OAuth (unofficial FitDash client).

Client id is the public Grok-CLI OAuth app used by `grok login --device-auth`.
It is not present in grok_ask.py; verified with a dry POST to
https://auth.x.ai/oauth2/device/code that does not store tokens.
Never log or return tokens / client_secret / device_code in JSON.
Never write ~/.grok/auth.json.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import base64
import hashlib
import hmac


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def signing_secret() -> Optional[str]:
    secret = (
        os.environ.get("FITDASH_SESSION_SECRET")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or ""
    ).strip()
    return secret or None

# Public Grok-CLI OAuth client (not an xAI-published FitDash app).
# Source: public Grok-CLI device-code client used by grok login; dry-start
# against auth.x.ai/oauth2/device/code returned verification_uri + user_code.
GROK_CLI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
DEVICE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
USERINFO_URL = "https://auth.x.ai/oauth2/userinfo"
SCOPE = "openid profile email offline_access grok-cli:access api:access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_COOKIE = "fitdash_grok_dc"
CONNECT_ERROR = "Connect SuperGrok to generate today's meal/workout plan."
ENTITLEMENT_NOTE = (
    "If inference returns 403 after a good login, that is xAI entitlement "
    "gating (SuperGrok or X Premium+), not a FitDash bug."
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _form_post(url: str, fields: Dict[str, str], timeout: float = 20.0) -> Tuple[int, dict]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "resistance-dashboard/fitdash-grok-cli",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            return int(resp.status), data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"error": "http_error", "error_description": raw[:400]}
        return int(exc.code), data if isinstance(data, dict) else {}
    except urllib.error.URLError as exc:
        return 502, {"error": "network_error", "error_description": str(exc.reason)[:200]}


def start_device_code() -> Dict[str, Any]:
    """POST device-code. Returns public fields + device_code for the ticket."""
    status, data = _form_post(
        DEVICE_URL,
        {"client_id": GROK_CLI_CLIENT_ID, "scope": SCOPE},
    )
    if status != 200:
        return {
            "ok": False,
            "error": data.get("error_description") or data.get("error") or f"device_start_http_{status}",
        }
    user_code = str(data.get("user_code") or "").strip()
    uri = str(data.get("verification_uri") or "").strip()
    device_code = str(data.get("device_code") or "").strip()
    if not user_code or not uri or not device_code:
        return {"ok": False, "error": "device_start_incomplete"}
    expires_in = int(data.get("expires_in") or 1800)
    interval = int(data.get("interval") or 5)
    out = {
        "ok": True,
        "verification_uri": uri,
        "user_code": user_code,
        "expires_in": expires_in,
        "interval": max(1, interval),
    }
    complete = str(data.get("verification_uri_complete") or "").strip()
    if complete:
        out["verification_uri_complete"] = complete
    out["_device_code"] = device_code  # caller puts this in HttpOnly cookie only
    return out


def public_start_payload(start: Dict[str, Any]) -> Dict[str, Any]:
    """JSON for the browser: never secret, client_id, or device_code."""
    banned = {
        "client_secret",
        "client_id",
        "device_code",
        "_device_code",
        "access_token",
        "refresh_token",
        "id_token",
    }
    return {k: v for k, v in start.items() if k not in banned}


def poll_device_code(device_code: str) -> Dict[str, Any]:
    """One token poll. Returns status pending|approved|denied|expired. No tokens in public view."""
    code = (device_code or "").strip()
    if not code:
        return {"ok": False, "status": "expired", "error": "no_pending_login"}
    status, data = _form_post(
        TOKEN_URL,
        {
            "grant_type": DEVICE_GRANT,
            "client_id": GROK_CLI_CLIENT_ID,
            "device_code": code,
        },
    )
    err = str(data.get("error") or "")
    if status == 200 and data.get("access_token"):
        email = _email_from_token_payload(data)
        expires_in = int(data.get("expires_in") or 3600)
        expires_at = _iso(_utc_now() + timedelta(seconds=max(60, expires_in)))
        return {
            "ok": True,
            "status": "approved",
            "_tokens": {
                "access_token": str(data.get("access_token") or ""),
                "refresh_token": str(data.get("refresh_token") or ""),
                "expires_at": expires_at,
                "email": email,
            },
            "email": email,
        }
    if err in ("authorization_pending", "slow_down"):
        return {"ok": True, "status": "pending"}
    if err in ("access_denied", "authorization_denied"):
        return {"ok": True, "status": "denied", "error": "authorization_denied"}
    if err in ("expired_token", "expired"):
        return {"ok": True, "status": "expired", "error": "device_code_expired"}
    return {
        "ok": False,
        "status": "expired" if status in (400, 401) else "pending",
        "error": data.get("error_description") or err or f"poll_http_{status}",
    }


def refresh_access_token(refresh_token: str) -> Optional[Dict[str, str]]:
    rt = (refresh_token or "").strip()
    if not rt:
        return None
    status, data = _form_post(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": GROK_CLI_CLIENT_ID,
        },
    )
    if status != 200 or not data.get("access_token"):
        return None
    expires_in = int(data.get("expires_in") or 3600)
    return {
        "access_token": str(data.get("access_token") or ""),
        "refresh_token": str(data.get("refresh_token") or rt),
        "expires_at": _iso(_utc_now() + timedelta(seconds=max(60, expires_in))),
        "email": _email_from_token_payload(data),
    }


def _email_from_token_payload(data: dict) -> str:
    email = str(data.get("email") or "").strip()
    if email:
        return email
    id_token = str(data.get("id_token") or "").strip()
    if id_token:
        email = _email_from_jwt(id_token)
        if email:
            return email
    access = str(data.get("access_token") or "").strip()
    if access:
        try:
            req = urllib.request.Request(
                USERINFO_URL,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Accept": "application/json",
                    "User-Agent": "resistance-dashboard/fitdash-grok-cli",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                info = json.loads(resp.read().decode("utf-8"))
            if isinstance(info, dict):
                return str(info.get("email") or "").strip()
        except Exception:
            return ""
    return ""


def _email_from_jwt(token: str) -> str:
    parts = token.split(".")
    if len(parts) < 2:
        return ""
    try:
        payload = json.loads(_b64d(parts[1]).decode("utf-8"))
    except Exception:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("email") or "").strip()
    return ""


def make_device_ticket(device_code: str, expires_in: int) -> str:
    secret = signing_secret()
    if not secret:
        raise RuntimeError("missing signing secret")
    payload = {
        "dc": device_code,
        "exp": int(time.time()) + max(60, int(expires_in or 1800)),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    import hashlib
    import hmac

    mac = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def read_device_ticket(token: str) -> Optional[str]:
    secret = signing_secret()
    if not secret or not token or "." not in token:
        return None
    body, mac = token.rsplit(".", 1)
    import hashlib
    import hmac

    expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expect):
        return None
    try:
        data = json.loads(_b64d(body).decode("utf-8"))
    except Exception:
        return None
    try:
        exp = int(data.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp < int(time.time()):
        return None
    code = str(data.get("dc") or "").strip()
    return code or None


def device_set_cookie(ticket: str, max_age: int) -> str:
    return (
        f"{DEVICE_COOKIE}={ticket}; Path=/; HttpOnly; Secure; SameSite=Lax; "
        f"Max-Age={max(60, int(max_age))}"
    )


def device_clear_cookie() -> str:
    return f"{DEVICE_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"


def on_vercel() -> bool:
    return bool((os.environ.get("VERCEL") or "").strip())
