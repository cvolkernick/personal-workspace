#!/usr/bin/env python3
"""One-shot localhost Plaid Link. Human in the browser. Token never printed."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from treasury.plaid_mcp.plaid_http import CONFIG_DIR, PLAID_HOSTS, load_credentials

LINK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>plaid-bank Link (X Money)</title>
  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; color: #111; }
    .ok { color: #0a0; }
    .err { color: #a00; }
  </style>
</head>
<body>
  <h1>plaid-bank — X Money</h1>
  <p>Read-only Plaid Link. Connect the X Money login (four checkings, one Item). EveryDay Checking / Navy Fed is out of scope.</p>
  <p id="status">Opening Plaid Link…</p>
  <script>
    const handler = Plaid.create({
      token: TOKEN_JSON,
      onSuccess: async (public_token) => {
        document.getElementById('status').textContent = 'Exchanging token…';
        const res = await fetch('/exchange', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({public_token})
        });
        const body = await res.json();
        if (res.ok && body.ok) {
          document.getElementById('status').className = 'ok';
          document.getElementById('status').textContent =
            'Linked. Access token saved locally (mode 600). You can close this tab.';
        } else {
          document.getElementById('status').className = 'err';
          document.getElementById('status').textContent = body.error || 'exchange failed';
        }
      },
      onExit: (err) => {
        if (err) {
          document.getElementById('status').className = 'err';
          document.getElementById('status').textContent = 'Link exited: ' + (err.display_message || err.error_code || 'cancelled');
        } else {
          document.getElementById('status').textContent = 'Link closed.';
        }
      }
    });
    handler.open();
  </script>
</body>
</html>
"""


def _plaid_post(env: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    host = PLAID_HOSTS[env]
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        host + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "personal-workspace-plaid-bank-link/0.1",
            "PLAID-CLIENT-ID": payload.get("client_id") or "",
            "PLAID-SECRET": payload.get("secret") or "",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"Plaid HTTP {e.code} {path}: {err}") from e


def create_link_token(creds: Dict[str, str]) -> str:
    payload = {
        "client_id": creds["client_id"],
        "secret": creds["secret"],
        "client_name": "plaid-bank",
        "language": "en",
        "country_codes": ["US"],
        "user": {"client_user_id": "chris-x-money"},
        "products": ["transactions"],
    }
    out = _plaid_post(creds["env"], "/link/token/create", payload)
    token = out.get("link_token")
    if not token:
        raise RuntimeError("Plaid did not return link_token")
    return str(token)


def exchange_public_token(creds: Dict[str, str], public_token: str) -> str:
    payload = {
        "client_id": creds["client_id"],
        "secret": creds["secret"],
        "public_token": public_token,
    }
    out = _plaid_post(creds["env"], "/item/public_token/exchange", payload)
    access = out.get("access_token")
    if not access:
        raise RuntimeError("Plaid did not return access_token")
    return str(access)


def save_access_token(token: str) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / "access_token"
    path.write_text(token + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="One-shot Plaid Link for X Money (localhost)")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    creds = load_credentials()
    if not creds["client_id"] or not creds["secret"]:
        print(
            "Need PLAID_CLIENT_ID and PLAID_SECRET (env or ~/.config/plaid-bank/).",
            file=sys.stderr,
        )
        return 1
    if creds["env"] not in PLAID_HOSTS:
        print(f"unsupported PLAID_ENV={creds['env']!r}", file=sys.stderr)
        return 1

    try:
        link_token = create_link_token(creds)
    except Exception as e:
        print(f"link/token/create failed: {e}", file=sys.stderr)
        return 1

    html = LINK_HTML.replace("TOKEN_JSON", json.dumps(link_token))
    state = {"done": False, "error": ""}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *a: Any) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), self.command + " " + self.path))

        def do_GET(self) -> None:  # noqa: N802
            if self.path not in ("/", "/index.html"):
                self.send_response(404)
                self.end_headers()
                return
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/exchange":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
                public_token = str(body.get("public_token") or "")
                if not public_token:
                    raise RuntimeError("missing public_token")
                access = exchange_public_token(creds, public_token)
                save_access_token(access)
                state["done"] = True
                out = {"ok": True}
            except Exception as e:
                state["error"] = str(e)
                out = {"ok": False, "error": "exchange failed"}
            raw_out = json.dumps(out).encode("utf-8")
            self.send_response(200 if out.get("ok") else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw_out)))
            self.end_headers()
            self.wfile.write(raw_out)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Plaid Link: {url}  (127.0.0.1 only; token not printed)", file=sys.stderr)
    if not args.no_browser:
        webbrowser.open(url)
    httpd.timeout = 0.5
    try:
        while not state["done"]:
            httpd.handle_request()
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 1
    if state["done"]:
        print("access token saved to ~/.config/plaid-bank/access_token (mode 600)", file=sys.stderr)
        return 0
    if state["error"]:
        print("link failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
