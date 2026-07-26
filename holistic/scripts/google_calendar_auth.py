#!/usr/bin/env python3
"""One-time Google Calendar OAuth for the Time Allocator.

Writes GOOGLE_CALENDAR_REFRESH_TOKEN (and client id/secret if needed) into
  ~/.config/resistance-dashboard/env

Does NOT overwrite GOOGLE_REFRESH_TOKEN used by Google Health.

Prereqs (same Cloud project as Health, e.g. Grok-code):
  1. Enable Google Calendar API
  2. OAuth consent screen + your email as Test user
  3. Web application OAuth client with redirect:
       http://127.0.0.1:8789/
  4. Client JSON at ~/.config/resistance-dashboard/google-oauth-client.json

Usage:
  python3 holistic/scripts/google_calendar_auth.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
# Distinct port so Health auth (8788) can stay registered separately
CALLBACK_PORT = 8789
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/"

ENV_PATH = Path.home() / ".config" / "resistance-dashboard" / "env"
CREDENTIAL_CANDIDATES = [
    Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", "")),
    Path.home() / ".config" / "resistance-dashboard" / "google-oauth-client.json",
    Path.home() / "Downloads" / "credentials.json",
    Path.home() / "grok_excel_test" / "credentials.json",
]


def load_client() -> tuple[str, str, Path]:
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    sec = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if cid and sec:
        return cid, sec, Path("(env)")
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
            print(f"Loaded OAuth client from {p}")
            return cid, sec, p
    raise SystemExit(
        "No Google OAuth client JSON found.\n"
        f"Save Web client JSON to:\n  {Path.home() / '.config/resistance-dashboard/google-oauth-client.json'}\n"
        f"Authorized redirect URI must include:\n  {REDIRECT_URI}\n"
    )


def upsert_env(path: Path, updates: dict[str, str]) -> None:
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
            out.append(line)
            continue
        key = work.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{export_prefix}{key}='{updates[key]}'")
            keys_done.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in keys_done:
            out.append(f"export {k}='{v}'")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


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
        raise SystemExit(f"Token exchange failed HTTP {e.code}: {err}") from e


def main() -> int:
    client_id, client_secret, _src = load_client()
    code_holder: dict[str, str] = {}

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
                    b"<html><body style='font-family:system-ui;padding:2rem'>"
                    b"<h2>Google Calendar connected</h2>"
                    b"<p>You can close this tab and restart the Time Allocator.</p>"
                    b"</body></html>"
                )
            elif "error" in qs:
                code_holder["error"] = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authorization error")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code")

        def log_message(self, fmt, *args):
            return

    try:
        httpd = HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    except OSError as e:
        raise SystemExit(
            f"Port {CALLBACK_PORT} in use ({e}). Free it and retry."
        ) from e

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("Opening browser for Google Calendar consent…")
    print(f"Redirect URI (must match Cloud Console exactly):\n  {REDIRECT_URI}\n")
    print(f"If browser does not open, visit:\n{url}\n")
    webbrowser.open(url)

    thread = threading.Thread(target=httpd.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    httpd.server_close()

    if code_holder.get("error"):
        print(f"Authorization error: {code_holder['error']}")
        return 1
    if "code" not in code_holder:
        print(
            "\nNo authorization code received.\n"
            "Checklist:\n"
            "  1) Google Calendar API enabled\n"
            "  2) OAuth consent → Test users includes your Google email\n"
            f"  3) Web client redirect URI: {REDIRECT_URI}\n"
            "  4) Scopes: calendar.readonly (+ events.readonly)\n"
        )
        return 1

    tokens = exchange_code(client_id, client_secret, code_holder["code"])
    refresh = tokens.get("refresh_token")
    if not refresh:
        print(
            "No refresh_token returned. Remove app access at "
            "https://myaccount.google.com/permissions then run again "
            "(prompt=consent is required for a new refresh token)."
        )
        return 1

    upsert_env(
        ENV_PATH,
        {
            "GOOGLE_CLIENT_ID": client_id,
            "GOOGLE_CLIENT_SECRET": client_secret,
            "GOOGLE_CALENDAR_REFRESH_TOKEN": refresh,
        },
    )
    print(f"\nSaved GOOGLE_CALENDAR_REFRESH_TOKEN to {ENV_PATH}")

    access = tokens.get("access_token", "")
    if access:
        api = "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=3"
        req = urllib.request.Request(
            api, headers={"Authorization": f"Bearer {access}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                n = len(data.get("items") or [])
                print(f"Calendar API OK — sample calendars returned: {n}")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:500]
            print(f"Calendar smoke test HTTP {e.code}: {err}")
    print("\nRestart Time Allocator, then click “Sync calendar”.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
