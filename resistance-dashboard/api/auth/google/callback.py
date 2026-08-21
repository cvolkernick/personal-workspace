"""GET /api/auth/google/callback — exchange code, set identity cookie, no SQLite."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from api.auth.session_util import (
    TOKEN_URL,
    USERINFO_URL,
    make_session,
    query_first,
    redirect_uri,
    session_set_cookie,
    verify_state,
)


def _fail_home(message: str) -> str:
    return "/?auth_error=" + quote(message[:200])


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        err = query_first(parsed.query, "error")
        code = query_first(parsed.query, "code")
        state = query_first(parsed.query, "state")
        try:
            if err:
                raise RuntimeError(f"Google OAuth error: {err}")
            if not code:
                raise RuntimeError("Missing OAuth code")
            if not verify_state(state):
                raise RuntimeError("Invalid or expired OAuth state — start login again")
            client_id = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
            client_secret = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
            if not client_id or not client_secret:
                raise RuntimeError("missing_env")
            body = urlencode(
                {
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                }
            ).encode("utf-8")
            req = Request(
                TOKEN_URL,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urlopen(req, timeout=30) as resp:
                    tokens = json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Token exchange failed HTTP {exc.code}") from exc
            access = tokens.get("access_token") or ""
            if not access:
                raise RuntimeError("No access_token from Google")
            info_req = Request(
                USERINFO_URL, headers={"Authorization": f"Bearer {access}"}
            )
            with urlopen(info_req, timeout=20) as resp:
                info = json.loads(resp.read().decode("utf-8"))
            sub = str(info.get("sub") or "").strip()
            if not sub:
                raise RuntimeError("Google userinfo missing sub")
            email = str(info.get("email") or "")
            name = str(info.get("name") or info.get("given_name") or email or sub)
            cookie = session_set_cookie(
                make_session(
                    {
                        "id": sub,
                        "email": email,
                        "display_name": name,
                        "refresh_token": tokens.get("refresh_token") or "",
                        "access_token": access,
                        "scope": tokens.get("scope") or "",
                        "expires_in": tokens.get("expires_in"),
                    }
                )
            )
            self.send_response(302)
            self.send_header("Set-Cookie", cookie)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except Exception as exc:
            self.send_response(302)
            self.send_header("Location", _fail_home(str(exc)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return
