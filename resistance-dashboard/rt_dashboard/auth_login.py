"""Google sign-in for FitDash (login = account identity + optional Health scopes).

Uses the same GOOGLE_CLIENT_ID/SECRET as Health connect. Redirect URI is
``{FITDASH_PUBLIC_URL}/api/auth/google/callback`` (default http://127.0.0.1:8787).

Must be added to the Google Cloud OAuth client's authorized redirect URIs.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .google_auth import load_oauth_client
from .user_store import UserStore

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Login + Health in one consent so demo uses Chris's Google as both identity and health
LOGIN_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
]

# CSRF state store (in-memory; fine for single-process Pi)
_pending: Dict[str, float] = {}


def public_base_url() -> str:
    base = (os.environ.get("FITDASH_PUBLIC_URL") or "").strip().rstrip("/")
    if base:
        return base
    # Legacy local default
    port = os.environ.get("PORT") or "8787"
    return f"http://127.0.0.1:{port}"


def redirect_uri() -> str:
    return f"{public_base_url()}/api/auth/google/callback"


def build_login_url() -> Tuple[str, str]:
    """Return (auth_url, state)."""
    client_id, _secret, _src = load_oauth_client()
    state = secrets.token_urlsafe(24)
    _pending[state] = time.time()
    # prune old states
    cutoff = time.time() - 600
    for k, t0 in list(_pending.items()):
        if t0 < cutoff:
            _pending.pop(k, None)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(LOGIN_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params), state


def _exchange_code(code: str) -> dict:
    client_id, client_secret, _ = load_oauth_client()
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Token exchange failed HTTP {e.code}: {err}") from e


def _userinfo(access_token: str) -> dict:
    req = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def complete_login(code: str, state: str, store: Optional[UserStore] = None) -> Dict[str, Any]:
    """Exchange code, create session, claim legacy default workouts. Returns session payload."""
    if not state or state not in _pending:
        raise RuntimeError("Invalid or expired OAuth state — start login again")
    _pending.pop(state, None)
    tokens = _exchange_code(code)
    access = tokens.get("access_token") or ""
    refresh = tokens.get("refresh_token") or ""
    if not access:
        raise RuntimeError("No access_token from Google")
    info = _userinfo(access)
    sub = str(info.get("sub") or "").strip()
    if not sub:
        raise RuntimeError("Google userinfo missing sub")
    email = str(info.get("email") or "")
    name = str(info.get("name") or info.get("given_name") or email or sub)
    store = store or UserStore()
    store.upsert_user_from_google(
        sub=sub,
        email=email,
        display_name=name,
        health_refresh_token=refresh,
    )
    claimed = store.claim_legacy_default_workouts(sub)
    # Fresh review/prod DB: no legacy default rows → seed this user from workspace
    # markdown once so first login is not an empty dashboard.
    seeded = 0
    try:
        from .workout_repo import WorkoutRepository

        repo = WorkoutRepository(db_path=store.db_path, user_id=sub)
        if repo.count() == 0:
            ws = (os.environ.get("LOCAL_WORKSPACE_DIR") or "").strip()
            if ws:
                result = repo.ensure_seeded_from_workspace(ws)
                if result.get("seeded"):
                    seeded = int(result.get("imported") or result.get("count") or 0)
    except Exception:
        seeded = 0
    sid = store.create_session(sub)
    return {
        "ok": True,
        "session_id": sid,
        "user": store.get_user(sub),
        "claimed_legacy_sessions": claimed,
        "seeded_sessions": seeded,
        "redirect_uri": redirect_uri(),
    }
