"""Signed OAuth state + session cookie for Vercel preview. No SQLite."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qs

SESSION_COOKIE = "fitdash_session"
SESSION_MAX_AGE = 14 * 24 * 3600
STATE_MAX_AGE = 600

# Same FitDash Google login also grants Tasks. User re-consents once.
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"

LOGIN_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    TASKS_SCOPE,
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


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


# Production alias — never a preview-branch host (fitdash-git-…).
PROD_PUBLIC_URL = "https://fitdash-cvolkernick.vercel.app"


def _with_https(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    return host if host.startswith("http") else f"https://{host}"


def _is_preview_host(url: str) -> bool:
    """Vercel preview hosts are {project}-git-{branch}-{scope}.vercel.app."""
    host = (url or "").lower().replace("https://", "").replace("http://", "")
    return "-git-" in host


def missing_oauth_env() -> list[str]:
    missing = []
    if not (os.environ.get("GOOGLE_CLIENT_ID") or "").strip():
        missing.append("GOOGLE_CLIENT_ID")
    if not (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip():
        missing.append("GOOGLE_CLIENT_SECRET")
    if not public_base_url():
        missing.append("FITDASH_PUBLIC_URL")
    return missing


def public_base_url() -> str:
    vercel_env = (os.environ.get("VERCEL_ENV") or "").strip().lower()
    explicit = (os.environ.get("FITDASH_PUBLIC_URL") or "").strip().rstrip("/")
    production = _with_https(os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or "")

    if vercel_env == "production":
        if explicit and not _is_preview_host(explicit):
            return explicit
        if production and not _is_preview_host(production):
            return production
        return PROD_PUBLIC_URL

    if explicit:
        return explicit
    vercel = (os.environ.get("VERCEL_URL") or "").strip()
    if vercel:
        return _with_https(vercel)
    return ""


def redirect_uri() -> str:
    return f"{public_base_url()}/api/auth/google/callback"


def _sign(payload: dict) -> str:
    secret = signing_secret()
    if not secret:
        raise RuntimeError("missing signing secret")
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    mac = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def _verify(token: str) -> Optional[dict]:
    secret = signing_secret()
    if not secret or not token or "." not in token:
        return None
    body, mac = token.rsplit(".", 1)
    expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expect):
        return None
    try:
        return json.loads(_b64d(body).decode("utf-8"))
    except Exception:
        return None


def make_state() -> str:
    return _sign({"n": secrets.token_urlsafe(16), "t": int(time.time())})


def verify_state(state: str) -> bool:
    data = _verify(state)
    if not data:
        return False
    try:
        issued = int(data.get("t") or 0)
    except (TypeError, ValueError):
        return False
    return issued > 0 and (time.time() - issued) <= STATE_MAX_AGE


def make_session(user: dict) -> str:
    payload = {
        "sub": user["id"],
        "email": user.get("email") or "",
        "name": user.get("display_name") or "",
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    refresh = (user.get("refresh_token") or "").strip()
    access = (user.get("access_token") or "").strip()
    scope = (user.get("scope") or "").strip()
    if refresh:
        payload["rt"] = refresh
    if access:
        payload["at"] = access
    if scope:
        payload["sc"] = scope
    expires_in = user.get("expires_in")
    if expires_in not in (None, ""):
        try:
            payload["ate"] = int(time.time()) + int(expires_in)
        except (TypeError, ValueError):
            pass
    return _sign(payload)


def _session_payload(token: str) -> Optional[dict]:
    data = _verify(token)
    if not data:
        return None
    try:
        exp = int(data.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp < int(time.time()):
        return None
    sub = str(data.get("sub") or "").strip()
    if not sub:
        return None
    data["sub"] = sub
    return data


def read_session(token: str) -> Optional[dict]:
    """Public identity only. Never returns Google tokens."""
    data = _session_payload(token)
    if not data:
        return None
    sub = str(data.get("sub") or "").strip()
    return {
        "id": sub,
        "email": str(data.get("email") or ""),
        "display_name": str(data.get("name") or data.get("email") or sub),
    }


def read_session_google(token: str) -> Optional[dict]:
    """Server-only Google OAuth fields from the signed session cookie."""
    data = _session_payload(token)
    if not data:
        return None
    try:
        access_expires_at = int(data.get("ate") or 0)
    except (TypeError, ValueError):
        access_expires_at = 0
    return {
        "refresh_token": str(data.get("rt") or ""),
        "access_token": str(data.get("at") or ""),
        "scope": str(data.get("sc") or ""),
        "access_expires_at": access_expires_at,
    }


def session_has_tasks_scope(google: Optional[dict]) -> bool:
    if not google:
        return False
    scopes = str(google.get("scope") or "").split()
    return TASKS_SCOPE in scopes


def session_google_from_headers(headers) -> Optional[dict]:
    cookies = cookie_from_header((headers or {}).get("Cookie") or "")
    return read_session_google(cookies.get(SESSION_COOKIE) or "")


def cookie_from_header(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def session_from_headers(headers) -> Optional[dict]:
    cookies = cookie_from_header(headers.get("Cookie") or "")
    return read_session(cookies.get(SESSION_COOKIE) or "")


def session_set_cookie(token: str) -> str:
    return (
        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; Secure; SameSite=Lax; "
        f"Max-Age={SESSION_MAX_AGE}"
    )


def session_clear_cookie() -> str:
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"


def query_first(query: str, key: str) -> str:
    return ((parse_qs(query or "").get(key) or [""])[0] or "").strip()


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")
