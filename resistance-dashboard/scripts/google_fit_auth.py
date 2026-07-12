#!/usr/bin/env python3
"""
One-time Google Health API OAuth helper (replaces legacy Google Fit for new projects).

Opens a browser, captures the consent redirect on http://127.0.0.1:8788/,
and writes GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN
into ~/.config/resistance-dashboard/env

Prereqs (Google Cloud project, e.g. Grok-code):
  1. Enable Google Health API
  2. OAuth consent screen + your email as Test user
  3. Create OAuth client type "Web application"
  4. Authorized redirect URI: http://127.0.0.1:8788/  (and optionally http://localhost:8788/)
  5. Download the client JSON to:
       ~/.config/resistance-dashboard/google-oauth-client.json
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

# Google Health API scopes: weight/metrics, sleep, nutrition/macros, activity calories
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
# Fixed port so it can be registered on a Web OAuth client
CALLBACK_PORT = 8788
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
            kind = "web" if "web" in data else "installed"
            print(f"  client type in JSON: {kind}")
            return cid, sec, p
    raise SystemExit(
        "No Google OAuth client JSON found.\n\n"
        "Create a Web application OAuth client in project Grok-code, download the JSON,\n"
        f"and save it as:\n  {Path.home() / '.config/resistance-dashboard/google-oauth-client.json'}\n"
        f"Redirect URI on that client must include:\n  {REDIRECT_URI}\n"
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
            # drop old commented google stubs we replace
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
        out.append("# Google Health API — weight + sleep")
    for k, v in updates.items():
        if k not in keys_done:
            out.append(f"export {k}='{v}'")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
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
    client_id, client_secret, src = load_client()
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
                    b"<h2>Google Health connected</h2>"
                    b"<p>You can close this tab and return to the app.</p>"
                    b"</body></html>"
                )
            elif "error" in qs:
                code_holder["error"] = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authorization error - see terminal")
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
            f"Port {CALLBACK_PORT} is in use ({e}). Close whatever is using it and retry."
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
    print("Opening browser for Google Health consent…")
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
            "Checklist for project Grok-code:\n"
            "  1) Google Health API enabled\n"
            "  2) OAuth consent screen → Test users includes your Google email\n"
            "  3) Credentials → Create OAuth client ID → type Web application\n"
            f"  4) Authorized redirect URI exactly: {REDIRECT_URI}\n"
            "  5) Data Access / scopes include Health metrics + Sleep readonly\n"
            f"  6) Client JSON saved at ~/.config/resistance-dashboard/google-oauth-client.json\n"
        )
        return 1

    tokens = exchange_code(client_id, client_secret, code_holder["code"], REDIRECT_URI)
    refresh = tokens.get("refresh_token")
    if not refresh:
        print(
            "No refresh_token returned. Remove app access at "
            "https://myaccount.google.com/permissions then run again."
        )
        return 1

    upsert_env(
        ENV_PATH,
        {
            "GOOGLE_CLIENT_ID": client_id,
            "GOOGLE_CLIENT_SECRET": client_secret,
            "GOOGLE_REFRESH_TOKEN": refresh,
        },
    )
    print(f"\nSaved credentials to {ENV_PATH}")

    access = tokens.get("access_token", "")
    if access:
        # Smoke-test Google Health API weight list
        q = urllib.parse.urlencode({"pageSize": "5"})
        api = f"https://health.googleapis.com/v4/users/me/dataTypes/weight/dataPoints?{q}"
        req = urllib.request.Request(
            api,
            headers={"Authorization": f"Bearer {access}", "User-Agent": "rt-dashboard"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                n = len(data.get("dataPoints") or [])
                print(f"Google Health API OK — sample weight points returned: {n}")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:500]
            print(f"Health API smoke test HTTP {e.code}: {err}")
            print("Token was saved; fix API enablement/scopes if smoke test failed.")
    print("\nRestart the resistance dashboard to load live weight/sleep.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
