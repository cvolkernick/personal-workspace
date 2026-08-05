"""In-app Google Health OAuth (re-auth from the dashboard).

Local Mac dev: short-lived callback on http://127.0.0.1:8788/ (must match the
Web OAuth client's authorized redirect URI).

Remote Pi / Tailscale: when FITDASH_PUBLIC_URL is set to a non-localhost base,
start_auth_flow() returns use_login so the browser uses FitDash login OAuth
({public}/api/auth/google/callback) instead of unreachable localhost:8788.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALLBACK_PORT = 8788
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/"
ENV_PATH = Path.home() / ".config" / "resistance-dashboard" / "env"
CREDENTIAL_CANDIDATES = [
    Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", "")),
    Path.home() / ".config" / "resistance-dashboard" / "google-oauth-client.json",
    Path.home() / "Downloads" / "credentials.json",
    Path.home() / "grok_excel_test" / "credentials.json",
]

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "status": "idle",  # idle | pending | ok | error
    "message": "",
    "auth_url": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_httpd: Optional[HTTPServer] = None
_listener_thread: Optional[threading.Thread] = None


def load_oauth_client() -> Tuple[str, str, str]:
    """Return (client_id, client_secret, source_label)."""
    cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    sec = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if cid and sec:
        return cid, sec, "env"
    for p in CREDENTIAL_CANDIDATES:
        if not p or not str(p) or not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        block = data.get("web") or data.get("installed") or {}
        cid = (block.get("client_id") or "").strip()
        sec = (block.get("client_secret") or "").strip()
        if cid and sec:
            return cid, sec, str(p)
    raise RuntimeError(
        "No Google OAuth client found. Save the Web client JSON to "
        f"{Path.home() / '.config/resistance-dashboard/google-oauth-client.json'} "
        f"(redirect URI must include {REDIRECT_URI})."
    )


def upsert_env(path: Path, updates: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    keys_done: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        export_prefix = ""
        work = stripped
        if work.startswith("export "):
            export_prefix = "export "
            work = work[len("export ") :].strip()
        if work.startswith("#") or "=" not in work:
            if any(k in work for k in updates):
                continue
            out.append(line)
            continue
        key = work.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{export_prefix}{key}='{updates[key]}'")
            keys_done.add(key)
        else:
            out.append(line)
    if not any("GOOGLE_REFRESH_TOKEN" in l for l in out if not l.strip().startswith("#")):
        out.append("")
        out.append("# Google Health API — weight + sleep + nutrition")
    for k, v in updates.items():
        if k not in keys_done:
            out.append(f"export {k}='{v}'")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    # Live process picks up tokens immediately
    for k, v in updates.items():
        os.environ[k] = v


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
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


def build_auth_url(client_id: str) -> str:
    # include_granted_scopes=false: do NOT re-attach Calendar (or other) scopes
    # previously granted to this OAuth client. Google Health API rejects tokens
    # that also carry calendar scopes (DISALLOWED_OAUTH_SCOPES).
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _set_state(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)


def auth_flow_status() -> dict:
    with _lock:
        snap = dict(_state)
    # Live credential probe (cheap: no network if missing)
    has_client = bool(
        (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
        and (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    )
    has_refresh = bool((os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip())
    probe: Dict[str, Any] = {"credentials_present": has_client and has_refresh}
    if has_client and has_refresh:
        try:
            from .google_health import GoogleHealthClient, GoogleHealthError

            client = GoogleHealthClient()
            client.ensure_access_token()
            probe["token_ok"] = True
            probe["token_error"] = None
        except Exception as e:  # noqa: BLE001
            probe["token_ok"] = False
            probe["token_error"] = str(e)
    else:
        probe["token_ok"] = False
        probe["token_error"] = (
            "Missing GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN"
            if not has_client
            else "Missing GOOGLE_REFRESH_TOKEN"
        )
    return {
        "ok": True,
        "flow": snap,
        "redirect_uri": REDIRECT_URI,
        "env_path": str(ENV_PATH),
        **probe,
    }


def _cleanup_httpd() -> None:
    global _httpd, _listener_thread
    if _httpd is not None:
        try:
            _httpd.server_close()
        except Exception:
            pass
        _httpd = None
    _listener_thread = None


def _run_listener(client_id: str, client_secret: str) -> None:
    global _httpd
    code_holder: Dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                code_holder["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<!DOCTYPE html><html><body style='font-family:system-ui;padding:2rem;"
                    b"background:#0f1419;color:#e7ecf3'>"
                    b"<h2>Google Health connected</h2>"
                    b"<p>You can close this tab and return to the dashboard.</p>"
                    b"<script>try{window.close()}catch(e){}</script>"
                    b"</body></html>"
                )
            elif "error" in qs:
                code_holder["error"] = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:system-ui;padding:2rem'>"
                    b"<h2>Authorization error</h2><p>Return to the dashboard and try again.</p>"
                    b"</body></html>"
                )
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code")

        def log_message(self, fmt, *args):
            return

    try:
        httpd = HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
        _httpd = httpd
    except OSError as e:
        _set_state(
            status="error",
            error=f"Port {CALLBACK_PORT} in use: {e}",
            message=f"Close whatever is using port {CALLBACK_PORT} and try again.",
            finished_at=time.time(),
        )
        return

    # Single request with timeout via handle_request + socket timeout
    httpd.timeout = 300
    try:
        httpd.handle_request()
    except Exception as e:  # noqa: BLE001
        _set_state(
            status="error",
            error=str(e),
            message="OAuth callback listener failed.",
            finished_at=time.time(),
        )
        _cleanup_httpd()
        return

    try:
        if code_holder.get("error"):
            _set_state(
                status="error",
                error=code_holder["error"],
                message=f"Google authorization error: {code_holder['error']}",
                finished_at=time.time(),
            )
            return
        if "code" not in code_holder:
            _set_state(
                status="error",
                error="timeout_or_no_code",
                message="No authorization code received (timed out or cancelled).",
                finished_at=time.time(),
            )
            return
        tokens = exchange_code(client_id, client_secret, code_holder["code"])
        refresh = tokens.get("refresh_token")
        if not refresh:
            _set_state(
                status="error",
                error="no_refresh_token",
                message=(
                    "No refresh_token returned. Revoke app access at "
                    "https://myaccount.google.com/permissions then try again."
                ),
                finished_at=time.time(),
            )
            return
        upsert_env(
            ENV_PATH,
            {
                "GOOGLE_CLIENT_ID": client_id,
                "GOOGLE_CLIENT_SECRET": client_secret,
                "GOOGLE_REFRESH_TOKEN": refresh,
            },
        )
        # Drop any short-lived access token override
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        _set_state(
            status="ok",
            error=None,
            message=f"Saved credentials to {ENV_PATH}. Click Refresh remotes to pull Health data.",
            finished_at=time.time(),
        )
    except Exception as e:  # noqa: BLE001
        _set_state(
            status="error",
            error=str(e),
            message=str(e),
            finished_at=time.time(),
        )
    finally:
        _cleanup_httpd()


def start_auth_flow(*, force: bool = False) -> dict:
    """Begin OAuth; return auth_url for the browser. Idempotent while pending."""
    # Remote prod (FITDASH_PUBLIC_URL) cannot use 127.0.0.1:8788 — the browser
    # runs on phone/Mac, while the listener would be on the Pi. Route through
    # FitDash login OAuth which uses the public /api/auth/google/callback.
    public = (os.environ.get("FITDASH_PUBLIC_URL") or "").strip().rstrip("/")
    if public and "127.0.0.1" not in public and "localhost" not in public.lower():
        return {
            "ok": True,
            "status": "use_login",
            "use_same_window": True,
            "auth_url": "/api/auth/google/start",
            "redirect_uri": f"{public}/api/auth/google/callback",
            "message": (
                "Remote FitDash: Google Health re-auth uses sign-in callback "
                f"({public}/api/auth/google/callback), not localhost:8788."
            ),
        }

    with _lock:
        if _state.get("status") == "pending" and not force:
            return {
                "ok": True,
                "status": "pending",
                "auth_url": _state.get("auth_url"),
                "redirect_uri": REDIRECT_URI,
                "message": _state.get("message")
                or "Consent already in progress — complete Google sign-in in the open tab.",
            }

    if force:
        # Best-effort close prior listener so port 8788 is free.
        _cleanup_httpd()

    try:
        client_id, client_secret, src = load_oauth_client()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "status": "error"}

    auth_url = build_auth_url(client_id)
    _set_state(
        status="pending",
        message="Waiting for Google consent… complete sign-in in the browser tab.",
        auth_url=auth_url,
        started_at=time.time(),
        finished_at=None,
        error=None,
    )

    global _listener_thread
    t = threading.Thread(
        target=_run_listener,
        args=(client_id, client_secret),
        daemon=True,
        name="google-health-oauth",
    )
    _listener_thread = t
    t.start()

    return {
        "ok": True,
        "status": "pending",
        "auth_url": auth_url,
        "redirect_uri": REDIRECT_URI,
        "client_source": src,
        "message": (
            "Browser tab opened for Google consent. "
            f"Redirect URI must match Cloud Console: {REDIRECT_URI}"
        ),
    }
