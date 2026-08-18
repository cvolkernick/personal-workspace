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

LOGIN_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
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


def missing_oauth_env() -> list[str]:
    missing = []
    if not (os.environ.get("GOOGLE_CLIENT_ID") or "").strip():
        missing.append("GOOGLE_CLIENT_ID")
    if not (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip():
        missing.append("GOOGLE_CLIENT_SECRET")
    if not (os.environ.get("FITDASH_PUBLIC_URL") or "").strip():
        missing.append("FITDASH_PUBLIC_URL")
    return missing


def public_base_url() -> str:
    base = (os.environ.get("FITDASH_PUBLIC_URL") or "").strip().rstrip("/")
    if base:
        return base
    vercel = (os.environ.get("VERCEL_URL") or "").strip()
    if vercel:
        return vercel if vercel.startswith("http") else f"https://{vercel}"
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
    return _sign(
        {
            "sub": user["id"],
            "email": user.get("email") or "",
            "name": user.get("display_name") or "",
            "exp": int(time.time()) + SESSION_MAX_AGE,
        }
    )


def read_session(token: str) -> Optional[dict]:
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
    return {
        "id": sub,
        "email": str(data.get("email") or ""),
        "display_name": str(data.get("name") or data.get("email") or sub),
    }


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
